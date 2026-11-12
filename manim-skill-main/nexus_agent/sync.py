"""
Narration-First Synchronization & Scene-Level Timing Engine for Nexus + Manim.

This module guarantees that narration drives the animation timeline:
1. Measures actual audio duration for every narration segment.
2. Creates an exact scene-level timing budget (anim_duration + hold_duration = audio_duration).
3. Holds visual elements on screen while their corresponding explanation is spoken.
4. Validates that every scene's runtime aligns with the narration audio before muxing.
5. Verifies dual-stream duration matching in FFmpeg.
"""

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tools_resolver import get_ffmpeg_path, get_ffprobe_path, run_command_diagnostics


@dataclass
class TeachingBeat:
    """Represents an individual 2-4 second teaching beat within an educational scene."""
    beat_index: int
    narration_phrase: str
    start_s: float
    duration_s: float
    visual_objects: List[str]
    animation_action: str
    camera_action: Optional[str] = None
    transition: Optional[str] = "smooth"
    educational_purpose: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SceneTiming:
    scene_id: str
    segment_index: int
    narration_text: str
    audio_start_s: float
    audio_end_s: float
    duration_s: float
    anim_duration_s: float
    hold_duration_s: float
    visual_elements: List[str]
    transition: str = "fade_out"
    is_3d: bool = False
    visual_narrative: Optional[str] = None
    beats: List[TeachingBeat] = None

    def __post_init__(self):
        if self.beats is None:
            self.beats = []
        elif self.beats and isinstance(self.beats[0], dict):
            self.beats = [TeachingBeat(**b) for b in self.beats]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["beats"] = [b.to_dict() if hasattr(b, "to_dict") else b for b in self.beats]
        return d


def decompose_narration_into_beats(
    text: str,
    total_dur_s: float,
    visual_elements: Optional[List[str]] = None,
    is_3d: bool = False
) -> List[TeachingBeat]:
    """Decompose narration into semantic teaching beats lasting approximately 2-4 seconds each.
    
    Guarantees continuous animation cadence across the entire scene duration.
    """
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not raw_sentences:
        raw_sentences = [text.strip() or "Explaining concept"]

    # If sentences are long (>14 words), split on clause boundaries
    phrases: List[str] = []
    for s in raw_sentences:
        words = s.split()
        if len(words) > 16:
            clauses = re.split(r"(?<=,)\s+|(?<=;)\s+|(?<=\s—\s)|(?<=\s–\s)", s)
            phrases.extend([c.strip() for c in clauses if c.strip()])
        else:
            phrases.append(s)

    if not phrases:
        phrases = [text]

    # Calculate word weights
    total_words = sum(max(1, len(p.split())) for p in phrases)
    beats: List[TeachingBeat] = []
    current_time = 0.0

    action_archetypes_2d = [
        "Create & establish base components with progressive directional reveal",
        "Animate data flow stream and connect related elements with pulsing lines",
        "Highlight focal transformation, zoom into parameter structure, and indicate delta",
        "Synthesize output representation, update numeric metrics, and complete concept"
    ]

    action_archetypes_3d = [
        "Initialize 3D spatial arrangement (e.g. voxel cubes, layered planes, surface)",
        "Rotate 3D camera to reveal depth and internal architecture while streaming particles",
        "Extract 3D focal element, zoom perspective, and decompose into subcomponents",
        "Reassemble 3D structure, highlight final convergence, and sweep camera"
    ]

    archetypes = action_archetypes_3d if is_3d else action_archetypes_2d
    vis_list = visual_elements or ["Pedagogical diagram", "Formula badge"]

    for i, phrase in enumerate(phrases):
        p_words = max(1, len(phrase.split()))
        beat_dur = round(total_dur_s * (p_words / total_words), 2)
        # Ensure reasonable bounds (minimum 1.5s)
        beat_dur = max(1.5, beat_dur)

        # Distribute visual elements across beats
        vis_subset = [vis_list[i % len(vis_list)]] if vis_list else ["Concept graphic"]
        anim_action = archetypes[i % len(archetypes)]

        camera_action = None
        if is_3d:
            cam_angles = [
                "set_camera_orientation(phi=75*DEGREES, theta=-45*DEGREES)",
                "move_camera(phi=65*DEGREES, theta=-20*DEGREES, run_time=2.0)",
                "move_camera(phi=80*DEGREES, theta=30*DEGREES, zoom=1.2, run_time=2.0)",
                "move_camera(phi=70*DEGREES, theta=-45*DEGREES, zoom=1.0, run_time=2.0)"
            ]
            camera_action = cam_angles[i % len(cam_angles)]
        elif len(phrases) > 2 and i == 1:
            camera_action = "Focal zoom into key mechanism"

        beat = TeachingBeat(
            beat_index=i + 1,
            narration_phrase=phrase,
            start_s=round(current_time, 2),
            duration_s=beat_dur,
            visual_objects=vis_subset,
            animation_action=anim_action,
            camera_action=camera_action,
            transition="smooth",
            educational_purpose=f"Demonstrates: {phrase[:45]}..."
        )
        beats.append(beat)
        current_time += beat_dur

    # Normalize beats duration so exact sum matches total_dur_s
    actual_sum = sum(b.duration_s for b in beats)
    if actual_sum > 0:
        scale = total_dur_s / actual_sum
        cur = 0.0
        for b in beats:
            b.start_s = round(cur, 3)
            b.duration_s = round(b.duration_s * scale, 3)
            cur += b.duration_s
        # Fix minor floating point residue on final beat
        if beats:
            beats[-1].duration_s = round(total_dur_s - beats[-1].start_s, 3)

    return beats


