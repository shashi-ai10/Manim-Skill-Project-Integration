"""Two-pass EBU R128 audio loudness mastering targeting -14.0 LUFS and -1.0 dBTP."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from nexus_agent.tools_resolver import get_ffmpeg_path, run_command_diagnostics, validate_media_file

TARGET_I: float = -14.0
TARGET_TP: float = -1.0
TARGET_LRA: float = 11.0


class TwoPassLoudnorm:
    """Implements professional broadcast-standard two-pass EBU R128 audio mastering."""

    @staticmethod
    def analyze_first_pass(audio_or_video_path: Path | str) -> Dict[str, str]:
        """
        Pass 1: Run ffmpeg loudnorm analysis filter to measure input audio metrics.
        Returns a dictionary with measured values or raises RuntimeError if analysis fails.
        """
        path = Path(audio_or_video_path)
        filter_str = (
            f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json"
        )
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-nostats",
            "-i", str(path),
            "-af", filter_str,
            "-vn", "-f", "null", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stderr = proc.stderr

        # Extract the JSON block printed by loudnorm filter
        start = stderr.rfind("{")
        end = stderr.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"Loudnorm Pass 1 failed to output JSON metrics:\n{stderr}")

        try:
            metrics = json.loads(stderr[start : end + 1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Could not parse loudnorm Pass 1 JSON output: {e}\n{stderr}")

        required = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
        if not required.issubset(metrics.keys()):
            raise RuntimeError(f"Missing required loudnorm keys in Pass 1 metrics: {metrics}")

        return metrics

    @staticmethod
    def apply_second_pass(
        input_path: Path | str,
        output_path: Path | str,
        metrics: Dict[str, str],
        audio_codec: str = "pcm_s16le",
        bitrate: Optional[str] = None,
        sample_rate: int = 48000,
    ) -> Path:
        """
        Pass 2: Apply linear normalization filter using the measured Pass 1 metrics.
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filter_str = (
            f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
            f":measured_I={metrics['input_i']}"
            f":measured_TP={metrics['input_tp']}"
            f":measured_LRA={metrics['input_lra']}"
            f":measured_thresh={metrics['input_thresh']}"
            f":offset={metrics['target_offset']}"
            f":linear=true"
        )

        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-nostats",
            "-i", str(input_path),
            "-af", filter_str,
            "-ar", str(sample_rate),
        ]

        if output_path.suffix.lower() in (".mp4", ".mov", ".mkv"):
            cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", bitrate or "192k", "-movflags", "+faststart"])
        else:
            cmd.extend(["-c:a", audio_codec])
            if bitrate:
                cmd.extend(["-b:a", bitrate])

        temp_partial_path = output_path.with_name(f"{output_path.stem}.part{output_path.suffix}")
        temp_partial_path.unlink(missing_ok=True)
        cmd.append(str(temp_partial_path))

        try:
            run_command_diagnostics(cmd)
            if not temp_partial_path.exists() or temp_partial_path.stat().st_size == 0:
                raise RuntimeError(f"FFmpeg loudnorm pass 2 failed to produce {temp_partial_path.name}")
            
            output_path.unlink(missing_ok=True)
            temp_partial_path.replace(output_path)
            return output_path
        except Exception:
            temp_partial_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            raise

    @classmethod
    def master_audio(
        cls,
        input_path: Path | str,
        output_path: Path | str,
    ) -> Dict[str, Any]:
        """Execute full two-pass mastering and measure the final output."""
        print(f"Loudnorm Pass 1: Analyzing {Path(input_path).name}...")
        metrics = cls.analyze_first_pass(input_path)
        print(f"  Measured: I={metrics['input_i']} LUFS, TP={metrics['input_tp']} dBFS, LRA={metrics['input_lra']} LU")

        print(f"Loudnorm Pass 2: Applying linear normalization to {Path(output_path).name}...")
        cls.apply_second_pass(input_path, output_path, metrics)

        # Integrity check: Ensure output file is complete and not truncated
        in_val = validate_media_file(input_path)
        out_val = validate_media_file(output_path)
        in_dur = in_val.get("duration_s", 0.0)
        out_dur = out_val.get("duration_s", 0.0)
        if in_dur > 0 and out_dur > 0 and abs(out_dur - in_dur) > 0.15:
            output_path_p = Path(output_path)
            output_path_p.unlink(missing_ok=True)
            raise RuntimeError(
                f"Audio mastering produced truncated file: input={in_dur:.3f}s, output={out_dur:.3f}s (delta: {abs(out_dur - in_dur):.3f}s). "
                f"Corrupted output removed."
            )

        # Verification pass using ebur128
        final_stats = cls.verify_loudness(output_path)
        print(f"  Verified Final: I={final_stats['integrated_lufs']} LUFS, TruePeak={final_stats['true_peak_db']} dBFS, LRA={final_stats['lra']} LU")

        return {
            "input_metrics": metrics,
            "final_stats": final_stats,
            "output_path": str(output_path),
        }

    @staticmethod
    def verify_loudness(audio_or_video_path: Path | str) -> Dict[str, float]:
        """Measure precise integrated loudness and true peak using ebur128 from final Summary block."""
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-hide_banner",
            "-i", str(audio_or_video_path),
            "-af", "ebur128=peak=true",
            "-f", "null", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stderr = proc.stderr

        # Look specifically in the Summary block at the end of output
        summary_pos = stderr.rfind("Summary:")
        text_to_search = stderr[summary_pos:] if summary_pos != -1 else stderr

        i_match = re.search(r"I:\s+([-\d\.]+)\s+LUFS", text_to_search)
        tp_match = re.search(r"Peak:\s+([-\d\.]+)\s+dBFS", text_to_search)
        lra_match = re.search(r"LRA:\s+([-\d\.]+)\s+LU", text_to_search)

        return {
            "integrated_lufs": float(i_match.group(1)) if i_match else -99.0,
            "true_peak_db": float(tp_match.group(1)) if tp_match else -99.0,
            "lra": float(lra_match.group(1)) if lra_match else 0.0,
        }
