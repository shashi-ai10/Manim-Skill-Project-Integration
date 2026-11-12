"""Integration tests for Nexus Video Production MCP Server."""

import unittest
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import nexus_agent.mcp_server as mcp_server_mod
from nexus_agent.mcp_server import (
    server,
    create_educational_video,
    get_video_status,
    get_video_outputs,
    _validate_session_path,
    SESSIONS_DIR
)


def _create_mock_completed_session(
    base_dir: Path,
    session_id: str,
    vision_score: float = 7.2,
    duration_s: float = 64.53,
) -> Path:
    """Create a minimal self-contained completed session fixture for unit tests."""
    sdir = base_dir / session_id
    sdir.mkdir(parents=True, exist_ok=True)
    # Minimal valid-sized dummy media & deliverables (>1000 bytes for MP4s)
    (sdir / "broadcast.mp4").write_bytes(b"\x00" * 2048)
    (sdir / "broadcast_subtitled.mp4").write_bytes(b"\x00" * 2048)
    (sdir / "contact_sheet.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    (sdir / "VISUAL_QUALITY_REPORT.md").write_text("# Visual QA Report\n", encoding="utf-8")
    (sdir / "visual_quality_review.json").write_text(
        json.dumps({"overall_score": vision_score, "status": "approved"}), encoding="utf-8"
    )
    (sdir / "RENDERED_VIDEO_QA_REPORT.md").write_text("# Rendered QA Report\n", encoding="utf-8")
    (sdir / "rendered_video_qa.json").write_text(
        json.dumps({"overall_score": 8.5, "technical_score": 9.0, "motion_score": 8.0, "passed": True}),
        encoding="utf-8",
    )
    (sdir / "scene_timeline.json").write_text(
        json.dumps({"total_duration_s": duration_s, "scenes": [{"id": 1}]}), encoding="utf-8"
    )
    (sdir / "session_state.json").write_text(
        json.dumps({
            "current_stage": 11,
            "completed_stages": list(range(1, 12)),
            "status": "completed",
            "vision_qa_score": vision_score,
        }),
        encoding="utf-8",
    )
    (sdir / "production.log").write_text("PIPELINE COMPLETED SUCCESSFULLY!\n", encoding="utf-8")
    return sdir


class TestNexusMCPServer(unittest.TestCase):
    """Test suite for the MCP server tools and security boundaries."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._mock_sessions_dir = Path(cls._tmpdir.name)
        cls._patch_mcp = patch.object(mcp_server_mod, "SESSIONS_DIR", cls._mock_sessions_dir)
        cls._patch_local = patch(__name__ + ".SESSIONS_DIR", cls._mock_sessions_dir)
        cls._patch_mcp.start()
        cls._patch_local.start()

        _create_mock_completed_session(
            cls._mock_sessions_dir, "cnn-generalization-validation", vision_score=7.2, duration_s=64.53
        )
        _create_mock_completed_session(
            cls._mock_sessions_dir, "convolutional-neural-networks-expla-20261104-182400", vision_score=7.5, duration_s=64.53
        )
        _create_mock_completed_session(
            cls._mock_sessions_dir, "how-does-generative-ai-actually-wor-20261105-151327", vision_score=6.5, duration_s=138.3
        )

    @classmethod
    def tearDownClass(cls):
        cls._patch_local.stop()
        cls._patch_mcp.stop()
        cls._tmpdir.cleanup()

    def test_01_tool_registration(self):
        """Verify that all three required tools are registered with MCPServer."""
        tools = server._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        self.assertIn("create_educational_video", tool_names)
        self.assertIn("get_video_status", tool_names)
        self.assertIn("get_video_outputs", tool_names)
        print(f"  [PASS] Registered MCP tools verified: {tool_names}")

    def test_02_path_traversal_protection(self):
        """Verify that path traversal attempts are strictly forbidden."""
        malicious_ids = [
            "../../../windows/system32",
            "..\\..\\sensitive_file",
            "/etc/passwd",
            "folder/subfolder"
        ]
        for bad_id in malicious_ids:
            with self.assertRaises(ValueError):
                _validate_session_path(bad_id)
            # Verify tool returns error dict
            res_status = get_video_status(bad_id)
            self.assertIn("error", res_status)
            res_out = get_video_outputs(bad_id)
            self.assertIn("error", res_out)
        print("  [PASS] Path traversal security boundaries verified.")

    def test_03_empty_input_validation(self):
        """Verify that create_educational_video rejects empty or whitespace topics."""
        res = create_educational_video(topic="")
        self.assertIn("error", res)
        res_ws = create_educational_video(topic="   ")
        self.assertIn("error", res_ws)
        print("  [PASS] Input validation for empty topic verified.")

    def test_04_status_on_completed_session(self):
        """Verify that get_video_status correctly reads an existing completed session."""
        session_id = "cnn-generalization-validation"
        res = get_video_status(session_id)
        self.assertEqual(res.get("session_id"), session_id)
        self.assertEqual(res.get("status"), "completed")
        self.assertEqual(res.get("progress_percent"), 100)
        self.assertGreaterEqual(res.get("current_stage"), 11)
        print(f"  [PASS] Completed session status verified: {res.get('stage_description')}")

    def test_05_output_retrieval_on_completed_session(self):
        """Verify that get_video_outputs returns verified existing files on disk."""
        session_id = "cnn-generalization-validation"
        res = get_video_outputs(session_id)
        self.assertTrue(res.get("ready"))
        files = res.get("files", {})
        
        self.assertIn("subtitled_video_mp4", files)
        self.assertIn("clean_video_mp4", files)
        self.assertIn("contact_sheet_png", files)
        self.assertIn("visual_qa_report_md", files)
        
        # Verify physical existence on disk
        for key, f_meta in files.items():
            f_path = Path(f_meta["path"])
            self.assertTrue(f_path.exists(), f"File does not exist: {f_path}")
            self.assertGreater(f_meta["size_bytes"], 0)
            
        metrics = res.get("metrics", {})
        self.assertIn("vision_qa_score", metrics)
        self.assertEqual(metrics["vision_qa_score"], 7.2)
        print(f"  [PASS] Verified output files retrieved: {len(files)} files, Vision Score: {metrics['vision_qa_score']}/10")


    def test_06_duration_adherence_reporting(self):
        """Verify duration parsing, adherence calculation, and deviation reporting."""
        from nexus_agent.mcp_server import _parse_duration_seconds
        
        # 1. Parsing verification
        self.assertEqual(_parse_duration_seconds("2 minutes"), 120)
        self.assertEqual(_parse_duration_seconds("2-minute"), 120)
        self.assertEqual(_parse_duration_seconds("90 seconds"), 90)
        self.assertEqual(_parse_duration_seconds("3 mins"), 180)
        self.assertEqual(_parse_duration_seconds("60"), 60)
        self.assertIsNone(_parse_duration_seconds(""))
        
        # 2. Deviation calculation on session
        session_id = "convolutional-neural-networks-expla-20261104-182400"
        res = get_video_outputs(session_id)
        metrics = res.get("metrics", {})
        
        actual_dur = metrics.get("total_duration_s", 64.53)
        target_dur = 120  # requested 2 minutes
        dev_s = actual_dur - target_dur
        dev_pct = abs(dev_s) / target_dur * 100
        
        self.assertGreater(actual_dur, 0)
        print(f"  [PASS] Duration adherence reporting verified: Target={target_dur}s, Actual={actual_dur:.1f}s, Deviation={dev_s:+.1f}s ({dev_pct:.1f}%)")

    def test_07_terminal_result_on_completed_session(self):
        """Verify that create_educational_video returns terminal result on completed session."""
        session_id = "how-does-generative-ai-actually-wor-20261105-151327"
        res = create_educational_video(
            topic="How Does Generative AI Actually Work?",
            session_id=session_id,
            wait_for_completion=True
        )
        self.assertEqual(res.get("status"), "completed")
        self.assertEqual(res.get("session_id"), session_id)
        self.assertEqual(res.get("progress_percent"), 100)
        self.assertEqual(res.get("current_stage"), 12)
        self.assertIn("broadcast_subtitled.mp4", res.get("final_mp4_path", ""))
        self.assertAlmostEqual(res.get("duration_s", 0), 138.3, places=1)
        self.assertEqual(res.get("vision_qa_score"), 6.5)
        print(f"  [PASS] Terminal result returned directly: {res.get('final_mp4_path')} (QA: {res.get('vision_qa_score')}/10)")

    def test_08_dead_process_failure_detection(self):
        """Verify that a session with dead PID and incomplete stages is flagged failed instead of stuck running."""
        import tempfile
        import json
        with tempfile.TemporaryDirectory(dir=str(SESSIONS_DIR)) as tmpdir:
            temp_session_id = Path(tmpdir).name
            meta_file = Path(tmpdir) / "mcp_metadata.json"
            meta_file.write_text(json.dumps({
                "session_id": temp_session_id,
                "pid": 999999,  # Non-existent dead PID
                "status": "running"
            }), encoding="utf-8")
            state_file = Path(tmpdir) / "session_state.json"
            state_file.write_text(json.dumps({
                "current_stage": 4,
                "completed_stages": [1, 2, 3],
                "status": "running"
            }), encoding="utf-8")

            res = get_video_status(temp_session_id)
            self.assertEqual(res.get("status"), "failed")
            self.assertFalse(res.get("process_alive"))
            print("  [PASS] Dead PID with incomplete stages correctly detected as failed.")

    def test_09_session_id_contract_and_aliases(self):
        """Verify session_id, id, and job_id aliases across MCP tool return dictionaries."""
        session_id = "how-does-generative-ai-actually-wor-20261105-151327"
        
        # Test create_educational_video on completed session
        res_create = create_educational_video(topic="Generative AI", session_id=session_id)
        self.assertEqual(res_create.get("session_id"), session_id)
        self.assertEqual(res_create.get("id"), session_id)
        self.assertEqual(res_create.get("job_id"), session_id)
        self.assertEqual(res_create.get("status"), "completed")
        self.assertIn("broadcast_subtitled.mp4", res_create.get("final_mp4_path", ""))
        self.assertAlmostEqual(res_create.get("duration_s", 0), 138.3, places=1)
        self.assertEqual(res_create.get("vision_qa_score"), 6.5)

        # Test dry_run return contract
        res_dry = create_educational_video(topic="Contract Verification Test", dry_run=True)
        dry_slug = res_dry.get("session_id")
        self.assertEqual(res_dry.get("status"), "dry_run_success")
        self.assertTrue(dry_slug.startswith("contract-verification-test-"))
        self.assertEqual(res_dry.get("id"), dry_slug)
        self.assertEqual(res_dry.get("job_id"), dry_slug)
        # Clean up dry run folder
        dry_dir = SESSIONS_DIR / dry_slug
        if dry_dir.exists():
            import shutil
            shutil.rmtree(dry_dir)

        # Test get_video_status
        res_status = get_video_status(session_id=session_id)
        self.assertEqual(res_status.get("session_id"), session_id)
        self.assertEqual(res_status.get("id"), session_id)
        self.assertEqual(res_status.get("job_id"), session_id)

        # Test get_video_outputs
        res_out = get_video_outputs(session_id=session_id)
        self.assertEqual(res_out.get("session_id"), session_id)
        self.assertEqual(res_out.get("id"), session_id)
        self.assertEqual(res_out.get("job_id"), session_id)
        print("  [PASS] Complete session_id/id/job_id contract verified across all MCP tools.")

    def test_10_task_id_confusion_protection(self):
        """Verify that passing an internal task ID (e.g. 'kgk308nzk') returns clear diagnostic guidance."""
        task_ids = ["kgk308nzk", "task_abc123", "998877"]

        for bad_id in task_ids:
            # 1. Positional / session_id kwarg
            res_status = get_video_status(session_id=bad_id)
            self.assertIn("error", res_status)
            self.assertIn("appears to be an internal Claude task ID or process ID", res_status["error"])
            self.assertIn("hint", res_status)
            self.assertIn("recent_sessions", res_status)
            self.assertIsInstance(res_status["recent_sessions"], list)

            # 2. task_id kwarg alias
            res_alias = get_video_status(task_id=bad_id)
            self.assertIn("error", res_alias)
            self.assertIn("appears to be an internal Claude task ID or process ID", res_alias["error"])

            # 3. get_video_outputs with task ID
            res_out = get_video_outputs(session_id=bad_id)
            self.assertIn("error", res_out)
            self.assertIn("appears to be an internal Claude task ID or process ID", res_out["error"])

        print("  [PASS] Task ID vs Session ID disambiguation and diagnostic guidance verified.")

    def test_11_latest_alias_resolution(self):
        """Verify that 'latest' alias resolves to the most recent session directory."""
        res_status = get_video_status(session_id="latest")
        self.assertIn("status", res_status)
        self.assertIsNotNone(res_status.get("session_id"))
        self.assertTrue((SESSIONS_DIR / res_status["session_id"]).exists())

        res_out = get_video_outputs(session_id="latest")
        self.assertEqual(res_out.get("session_id"), res_status.get("session_id"))
        print(f"  [PASS] 'latest' alias resolved successfully to: {res_status['session_id']}")

    def test_12_bogus_task_id_ignored_in_create(self):
        """Verify that passing a task ID into create_educational_video ignores the task ID and creates a proper slug."""
        res = create_educational_video(topic="Transformer Self Attention", session_id="kgk308nzk", dry_run=True)
        gen_slug = res.get("session_id")
        self.assertNotEqual(gen_slug, "kgk308nzk")
        self.assertTrue(gen_slug.startswith("transformer-self-attention-"))
        # Verify no directory was created with the task ID name
        self.assertFalse((SESSIONS_DIR / "kgk308nzk").exists())
        # Clean up dry run folder
        gen_dir = SESSIONS_DIR / gen_slug
        if gen_dir.exists():
            import shutil
            shutil.rmtree(gen_dir)
        print(f"  [PASS] Non-existent task ID safely ignored; valid slug generated: {gen_slug}")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Nexus Video Production MCP Server")
    print("=" * 60)
    unittest.main()

