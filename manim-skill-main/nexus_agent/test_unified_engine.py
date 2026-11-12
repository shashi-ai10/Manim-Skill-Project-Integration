"""Unit and integration tests for the Unified Nexus AI Video Production Engine.

Tests capabilities integrated from:
- manim-skill (video_viewer MCP tool, subtitle AST checks)
- claude-motion (pure-Python DSP reverb, procedural audio synthesis)
- video-use (narrative arcs, reference guides, MarginV=90 safe subtitles)
"""

import json
import os
import unittest
from pathlib import Path

from nexus_agent.audio import dsp
from nexus_agent.audio.sfx_synth import SR, chime, whoosh
from nexus_agent.compositor.overlays import build_composite_filtergraph
from nexus_agent.mcp_server import server, launch_video_viewer, get_video_status, _list_recent_sessions
from nexus_agent.orchestrator import VideoProductionOrchestrator


class TestUnifiedEngine(unittest.TestCase):
    """Test suite for the unified engine integrations."""

    def test_01_dsp_reverb_and_delays(self):
        """Verify pure-Python DSP delay, comb/allpass reverb, and softclip algorithms (from claude-motion)."""
        # Test oscillator
        tone = dsp.sine(440, SR // 4)
        self.assertEqual(len(tone), SR // 4)
        self.assertAlmostEqual(dsp.peak(tone), 1.0, delta=0.01)

        # Test softclip
        clipped = dsp.softclip(tone, drive=2.0)
        self.assertEqual(len(clipped), len(tone))
        self.assertTrue(all(abs(v) <= 1.001 for v in clipped))

        # Test pingpong delay
        left_del, right_del = dsp.pingpong(tone, d=100, fb=0.5, damp=0.2)
        self.assertEqual(len(left_del), len(tone))
        self.assertEqual(len(right_del), len(tone))

        # Test Freeverb reverb (mono in -> stereo out)
        left_rev, right_rev = dsp.reverb(tone, size=0.6, damp=0.3)
        self.assertEqual(len(left_rev), len(tone))
        self.assertEqual(len(right_rev), len(tone))
        self.assertGreater(dsp.rms(left_rev), 0.01)

    def test_02_video_use_references_present(self):
        """Verify all 14 reference guides from video-use are present in skills/nexus-video-production/references."""
        ref_dir = Path(__file__).resolve().parent.parent / "skills" / "nexus-video-production" / "references"
        self.assertTrue(ref_dir.exists(), f"Reference directory not found: {ref_dir}")

        expected_guides = [
            "animation-design-thinking.md",
            "animations.md",
            "camera-and-3d.md",
            "decorations.md",
            "equations.md",
            "graphs-and-data.md",
            "mobjects.md",
            "paper-explainer.md",
            "production-quality.md",
            "rendering.md",
            "scene-planning.md",
            "troubleshooting.md",
            "updaters-and-trackers.md",
            "visual-design.md",
            "ATTRIBUTION.md"
        ]
        for guide in expected_guides:
            guide_path = ref_dir / guide
            self.assertTrue(guide_path.exists(), f"Expected reference guide missing: {guide}")
            self.assertGreater(guide_path.stat().st_size, 50, f"Guide file is unexpectedly empty: {guide}")

    def test_03_narrative_arcs_in_stage2_prompt(self):
        """Verify Stage 2 prompt in VideoProductionOrchestrator contains Narrative Arcs and pedagogical principles."""
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="orch_test_")
        try:
            orch = VideoProductionOrchestrator(session_dir=tmp_dir, auto_approve=True)
            orch.stage1_analyze_topic("Transformer Architecture")
            self.assertEqual(orch.artifacts.topic, "Transformer Architecture")

            # Inspect source code of stage2_generate_script to ensure arcs are documented
            import inspect
            src = inspect.getsource(orch.stage2_generate_script)
            self.assertIn("Discovery Arc", src)
            self.assertIn("Problem-Solution Arc", src)
            self.assertIn("Comparison Arc", src)
            self.assertIn("Build-Up Arc", src)
            self.assertIn("Geometry before algebra", src)
            self.assertIn("The see-then-hear principle", src)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_04_subtitle_safe_margin_v_90(self):
        """Verify default subtitle style in build_composite_filtergraph enforces MarginV=90 for mobile UI safety."""
        import inspect
        sig = inspect.signature(build_composite_filtergraph)
        default_style = sig.parameters["subtitle_style"].default
        self.assertIn("MarginV=90", default_style)
        self.assertIn("OutlineColour=&H00000000", default_style)

    def test_05_registered_mcp_tools(self):
        """Verify all 5 tools are registered on the MCP server."""
        # FastMCP tool registry inspect
        tools = server._tool_manager._tools if hasattr(server, "_tool_manager") else getattr(server, "_tools", {})
        tool_names = list(tools.keys())
        expected_tools = [
            "ping",
            "create_educational_video",
            "get_video_status",
            "get_video_outputs",
            "launch_video_viewer"
        ]
        for et in expected_tools:
            self.assertIn(et, tool_names, f"Expected MCP tool not registered: {et}")

    def test_06_launch_video_viewer_mcp_tool(self):
        """Verify launch_video_viewer tool executes cleanly for existing completed session."""
        import tempfile
        from unittest.mock import patch
        import nexus_agent.mcp_server as mcp_server_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_sessions = Path(tmpdir)
            sdir = mock_sessions / "viewer-smoke-session"
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "broadcast_subtitled.mp4").write_bytes(b"\x00" * 2048)

            with patch.object(mcp_server_mod, "SESSIONS_DIR", mock_sessions):
                res = launch_video_viewer(session_id="latest", open_browser=False)

            self.assertEqual(res.get("status"), "viewer_launched")
            self.assertIn("http://localhost:", res.get("viewer_url", ""))
            self.assertGreater(res.get("port", 0), 1024)
            self.assertTrue(Path(res.get("video_file")).exists())

            # Cleanup background viewer process before tempdir cleanup
            target_id = res.get("session_id")
            from nexus_agent.mcp_server import _ACTIVE_JOBS
            proc = _ACTIVE_JOBS.pop(f"viewer_{target_id}", None)
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()

    def test_07_manim_code_static_linter(self):
        """Verify AST static quality linter catches static freezes, LaTeX classes, and passes valid beat code."""
        from nexus_agent.orchestrator import lint_manim_code
        from nexus_agent.sync import SceneTiming, TeachingBeat

        dummy_beat = TeachingBeat(
            beat_index=1, narration_phrase="Testing", start_s=0.0, duration_s=3.0,
            visual_objects=["Dot"], animation_action="Create"
        )
        timeline = [
            SceneTiming(
                scene_id="Scene1_Intro", segment_index=0, narration_text="Intro",
                audio_start_s=0.0, audio_end_s=3.0, duration_s=3.0, anim_duration_s=2.5,
                hold_duration_s=0.5, visual_elements=["Dot"], beats=[dummy_beat, dummy_beat, dummy_beat]
            )
        ]

        # 1. Defective code: static freeze with self.wait(15) and only 1 play call
        defective_code = """
from manim import *
class Scene1_Intro(Scene):
    def construct(self):
        dot = Dot()
        self.play(Create(dot))
        self.wait(15)
"""
        issues = lint_manim_code(defective_code, timeline)
        self.assertTrue(any("static freeze" in iss for iss in issues))
        self.assertTrue(any("only 1 animation calls" in iss for iss in issues))

        # 2. Defective code: MathTex usage
        latex_code = """
from manim import *
class Scene1_Intro(Scene):
    def construct(self):
        tex = MathTex("x^2")
        self.play(Write(tex))
"""
        issues_latex = lint_manim_code(latex_code, timeline)
        self.assertTrue(any("Forbidden LaTeX class 'MathTex'" in iss for iss in issues_latex))

        # 3. Clean First-Pass Excellence code: 3 play calls, no long wait, rich mobjects (>=6)
        clean_code = """
from manim import *
class Scene1_Intro(Scene):
    def construct(self):
        grid = NumberPlane()
        c = Circle(radius=0.4, stroke_width=3)
        c2 = Circle(radius=0.4, stroke_width=3)
        l = Line(LEFT, RIGHT)
        t = Text("Title", font_size=24)
        card = RoundedRectangle(width=4, height=1)
        self.play(Create(grid), run_time=1.0)
        self.play(Create(c), Create(c2), Create(l), Write(t), run_time=1.0)
        self.play(FadeIn(card), run_time=1.0)
"""
        issues_clean = lint_manim_code(clean_code, timeline)
        self.assertEqual(len(issues_clean), 0)

    def test_08_beat_level_subtitles(self):
        """Verify master SRT generator creates multi-beat cues when teaching beats are provided."""
        import tempfile
        from nexus_agent.compositor.subtitles import build_master_srt_from_timeline

        temp_dir = Path(tempfile.mkdtemp())
        tl_data = {
            "scenes": [
                {
                    "scene_id": "Scene1_Demo",
                    "narration_text": "First sentence. Second sentence.",
                    "audio_start_s": 0.0,
                    "audio_end_s": 6.0,
                    "duration_s": 6.0,
                    "beats": [
                        {"beat_index": 1, "narration_phrase": "First sentence.", "start_s": 0.0, "duration_s": 3.0},
                        {"beat_index": 2, "narration_phrase": "Second sentence.", "start_s": 3.0, "duration_s": 3.0}
                    ]
                }
            ]
        }
        tl_path = temp_dir / "timeline.json"
        srt_path = temp_dir / "test.srt"
        tl_path.write_text(json.dumps(tl_data), encoding="utf-8")

        build_master_srt_from_timeline(tl_path, srt_path)
        self.assertTrue(srt_path.exists())
        content = srt_path.read_text(encoding="utf-8")
        self.assertIn("First sentence.", content)
        self.assertIn("Second sentence.", content)
        # Should have 2 separate cues (numbered 1 and 2)
        self.assertIn("\n1\n", "\n" + content)
        self.assertIn("\n2\n", "\n" + content)


if __name__ == "__main__":
    unittest.main()
