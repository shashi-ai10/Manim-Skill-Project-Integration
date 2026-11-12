"""Procedural sound design engine mapping educational timelines to audio scores."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from nexus_agent.tools_resolver import get_ffmpeg_path, run_command_diagnostics

from .mixer import AudioMixer
from .sfx_synth import blip, chime, click, impact, pop, scratch, whoosh


class SoundDesignEngine:
    """Orchestrates procedural audio synthesis, speech-aware ducking, and video muxing."""

    def __init__(
        self,
        timeline_path: Path | str = "scene_timeline.json",
        voiceover_path: Path | str = "neural_network_voiceover.mp3",
    ):
        self.timeline_path = Path(timeline_path)
        self.voiceover_path = Path(voiceover_path)

        if not self.timeline_path.exists():
            raise FileNotFoundError(f"Timeline file not found: {self.timeline_path}")
        if not self.voiceover_path.exists():
            raise FileNotFoundError(f"Voiceover file not found: {self.voiceover_path}")

        with open(self.timeline_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            self.timeline: List[Dict[str, Any]] = (
                raw_data.get("scenes", raw_data)
                if isinstance(raw_data, dict)
                else raw_data
            )

        # Total duration from last scene's audio_end_s or duration_s
        last_scene = self.timeline[-1]
        self.total_duration_s = float(last_scene.get("audio_end_s", 37.6))
        self.mixer = AudioMixer(duration_s=self.total_duration_s + 0.5)

    def generate_sound_score(self) -> Dict[str, Any]:
        """
        Synthesize procedural sound effects aligned with visual actions and scene holds,
        then mix and master with narration.
        """
        print(f"Loading narration bed from {self.voiceover_path.name}...")
        self.mixer.add_narration(self.voiceover_path, gain=1.0)
        print(f"  Detected {len(self.mixer.speech_intervals)} active speech window(s) for ducking.")

        print("Synthesizing and placing procedural SFX cues across 8 scenes...")
        placed_cues: List[Dict[str, Any]] = []

        for scene in self.timeline:
            idx = int(scene.get("segment_index", 0))
            sid = scene["scene_id"]
            t_start = float(scene.get("audio_start_s", 0.0))
            anim_dur = float(scene.get("anim_duration_s", 1.8))
            hold_dur = float(scene.get("hold_duration_s", 2.0))

            # 1. Subtle scene transition whoosh at scene entry
            whoosh_gain = 0.09 if idx == 0 else 0.12
            self.mixer.put_sfx(t_start + 0.15, whoosh(0.35, 380, 1600, peak=0.45), gain=whoosh_gain, pan=-0.2)
            placed_cues.append({"scene": sid, "t": round(t_start + 0.15, 2), "type": "whoosh"})

            # 2. Scene-specific pedagogical visual cues
            if sid == "Scene1_BrainAnalogy" or (idx == 0 and "BrainAnalogy" in sid):
                # Nodes pop in
                self.mixer.put_sfx(t_start + 1.20, pop(680, 310), gain=0.18, pan=0.1)
                # Signal wave arrives
                self.mixer.put_sfx(t_start + 2.40, blip(440, dur=0.25, tau=0.08), gain=0.16, pan=-0.1)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 1.20, 2), "type": "pop"},
                    {"scene": sid, "t": round(t_start + 2.40, 2), "type": "blip"},
                ])

            elif sid == "Scene2_InterconnectedNeurons" or (idx == 1 and "Interconnected" in sid):
                # Synapses connect sequentially
                for step, offset in enumerate([0.70, 1.10, 1.50]):
                    freq = 1600 + step * 250
                    self.mixer.put_sfx(t_start + offset, click(freq, dur=0.04), gain=0.15, pan=-0.3 + step * 0.3)
                    placed_cues.append({"scene": sid, "t": round(t_start + offset, 2), "type": "click"})

            elif sid == "Scene3_InputLayer" or (idx == 2 and "InputLayer" in sid):
                # Input pixels enter
                self.mixer.put_sfx(t_start + 0.85, pop(540, 260), gain=0.16, pan=-0.3)
                self.mixer.put_sfx(t_start + 1.35, pop(620, 300), gain=0.16, pan=-0.1)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 0.85, 2), "type": "pop"},
                    {"scene": sid, "t": round(t_start + 1.35, 2), "type": "pop"},
                ])

            elif sid == "Scene4_HiddenProcessing" or (idx == 3 and "Hidden" in sid):
                # Summation formula appears
                self.mixer.put_sfx(t_start + 1.10, blip(523.25, dur=0.28), gain=0.18, pan=0.0)
                # Activation function activates
                self.mixer.put_sfx(t_start + 1.80, blip(659.25, dur=0.30), gain=0.20, pan=0.2)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 1.10, 2), "type": "blip"},
                    {"scene": sid, "t": round(t_start + 1.80, 2), "type": "blip"},
                ])

            elif sid == "Scene5_OutputPrediction" or (idx == 4 and "Output" in sid):
                # Output winner box reveals
                self.mixer.put_sfx(t_start + 1.25, pop(780, 380), gain=0.22, pan=0.3)
                # Confidence confirmation blip
                self.mixer.put_sfx(t_start + 2.05, blip(880.0, dur=0.35, tau=0.12), gain=0.18, pan=0.2)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 1.25, 2), "type": "pop"},
                    {"scene": sid, "t": round(t_start + 2.05, 2), "type": "blip"},
                ])

            elif sid == "Scene6_LossComparison" or (idx == 5 and "Loss" in sid):
                # Difference calculation
                self.mixer.put_sfx(t_start + 1.05, scratch(0.40), gain=0.14, pan=-0.2)
                # Error loss value lands
                self.mixer.put_sfx(t_start + 1.90, blip(330.0, dur=0.32, tau=0.10), gain=0.20, pan=0.0)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 1.05, 2), "type": "scratch"},
                    {"scene": sid, "t": round(t_start + 1.90, 2), "type": "blip"},
                ])

            elif sid == "Scene7_Backpropagation" or (idx == 6 and "Backpropagation" in sid):
                # Reverse error signal sweep
                self.mixer.put_sfx(t_start + 0.90, whoosh(0.40, 1800, 420), gain=0.15, pan=0.3)
                # Weight update pop
                self.mixer.put_sfx(t_start + 1.75, pop(480, 820), gain=0.22, pan=-0.2)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 0.90, 2), "type": "whoosh"},
                    {"scene": sid, "t": round(t_start + 1.75, 2), "type": "pop"},
                ])

            elif sid == "Scene8_Conclusion" or (idx == len(self.timeline) - 1 and idx >= 7 and "Conclusion" in sid):
                # Trained network completion bell chord with Schroeder Freeverb space
                chord = [587.33, 739.99, 880.0, 1174.66]
                self.mixer.put_spatial_sfx(t_start + 0.95, chime(chord, dur=2.2, tau=0.7), gain=0.24, pan=0.0, reverb_mix=0.20)
                # Subtle grounding impact
                self.mixer.put_sfx(t_start + 0.98, impact(0.6), gain=0.12, pan=0.0)
                placed_cues.extend([
                    {"scene": sid, "t": round(t_start + 0.95, 2), "type": "chime"},
                    {"scene": sid, "t": round(t_start + 0.98, 2), "type": "impact"},
                ])
            else:
                # Generalized adaptive sound design for any arbitrary educational scenes
                is_last = (idx == len(self.timeline) - 1)
                if is_last:
                    # Closing chime chord and grounding impact
                    chord = [587.33, 739.99, 880.0, 1174.66]
                    self.mixer.put_sfx(t_start + 0.90, chime(chord, dur=2.0, tau=0.7), gain=0.24, pan=0.0)
                    self.mixer.put_sfx(t_start + 0.95, impact(0.55), gain=0.12, pan=0.0)
                    placed_cues.extend([
                        {"scene": sid, "t": round(t_start + 0.90, 2), "type": "chime"},
                        {"scene": sid, "t": round(t_start + 0.95, 2), "type": "impact"},
                    ])
                else:
                    # Visual entrance pop
                    pop_offset = min(1.10, max(0.65, anim_dur * 0.45))
                    pop_f1 = 540 + (idx % 4) * 70
                    self.mixer.put_sfx(t_start + pop_offset, pop(pop_f1, int(pop_f1 * 0.55)), gain=0.17, pan=-0.2 + (idx % 3) * 0.2)
                    placed_cues.append({"scene": sid, "t": round(t_start + pop_offset, 2), "type": "pop"})

                    # Secondary emphasis blip or click
                    blip_offset = min(anim_dur + 0.1, pop_offset + 0.75)
                    blip_freq = 523.25 * (1.0 + 0.12 * (idx % 4))
                    self.mixer.put_sfx(t_start + blip_offset, blip(blip_freq, dur=0.25), gain=0.16, pan=0.15)
                    placed_cues.append({"scene": sid, "t": round(t_start + blip_offset, 2), "type": "blip"})

        print(f"Mastering multi-track audio to -14.0 LUFS target...")
        stats = self.mixer.master(target_lufs=-14.0, ceiling_db=-1.5)
        print(f"  Mastered: Pre={stats['pre_lufs']} LUFS -> Final={stats['final_lufs']} LUFS, TruePeak={stats['final_tp_db']} dBFS")

        return {
            "total_cues_placed": len(placed_cues),
            "cues": placed_cues,
            "master_stats": stats,
        }

    def export_audio_master(
        self,
        wav_path: Path | str = "neural_network_audio_master.wav",
        mp3_path: Path | str = "neural_network_audio_master.mp3",
    ) -> Dict[str, str]:
        """Export mastered stereo audio to WAV and MP3."""
        wav_out = self.mixer.export_wav(wav_path)
        mp3_out = self.mixer.export_mp3(mp3_path)
        print(f"Exported WAV: {wav_out}")
        print(f"Exported MP3: {mp3_out}")
        return {"wav": str(wav_out), "mp3": str(mp3_out)}

    def mux_with_video(
        self,
        video_source: Path | str = "neural_network_synced.mp4",
        output_video: Path | str = "neural_network_with_sfx.mp4",
        master_audio: Optional[Path | str] = None,
    ) -> Path:
        """
        Mux picture and new mastered procedural audio into a demonstration video.
        Preserves original neural_network_synced.mp4 untouched.
        """
        video_source = Path(video_source)
        output_video = Path(output_video)

        if master_audio is None:
            master_audio = Path("neural_network_audio_master.wav")
            if not master_audio.exists():
                self.export_audio_master(master_audio)
        else:
            master_audio = Path(master_audio)

        print(f"Muxing {video_source.name} + {master_audio.name} -> {output_video.name}...")
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(video_source),
            "-i", str(master_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_video),
        ]
        run_command_diagnostics(cmd)
        print(f"Successfully generated video with procedural SFX: {output_video}")
        return output_video