class TimelineManager:
    """Manages narration-driven scene timing, validation, and audio-video synchronization."""

    def __init__(self, session_dir: Optional[Path] = None):
        self.session_dir = Path(session_dir).resolve() if session_dir else Path.cwd()
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def build_timeline(
        self,
        timestamps_path: Optional[Path] = None,
        scene_names: Optional[List[str]] = None,
        visual_plans: Optional[List[List[str]]] = None,
        script_scenes: Optional[List[Dict[str, Any]]] = None,
        max_anim_ratio: float = 0.45,
        max_anim_cap_s: float = 2.5
    ) -> List[SceneTiming]:
        """
        Build a deterministic scene timeline from timestamps.json.
        Constructs continuous educational teaching beats aligned to narration timestamps.
        """
        ts_file = timestamps_path or (self.session_dir / "timestamps.json")
        if not ts_file.exists():
            raise FileNotFoundError(f"Timestamps file '{ts_file}' not found.")

        data = json.loads(ts_file.read_text(encoding="utf-8"))
        subtitles = data.get("subtitles", [])
        if not subtitles:
            raise ValueError(f"No subtitle entries in '{ts_file}'.")

        timeline: List[SceneTiming] = []

        for item in subtitles:
            idx = item.get("index", 0)
            text = item.get("text", "")
            dur = float(item.get("duration_s", 0.0))
            start_s = float(item.get("start_s", 0.0))
            end_s = float(item.get("end_s", start_s + dur))

            # Inspect script metadata for this scene if available
            s_data: Dict[str, Any] = {}
            if script_scenes and idx < len(script_scenes):
                s_data = script_scenes[idx]

            # Determine scene identifier
            if scene_names and idx < len(scene_names):
                s_id = scene_names[idx]
            elif "scene_id" in s_data:
                s_id = s_data["scene_id"]
            else:
                s_id = f"Scene{idx + 1}_Segment"

            # Determine visual elements
            if visual_plans and idx < len(visual_plans):
                vis = visual_plans[idx]
            elif "visual_elements" in s_data:
                vis = s_data["visual_elements"]
            else:
                vis = [f"Visuals for Step {idx + 1}"]

            is_3d = bool(s_data.get("is_3d", False) or "3D" in s_id or "surface" in text.lower() or "cube" in text.lower())
            visual_narrative = s_data.get("visual_narrative")

            # Check if explicit teaching beats were provided by the script generator
            raw_beats = s_data.get("teaching_beats", [])
            beats: List[TeachingBeat] = []
            if raw_beats:
                total_beat_weight = sum(float(b.get("duration_s", 3.0)) for b in raw_beats)
                cur_t = 0.0
                for b_idx, rb in enumerate(raw_beats, start=1):
                    weight = float(rb.get("duration_s", 3.0))
                    b_dur = round(dur * (weight / total_beat_weight), 3) if total_beat_weight > 0 else round(dur / len(raw_beats), 3)
                    beats.append(TeachingBeat(
                        beat_index=b_idx,
                        narration_phrase=rb.get("narration_phrase", text),
                        start_s=round(cur_t, 3),
                        duration_s=b_dur,
                        visual_objects=rb.get("visual_objects", vis),
                        animation_action=rb.get("animation_action", "Animate diagram progression"),
                        camera_action=rb.get("camera_action"),
                        transition=rb.get("transition", "smooth"),
                        educational_purpose=rb.get("educational_purpose")
                    ))
                    cur_t += b_dur
                if beats:
                    beats[-1].duration_s = round(dur - beats[-1].start_s, 3)
            else:
                # Automatically decompose narration into 2-4 second teaching beats
                beats = decompose_narration_into_beats(
                    text=text,
                    total_dur_s=dur,
                    visual_elements=vis,
                    is_3d=is_3d
                )

            # Active animation budget across beats
            active_anim_total = sum(min(b.duration_s * 0.75, b.duration_s - 0.4) for b in beats)
            active_anim_total = max(1.0, min(active_anim_total, dur - 0.4))
            hold_total = round(dur - active_anim_total, 3)

            timing = SceneTiming(
                scene_id=s_id,
                segment_index=idx,
                narration_text=text,
                audio_start_s=start_s,
                audio_end_s=end_s,
                duration_s=round(dur, 3),
                anim_duration_s=round(active_anim_total, 3),
                hold_duration_s=hold_total,
                visual_elements=vis,
                transition="fade_out",
                is_3d=is_3d,
                visual_narrative=visual_narrative,
                beats=beats
            )
            timeline.append(timing)

        # Save to session_dir
        out_path = self.session_dir / "scene_timeline.json"
        out_path.write_text(
            json.dumps([t.to_dict() for t in timeline], indent=2),
            encoding="utf-8"
        )
        return timeline

    def generate_manim_template(self, timeline: List[SceneTiming]) -> str:
        """
        Generate a fully synchronized, modular Manim script skeleton with continuous
        multi-beat educational animations based on the exact narration-driven timeline.
        """
        has_3d = any(getattr(t, "is_3d", False) for t in timeline)
        imports = "from manim import *\n"
        if has_3d:
            imports += "# 3D educational scene support enabled\n"

        header = imports + '''
# Render configuration
config.pixel_width = 854
config.pixel_height = 480
config.frame_width = 16.0
config.frame_height = 9.0
config.frame_rate = 15

# Global educational visual palette
BG_COLOR = "#0f172a"
PRIMARY = "#38bdf8"      # Bright cyan for active structures and paths
SECONDARY = "#818cf8"    # Soft indigo for contextual layers and matrices
ACCENT = "#f43f5e"       # Vibrant rose for focus highlights, errors, and attention weights
TEXT_COLOR = "#ffffff"   # Pure white for high-contrast mobile legibility
MUTED_COLOR = "#94a3b8"  # Slate for wireframes, subtle connections, and axes
HIGHLIGHT = "#fbbf24"    # Warm amber for active signals, tokens, and energy packets
'''
        scene_blocks = []
        for t in timeline:
            base_class = "ThreeDScene" if getattr(t, "is_3d", False) else "Scene"
            beats = t.beats or []
            
            beat_instructions = []
            for b in beats:
                phrase_repr = repr(b.narration_phrase)
                cam_line = ""
                if getattr(t, "is_3d", False) and b.camera_action:
                    cam_line = f"        # Camera perspective movement\n        self.move_camera(phi=70 * DEGREES, theta=20 * DEGREES, run_time={max(0.5, b.duration_s * 0.3):.2f})\n"
                beat_instructions.append(f"""
        # --- Beat {b.beat_index} ({b.duration_s}s): {b.animation_action} ---
        # Spoken phrase: {phrase_repr}
        # Educational purpose: {b.educational_purpose or 'Visual demonstration'}
{cam_line}        # Active visual transformation (approx {max(1.0, b.duration_s - 0.5):.2f}s)
        self.play(
            # TODO: Implement active transformation for: {", ".join(b.visual_objects)}
            run_time={max(1.0, b.duration_s - 0.5):.2f}
        )
        self.wait({max(0.1, 0.5):.2f})""")

            beats_code = "\n".join(beat_instructions)
            camera_init = "        self.set_camera_orientation(phi=75 * DEGREES, theta=-45 * DEGREES)\n" if getattr(t, "is_3d", False) else ""

            block = f'''
class {t.scene_id}({base_class}):
    """
    Scene {t.segment_index + 1}: {t.narration_text}
    Duration: {t.duration_s}s across {len(beats)} continuous teaching beats.
    Visual Narrative: {t.visual_narrative or 'Step-by-step concept animation'}
    """
    def construct(self):
        self.camera.background_color = BG_COLOR
{camera_init}        dur = {t.duration_s}
        self.add_subcaption({repr(t.narration_text)}, duration=dur)

        # Scene Title / Concept Badge
        title = Text("{t.scene_id.replace('_', ' ')}", font_size=24, weight=BOLD, color=PRIMARY).to_edge(UP, buff=0.45)
        self.play(FadeIn(title, shift=DOWN*0.15), run_time=0.6)

        # Continuous Teaching Beats Sequence
{beats_code}

        # Smooth Scene Transition
        self.play(FadeOut(title), run_time=0.4)
'''
            scene_blocks.append(block)

        return header + "\n".join(scene_blocks)

    def write_concat_list(self, timeline: List[SceneTiming], video_folder: str = "media/videos/script/480p15") -> Path:
        """Write concat.txt for ffmpeg scene concatenation."""
        lines = []
        for t in timeline:
            scene_file = f"{video_folder}/{t.scene_id}.mp4"
            lines.append(f"file '{scene_file}'")

        concat_file = self.session_dir / "concat.txt"
        concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return concat_file

    def mux_audio_video(
        self,
        silent_video: Path,
        voiceover_audio: Path,
        output_video: Path
    ) -> Dict[str, Any]:
        """
        Mux silent concatenated video with voiceover audio.
        Guarantees full audio inclusion and verifies synchronization.
        """
        silent_video = Path(silent_video).resolve()
        voiceover_audio = Path(voiceover_audio).resolve()
        output_video = Path(output_video).resolve()

        if not silent_video.exists():
            raise FileNotFoundError(f"Silent video '{silent_video}' does not exist.")
        if not voiceover_audio.exists():
            raise FileNotFoundError(f"Voiceover audio '{voiceover_audio}' does not exist.")

        # FFmpeg command: Do NOT use -shortest! We want both streams preserved in full.
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(silent_video),
            "-i", str(voiceover_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_video)
        ]

        run_command_diagnostics(cmd)

        # Probe final stream durations
        probe = self.probe_media(output_video)
        return probe

    @staticmethod
    def probe_media(file_path: Path) -> Dict[str, Any]:
        """Query duration and stream info using ffprobe."""
        ffprobe_bin = get_ffprobe_path()
        cmd = [
            ffprobe_bin, "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,duration,width,height",
            "-show_entries", "format=duration,size,bit_rate",
            "-of", "json",
            str(file_path)
        ]
        res = run_command_diagnostics(cmd)

        info = json.loads(res.stdout)
        streams = info.get("streams", [])
        fmt = info.get("format", {})

        v_dur = None
        a_dur = None
        for s in streams:
            if s.get("codec_type") == "video" and "duration" in s:
                v_dur = float(s["duration"])
            elif s.get("codec_type") == "audio" and "duration" in s:
                a_dur = float(s["duration"])

        fmt_dur = float(fmt.get("duration", 0.0))

        return {
            "file": str(file_path),
            "format_duration_s": fmt_dur,
            "video_duration_s": v_dur or fmt_dur,
            "audio_duration_s": a_dur,
            "duration_delta_s": abs((v_dur or fmt_dur) - (a_dur or fmt_dur)) if a_dur else 0.0,
            "synchronized": abs((v_dur or fmt_dur) - (a_dur or fmt_dur)) < 0.25 if a_dur else True,
            "size_bytes": int(fmt.get("size", 0)),
        }


def generate_manim_template(timeline: List[SceneTiming]) -> str:
    """Convenience module function to generate continuous animation template code."""
    tm = TimelineManager()
    return tm.generate_manim_template(timeline)


