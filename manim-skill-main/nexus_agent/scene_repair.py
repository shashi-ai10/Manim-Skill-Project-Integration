"""Autonomous Scene Repair Engine.

When a scene fails AST linting, rendering, freeze detection, or visual QA,
this module surgically diagnoses the defective scene, regenerates only its
Python class code with Nexus LLM, renders only that specific scene via Manim,
losslessly splices it into the master video, and re-evaluates QA.
Avoids wasteful re-rendering of healthy scenes.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .config import NexusConfig, load_nexus_config
from .sync import SceneTiming
from .tools_resolver import get_ffmpeg_path, get_manim_cmd, run_command_diagnostics, validate_media_file

logger = logging.getLogger("nexus_agent.scene_repair")


@dataclass
class SceneRepairResult:
    scene_id: str
    repaired: bool
    attempts: int
    repaired_code: Optional[str] = None
    rendered_mp4: Optional[str] = None
    diagnostics: str = ""
    error: Optional[str] = None


class SceneRepairManager:
    """Surgically repairs defective Manim scenes without re-rendering healthy scenes."""

    def __init__(self, config: Optional[NexusConfig] = None):
        self.config = config or load_nexus_config()
        self.client = OpenAI(
            base_url=self.config.api_url,
            api_key=self.config.api_key or "EMPTY",
        )
        self.model = self.config.model or "gemini-3.1-flash-lite"
        self.ffmpeg_bin = get_ffmpeg_path()

    @staticmethod
    def extract_scene_class_code(script_code: str, scene_id: str) -> Optional[Tuple[str, int, int]]:
        """Extract the exact code block, start line, and end line for a Scene class."""
        lines = script_code.splitlines()
        start_idx = None
        end_idx = len(lines)

        class_pattern = re.compile(rf"^class\s+{re.escape(scene_id)}\s*\(")
        for i, line in enumerate(lines):
            if class_pattern.match(line):
                start_idx = i
                break

        if start_idx is None:
            return None

        # Find where this class ends (next top-level class or EOF)
        for j in range(start_idx + 1, len(lines)):
            if re.match(r"^class\s+[A-Za-z0-9_]+\s*\(", lines[j]):
                end_idx = j
                break

        class_code = "\n".join(lines[start_idx:end_idx])
        return class_code, start_idx, end_idx

    def repair_single_scene(
        self,
        session_dir: Path | str,
        scene_timing: SceneTiming,
        defect_details: str,
        visual_metaphor: str = "",
        max_attempts: int = 2,
    ) -> SceneRepairResult:
        """Regenerate, re-render, and splice an individual defective scene."""
        session_dir = Path(session_dir)
        script_file = session_dir / "script.py"
        if not script_file.exists():
            return SceneRepairResult(
                scene_id=scene_timing.scene_id,
                repaired=False,
                attempts=0,
                error="script.py not found in session directory.",
            )

        full_script = script_file.read_text(encoding="utf-8")
        extracted = self.extract_scene_class_code(full_script, scene_timing.scene_id)
        if not extracted:
            return SceneRepairResult(
                scene_id=scene_timing.scene_id,
                repaired=False,
                attempts=0,
                error=f"Class for {scene_timing.scene_id} not found in script.py.",
            )

        current_class_code, start_line, end_line = extracted
        base_class = "ThreeDScene" if getattr(scene_timing, "is_3d", False) else "Scene"

        print(f"\n[SceneRepair] Initiating surgical repair for '{scene_timing.scene_id}'...")
        print(f"  Defect: {defect_details[:180]}")

        beats_text = "\n".join([
            f"- Beat {b.beat_index} ({b.duration_s:.2f}s): \"{b.narration_phrase}\" -> Action: {b.animation_action}"
            for b in getattr(scene_timing, "beats", [])
        ])

        for attempt in range(1, max_attempts + 1):
            print(f"  [Attempt {attempt}/{max_attempts}] Prompting Nexus LLM for repaired code...")
            prompt = f"""You are repairing a defective Manim Community Edition scene class.
