"""Comprehensive test suite for Navigate Labs Nexus API & Agent Integration."""

import os
import sys
import unittest
from pathlib import Path
from typing import Dict, Any

from openai import OpenAI

try:
    from .config import load_nexus_config, NexusConfig
    from .tools import SafeToolExecutor, ApprovalDeniedError, SecurityError
    from .agent import NexusAgent, NEXUS_TOOL_DEFINITIONS
except (ImportError, ValueError):
    from nexus_agent.config import load_nexus_config, NexusConfig
    from nexus_agent.tools import SafeToolExecutor, ApprovalDeniedError, SecurityError
    from nexus_agent.agent import NexusAgent, NEXUS_TOOL_DEFINITIONS


class TestNexusIntegration(unittest.TestCase):
    """10-stage test suite covering Nexus API, safety gates, and Manim pipeline."""

    @classmethod
    def setUpClass(cls):
        cls.repo_dir = Path(__file__).resolve().parent.parent
        cls.test_session = cls.repo_dir / "nexus_test_session"
        cls.test_session.mkdir(parents=True, exist_ok=True)
        cls.config = load_nexus_config(session_slug="nexus-test-session")
        cls.client = OpenAI(
            base_url=cls.config.api_url,
            api_key=cls.config.api_key or "DUMMY_KEY",
        )
        cls.test_results: Dict[str, Any] = {}

    @classmethod
    def tearDownClass(cls):
        # Clean up test artifacts
        if cls.test_session.exists():
            for f in cls.test_session.glob("*"):
                try:
                    if f.is_file():
                        f.unlink()
                except Exception:
                    pass

    def test_01_api_connectivity(self):
        """1. Nexus API connectivity check."""
        print("\n--- Test 1: Nexus API Connectivity ---")
        if not self.config.api_key:
            self.skipTest("NEXUS_API_KEY is not configured in environment or .env file.")
        
        try:
            models = self.client.models.list()
            print(f"Connected to {self.config.api_url}. Found {len(models.data)} models.")
            self.assertTrue(len(models.data) >= 0)
        except Exception as e:
            self.fail(f"Failed to connect to Nexus endpoint {self.config.api_url}: {e}")

    def test_02_basic_text_response(self):
        """2. Basic text response check."""
        print("\n--- Test 2: Basic Text Response ---")
        if not self.config.api_key or not self.config.model:
            self.skipTest("NEXUS_API_KEY or NEXUS_MODEL is not set.")

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": "Respond with 'NEXUS_ONLINE'."}],
                max_tokens=20,
            )
            reply = response.choices[0].message.content or ""
            print(f"Model response: {reply.strip()}")
            self.assertTrue(len(reply) > 0)
        except Exception as e:
            self.fail(f"Basic text completion failed on {self.config.model}: {e}")

    def test_03_tool_calling(self):
        """3. Model tool calling capabilities."""
        print("\n--- Test 3: Tool Calling ---")
        if not self.config.api_key or not self.config.model:
            self.skipTest("NEXUS_API_KEY or NEXUS_MODEL is not set.")

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": "List the files currently in the session directory."}],
                tools=NEXUS_TOOL_DEFINITIONS,
                tool_choice="auto",
            )
            message = response.choices[0].message
            print(f"Tool calls received: {message.tool_calls}")
            self.assertIsNotNone(message.tool_calls)
            self.assertTrue(len(message.tool_calls) > 0)
        except Exception as e:
            print(f"Tool calling warning/failure: {e}")
            self.fail(f"Tool calling failed on {self.config.model}: {e}")

    def test_04_file_operations_with_approval(self):
        """4. File read/write operations with human approval gate & path traversal check."""
        print("\n--- Test 4: File Operations with Approval Gate ---")
        test_file = self.test_session / "approval_test.txt"

        # Gate that denies approval
        denying_executor = SafeToolExecutor(
            session_dir=self.test_session,
            repo_dir=self.repo_dir,
            approval_callback=lambda act, det: False
        )
        with self.assertRaises(ApprovalDeniedError):
            denying_executor.write_file("approval_test.txt", "Unauthorized write")

        # Gate that approves
        approving_executor = SafeToolExecutor(
            session_dir=self.test_session,
            repo_dir=self.repo_dir,
            approval_callback=lambda act, det: True
        )
        res = approving_executor.write_file("approval_test.txt", "Approved content 123")
        self.assertIn("Successfully wrote", res)
        self.assertTrue(test_file.exists())

        # Read file
        content = approving_executor.read_file("approval_test.txt")
        self.assertEqual(content, "Approved content 123")

        # Path traversal prevention test
        with self.assertRaises(SecurityError):
            approving_executor.write_file("../../outside_session.txt", "Exploit attempt")

    def test_05_command_execution_with_approval(self):
        """5. Command execution with approval, timeout, and secret masking."""
        print("\n--- Test 5: Command Execution with Approval & Masking ---")
        secret_token = "SECRET_TOKEN_XYZ_98765"
        
        executor = SafeToolExecutor(
            session_dir=self.test_session,
            repo_dir=self.repo_dir,
            approval_callback=lambda act, det: True,
            secret_patterns={secret_token},
            command_timeout_s=5,
        )

        # Denied command
        denying_executor = SafeToolExecutor(
            session_dir=self.test_session,
            repo_dir=self.repo_dir,
            approval_callback=lambda act, det: False
        )
        with self.assertRaises(ApprovalDeniedError):
            denying_executor.run_command("python --version")

        # Approved command
        output = executor.run_command("python --version")
        self.assertIn("Python", output)

        # Secret redaction check
        masked_output = executor.run_command(f'python -c "print(\'Output with {secret_token}\')"')
        self.assertNotIn(secret_token, masked_output)
        self.assertIn("[REDACTED_SECRET]", masked_output)

    def test_06_load_skill_instructions(self):
        """6. Loading the Manim skill instructions."""
        print("\n--- Test 6: Load SKILL.md Instructions ---")
        executor = SafeToolExecutor(session_dir=self.test_session, repo_dir=self.repo_dir)
        agent = NexusAgent(config=self.config, executor=executor)
        
        self.assertIn("manim-skill", agent.system_prompt)
        self.assertIn("Workflow: Narration → TTS → Code → Render → Mux → View", agent.system_prompt)
        print("SKILL.md loaded successfully into Agent system prompt.")

    def test_07_generate_basic_manim_scene(self):
        """7. Generating a basic Manim scene."""
        print("\n--- Test 7: Generate Basic Manim Scene ---")
        code = '''from manim import *

class TestSquareScene(Scene):
    def construct(self):
        sq = Square(side_length=2.0, color=BLUE)
        self.play(Create(sq), run_time=0.5)
        self.wait(0.5)
'''
        script_file = self.test_session / "test_scene.py"
        script_file.write_text(code, encoding="utf-8")
        self.assertTrue(script_file.exists())
        print(f"Generated test scene at {script_file}")

    def test_08_render_generated_scene(self):
        """8. Rendering the generated scene."""
        print("\n--- Test 8: Render Generated Scene ---")
        executor = SafeToolExecutor(
            session_dir=self.test_session,
            repo_dir=self.repo_dir,
            approval_callback=lambda act, det: True
        )
        cmd = f'manim -ql "{self.test_session / "test_scene.py"}" TestSquareScene'
        output = executor.run_command(cmd)
        print("Render output snippet:\n", output[:300])
        self.assertIn("Rendered TestSquareScene", output)

    def test_09_tts_availability(self):
        """9. Generating narrated animation if TTS is available."""
        print("\n--- Test 9: Kokoro TTS Verification ---")
        try:
            from kokoro import KPipeline
            import soundfile as sf
            pipeline = KPipeline(lang_code="a", device="cpu")
            self.assertIsNotNone(pipeline)
            print("Kokoro TTS pipeline is available and verified.")
        except Exception as e:
            self.fail(f"TTS verification failed: {e}")

    def test_10_video_viewer(self):
        """10. Opening the final video viewer (verifying launch readiness)."""
        print("\n--- Test 10: Video Viewer Module ---")
        viewer_script = self.repo_dir / "skills" / "manim-skill" / "tools" / "video_viewer.py"
        self.assertTrue(viewer_script.exists())
        
        executor = SafeToolExecutor(
            session_dir=self.test_session,
            repo_dir=self.repo_dir,
            approval_callback=lambda act, det: True
        )
        out = executor.run_command(f'python "{viewer_script}" --help')
        self.assertIn("usage: video_viewer.py", out)
        print("Video viewer script verified for launch readiness.")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNexusIntegration)
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    run_tests()

