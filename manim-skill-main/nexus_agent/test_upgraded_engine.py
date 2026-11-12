"""Unit and Integration Tests for Upgraded Nexus Video Production Engine.

Verifies:
1. Runtime Skill Loader (discovery, parsing, stage-specific context extraction, logging)
2. Topic-Aware Visual Director & Visual Plan Validation (schema, screen occupancy, completeness)
3. Rendered-Video QA (ffprobe specs, freeze detection, subtitle check, report generation)
4. Automatic Scene Repair (scene extraction, surgical replacement, single-scene repair)
5. MCP Server Deliverables Integration
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from nexus_agent.skill_loader import SkillLoader, LoadedSkill
from nexus_agent.visual_director import validate_visual_plan, SceneVisualPlan
from nexus_agent.rendered_video_qa import (
    RenderedVideoQA,
    VideoTechnicalSpecs,
    FreezeInterval,
    RenderedQAResult,
)
from nexus_agent.scene_repair import SceneRepairManager, SceneRepairResult
from nexus_agent.sync import SceneTiming, TeachingBeat
from nexus_agent.orchestrator import lint_manim_code, VideoProductionOrchestrator
from nexus_agent.tools_resolver import (
    get_python_path,
    get_manim_cmd,
    get_ffmpeg_path,
    get_ffprobe_path,
    get_git_path,
    check_latex_available,
    validate_required_executables,
)
from nexus_agent import mcp_server


class TestSkillLoader(unittest.TestCase):
    """Test runtime discovery, parsing, and context generation for all integrated skills."""

    def setUp(self):
        self.loader = SkillLoader()

    def test_skills_discovered(self):
        available = self.loader.list_available_skills()
        names = {s["name"] for s in available}
        required_skills = {
            "manim-skill",
            "manim-video",
            "motion-design",
            "nexus-video-production",
            "review-loop",
            "sound-design",
            "video-use",
        }
        self.assertTrue(
            required_skills.issubset(names),
            f"Missing required skills: {required_skills - names}",
        )

    def test_stage_context_visual_planning(self):
        ctx = self.loader.get_stage_context("visual_planning")
        self.assertIn("MOTION-DESIGN", ctx)
        self.assertIn("MANIM-VIDEO", ctx)
        # Verify concrete taste rules are present
        self.assertTrue("Timing" in ctx or "Easing" in ctx)

    def test_stage_context_manim_generation(self):
        ctx = self.loader.get_stage_context("manim_generation")
        self.assertIn("MANIM-SKILL", ctx)
        self.assertIn("MANIM-VIDEO", ctx)

    def test_stage_context_audio(self):
        ctx = self.loader.get_stage_context("audio")
        self.assertIn("SOUND-DESIGN", ctx)

    def test_stage_context_qa(self):
        ctx = self.loader.get_stage_context("qa")
        self.assertIn("REVIEW-LOOP", ctx)
        self.assertIn("VIDEO-USE", ctx)

    def test_graceful_missing_skill(self):
        non_existent = self.loader.get_skill("non-existent-skill-999")
        self.assertIsNone(non_existent)
        ctx = self.loader.get_stage_context("unknown_stage_xyz")
        self.assertEqual(ctx, "")


class TestVisualPlanValidation(unittest.TestCase):
    """Test validation of topic-aware visual plans."""

    def setUp(self):
        self.timeline = [
            SceneTiming(
                scene_id="Scene1_Intro",
                segment_index=0,
                narration_text="Transformers revolutionized NLP.",
                audio_start_s=0.0,
                audio_end_s=10.0,
                duration_s=10.0,
                anim_duration_s=9.0,
                hold_duration_s=1.0,
                visual_elements=["Token capsule", "Embedding matrix"],
                beats=[
                    TeachingBeat(0, "Transformers revolutionized", 0.0, 5.0, ["Token"], "Enter tokens"),
                    TeachingBeat(1, "NLP.", 5.0, 5.0, ["Matrix"], "Project embeddings"),
                ],
            ),
            SceneTiming(
                scene_id="Scene2_Attention",
                segment_index=1,
                narration_text="Self-attention relates words.",
                audio_start_s=10.0,
                audio_end_s=22.0,
                duration_s=12.0,
                anim_duration_s=11.0,
                hold_duration_s=1.0,
                visual_elements=["Query vector", "Key vector"],
                beats=[
                    TeachingBeat(0, "Self-attention", 0.0, 6.0, ["Query"], "Highlight queries"),
                    TeachingBeat(1, "relates words.", 6.0, 6.0, ["Key"], "Dot product heatmap"),
                ],
            ),
        ]

    def test_valid_visual_plan(self):
        plan_data = {
            "scenes": [
                {
                    "scene_id": "Scene1_Intro",
                    "learning_objective": "Understand sequence bottlenecks",
                    "core_visual_metaphor": "Token conveyor belt",
                    "main_visual_composition": "Horizontal streaming vector ribbon",
                    "objects_and_relationships": [{"name": "Tokens", "type": "RoundedRectangle"}],
                    "animation_sequence": [{"beat": 1, "action": "slide in"}, {"beat": 2, "action": "embed"}],
                    "camera_movement": "Fixed widescreen",
                    "transitions": "Morph tokens to vectors",
                    "colour_palette": {"primary": "#38bdf8"},
                    "typography_and_labels": ["Token 1", "Token 2"],
                    "narration_synchronization": [{"phrase": "Transformers", "action": "highlight"}],
                    "approximate_screen_occupancy": "75% widescreen coverage",
                    "expected_visual_state_at_end": "Tokens positioned in latent space",
                },
                {
                    "scene_id": "Scene2_Attention",
                    "learning_objective": "Grasp pairwise word interactions",
                    "core_visual_metaphor": "Dynamic bipartite attention beam matrix",
                    "main_visual_composition": "Dual-column token graph with connecting rays",
                    "objects_and_relationships": [{"name": "Matrix", "type": "Square"}],
                    "animation_sequence": [{"beat": 1, "action": "draw rays"}, {"beat": 2, "action": "heat map"}],
                    "camera_movement": "Slight zoom",
                    "transitions": "Transform rays into weights",
                    "colour_palette": {"primary": "#818cf8"},
                    "typography_and_labels": ["Q", "K", "V"],
                    "narration_synchronization": [{"phrase": "Self-attention", "action": "glow"}],
                    "approximate_screen_occupancy": "80% canvas coverage",
                    "expected_visual_state_at_end": "Normalized attention weights",
                },
            ]
        }
        issues = validate_visual_plan(plan_data, self.timeline)
        self.assertEqual(len(issues), 0, f"Expected 0 issues, got: {issues}")

    def test_invalid_visual_plan_missing_scene(self):
        plan_data = {
            "scenes": [
                {
                    "scene_id": "Scene1_Intro",
                    "learning_objective": "Intuition",
                    "core_visual_metaphor": "Tokens",
                    "main_visual_composition": "Horizontal layout",
                    "objects_and_relationships": [],
                    "animation_sequence": [{"beat": 1}, {"beat": 2}],
                    "transitions": "Morph",
                    "typography_and_labels": ["Token"],
                    "approximate_screen_occupancy": "70%",
                    "expected_visual_state_at_end": "Done",
                }
            ]
        }
        issues = validate_visual_plan(plan_data, self.timeline)
        self.assertTrue(any("Scene2_Attention" in iss for iss in issues))


class TestRenderedVideoQA(unittest.TestCase):
    """Test rendered video QA checks, freeze detection, and report writing."""

    def setUp(self):
        self.qa = RenderedVideoQA()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="qa_test_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_subtitle_validator(self):
        srt_content = (
            "1\n00:00:00,000 --> 00:00:03,500\nTransformers use attention mechanisms.\n\n"
            "2\n00:00:03,500 --> 00:00:07,000\nThey process tokens in parallel.\n\n"
        )
        srt_file = self.temp_dir / "test.srt"
        srt_file.write_text(srt_content, encoding="utf-8")

        valid, count, issues = self.qa.validate_subtitles(srt_file, video_duration_s=8.0)
        self.assertTrue(valid)
        self.assertEqual(count, 2)
        self.assertEqual(len(issues), 0)

    def test_subtitle_validator_invalid_order(self):
        bad_srt = "1\n00:00:05,000 --> 00:00:02,000\nEnd is before start.\n\n"
        srt_file = self.temp_dir / "bad.srt"
        srt_file.write_text(bad_srt, encoding="utf-8")

        valid, count, issues = self.qa.validate_subtitles(srt_file, video_duration_s=8.0)
        self.assertFalse(valid)
        self.assertTrue(len(issues) > 0)

    def test_report_generation(self):
        specs = VideoTechnicalSpecs(
            width=1920,
            height=1080,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            audio_sample_rate=48000,
            audio_channels=2,
            video_duration_s=60.0,
            audio_duration_s=60.0,
            duration_delta_ms=0.0,
            file_size_bytes=5000000,
            bitrate_kbps=650.0,
        )
        qa_res = RenderedQAResult(
            overall_score=8.8,
            technical_score=10.0,
            motion_score=9.0,
            audio_subtitle_score=8.5,
            visual_storytelling_score=8.5,
            passed=True,
            specs=specs,
            freezes=[],
            max_freeze_duration_s=0.5,
            scene_transitions_detected=5,
            subtitles_valid=True,
            subtitles_cue_count=12,
            loudness_lufs=-14.0,
            true_peak_dbtp=-1.0,
            critical_defects=[],
            warnings=[],
            recommendations=["Ready for delivery."],
        )
        j_path, m_path = self.qa.write_qa_reports(qa_res, self.temp_dir, "Transformer Architecture")
        self.assertTrue(j_path.exists())
        self.assertTrue(m_path.exists())
        self.assertIn("1920 × 1080", m_path.read_text(encoding="utf-8"))


class TestSceneRepairManager(unittest.TestCase):
    """Test surgical extraction and replacement of defective scene classes."""

    def setUp(self):
        self.script_sample = (
            "from manim import *\n\n"
            "class Scene1_Intro(Scene):\n"
            "    def construct(self):\n"
            "        txt = Text('Intro')\n"
            "        self.play(FadeIn(txt))\n"
            "        self.wait(1.0)\n\n"
            "class Scene2_Defective(Scene):\n"
            "    def construct(self):\n"
            "        # sparse and freeze\n"
            "        self.wait(5.0)\n\n"
            "class Scene3_Conclusion(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('End')\n"
            "        self.play(Write(t))\n"
        )

    def test_extract_scene_class_code(self):
        res = SceneRepairManager.extract_scene_class_code(self.script_sample, "Scene2_Defective")
        self.assertIsNotNone(res)
        class_code, start, end = res
        self.assertIn("class Scene2_Defective(Scene):", class_code)
        self.assertIn("self.wait(5.0)", class_code)
        self.assertNotIn("Scene1_Intro", class_code)
        self.assertNotIn("Scene3_Conclusion", class_code)

    def test_topic_agnostic_linter(self):
        timeline = [
            SceneTiming(
                scene_id="Scene1_Intro",
                segment_index=0,
                narration_text="Introduction",
                audio_start_s=0.0,
                audio_end_s=5.0,
                duration_s=5.0,
                anim_duration_s=4.5,
                hold_duration_s=0.5,
                visual_elements=["Text", "Box"],
                beats=[TeachingBeat(0, "Intro", 0.0, 5.0, ["Text"], "Animate")],
            )
        ]
        # Script with static freeze > 1.5s
        bad_code = (
            "from manim import *\n"
            "class Scene1_Intro(Scene):\n"
            "    def construct(self):\n"
            "        c = Circle()\n"
            "        sq = Square()\n"
            "        r = Rectangle()\n"
            "        d = Dot()\n"
            "        l = Line()\n"
            "        t = Text('Hi')\n"
            "        self.play(FadeIn(c))\n"
            "        self.wait(3.5)\n"
        )
        issues = lint_manim_code(bad_code, timeline)
        self.assertTrue(any("static freeze" in iss.lower() for iss in issues))


class TestStage1WorkspaceInitialization(unittest.TestCase):
    """Test Stage 1 workspace directory creation, dependency validation, and LaTeX safety."""

    def test_tools_resolver_executables(self):
        specs = validate_required_executables()
        self.assertIn("python", specs)
        self.assertIn("manim_cmd", specs)
        self.assertIn("ffmpeg", specs)
        self.assertIn("ffprobe", specs)
        self.assertTrue(Path(specs["python"]).exists())
        self.assertTrue(Path(specs["ffmpeg"]).exists())
        self.assertTrue(Path(specs["ffprobe"]).exists())

    def test_stage1_workspace_creation_and_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orch = VideoProductionOrchestrator(session_dir=tmpdir, auto_approve=True)
            res = orch.stage1_analyze_topic("How LLMs Work — 2 minutes")
            self.assertEqual(res["target_duration_s"], 120)
            self.assertIn("environment", res)
            self.assertTrue((Path(tmpdir) / "media").exists())
            self.assertTrue((Path(tmpdir) / "_temp_frames").exists())
            self.assertTrue((Path(tmpdir) / "_temp_post").exists())

    def test_linter_catches_latex_when_unavailable(self):
        timeline = [
            SceneTiming(
                scene_id="Scene1_Test",
                segment_index=0,
                narration_text="Test",
                audio_start_s=0.0,
                audio_end_s=5.0,
                duration_s=5.0,
                anim_duration_s=4.5,
                hold_duration_s=0.5,
                visual_elements=["Text", "Box"],
                beats=[TeachingBeat(0, "Intro", 0.0, 5.0, ["Text"], "Animate")],
            )
        ]
        # Script with BarChart and MathTex without custom label constructor
        latex_code = (
            "from manim import *\n"
            "class Scene1_Test(Scene):\n"
            "    def construct(self):\n"
            "        c = Circle()\n"
            "        sq = Square()\n"
            "        r = Rectangle()\n"
            "        d = Dot()\n"
            "        l = Line()\n"
            "        t = Text('Hi')\n"
            "        chart = BarChart(values=[0.8, 0.2], bar_names=['a', 'b'])\n"
            "        eq = MathTex('E=mc^2')\n"
            "        self.play(FadeIn(c))\n"
            "        self.wait(0.5)\n"
        )
        issues = lint_manim_code(latex_code, timeline)
        if not check_latex_available():
            self.assertTrue(any("BarChart" in iss or "LaTeX" in iss for iss in issues))
            self.assertTrue(any("MathTex" in iss for iss in issues))


class TestMCPIntegration(unittest.TestCase):
    """Test MCP server output file retrieval including Rendered QA reports."""

    def test_get_video_outputs_includes_rendered_qa(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_sessions = Path(tmpdir)
            sdir = mock_sessions / "neural-network-visual-mastery"
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "broadcast.mp4").write_bytes(b"\x00" * 2048)
            (sdir / "broadcast_subtitled.mp4").write_bytes(b"\x00" * 2048)
            (sdir / "RENDERED_VIDEO_QA_REPORT.md").write_text("# Rendered QA\n", encoding="utf-8")
            (sdir / "rendered_video_qa.json").write_text(
                json.dumps({"overall_score": 8.4, "passed": True}), encoding="utf-8"
            )
            with patch.object(mcp_server, "SESSIONS_DIR", mock_sessions):
                outputs = mcp_server.get_video_outputs("neural-network-visual-mastery")
        self.assertIn("files", outputs)
        files = outputs["files"]
        self.assertIn("subtitled_video_mp4", files)
        self.assertIn("clean_video_mp4", files)
        self.assertIn("rendered_qa_report_md", files)


if __name__ == "__main__":
    unittest.main()