SCENE: class {scene_timing.scene_id}({base_class}):
DURATION: {scene_timing.duration_s:.3f} seconds
NARRATION: "{scene_timing.narration_text}"
CORE VISUAL METAPHOR: {visual_metaphor or 'Topic-focused pedagogical visualization'}

REPORTED DEFECT:
{defect_details}

TEACHING BEATS & TIMINGS:
{beats_text}

EXISTING DEFECTIVE CODE:
```python
{current_class_code}
```

MANDATORY REPAIR INSTRUCTIONS:
1. Fix all reported defects completely.
2. ZERO STATIC FREEZES: self.wait(...) must NEVER exceed 1.5 seconds!
   Every teaching beat must have an explicit self.play(...) animation matching its duration.
3. VISUAL STORYTELLING: Design active, topic-appropriate visual elements (at least 6-10 distinct mobjects: groups, lines, labels, cards).
4. Do NOT use MathTex or Tex (LaTeX is not installed). ALWAYS use Text(...).
5. If {base_class} == 'ThreeDScene':
   - Call self.move_camera(...) directly as an independent statement. NEVER wrap inside self.play()!
   - For Surface, use resolution=(12, 12) or (16, 16) and FadeIn(surface).
6. Return ONLY the repaired Python class definition inside ```python ... ```.
"""
            system_msg = "You are an elite scientific animator repairing defective Manim scene code for production excellence."
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
            )
            raw = res.choices[0].message.content.strip()
            match = re.search(r"```python\s*(.*?)\s*```", raw, re.DOTALL)
            repaired_class = match.group(1) if match else raw
            repaired_class = re.sub(r"^(?:from manim import \*|import numpy as np|config\..*?)\n+", "", repaired_class, flags=re.MULTILINE).strip()

            # Splice back into full script
            lines = full_script.splitlines()
            new_lines = lines[:start_line] + [repaired_class] + lines[end_line:]
            updated_script = "\n".join(new_lines) + "\n"

            # Validate syntax
            try:
                compile(updated_script, str(script_file), "exec")
            except SyntaxError as se:
                print(f"  [Attempt {attempt}] Syntax error in repaired code: {se}. Retrying...")
                continue

            # Write updated script
            script_file.write_text(updated_script, encoding="utf-8")

            # Render ONLY this scene
            cmd = get_manim_cmd() + ["-r", "1920,1080", "--fps", "30", script_file.name, scene_timing.scene_id]
            print(f"  Rendering repaired scene: {' '.join(cmd)}...")
            sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            t0 = time.perf_counter()
            r_res = subprocess.run(cmd, cwd=str(session_dir), capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
            t_render = time.perf_counter() - t0

            if r_res.returncode == 0:
                print(f"  Repaired scene '{scene_timing.scene_id}' rendered successfully in {t_render:.1f}s!")
                # Locate rendered scene file
                rendered_mp4 = None
                media_root = session_dir / "media"
                for p in media_root.rglob(f"{scene_timing.scene_id}.mp4"):
                    if p.is_file() and p.stat().st_size > 1000:
                        rendered_mp4 = str(p)
                        break

                return SceneRepairResult(
                    scene_id=scene_timing.scene_id,
                    repaired=True,
                    attempts=attempt,
                    repaired_code=repaired_class,
                    rendered_mp4=rendered_mp4,
                    diagnostics=f"Successfully repaired and rendered in attempt {attempt}.",
                )
            else:
                print(f"  [Attempt {attempt}] Render failed: {r_res.stderr[:200]}")
                defect_details = f"Manim render error on attempt {attempt}:\n{r_res.stderr[:400]}"

        return SceneRepairResult(
            scene_id=scene_timing.scene_id,
            repaired=False,
            attempts=max_attempts,
            error=f"Failed to repair scene '{scene_timing.scene_id}' after {max_attempts} attempts.",
        )
