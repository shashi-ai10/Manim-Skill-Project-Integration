"""Unit and integration tests for the continuous visual animation engine and 3D scenes."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from nexus_agent.sync import (
    TeachingBeat,
    SceneTiming,
    TimelineManager,
    decompose_narration_into_beats,
    generate_manim_template,
)
from nexus_agent.contact_sheet import generate_contact_sheet
from nexus_agent.vision_review import VisualQualityReviewer
from nexus_agent.tools_resolver import get_ffmpeg_path, get_ffprobe_path


class TestVisualEngine(unittest.TestCase):
    """Test suite for narration-to-visual synchronization, teaching beats, and 3D scenes."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="visual_engine_test_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_teaching_beat_dataclass(self):
        """Verify TeachingBeat creation, timing constraints, and serialization."""
        beat = TeachingBeat(
            beat_index=1,
            narration_phrase="Every digital image is composed of tiny squares called pixels.",
            start_s=0.0,
            duration_s=3.2,
            visual_objects=["composite_image", "pixel_grid"],
            animation_action="zoom camera into pixel grid",
            camera_action="move_camera zoom=1.5",
            transition="smooth",
            educational_purpose="Demonstrate discrete pixel block composition",
        )
        self.assertEqual(beat.beat_index, 1)
        self.assertEqual(beat.duration_s, 3.2)
        self.assertIn("pixel_grid", beat.visual_objects)
        self.assertEqual(beat.camera_action, "move_camera zoom=1.5")

    def test_02_decompose_narration_into_beats(self):
        """Verify narration text decomposition into 2-4s pedagogical beats matching total duration."""
        narration = (
            "Every digital image is built from tiny individual blocks called pixels. "
            "When we magnify the image closely, the smooth picture separates into a discrete grid of colored squares. "
            "Each individual pixel stores a specific recipe of red, green, and blue light."
        )
        total_dur = 9.0
        visual_elements = ["composite_image", "pixel_grid", "rgb_cube", "color_channels"]

        beats = decompose_narration_into_beats(narration, total_dur, visual_elements, is_3d=True)

        self.assertGreaterEqual(len(beats), 2)
        total_beat_time = sum(b.duration_s for b in beats)
        self.assertAlmostEqual(total_beat_time, total_dur, delta=0.01)

        # Ensure start_s are sequential
        expected_start = 0.0
        for b in beats:
            self.assertAlmostEqual(b.start_s, expected_start, delta=0.01)
            expected_start += b.duration_s
            self.assertTrue(len(b.narration_phrase) > 0)
            self.assertTrue(len(b.animation_action) > 0)
            self.assertTrue(len(b.camera_action) > 0)

    def test_03_timeline_manager_with_beats_and_3d(self):
        """Verify TimelineManager builds timelines with beats, 3D flags, and duration adherence."""
        timestamps_file = self.test_dir / "timestamps.json"
        timestamps_file.write_text(json.dumps({
            "total_duration_s": 8.0,
            "subtitles": [
                {"index": 0, "text": "Zooming into pixels.", "start_s": 0.0, "duration_s": 4.0},
                {"index": 1, "text": "Decomposing into RGB channels.", "start_s": 4.0, "duration_s": 4.0},
            ]
        }), encoding="utf-8")

        script_scenes = [
            {
                "scene_id": "Scene1_PixelGrid",
                "is_3d": False,
                "narration": "Zooming into pixels.",
                "visual_narrative": "Zoom camera from composite image to a 2D pixel grid.",
                "visual_elements": ["composite_image", "grid_overlay"],
            },
            {
                "scene_id": "Scene2_3DCubeDecomposition",
                "is_3d": True,
                "narration": "Decomposing into RGB channels.",
                "visual_narrative": "Extract single pixel into 3D cube and split into RGB channel bars.",
                "visual_elements": ["pixel_cube", "red_channel", "green_channel", "blue_channel"],
                "teaching_beats": [
                    {"narration_phrase": "Extract pixel into 3D", "visual_action": "Extrude cube and tilt camera"},
                    {"narration_phrase": "Separate RGB channels", "visual_action": "Split cube into 3 color bars"}
                ]
            }
        ]

        tm = TimelineManager(session_dir=self.test_dir)
        timeline = tm.build_timeline(
            timestamps_path=timestamps_file,
            scene_names=["Scene1_PixelGrid", "Scene2_3DCubeDecomposition"],
            visual_plans=[s["visual_elements"] for s in script_scenes],
            script_scenes=script_scenes,
        )

        self.assertEqual(len(timeline), 2)
        self.assertFalse(timeline[0].is_3d)
        self.assertTrue(timeline[1].is_3d)
        self.assertGreater(len(timeline[0].beats), 0)
        self.assertEqual(len(timeline[1].beats), 2)

        # Verify scene_timeline.json was written and contains beats and is_3d
        timeline_file = self.test_dir / "scene_timeline.json"
        self.assertTrue(timeline_file.exists())
        saved_data = json.loads(timeline_file.read_text(encoding="utf-8"))
        scenes_data = saved_data.get("scenes", saved_data) if isinstance(saved_data, dict) else saved_data
        self.assertTrue(scenes_data[1]["is_3d"])
        self.assertEqual(len(scenes_data[1]["beats"]), 2)

    def test_04_manim_template_syntax_compilation(self):
        """Verify generated Manim code templates for both 2D and 3D scenes compile cleanly."""
        timing_3d = SceneTiming(
            scene_id="Scene1_3DPixelDecomposition",
            segment_index=0,
            narration_text="We extract a single pixel as a 3D volume.",
            audio_start_s=0.0,
            audio_end_s=4.0,
            duration_s=4.0,
            anim_duration_s=3.6,
            hold_duration_s=0.4,
            visual_elements=["pixel_cube", "rgb_channels"],
            is_3d=True,
            visual_narrative="Pixel lifts out into 3D and splits into channels.",
            beats=[
                TeachingBeat(
                    beat_index=1,
                    narration_phrase="Extract pixel as 3D cube.",
                    start_s=0.0,
                    duration_s=2.0,
                    visual_objects=["pixel_cube"],
                    animation_action="Extrude 3D Cube and rotate camera",
                    camera_action="move_camera(phi=65*DEGREES, theta=-45*DEGREES)",
                    transition="smooth",
                    educational_purpose="Establish 3D spatial depth",
                ),
                TeachingBeat(
                    beat_index=2,
                    narration_phrase="Split into color channels.",
                    start_s=2.0,
                    duration_s=2.0,
                    visual_objects=["red_bar", "green_bar", "blue_bar"],
                    animation_action="Split cube into RGB color components",
                    camera_action="zoom camera",
                    transition="smooth",
                    educational_purpose="Show additive color decomposition",
                ),
            ],
        )

        code = generate_manim_template([timing_3d])
        self.assertIn("class Scene1_3DPixelDecomposition(ThreeDScene):", code)
        self.assertIn("self.set_camera_orientation", code)
        self.assertIn("self.move_camera", code)

        # Validate python syntax
        compile(code, "script_template.py", "exec")

    def test_05_standalone_manim_render_pixel_decomposition(self):
        """
        Verify standalone rendering of a continuous 3D Manim scene.
        Demonstrates the Pixel Visual Archetype:
        1. 2D pixel grid.
        2. Camera zoom & extraction of single pixel into 3D Cube.
        3. Color channel decomposition (R, G, B bars).
        4. Reassembly into pixel.
        """
        script_code = '''from manim import *
import numpy as np

config.pixel_width = 854
config.pixel_height = 480
config.frame_width = 16.0
config.frame_height = 9.0
config.frame_rate = 15

BG_COLOR = "#0f172a"
PRIMARY = "#38bdf8"
SECONDARY = "#818cf8"
ACCENT = "#f43f5e"
SUCCESS = "#10b981"
WARNING = "#fbbf24"
TEXT_COLOR = "#f8fafc"
MUTED_COLOR = "#94a3b8"

class Scene1_PixelDecomposition(ThreeDScene):
    def construct(self):
        self.camera.background_color = BG_COLOR
        total_dur = 3.0
        self.add_subcaption("Zooming into pixels and splitting RGB channels in 3D.", duration=total_dur)

        # Beat 1: Title and initial 2D pixel grid (0.0s - 1.0s)
        title = Text("1. Pixel Architecture & RGB Decomposition", font_size=24, color=PRIMARY, weight=BOLD).to_edge(UP, buff=0.45)
        self.add_fixed_in_frame_mobjects(title)
        self.play(FadeIn(title, shift=DOWN*0.2), run_time=0.4)

        grid = VGroup(*[
            Square(side_length=0.7, fill_opacity=0.85, fill_color=PRIMARY, stroke_width=1.5, stroke_color=BG_COLOR)
            .shift(RIGHT * (i - 1) * 0.75 + UP * (j - 1) * 0.75)
            for i in range(3) for j in range(3)
        ])
        self.play(Create(grid), run_time=0.6)

        # Beat 2: Zoom in, tilt 3D camera, pull central pixel into 3D Cube (1.0s - 2.0s)
        self.set_camera_orientation(phi=65 * DEGREES, theta=-45 * DEGREES)
        target_pixel = Cube(side_length=1.0, fill_opacity=0.9, fill_color=PRIMARY, stroke_color=TEXT_COLOR, stroke_width=1.5)
        target_pixel.shift(OUT * 1.5)

        self.play(
            FadeOut(grid),
            GrowFromCenter(target_pixel),
            run_time=0.5
        )
        self.move_camera(phi=75 * DEGREES, theta=-15 * DEGREES, run_time=0.5)

        # Beat 3: Split into Red, Green, Blue channel layers & conclude (2.0s - 3.0s)
        r_bar = Cube(side_length=0.9, fill_opacity=0.95, fill_color=ACCENT).shift(LEFT * 1.6 + OUT * 1.5)
        g_bar = Cube(side_length=0.9, fill_opacity=0.95, fill_color=SUCCESS).shift(OUT * 1.5)
        b_bar = Cube(side_length=0.9, fill_opacity=0.95, fill_color=SECONDARY).shift(RIGHT * 1.6 + OUT * 1.5)

        rgb_channels = VGroup(r_bar, g_bar, b_bar)
        callout = Text("Channels: [R: 244, G: 63, B: 94]", font_size=18, color=TEXT_COLOR).to_edge(DOWN, buff=0.55)
        self.add_fixed_in_frame_mobjects(callout)

        self.play(
            ReplacementTransform(target_pixel, rgb_channels),
            FadeIn(callout, shift=UP*0.2),
            run_time=1.0
        )
'''
        script_file = self.test_dir / "script.py"
        script_file.write_text(script_code, encoding="utf-8")

        # Render with Manim Community
        cmd = ["manim", "-ql", str(script_file.name), "Scene1_PixelDecomposition"]
        sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        res = subprocess.run(cmd, cwd=str(self.test_dir), capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        self.assertEqual(res.returncode, 0, f"Manim render failed:\n{res.stderr}\n{res.stdout}")

        # Locate rendered MP4
        candidates = list((self.test_dir / "media").rglob("Scene1_PixelDecomposition.mp4"))
        self.assertTrue(len(candidates) > 0, "Rendered scene MP4 not found in media dir.")
        scene_mp4 = candidates[0]
        self.assertGreater(scene_mp4.stat().st_size, 5000, "Rendered MP4 file size is suspiciously small.")

        # Inspect with ffprobe
        ffprobe_bin = get_ffprobe_path()
        probe_cmd = [
            ffprobe_bin, "-v", "error",
            "-show_entries", "format=duration:stream=width,height,codec_name",
            "-of", "json",
            str(scene_mp4)
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(probe_res.stdout)
        duration = float(probe_data["format"]["duration"])
        self.assertAlmostEqual(duration, 3.0, delta=0.2)
        stream = probe_data["streams"][0]
        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual(stream["width"], 854)
        self.assertEqual(stream["height"], 480)

        # Store for contact sheet test
        self.rendered_scene_mp4 = scene_mp4

    def test_06_contact_sheet_and_offline_qa(self):
        """Verify contact sheet generation with [3D] badge and offline visual review."""
        # First ensure we have a rendered MP4 from test 05
        self.test_05_standalone_manim_render_pixel_decomposition()
        scene_mp4 = self.rendered_scene_mp4

        timeline_data = {
            "scenes": [
                {
                    "scene_id": "Scene1_PixelDecomposition",
                    "narration_text": "Zooming into pixels and splitting RGB channels in 3D.",
                    "audio_start_s": 0.0,
                    "duration_s": 3.0,
                    "anim_duration_s": 2.8,
                    "hold_duration_s": 0.2,
                    "is_3d": True,
                    "visual_elements": ["pixel_grid", "3D_cube", "rgb_color_bars"],
                    "beats": [
                        {"beat_index": 1, "narration_phrase": "Zooming into pixels", "duration_s": 1.0},
                        {"beat_index": 2, "narration_phrase": "Extract into 3D", "duration_s": 1.0},
                        {"beat_index": 3, "narration_phrase": "Split into RGB channels", "duration_s": 1.0},
                    ]
                }
            ],
            "total_duration_s": 3.0,
        }
        timeline_file = self.test_dir / "scene_timeline.json"
        timeline_file.write_text(json.dumps(timeline_data, indent=2), encoding="utf-8")

        contact_sheet_png = self.test_dir / "contact_sheet.png"
        cs_meta = generate_contact_sheet(
            timeline_path=timeline_file,
            video_path=scene_mp4,
            output_path=contact_sheet_png,
            cols=1,
            rows=1,
            tile_width=480,
            title="Continuous Visual Animation Review",
        )

        self.assertTrue(contact_sheet_png.exists())
        self.assertGreater(contact_sheet_png.stat().st_size, 5000)
        self.assertEqual(len(cs_meta["scenes"]), 1)
        self.assertTrue(cs_meta["scenes"][0]["is_3d"])
        self.assertEqual(cs_meta["scenes"][0]["beats_count"], 3)

        # Run offline Visual Quality Review
        os.environ["SKIP_NEXUS_API"] = "1"
        try:
            reviewer = VisualQualityReviewer()
            report = reviewer.review_contact_sheet(
                contact_sheet_path=contact_sheet_png,
                timeline_path=timeline_file,
                topic="How Pixels Work in 3D",
            )
            self.assertGreaterEqual(report["overall_score"], 8.0)
            self.assertEqual(len(report["scenes"]), 1)
            self.assertEqual(report["scenes"][0]["animation_continuity"], "continuous")
            self.assertTrue(report["scenes"][0]["is_3d"])
        finally:
            os.environ.pop("SKIP_NEXUS_API", None)


if __name__ == "__main__":
    unittest.main()
