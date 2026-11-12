"""Master post-production pipeline orchestrating duration alignment, boundary protection, and two-pass loudness mastering."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import soundfile as sf
import numpy as np

from .boundary_fades import apply_boundary_fades
from .loudnorm import TwoPassLoudnorm
from .overlays import build_composite_filtergraph
from nexus_agent.audio.engine import SoundDesignEngine
from nexus_agent.tools_resolver import get_ffmpeg_path, get_ffprobe_path, run_command_diagnostics, validate_media_file


def probe_video_duration(video_path: Path | str) -> float:
    """Return exact duration of video in seconds using ffprobe."""
    ffprobe_bin = get_ffprobe_path()
    cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "format=duration:stream=duration",
        "-of", "json",
        str(video_path),
    ]
    res = run_command_diagnostics(cmd)
    data = json.loads(res.stdout)
    # Check stream duration first, fall back to format duration
    for s in data.get("streams", []):
        if "duration" in s and s["duration"]:
            return float(s["duration"])
    return float(data["format"]["duration"])


class PostProductionPipeline:
    """Orchestrates professional broadcast mastering and post-production."""

    def __init__(
        self,
        timeline_path: Path | str = "scene_timeline.json",
        video_source: Path | str = "neural_network_synced.mp4",
        voiceover_source: Path | str = "neural_network_voiceover.mp3",
    ):
        self.timeline_path = Path(timeline_path)
        self.video_source = Path(video_source)
        self.voiceover_source = Path(voiceover_source)

        if not self.video_source.exists():
            raise FileNotFoundError(f"Video source not found: {self.video_source}")
        if not self.voiceover_source.exists():
            raise FileNotFoundError(f"Voiceover source not found: {self.voiceover_source}")

        self.exact_video_duration_s = probe_video_duration(self.video_source)

    def produce_broadcast_master(
        self,
        output_video: Path | str = "neural_network_broadcast.mp4",
        temp_dir: Optional[Path | str] = None,
        subtitles_srt: Optional[Path | str] = None,
        master_wav_path: Optional[Path | str] = None,
    ) -> Dict[str, Any]:
        """
        Execute full post-production:
        1. Synthesize narration + bounded SFX soundtrack.
        2. Align audio duration exactly to the video duration (37.598s).
        3. Apply 30ms anti-pop boundary fades.
        4. Apply two-pass EBU R128 loudness mastering (-14.0 LUFS, -1.0 dBTP).
        5. Losslessly mux with video and apply subtitles LAST.
        """
        output_video = Path(output_video)
        output_video.parent.mkdir(parents=True, exist_ok=True)

        if temp_dir is None:
            temp_dir = output_video.parent / "_temp_post"
        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        print(f"Step 1: Ingesting video duration: {self.exact_video_duration_s:.6f}s")

        # Check if a valid permanent master audio file already exists (e.g. from prior clean pass or recovery)
        reused_master = False
        mastered_wav = temp_dir / "mastered_ebu128.wav"
        loudnorm_results: Dict[str, Any] = {"input_metrics": {}, "final_stats": {}}

        if master_wav_path is not None and Path(master_wav_path).exists():
            val = validate_media_file(master_wav_path)
            if val.get("duration_s", 0) > 0 and abs(val["duration_s"] - self.exact_video_duration_s) < 0.25:
                print(f"  [REUSE] Found valid master audio at {Path(master_wav_path).name} ({val['duration_s']:.3f}s). Skipping audio re-mastering.")
                mastered_wav = Path(master_wav_path)
                permanent_master_wav = Path(master_wav_path)
                final_loudness = TwoPassLoudnorm.verify_loudness(mastered_wav)
                loudnorm_results = {"input_metrics": {}, "final_stats": final_loudness}
                reused_master = True

        if not reused_master:
            # Clean up any partial or interrupted temporary output
            mastered_wav.unlink(missing_ok=True)

            # 1. Generate sound design score
            engine = SoundDesignEngine(
                timeline_path=self.timeline_path,
                voiceover_path=self.voiceover_source,
            )
            engine.generate_sound_score()
            raw_wav_path = temp_dir / "raw_mix.wav"
            engine.mixer.export_wav(raw_wav_path)

            # 2. Align audio duration to exact video duration and apply 30ms boundary protection
            print(f"Step 2: Aligning audio duration to exact video length ({self.exact_video_duration_s:.3f}s)...")
            raw_data, sr = sf.read(str(raw_wav_path))
            target_samples = int(self.exact_video_duration_s * sr)

            if len(raw_data) > target_samples:
                aligned_data = raw_data[:target_samples]
            elif len(raw_data) < target_samples:
                padding = np.zeros((target_samples - len(raw_data), raw_data.shape[1] if raw_data.ndim > 1 else 1))
                aligned_data = np.vstack([raw_data, padding]) if raw_data.ndim > 1 else np.concatenate([raw_data, padding])
            else:
                aligned_data = raw_data

            # Apply 30ms anti-pop boundary fades
            protected_data = apply_boundary_fades(aligned_data, sr=sr, fade_duration_s=0.03)
            protected_wav = temp_dir / "protected_aligned.wav"
            sf.write(str(protected_wav), protected_data, sr)

            # 3. Two-pass EBU R128 Loudness Mastering
            print("Step 3: Executing two-pass EBU R128 mastering (-14 LUFS, -1 dBTP)...")
            loudnorm_results = TwoPassLoudnorm.master_audio(protected_wav, mastered_wav)

            # Save permanent master audio file
            if master_wav_path is not None:
                permanent_master_wav = Path(master_wav_path)
            elif output_video.stem == "neural_network_broadcast":
                permanent_master_wav = Path("neural_network_broadcast_master.wav")
            else:
                permanent_master_wav = output_video.parent / f"{output_video.stem}_master.wav"

            sf.write(str(permanent_master_wav), *sf.read(str(mastered_wav)))

        # 4. Final Muxing (video copy + mastered audio + optional subtitles LAST)
        print(f"Step 4: Compositing broadcast video -> {output_video.name}...")
        temp_muxed = temp_dir / "_muxed_base.mp4"
        ffmpeg_bin = get_ffmpeg_path()
        mux_cmd = [
            ffmpeg_bin, "-y",
            "-i", str(self.video_source),
            "-i", str(mastered_wav),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(temp_muxed),
        ]
        run_command_diagnostics(mux_cmd)

        if subtitles_srt and Path(subtitles_srt).exists():
            print("  Applying subtitles strictly LAST...")
            build_composite_filtergraph(
                base_video=temp_muxed,
                output_video=output_video,
                subtitles_srt=subtitles_srt,
            )
        else:
            temp_muxed.replace(output_video)

        # 5. Measure and verify final output
        final_video_duration = probe_video_duration(output_video)
        final_loudness = TwoPassLoudnorm.verify_loudness(output_video)

        print("\n----------------------------------------------------------------")
        print("Broadcast Post-Production Verification Complete:")
        print(f"  Final Video File: {output_video.name} ({output_video.stat().st_size:,} bytes)")
        print(f"  Video Stream Duration: {final_video_duration:.3f}s (Target: {self.exact_video_duration_s:.3f}s)")
        print(f"  Duration Delta: {abs(final_video_duration - self.exact_video_duration_s) * 1000:.1f} ms")
        print(f"  Integrated Loudness: {final_loudness['integrated_lufs']:.2f} LUFS (Target: -14.0 LUFS)")
        print(f"  True Peak: {final_loudness['true_peak_db']:.2f} dBFS (Target: <= -1.0 dBFS)")
        print(f"  Loudness Range (LRA): {final_loudness['lra']:.2f} LU")
        print("----------------------------------------------------------------")

        return {
            "output_video": str(output_video),
            "master_wav": str(permanent_master_wav),
            "video_duration_s": final_video_duration,
            "target_duration_s": self.exact_video_duration_s,
            "duration_delta_ms": abs(final_video_duration - self.exact_video_duration_s) * 1000,
            "loudness": final_loudness,
            "loudnorm_pass1": loudnorm_results["input_metrics"],
        }

