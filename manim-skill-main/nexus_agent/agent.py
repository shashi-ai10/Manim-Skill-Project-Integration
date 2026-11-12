"""Nexus Agent Runtime using OpenAI Python SDK."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from openai import OpenAI

from .config import NexusConfig
from .tools import SafeToolExecutor, ApprovalDeniedError, SecurityError
from .orchestrator import VideoProductionOrchestrator

# Tool schemas compliant with OpenAI Chat Completion function calling
NEXUS_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read text file content from the session directory or repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute path to the file to read."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file in the dedicated session directory. Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target filename or relative path within the session directory."
                    },
                    "content": {
                        "type": "string",
                        "description": "The exact text content to write."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the session directory or an allowed repository subdirectory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Optional subdirectory to list. Defaults to the session directory."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command (such as manim, ffmpeg, or python tool scripts). Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command string to execute."
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional working directory for the command. Defaults to session directory."
                    }
                },
                "required": ["command"]
            }
        }
    }
]


class NexusAgent:
    """Agent running on Navigate Labs Nexus API with tool execution and safety gates."""

    def __init__(
        self,
        config: NexusConfig,
        executor: SafeToolExecutor,
        skills_path: Optional[Path] = None,
        max_iterations: int = 15
    ):
        self.config = config
        self.executor = executor
        self.max_iterations = max_iterations
        self.client = OpenAI(
            base_url=self.config.api_url,
            api_key=self.config.api_key or "EMPTY",
        )
        self.skills_path = skills_path or Path(__file__).resolve().parent.parent / "skills" / "manim-skill" / "SKILL.md"
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Load SKILL.md as foundational instructions."""
        skill_text = ""
        if self.skills_path.exists():
            skill_text = self.skills_path.read_text(encoding="utf-8", errors="replace")

        tools_dir = Path(__file__).resolve().parent.parent / "skills" / "manim-skill" / "tools"

        return f"""You are an expert mathematical animation assistant powered by Nexus API and Manim Community.
You follow the workflow defined in SKILL.md below with strict NARRATION-FIRST SYNCHRONIZATION.

IMPORTANT ENVIRONMENT CONTEXT:
- Session Directory: {self.executor.session_dir}
- Repository Tools Directory: {tools_dir}
- All generated files MUST be saved into the Session Directory.
- When generating Python code with Manim on Windows, use Pango-backed Text() instead of MathTex()/Tex() if LaTeX is not installed.

CONTINUOUS ANIMATED STORYTELLING & SYNCHRONIZATION RULES (CRITICAL):
1. Phase 1: Write narration lines in narration.txt.
2. Phase 2: Run TTS (python "{tools_dir}/tts-generate.py" --narration narration.txt). Read timestamps.json immediately.
3. Phase 3: Create a separate Scene class for each narration segment (or ThreeDScene when spatial depth helps).
4. Continuous Dynamic Animation:
   - Break each narration segment into sequential teaching beats (every 2-4 seconds).
   - Each beat must actively demonstrate the spoken concept (progressive builds, component splits, 3D rotations, moving data streams).
   - Call self.add_subcaption(text, duration=duration_s) using the exact duration_s from timestamps.json.
   - Distribute animations across the entire scene duration. NEVER leave the screen static while narration continues.
   - For 3D scenes (e.g. 3D pixel cubes, loss landscapes, 3D embeddings): use ThreeDScene, Cube, Prism, Surface, and dynamic camera rotations.
5. Phase 4: Render all scenes with manim, list them in concat.txt, concatenate with ffmpeg, and mux with voiceover.mp3.
6. Verify with ffprobe that video duration and audio duration match within 0.2s.

=== SKILL.MD INSTRUCTIONS ===
{skill_text}
"""

    def run(self, user_prompt: str) -> Dict[str, Any]:
        """
        Execute agent loop handling conversation, tool calling, and iterative error recovery.
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        iterations = 0
        final_response = ""

        while iterations < self.max_iterations:
            iterations += 1

            try:
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    tools=NEXUS_TOOL_DEFINITIONS,
                    tool_choice="auto",
                )
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Nexus API request error: {e}",
                    "iterations": iterations,
                    "session_dir": str(self.executor.session_dir),
                    "messages": messages,
                }

            choice = response.choices[0]
            message = choice.message
            tool_calls = message.tool_calls

            # Append assistant message to history
            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            if message.content:
                assistant_msg["content"] = message.content
                final_response = message.content
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            # If no tools called, agent has finished its thought or delivered the final response
            if not tool_calls:
                break

            # Execute tool calls
            for tc in tool_calls:
                fn_name = tc.function.name
                fn_args_raw = tc.function.arguments
                call_id = tc.id

                try:
                    args = json.loads(fn_args_raw)
                except Exception as e:
                    tool_result = f"Error parsing arguments: {e}"
                else:
                    tool_result = self._dispatch_tool(fn_name, args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_result,
                })

        # Locate final deliverables if generated
        video_path = self.executor.session_dir / "video.mp4"
        script_path = self.executor.session_dir / "script.py"

        return {
            "success": video_path.exists() or bool(final_response),
            "iterations": iterations,
            "final_response": final_response,
            "session_dir": str(self.executor.session_dir),
            "video_path": str(video_path) if video_path.exists() else None,
            "script_path": str(script_path) if script_path.exists() else None,
        }

    def produce_video(
        self,
        topic: str,
        session_dir: Optional[Path | str] = None,
        auto_approve: bool = False,
        resume_from_stage: int = 1,
        target_scenes: int = 5,
        voice: str = "af_heart",
    ) -> Dict[str, Any]:
        """
        Execute the unified 11-stage educational video production workflow.
        """
        target_dir = session_dir or self.executor.session_dir
        orchestrator = VideoProductionOrchestrator(
            config=self.config,
            session_dir=target_dir,
            auto_approve=auto_approve,
            approval_callback=self.executor.approval_callback,
            voice=voice,
            target_scenes=target_scenes,
        )
        return orchestrator.run_pipeline(topic=topic, resume_from_stage=resume_from_stage)

    def _dispatch_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Route function call to safe tool executor."""
        try:
            if name == "read_file":
                return self.executor.read_file(args.get("path", ""))
            elif name == "write_file":
                return self.executor.write_file(args.get("path", ""), args.get("content", ""))
            elif name == "list_files":
                items = self.executor.list_files(args.get("directory"))
                return json.dumps(items, indent=2)
            elif name == "run_command":
                return self.executor.run_command(args.get("command", ""), args.get("working_dir"))
            else:
                return f"Error: Unknown tool '{name}'."
        except ApprovalDeniedError as ade:
            return f"Action rejected: {ade}"
        except SecurityError as se:
            return f"Security violation: {se}"
        except Exception as e:
            return f"Tool execution exception: {e}"

