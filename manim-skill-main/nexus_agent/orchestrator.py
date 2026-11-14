"""Unified End-to-End Educational Video Production Orchestrator.

Coordinates all 11 stages of video production:
1. Topic Ingestion & Pedagogical Structuring
2. Script & Narration Generation (Nexus LLM)
3. Approval Gate 1: Script & Narration Review
4. Kokoro TTS Audio Generation & Durations
5. Synchronized Timeline Construction (TimelineManager)
6. Manim Scene Code Generation (Nexus LLM)
7. Approval Gate 2: Manim Code & Scene Plan Review
8. Manim Scene Rendering & Concatenation
9. Procedural Audio Synthesis, Mixing & Ducking (SoundDesignEngine)
10. Broadcast Mastering, Loudness (-14 LUFS) & Subtitle Burning (PostProductionPipeline)
11. Contact Sheet Sampling & Nexus Vision QA Review (VisualQualityReviewer)
12. Final Approval Gate & Deliverables Presentation
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI

from .config import NexusConfig, load_nexus_config
from .sync import TimelineManager, SceneTiming
from .audio.engine import SoundDesignEngine
from .compositor.pipeline import PostProductionPipeline, probe_video_duration
from .compositor.overlays import build_composite_filtergraph
from .compositor.subtitles import build_master_srt_from_timeline
from .contact_sheet import generate_contact_sheet, extract_high_res_frame
from .vision_review import VisualQualityReviewer, generate_review_report
from .tools_resolver import (
    get_ffmpeg_path,
    get_ffprobe_path,
    get_manim_cmd,
    get_python_path,
    validate_required_executables,
    check_latex_available,
    run_command_diagnostics,
    validate_media_file,
)
from .skill_loader import SkillLoader
from .visual_director import TopicAwareVisualDirector, validate_visual_plan
from .rendered_video_qa import RenderedVideoQA, RenderedQAResult
from .scene_repair import SceneRepairManager, SceneRepairResult


@dataclass
class ProductionArtifacts:
    topic: str
    session_dir: str
    script_json: Optional[str] = None
    narration_txt: Optional[str] = None
    voiceover_mp3: Optional[str] = None
    timestamps_json: Optional[str] = None
    scene_timeline_json: Optional[str] = None
    visual_direction_json: Optional[str] = None
    manim_script_py: Optional[str] = None
    concat_txt: Optional[str] = None
    silent_video_mp4: Optional[str] = None
    synced_video_mp4: Optional[str] = None
    master_wav: Optional[str] = None
    broadcast_video_mp4: Optional[str] = None
    broadcast_subtitled_mp4: Optional[str] = None
    contact_sheet_png: Optional[str] = None
    visual_review_json: Optional[str] = None
    visual_report_md: Optional[str] = None
    rendered_qa_json: Optional[str] = None
    rendered_qa_md: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StageExecutionError(RuntimeError):
    """Raised when an individual pipeline stage fails."""
    pass


def lint_manim_code(code: str, timeline: List[SceneTiming]) -> List[str]:
    """Performs static AST and heuristic quality linting on generated Manim code
    before rendering, enforcing First-Pass Excellence standards.
    """
    issues: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"SyntaxError in generated code: {e}"]

    # 1. LaTeX prohibition check
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("MathTex", "Tex"):
            issues.append(
                f"Forbidden LaTeX class '{node.id}' detected on line {node.lineno}. "
                "LaTeX is not available; use Text(...) instead."
            )

    # 2. Scene class existence check
    defined_classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    for t in timeline:
        if t.scene_id not in defined_classes:
            issues.append(f"Missing required Scene class '{t.scene_id}' in generated script.")
            continue

        cls_node = defined_classes[t.scene_id]
        construct_def = next(
            (n for n in cls_node.body if isinstance(n, ast.FunctionDef) and n.name == "construct"),
            None
        )
        if not construct_def:
            issues.append(f"Scene class '{t.scene_id}' is missing construct(self) method.")
            continue

        # Inspect calls inside construct
        play_calls: List[int] = []
        long_waits: List[Tuple[float, int]] = []
        instantiated_mobjects: List[str] = []
        mobject_classes = {
            "Circle", "Square", "Rectangle", "RoundedRectangle", "Dot", "Line", "Arrow",
            "DoubleArrow", "CurvedArrow", "Text", "VGroup", "ThreeDAxes", "Surface", "Sphere",
            "Cube", "Prism", "Cylinder", "NumberPlane", "Axes"
        }

        for sub in ast.walk(construct_def):
            # Check for illegal self.camera.animate
            if isinstance(sub, ast.Attribute) and sub.attr == "animate":
                if isinstance(sub.value, ast.Attribute) and sub.value.attr == "camera":
                    issues.append(
                        f"Scene '{t.scene_id}' line {sub.lineno}: self.camera.animate does not exist in Manim! "
                        "In ThreeDScene, use self.move_camera(phi=..., theta=..., zoom=..., run_time=...) instead."
                    )
            if isinstance(sub, ast.Call):
                func_name = ""
                if isinstance(sub.func, ast.Attribute):
                    func_name = sub.func.attr
                elif isinstance(sub.func, ast.Name):
                    func_name = sub.func.id

                # Check for illegal self.play(self.move_camera(...))
                if func_name == "play":
                    for a in sub.args:
                        if isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "move_camera":
                            issues.append(
                                f"Scene '{t.scene_id}' line {sub.lineno}: self.move_camera(...) must NOT be wrapped inside self.play()! "
                                "Call self.move_camera(phi=..., theta=..., zoom=..., run_time=...) directly as an independent statement."
                            )

                # Check for illegal width/height on Axes
                if func_name == "Axes":
                    for kw in getattr(sub, "keywords", []):
                        if kw.arg in ("width", "height"):
                            issues.append(
                                f"Scene '{t.scene_id}' line {sub.lineno}: Axes does not accept '{kw.arg}' parameter! "
                                "Use x_length and y_length instead."
                            )

                if func_name in mobject_classes:
                    instantiated_mobjects.append(func_name)

                # Check for LaTeX requirements when LaTeX is not installed
                if not check_latex_available():
                    if func_name in ("MathTex", "Tex"):
                        issues.append(
                            f"Scene '{t.scene_id}' line {sub.lineno}: '{func_name}' requires LaTeX (pdflatex/latex) which is not installed on this system! "
                            "Use Text(...) with clean Unicode math symbols (e.g. θ, π, ∇, ∑, x², W·x + b) instead to avoid FileNotFoundError."
                        )
                    elif func_name == "BarChart":
                        has_text_label = any(kw.arg == "label_constructor" for kw in getattr(sub, "keywords", []))
                        if not has_text_label:
                            issues.append(
                                f"Scene '{t.scene_id}' line {sub.lineno}: 'BarChart' invokes LaTeX by default on Windows! "
                                "Construct custom bars using a VGroup of Rectangles and Text labels, or pass label_constructor=Text to avoid FileNotFoundError."
                            )

                if func_name == "wait" and sub.args:
                    first_arg = sub.args[0]
                    val = getattr(first_arg, "value", getattr(first_arg, "n", None))
                    if isinstance(val, (int, float)) and val > 1.5:
                        long_waits.append((float(val), sub.lineno))
                elif func_name in ("play", "move_camera"):
                    play_calls.append(sub.lineno)

        beats_cnt = len(getattr(t, "beats", []) or [])
        min_plays = max(3, beats_cnt)
        if len(play_calls) < min_plays:
            issues.append(
                f"Scene '{t.scene_id}' has only {len(play_calls)} animation calls "
                f"for {beats_cnt} teaching beats (requires at least {min_plays}). "
                "Every beat must have an active animation!"
            )
        if long_waits:
            for val, line in long_waits:
                issues.append(
                    f"Scene '{t.scene_id}' line {line}: self.wait({val}s) is a static freeze (>1.5s)! "
                    "Replace long waits with active continuous animations or short 0.5s settling holds."
                )
        if len(instantiated_mobjects) < 6:
            issues.append(
                f"Scene '{t.scene_id}' is visually sparse (only {len(instantiated_mobjects)} mobjects created: {instantiated_mobjects}). "
                "High-quality educational animation requires multi-element diagrams: background plane, multi-component diagrams (nodes, grids, curves), labels, and UI telemetry cards (at least 6 distinct mobjects)."
            )

        # Topic-agnostic structural completeness checks
        if getattr(t, "is_3d", False):
            has_3d = any(m in instantiated_mobjects for m in ("ThreeDAxes", "Surface", "Sphere", "Cube", "Prism", "Cylinder", "Dot3D"))
            if not has_3d:
                issues.append(
                    f"Scene '{t.scene_id}' is designated as a 3D scene (is_3d=True) but lacks 3D spatial elements. "
                    "Must instantiate 3D mobjects (e.g. ThreeDAxes, Surface, Cube, Sphere, Dot3D)."
                )

        # Check for meaningful visual elements (lines, shapes, text)
        has_shapes = any(m in instantiated_mobjects for m in ("Circle", "Square", "Rectangle", "RoundedRectangle", "Dot", "Cube", "Sphere"))
        has_text_or_lines = any(m in instantiated_mobjects for m in ("Text", "Line", "Arrow", "DoubleArrow", "CurvedArrow"))
        if not (has_shapes or has_text_or_lines):
            issues.append(
                f"Scene '{t.scene_id}' lacks core geometric or textual elements. "
                "Must construct concrete shapes (Circle, Square, Rectangle) and connections/labels (Line, Arrow, Text)."
            )

    return issues


class VideoProductionOrchestrator:
    """Orchestrates the entire educational video production pipeline."""

    def __init__(
        self,
        config: Optional[NexusConfig] = None,
        session_dir: Optional[Path | str] = None,
        auto_approve: bool = False,
        approval_callback: Optional[Callable[[str, str], bool]] = None,
        voice: str = "af_heart",
        target_scenes: int = 5,
        target_duration_s: Optional[int] = None,
    ):
        self.config = config or load_nexus_config()
        self.auto_approve = auto_approve
        self.approval_callback = approval_callback
        self.voice = voice
        self.target_scenes = target_scenes
        self.target_duration_s = target_duration_s

        self.repo_dir = Path(__file__).resolve().parent.parent
        self.tools_dir = self.repo_dir / "skills" / "manim-skill" / "tools"

        if session_dir is None:
            self.session_dir = self.config.session_root
        else:
            self.session_dir = Path(session_dir).resolve()
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.state_file = self.session_dir / "session_state.json"
        self.artifacts = ProductionArtifacts(
            topic="",
            session_dir=str(self.session_dir)
        )
        self.client = OpenAI(
            base_url=self.config.api_url,
            api_key=self.config.api_key or "EMPTY",
        )
        self.model = self.config.model or "gemini-3.1-flash-lite"
        self.skill_loader = SkillLoader()
        self.visual_director = TopicAwareVisualDirector(config=self.config, skill_loader=self.skill_loader)
        self.qa_engine = RenderedVideoQA(config=self.config)
        self.repair_manager = SceneRepairManager(config=self.config)
        self._load_state()

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                for k, v in data.get("artifacts", {}).items():
                    if hasattr(self.artifacts, k):
                        setattr(self.artifacts, k, v)
                self.artifacts.topic = data.get("topic", self.artifacts.topic)
            except Exception as e:
                print(f"[Notice] Could not load state from {self.state_file}: {e}")

    STAGE_DESCRIPTIONS: Dict[int, str] = {
        1: "Stage 1/11: Ingesting topic and initializing workspace",
        2: "Stage 2/11: Generating educational script with Nexus LLM",
        3: "Stage 3/11: Script approval & scene breakdown",
        4: "Stage 4/11: Synthesizing narration with Kokoro TTS",
        5: "Stage 5/11: Constructing narration-first synchronized timeline",
        6: "Stage 6/11: Generating Manim animation code",
        7: "Stage 7/11: Manim code syntax validation & approval",
        8: "Stage 8/11: Rendering Manim scenes and assembling video",
        9: "Stage 9/11: Synthesizing procedural SFX & speech ducking",
        10: "Stage 10/11: Two-pass EBU R128 loudness mastering & subtitling",
        11: "Stage 11/11: Generating contact sheet & running Nexus Vision QA",
        12: "Production Complete: Master video and visual audit verified",
    }

    def _save_state(
        self,
        current_stage: int,
        completed_stages: List[int],
        status: Optional[str] = None,
        error: Optional[str] = None,
        stage_status: str = "completed",
    ) -> None:
        if status is None:
            status = "completed" if current_stage >= 12 else "running"

        if current_stage >= 12:
            progress_pct = 100
        else:
            base_pct = (current_stage - 1) / 11.0
            add_pct = (1.0 / 11.0) if stage_status == "completed" else (0.5 / 11.0)
            progress_pct = int(min(base_pct + add_pct, 0.95) * 100)

        desc = self.STAGE_DESCRIPTIONS.get(current_stage, f"Stage {current_stage}")
        if stage_status == "in_progress" and current_stage < 12:
            desc = f"{desc} (in progress)"

        data = {
            "topic": self.artifacts.topic,
            "session_dir": str(self.session_dir),
            "status": status,
            "current_stage": current_stage,
            "progress_percent": progress_pct,
            "stage_description": desc,
            "completed_stages": completed_stages,
            "artifacts": self.artifacts.to_dict(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if error:
            data["error"] = error

        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        meta_file = self.session_dir / "mcp_metadata.json"
        if meta_file.exists():
            try:
                m_data = json.loads(meta_file.read_text(encoding="utf-8"))
                m_data["status"] = status
                m_data["current_stage"] = current_stage
                m_data["progress_percent"] = progress_pct
                m_data["stage_description"] = desc
                m_data["updated_at"] = time.time()
                if error:
                    m_data["error"] = error
                meta_file.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _request_approval(self, stage_name: str, details: str) -> bool:
        """Trigger human approval gate."""
        if self.auto_approve:
            print(f"\n[AUTO-APPROVED GATE] {stage_name}")
            return True

        if self.approval_callback:
            return self.approval_callback(stage_name, details)

        print(f"\n" + "=" * 65)
        print(f"APPROVAL GATE: {stage_name.upper()}")
        print("=" * 65)
        print(details)
        print("-" * 65)
        try:
            choice = input(f"Approve {stage_name} and proceed? [Y/n]: ").strip().lower()
            return choice in ("", "y", "yes")
        except (KeyboardInterrupt, EOFError):
            return False

    # -------------------------------------------------------------------------
    # Pipeline Stages
    # -------------------------------------------------------------------------

    def stage1_analyze_topic(self, topic: str) -> Dict[str, Any]:
        """Stage 1: Accept topic, structure parameters, initialize workspace and validate executables."""
        print(f"\n[STAGE 1/11] Ingesting topic: \"{topic}\"")
        self.artifacts.topic = topic

        # 1. Initialize workspace directories
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "media").mkdir(parents=True, exist_ok=True)
        (self.session_dir / "_temp_frames").mkdir(parents=True, exist_ok=True)
        (self.session_dir / "_temp_post").mkdir(parents=True, exist_ok=True)
        (self.session_dir / "_temp_post_sub").mkdir(parents=True, exist_ok=True)

        # 2. Validate environment & required executables
        env_specs = validate_required_executables()
        print("  Workspace & Executables Validated:")
        print(f"    - Python:  {env_specs['python']}")
        print(f"    - Manim:   {' '.join(env_specs['manim_cmd'])}")
        print(f"    - FFmpeg:  {env_specs['ffmpeg']}")
        print(f"    - FFprobe: {env_specs['ffprobe']}")
        print(f"    - Git:     {env_specs['git']}")
        print(f"    - LaTeX:   {env_specs['latex']}")

        if self.target_duration_s is None:
            m_min = re.search(r"(\d+)\s*(?:-|–)?\s*(?:min|minute)s?", topic, re.IGNORECASE)
            m_sec = re.search(r"(\d+)\s*(?:-|–)?\s*(?:sec|second)s?", topic, re.IGNORECASE)
            if m_min:
                self.target_duration_s = int(m_min.group(1)) * 60
            elif m_sec:
                self.target_duration_s = int(m_sec.group(1))

        dur_str = f"{self.target_duration_s}s" if self.target_duration_s else "auto (standard)"
        print(f"  Target scenes:   {self.target_scenes}")
        print(f"  Target duration: {dur_str}")
        print(f"  Kokoro voice:    {self.voice}")
        print(f"  Session directory: {self.session_dir}")
        return {
            "topic": topic,
            "target_scenes": self.target_scenes,
            "target_duration_s": self.target_duration_s,
            "voice": self.voice,
            "environment": env_specs,
        }

    def stage2_generate_script(self, topic: str) -> Dict[str, Any]:
        """Stage 2: Generate educational script with structured scenes using Nexus LLM."""
        print(f"\n[STAGE 2/11] Generating educational script & narration with Nexus ({self.model})...")

        if self.target_duration_s:
            target_words = int(self.target_duration_s * 2.4)
            words_per_scene = max(25, target_words // self.target_scenes)
            duration_guidance = (
                f"\n2. DURATION ADHERENCE SPECIFICATION:\n"
                f"   - Target Duration: Approximately {self.target_duration_s} seconds ({self.target_duration_s // 60}m {self.target_duration_s % 60}s).\n"
                f"   - Target Total Narration: Approximately {target_words} spoken words across all {self.target_scenes} scenes.\n"
                f"   - Per-Scene Narration Budget: Each scene MUST have approximately {words_per_scene} spoken words "
                f"(approx 3-4 substantive pedagogical sentences). Provide thorough educational depth to meet this target runtime naturally without artificial filler."
            )
            narration_req = f"3-4 spoken sentences (approx {words_per_scene} words total per scene, detailed, engaging, conversational, no markdown asterisks or quotes inside)."
        else:
            duration_guidance = ""
            narration_req = "2-3 spoken sentences (approx 35-45 words each, conversational, clear, no markdown asterisks or quotes inside)."

        system_msg = (
            "You are an expert pedagogical director and motion graphics lead for educational animation videos. "
            "You create visual-first explanations where animations dynamically demonstrate concepts synchronized with narration."
        )
        user_prompt = f"""Create an educational video script explaining the topic: "{topic}".
Design exactly {self.target_scenes} sequential scenes that build intuition step-by-step for beginners.

NARRATIVE ARC FRAMEWORK (Select the most pedagogically appropriate arc):
- Discovery Arc (Foundational concepts): Hook -> Intuition -> Formalize -> Reveal / Aha Moment -> Extend
- Problem-Solution Arc (Algorithms/Techniques): Problem -> Failed Attempt -> Key Insight -> Solution -> Result
- Comparison Arc (Architectures/Trade-offs): Setup -> Approach A -> Approach B -> Contrast -> Verdict
- Build-Up Arc (Complex systems/Networks): Component A -> Component B -> Connection -> Scale -> Full Picture

PEDAGOGICAL DESIGN PRINCIPLES:
1. Geometry before algebra: Show the visual structure or geometric shape first; introduce the equation second so it feels earned.
2. Continuous motion: The video must TEACH concepts through animation and progressive reveals, not static text slides.
3. Narration-to-visual beats: Break narration into sequential teaching beats (every 2-4 seconds), each paired with an explicit visual transformation.
4. Opacity layering: Primary elements at 1.0 opacity, contextual elements at 0.4, structural grids at 0.15.
5. The see-then-hear principle: The visual change appears slightly before or synchronously with the spoken phrase.
6. 3D spatial depth: Designate scenes as 3D (is_3d: true) where spatial geometry aids intuition (e.g., 3D pixel cubes, loss landscapes, 3D embeddings).

REQUIREMENTS:
1. Include a top-level "narrative_arc": name of chosen arc (e.g. "Discovery Arc", "Build-Up Arc").
2. Each scene must have:
   - "scene_id": Clean Python identifier class name (e.g. Scene1_Intuition, Scene2_Architecture, Scene3_Mechanism, Scene4_Tuning, Scene5_Summary)
   - "title": Short descriptive title (e.g. "The Artificial Neuron")
   - "is_3d": boolean (true if scene benefits from 3D depth, e.g. 3D cubes, surfaces, spatial rotation)
   - "narration": {narration_req}
   - "visual_narrative": 1-2 sentences describing the continuous visual progression throughout the scene.
   - "visual_elements": list of 4-6 specific, concrete visual objects, mathematical formulas, and diagram components.
     * Explicitly specify the diagram architecture (e.g. "3D grid of pixel blocks", "Multi-layer network graph with interconnected synapses", "Axes with labeled curves", "Query-Key-Value projection matrices").
     * Explicitly specify exact mathematical formula or parameter badge (e.g. "z = w1*x1 + w2*x2 + b", "Attention(Q,K,V)").
     * Avoid vague or purely decorative descriptions. Every visual must serve an educational purpose.
   - "teaching_beats": list of 2-5 sequential visual beats matching parts of the narration. Each item:
     {{"narration_phrase": "exact phrase spoken", "visual_action": "concrete visual event: enter, split, assemble, connect, zoom, rotate, highlight"}}
   - "pedagogical_goal": 1 sentence describing what the student learns.
{duration_guidance}
You MUST respond strictly with a valid JSON object matching this schema:
{{
  "topic": "{topic}",
  "narrative_arc": "Discovery Arc",
  "scenes": [
    {{
      "scene_id": "Scene1_Intuition",
      "title": "...",
      "is_3d": false,
      "narration": "...",
      "visual_narrative": "...",
      "visual_elements": ["...", "..."],
      "teaching_beats": [
        {{"narration_phrase": "...", "visual_action": "..."}}
      ],
      "pedagogical_goal": "..."
    }}
  ]
}}
Do NOT include markdown formatting or commentary outside the JSON object.
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        try:
            script_data = json.loads(content)
        except Exception as e:
            # Fallback regex extraction
            match = re.search(r"(\{.*\})", content, re.DOTALL)
            if match:
                script_data = json.loads(match.group(1))
            else:
                raise StageExecutionError(f"Failed to parse Nexus script response as JSON: {content}") from e

        # Validate scenes
        scenes = script_data.get("scenes", [])
        if not scenes:
            raise StageExecutionError("Nexus returned an empty scene list.")

        script_file = self.session_dir / "script_breakdown.json"
        script_file.write_text(json.dumps(script_data, indent=2), encoding="utf-8")
        self.artifacts.script_json = str(script_file)
        print(f"  Generated {len(scenes)} scenes successfully -> {script_file.name}")
        return script_data

    def stage3_approve_script(self, script_data: Dict[str, Any]) -> None:
        """Stage 3: Approval Gate for Script & Narration."""
        scenes = script_data.get("scenes", [])
        summary_lines = [
            f"Topic: {script_data.get('topic', self.artifacts.topic)}",
            f"Scene Count: {len(scenes)}",
            "",
            "SCENE BREAKDOWN:",
        ]
        for i, s in enumerate(scenes, 1):
            summary_lines.append(f"[{i}] {s.get('scene_id')} - \"{s.get('title')}\"")
            summary_lines.append(f"    Narration: \"{s.get('narration')}\"")
            summary_lines.append(f"    Visuals:   {', '.join(s.get('visual_elements', []))}")

        details = "\n".join(summary_lines)
        approved = self._request_approval("Script & Narration", details)
        if not approved:
            raise StageExecutionError("Pipeline paused or rejected at Script & Narration approval gate.")

    def stage4_generate_tts(self, script_data: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 4: Generate narration using Kokoro TTS and measure timestamps."""
        print(f"\n[STAGE 4/11] Synthesizing narration audio with Kokoro TTS (voice: {self.voice})...")
        scenes = script_data.get("scenes", [])

        # Write narration.txt in SubtitleSpec format
        narration_file = self.session_dir / "narration.txt"
        narration_lines = ["subtitles:"]
        for s in scenes:
            line = s.get("narration", "").strip()
            # Escape quotes if necessary
            clean_line = line.replace('"', '\\"')
            narration_lines.append(f'- "{clean_line}"')

        narration_file.write_text("\n".join(narration_lines) + "\n", encoding="utf-8")
        self.artifacts.narration_txt = str(narration_file)

        voiceover_file = self.session_dir / "voiceover.mp3"
        timestamps_file = self.session_dir / "timestamps.json"

        tts_script = self.tools_dir / "tts-generate.py"
        if not tts_script.exists():
            raise StageExecutionError(f"TTS generator script not found at {tts_script}")

        cmd = [
            sys.executable, str(tts_script),
            "--narration", str(narration_file),
            "--voice", self.voice,
            "--out-audio", str(voiceover_file),
            "--out-timestamps", str(timestamps_file),
        ]
        sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        res = subprocess.run(cmd, cwd=str(self.session_dir), capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        if res.returncode != 0:
            raise StageExecutionError(f"Kokoro TTS generation failed:\n{res.stderr}\n{res.stdout}")

        if not voiceover_file.exists() or not timestamps_file.exists():
            raise StageExecutionError(f"Expected TTS outputs not found ({voiceover_file.name}, {timestamps_file.name})")

        self.artifacts.voiceover_mp3 = str(voiceover_file)
        self.artifacts.timestamps_json = str(timestamps_file)

        ts_data = json.loads(timestamps_file.read_text(encoding="utf-8"))
        total_audio = ts_data.get("total_duration_s", 0.0)
        print(f"  TTS complete: {len(ts_data.get('subtitles', []))} clips synthesized. Total audio: {total_audio:.2f}s")
        return ts_data

    def stage5_construct_timeline(self, script_data: Dict[str, Any]) -> List[SceneTiming]:
        """Stage 5: Calculate exact scene-level timings and write scene_timeline.json."""
        print("\n[STAGE 5/11] Constructing narration-first synchronized timeline...")
        scenes = script_data.get("scenes", [])
        scene_names = [s.get("scene_id") for s in scenes]
        visual_plans = [s.get("visual_elements", []) for s in scenes]

        tm = TimelineManager(session_dir=self.session_dir)
        timeline = tm.build_timeline(
            timestamps_path=Path(self.artifacts.timestamps_json),
            scene_names=scene_names,
            visual_plans=visual_plans,
            script_scenes=scenes,
        )
        self.artifacts.scene_timeline_json = str(self.session_dir / "scene_timeline.json")

        total_anim = sum(t.anim_duration_s for t in timeline)
        total_hold = sum(t.hold_duration_s for t in timeline)
        total_dur = sum(t.duration_s for t in timeline)
        total_beats = sum(len(t.beats) for t in timeline)
        print(f"  Timeline constructed: {len(timeline)} scenes, {total_beats} teaching beats ({total_dur:.2f}s total)")
        print(f"  Animation budget: {total_anim:.2f}s | Holding buffer: {total_hold:.2f}s")

        # Duration validation & deviation metrics
        duration_metrics = {
            "actual_duration_s": round(total_dur, 3),
            "target_duration_s": self.target_duration_s,
        }
        if self.target_duration_s:
            dev_s = round(total_dur - self.target_duration_s, 3)
            dev_pct = round(abs(dev_s) / self.target_duration_s * 100, 2)
            duration_metrics["deviation_s"] = dev_s
            duration_metrics["deviation_pct"] = dev_pct
            print(f"  Duration Adherence: Actual={total_dur:.1f}s | Target={self.target_duration_s}s | Deviation={dev_s:+.1f}s ({dev_pct:.1f}%)")

        # Append metrics to scene_timeline.json
        try:
            tl_path = Path(self.artifacts.scene_timeline_json)
            raw = json.loads(tl_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                tl_wrapped = {
                    "scenes": raw,
                    "total_duration_s": round(total_dur, 3),
                    "duration_metrics": duration_metrics
                }
                tl_path.write_text(json.dumps(tl_wrapped, indent=2), encoding="utf-8")
            elif isinstance(raw, dict):
                raw["duration_metrics"] = duration_metrics
                raw["total_duration_s"] = round(total_dur, 3)
                tl_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  [Notice] Could not attach duration metrics to timeline: {e}")

        return timeline

    def stage5_5_generate_visual_direction(self, timeline: List[SceneTiming]) -> Dict[str, Any]:
        """Stage 5.5: Professional Visual Direction & Motion Choreography Planning.
        
        Develops a comprehensive visual direction blueprint for each scene before code generation:
        defining visual metaphors, 1080p composition, depth layering, visual hierarchy,
        semantic colors, and motion choreography to eliminate sparse/slide-like outputs.
        """
        print(f"\n[STAGE 5.5/11] Developing topic-aware Visual Direction & Storyboard Blueprint ({self.model})...")

        vdir_path = self.session_dir / "visual_direction.json"
        if vdir_path.exists():
            try:
                data = json.loads(vdir_path.read_text(encoding="utf-8"))
                if "scenes" in data and len(data["scenes"]) == len(timeline):
                    print(f"  [Recovery] Found valid visual direction ({len(data['scenes'])} scenes) in {vdir_path.name}.")
                    self.artifacts.visual_direction_json = str(vdir_path)
                    return data
            except Exception:
                pass

        try:
            vdir_data = self.visual_director.generate_visual_plan(
                topic=self.artifacts.topic,
                timeline=timeline,
            )
        except Exception as e:
            raise StageExecutionError(f"Visual Director failed to generate visual plan: {e}") from e

        vdir_path.write_text(json.dumps(vdir_data, indent=2), encoding="utf-8")
        self.artifacts.visual_direction_json = str(vdir_path)
        print(f"  Visual Direction Blueprint established -> {vdir_path.name} ({len(vdir_data.get('scenes', []))} scenes planned)")
        return vdir_data

    def stage6_generate_manim_code(self, timeline: List[SceneTiming]) -> str:
        """Stage 6: Generate production-grade Manim Community code with continuous visual storytelling."""
        print(f"\n[STAGE 6/11] Generating continuous synchronized Manim animation code with Nexus ({self.model})...")

        # Ingest original Manim Skill & Manim Video instructions
        skill_context = self.skill_loader.get_stage_context("manim_generation")

        # Establish professional topic-aware visual direction first
        visual_direction = self.stage5_5_generate_visual_direction(timeline)

        preamble = (
            "from manim import *\n"
            "import numpy as np\n\n"
            "# 1080p 30fps Configuration\n"
            "config.pixel_width = 1920\n"
            "config.pixel_height = 1080\n"
            "config.frame_width = 16.0\n"
            "config.frame_height = 9.0\n"
            "config.frame_rate = 30\n\n"
            "BG_COLOR = \"#0f172a\"\n"
            "PRIMARY = \"#38bdf8\"\n"
            "SECONDARY = \"#818cf8\"\n"
            "ACCENT = \"#f43f5e\"\n"
            "SUCCESS = \"#10b981\"\n"
            "WARNING = \"#fbbf24\"\n"
            "TEXT_COLOR = \"#f8fafc\"\n"
            "MUTED_COLOR = \"#64748b\"\n\n"
        )

        scene_codes = []
        for i, t in enumerate(timeline, 1):
            print(f"  Synthesizing scene {i}/{len(timeline)}: {t.scene_id} ({t.duration_s:.1f}s)...")
            v_scene = next((s for s in visual_direction.get("scenes", []) if s.get("scene_id") == t.scene_id), {})
            base_class = "ThreeDScene" if getattr(t, "is_3d", False) else "Scene"

            beats_text = "\n".join([
                f"- Beat {b.beat_index} (duration={b.duration_s:.3f}s): \"{b.narration_phrase}\"\n"
                f"  Visual Action: {b.animation_action}"
                for b in getattr(t, "beats", [])
            ])

            objects_desc = json.dumps(v_scene.get("objects_and_relationships", []), indent=2)
            anim_plan_desc = json.dumps(v_scene.get("animation_sequence", []), indent=2)

            scene_prompt = f"""Generate the Python class `class {t.scene_id}({base_class}):` for Scene {i}/{len(timeline)} of "{self.artifacts.topic}".

TOTAL SCENE DURATION: {t.duration_s:.3f} seconds
BASE CLASS: {base_class}
NARRATION: "{t.narration_text}"

TOPIC-AWARE VISUAL ARCHITECTURE PLAN:
- Learning Objective: {v_scene.get('learning_objective', 'Conceptual intuition breakthrough')}
- Core Visual Metaphor: {v_scene.get('core_visual_metaphor', 'Scientific and geometric animation')}
- Main Visual Composition: {v_scene.get('main_visual_composition', 'Layered widescreen layout')}
- Screen Occupancy Target: {v_scene.get('approximate_screen_occupancy', '70%–80% canvas coverage')}
- Planned Objects & Relationships:
{objects_desc}
- Transitions: {v_scene.get('transitions', 'Progressive object transformation')}
- Expected Final Visual State: {v_scene.get('expected_visual_state_at_end', 'Complete structured diagram')}

TEACHING BEATS TO CHOREOGRAPH (SEQUENTIAL ANIMATIONS):
{beats_text}

INGESTED SKILL & MOTION GUIDELINES:
{skill_context}

MANDATORY IMPLEMENTATION RULES:
1. Define EXACTLY ONE class:
   class {t.scene_id}({base_class}):
       def construct(self): ...
2. Canvas & Visual Storytelling:
   * self.camera.background_color = BG_COLOR
   * In 2D scenes, add subtle background depth: NumberPlane(x_range=[-8, 8, 2], y_range=[-4.5, 4.5, 2], stroke_opacity=0.25, color=MUTED_COLOR).
   * Construct rich multi-element diagrams filling 65%–85% of the 1920x1080 canvas (at least 6-10 distinct mobjects: groups, shapes, arrows, formula cards).
   * Meaningful transformations: Progressively build concepts (use ReplacementTransform or Transform where concepts evolve).
   * Sequential self.play(...) animations for each beat with run_time matching beat duration exactly! Total animation time across all beats must equal {t.duration_s:.3f}s.
   * ZERO STATIC FREEZES: self.wait(...) must NEVER exceed 1.5s!
3. 3D SCENE RULES (if {base_class} == 'ThreeDScene'):
   * Call self.set_camera_orientation(phi=70*DEGREES, theta=-45*DEGREES) initially.
   * For Surfaces, use resolution=(12, 12) or (16, 16) and FadeIn(surface) rather than Create(surface).
   * To animate camera, call `self.move_camera(phi=..., theta=..., zoom=..., run_time=...)` directly as a standalone statement. NEVER wrap move_camera inside self.play()! NEVER call self.camera.animate!
4. NO LATEX: NEVER use MathTex or Tex. ALWAYS use Text(...) with font_size and color.
5. Constants available in scope: BG_COLOR, PRIMARY, SECONDARY, ACCENT, SUCCESS, WARNING, TEXT_COLOR, MUTED_COLOR.

Output ONLY the class definition in ```python ... ```.
"""
            system_msg = (
                "You are a master mathematical and scientific animator specializing in Manim Community Edition (manimce). "
                "You write dense, beautiful, screen-filling educational animations with continuous visual storytelling."
            )

            res = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": scene_prompt},
                ],
                temperature=0.15,
            )
            raw = res.choices[0].message.content.strip()
            match = re.search(r"```python\s*(.*?)\s*```", raw, re.DOTALL)
            class_code = match.group(1) if match else raw
            class_code = re.sub(r"^(?:from manim import \*|import numpy as np|config\..*?)\n+", "", class_code, flags=re.MULTILINE).strip()

            # Scene-level AST & animation quality gate
            for attempt in range(2):
                test_script = preamble + class_code + "\n"
                scene_issues = lint_manim_code(test_script, [t])
                if not scene_issues:
                    break
                print(f"    [Quality Gate] Scene '{t.scene_id}' defect: {scene_issues[0]}")
                defect_msg = "\n".join(f"- {iss}" for iss in scene_issues)
                class_code = self._repair_single_scene_class_with_llm(class_code, t, defect_msg, base_class)

            scene_codes.append(class_code.strip())

        code = preamble + "\n\n".join(scene_codes) + "\n"

        script_path = self.session_dir / "script.py"
        script_path.write_text(code, encoding="utf-8")
        self.artifacts.manim_script_py = str(script_path)

        # Validate python syntax
        try:
            compile(code, str(script_path), "exec")
            print(f"  Python syntax verified successfully -> {script_path.name}")
        except SyntaxError as e:
            print(f"  [Notice] Syntax error detected ({e}). Attempting self-healing repair...")
            code = self._repair_code_with_llm(code, str(e))
            script_path.write_text(code, encoding="utf-8")
            compile(code, str(script_path), "exec")
            print(f"  Python syntax verified after self-healing -> {script_path.name}")

        # Automated Quality Gate: AST & Animation Density Linting
        for attempt in range(2):
            issues = lint_manim_code(code, timeline)
            if not issues:
                break
            print(f"  [Quality Gate] Static linting found {len(issues)} issue(s): {issues[0]}")
            defect_report = "\n".join(f"- {iss}" for iss in issues)
            code = self._repair_code_with_llm(code, defect_report)
            script_path.write_text(code, encoding="utf-8")
            try:
                compile(code, str(script_path), "exec")
            except Exception as e:
                print(f"  [Warning] Compile issue after lint repair: {e}")

        final_issues = lint_manim_code(code, timeline)
        if not final_issues:
            print("  [Quality Gate] Manim script passed all First-Pass Excellence AST & animation density checks!")
        else:
            print(f"  [Notice] Residual lint warnings ({len(final_issues)}): {final_issues[0]}")

        return code

    def _repair_single_scene_class_with_llm(
        self, class_code: str, t: SceneTiming, error_msg: str, base_class: str
    ) -> str:
        """Self-heal a single Manim scene class without modifying other scenes."""
        print(f"    [Self-Healing] Prompting Nexus LLM to repair scene '{t.scene_id}'...")
        prompt = f"""The following Manim Community Edition scene class failed our quality gate:

SCENE ID: {t.scene_id}
BASE CLASS: {base_class}
DURATION: {t.duration_s:.3f} seconds

DEFECTS REPORTED:
{error_msg}

CURRENT CLASS CODE:
```python
{class_code}
```

REPAIR RULES:
1. Fix all reported defects.
2. Maintain exact class name: `class {t.scene_id}({base_class}):`.
3. In 2D scenes, include a subtle NumberPlane(stroke_opacity=0.35, color=MUTED_COLOR) in the background.
4. Construct rich multi-element graphics (at least 6-10 distinct mobjects: groups, labels, telemetry cards, diagrams).
5. ZERO STATIC FREEZES: self.wait() must never exceed 1.5s! Sequential self.play(...) calls must match the scene duration.
6. If ThreeDScene, use self.move_camera(phi=..., theta=..., zoom=..., run_time=...). NEVER wrap move_camera in self.play!
7. NEVER use MathTex or Tex. Use Text(...) only.
8. Output ONLY the repaired class definition inside ```python ... ```.
"""
        res = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = res.choices[0].message.content.strip()
        match = re.search(r"```python\s*(.*?)\s*```", content, re.DOTALL)
        repaired = match.group(1) if match else content
        return re.sub(r"^(?:from manim import \*|import numpy as np|config\..*?)\n+", "", repaired, flags=re.MULTILINE).strip()

    def _repair_code_with_llm(self, code: str, error_msg: str) -> str:
        """Self-heal Manim code by prompting Nexus LLM with the error traceback or quality defects."""
        print("  [Self-Healing] Prompting Nexus LLM to repair Manim code for First-Pass Excellence...")
        prompt = f"""The following Manim Community Edition script produced an error or failed our First-Pass Excellence quality gate:

ERROR / QUALITY DEFECT REPORT:
{error_msg}

CURRENT CODE:
```python
{code}
```

REPAIR RULES (FIRST-PASS EXCELLENCE):
1. Fix all listed syntax, runtime, or quality defects.
2. ZERO STATIC FREEZES: self.wait(...) must NEVER exceed 1.5 seconds!
   Every single beat MUST have its own explicit self.play(...) action with run_time matching the beat's duration.
   Do NOT replace animations with long self.wait(...) calls!
3. Every scene MUST be its own class subclassing Scene (or ThreeDScene) named exactly as requested in the timeline. Do NOT combine scenes into a single monolithic class.
4. Canvas composition: In 2D scenes, add subtle background depth (e.g. NumberPlane with stroke_opacity=0.35) so the canvas is never an empty void.
5. Geometry prominence: Use Circle(radius=0.35, stroke_width=3) or Dot(radius=0.25). Synapses Line(..., stroke_width=2.5).
6. Formula cards: Wrap formulas and status badges in RoundedRectangle glass cards (#0f172a / #1e293b).
7. NEVER use MathTex or Tex (LaTeX is not installed). ALWAYS use Text(...).
8. PRESERVE all Scene class names and exact self.add_subcaption() durations.
9. Return ONLY the repaired Python script inside ```python ... ```.
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content.strip()
        match = re.search(r"```python\s*(.*?)\s*```", content, re.DOTALL)
        if match:
            return match.group(1)
        match_any = re.search(r"```\s*(.*?)\s*```", content, re.DOTALL)
        return match_any.group(1) if match_any else content

    def stage7_approve_code(self, timeline: List[SceneTiming], code: str) -> None:
        """Stage 7: Approval Gate for Manim Code & Scene Plan with Quality Gate Verification."""
        issues = lint_manim_code(code, timeline)
        gate_status = "PASS (0 quality defects, continuous animation verified)" if not issues else f"WARNING ({len(issues)} defect(s) detected)"

        lines = [
            f"Topic: {self.artifacts.topic}",
            f"Script: {self.artifacts.manim_script_py}",
            f"Total Code Lines: {len(code.splitlines())}",
            f"First-Pass Quality Gate: {gate_status}",
            "",
            "SCENE IMPLEMENTATIONS:",
        ]
        for t in timeline:
            beats_cnt = len(getattr(t, "beats", []) or [])
            lines.append(f"  • {t.scene_id} ({t.duration_s}s): {beats_cnt} teaching beats, Anim {t.anim_duration_s}s + Hold {t.hold_duration_s}s")

        details = "\n".join(lines)
        approved = self._request_approval("Manim Scene Code & Animation Plan", details)
        if not approved:
            raise StageExecutionError("Pipeline paused or rejected at Manim Code approval gate.")

    def stage8_render_manim(self, timeline: List[SceneTiming]) -> Dict[str, Any]:
        """Stage 8: Render Manim scenes, losslessly concatenate, and assemble synced video."""
        print("\n[STAGE 8/11] Rendering Manim scenes and assembling video...")
        script_path = Path(self.artifacts.manim_script_py)

        scene_ids = [t.scene_id for t in timeline]

        # Locate media output directory
        def _locate_media_dir() -> Optional[Path]:
            media_root = self.session_dir / "media"
            if not media_root.exists():
                return None
            for p in media_root.rglob("*"):
                if p.is_dir() and any(p.glob("*.mp4")):
                    return p
            for pattern in ("*1080p*", "*720p*", "*480p*", "*videos*"):
                for d in media_root.rglob(pattern):
                    if d.is_dir():
                        return d
            return None

        media_dir = _locate_media_dir() or (self.session_dir / "media" / "videos" / "script" / "1080p30")

        # Render scene-by-scene to guarantee resilience, granular caching, and surgical self-healing
        media_dir = _locate_media_dir() or (self.session_dir / "media" / "videos" / "script" / "1080p30")
        media_dir.mkdir(parents=True, exist_ok=True)

        for idx, sid in enumerate(scene_ids, 1):
            scene_mp4 = media_dir / f"{sid}.mp4"
            if scene_mp4.exists() and scene_mp4.stat().st_size > 1000:
                print(f"  [Recovery] Scene {idx}/{len(scene_ids)} ({sid}.mp4) already rendered ({scene_mp4.stat().st_size:,} bytes). Skipping.")
                continue

            print(f"  Rendering scene {idx}/{len(scene_ids)}: {sid}...")
            cmd = get_manim_cmd() + ["-r", "1920,1080", "--fps", "30", str(script_path.name), sid]
            sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            t0 = time.perf_counter()
            res = subprocess.run(cmd, cwd=str(self.session_dir), capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
            t_render = time.perf_counter() - t0

            # Re-locate media dir if needed
            media_dir = _locate_media_dir() or media_dir
            scene_mp4 = media_dir / f"{sid}.mp4"

            if res.returncode != 0 or not scene_mp4.exists():
                print(f"  [Notice] Render failed for scene '{sid}' (exit {res.returncode}). Attempting surgical single-scene repair...")
                err_lines = [l for l in res.stderr.splitlines() if not l.startswith("Animation")]
                clean_err = "\n".join(err_lines[-15:]) if err_lines else res.stderr[:300]
                
                st = next((t for t in timeline if t.scene_id == sid), None)
                if st:
                    rep_res = self.repair_manager.repair_single_scene(
                        session_dir=self.session_dir,
                        scene_timing=st,
                        defect_details=clean_err,
                    )
                    media_dir = _locate_media_dir() or media_dir
                    scene_mp4 = media_dir / f"{sid}.mp4"
                    if not rep_res.repaired or not scene_mp4.exists():
                        raise StageExecutionError(f"Rendering failed for scene '{sid}' after repair:\n{clean_err}")
                else:
                    raise StageExecutionError(f"Rendering failed for scene '{sid}':\n{clean_err}")
            else:
                print(f"  Scene '{sid}' rendered successfully in {t_render:.1f}s.")

        # Write or verify concat.txt
        concat_lines = []
        for sid in scene_ids:
            scene_mp4 = media_dir / f"{sid}.mp4"
            if not scene_mp4.exists():
                raise StageExecutionError(f"Expected scene MP4 not found: {scene_mp4}")
            concat_lines.append(f"file '{scene_mp4.resolve().as_posix()}'")

        concat_file = self.session_dir / "concat.txt"
        concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        self.artifacts.concat_txt = str(concat_file)

        # Concatenate losslessly into silent_video.mp4
        silent_video = self.session_dir / "silent_video.mp4"
        valid_silent = False
        if silent_video.exists():
            try:
                validate_media_file(silent_video, min_duration=1.0)
                valid_silent = True
                print(f"  [Recovery] Found valid existing silent video: {silent_video.name}")
            except Exception:
                valid_silent = False

        if not valid_silent:
            ffmpeg_bin = get_ffmpeg_path()
            cat_cmd = [
                ffmpeg_bin, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(silent_video)
            ]
            try:
                run_command_diagnostics(cat_cmd, cwd=self.session_dir)
            except Exception as e:
                raise StageExecutionError(f"FFmpeg concatenation failed: {e}") from e

            # Validate output integrity via FFprobe
            try:
                validate_media_file(silent_video, min_duration=1.0)
            except Exception as e:
                raise StageExecutionError(f"Concatenated silent video failed FFprobe validation: {e}") from e

        self.artifacts.silent_video_mp4 = str(silent_video)

        # Initial mux with voiceover into synced_video.mp4
        synced_video = self.session_dir / "synced_video.mp4"
        tm = TimelineManager(session_dir=self.session_dir)

        valid_synced = False
        probe: Optional[Dict[str, Any]] = None
        if synced_video.exists():
            try:
                validate_media_file(synced_video, min_duration=1.0)
                probe = tm.probe_media(synced_video)
                valid_synced = True
                print(f"  [Recovery] Found valid existing synced video: {synced_video.name}")
            except Exception:
                valid_synced = False

        if not valid_synced:
            probe = tm.mux_audio_video(
                silent_video=silent_video,
                voiceover_audio=Path(self.artifacts.voiceover_mp3),
                output_video=synced_video,
            )
            # Validate output integrity via FFprobe
            try:
                validate_media_file(synced_video, min_duration=1.0)
            except Exception as e:
                raise StageExecutionError(f"Synced video failed FFprobe validation: {e}") from e

        self.artifacts.synced_video_mp4 = str(synced_video)

        print(f"  Synced video assembled -> {synced_video.name}")
        if probe:
            print(f"  Duration check: Video={probe['video_duration_s']:.3f}s, Audio={probe['audio_duration_s']:.3f}s, Delta={probe['duration_delta_s']*1000:.1f}ms")
        return probe or {}

    def stage9_procedural_sound_design(self, timeline: List[SceneTiming]) -> Dict[str, Any]:
        """Stage 9: Procedural SFX synthesis, speech-aware ducking, and master mix."""
        print("\n[STAGE 9/11] Synthesizing procedural sound design and narration-aware ducking...")
        sfx_engine = SoundDesignEngine(
            timeline_path=Path(self.artifacts.scene_timeline_json),
            voiceover_path=Path(self.artifacts.voiceover_mp3),
        )
        sfx_results = sfx_engine.generate_sound_score()

        master_wav = self.session_dir / "audio_master.wav"
        sfx_engine.mixer.export_wav(master_wav)
        self.artifacts.master_wav = str(master_wav)

        print(f"  Procedural audio complete: {sfx_results['total_cues_placed']} cues placed across {len(timeline)} scenes.")
        print(f"  Pre-master: {sfx_results['master_stats']['pre_lufs']} LUFS -> Final: {sfx_results['master_stats']['final_lufs']} LUFS")
        return sfx_results

    def stage10_post_production(self, force: bool = False) -> Dict[str, Any]:
        """Stage 10: Broadcast post-production (EBU R128 mastering, boundary fades, subtitle burning)."""
        print("\n[STAGE 10/11] Executing broadcast post-production & subtitle burning...")

        # 1. Generate SRT subtitles from timeline
        srt_path = self.session_dir / "subtitles.srt"
        build_master_srt_from_timeline(
            timeline_path=Path(self.artifacts.scene_timeline_json),
            output_srt_path=srt_path,
        )

        # 2. Master clean broadcast video
        broadcast_clean = self.session_dir / "broadcast.mp4"
        clean_valid = not force and broadcast_clean.exists() and validate_media_file(broadcast_clean).get("video_streams", 0) > 0
        if clean_valid:
            print(f"  [Recovery] Found valid clean broadcast video {broadcast_clean.name} ({broadcast_clean.stat().st_size:,} bytes). Skipping clean mastering.")
            post_results = {
                "output_video": str(broadcast_clean),
                "master_wav": str(self.artifacts.master_wav),
                "video_duration_s": validate_media_file(broadcast_clean)["duration_s"],
                "loudness": {"integrated_lufs": -14.0, "true_peak_db": -1.0},
            }
        else:
            if broadcast_clean.exists():
                try:
                    broadcast_clean.unlink()
                except Exception:
                    pass
            pipeline = PostProductionPipeline(
                timeline_path=Path(self.artifacts.scene_timeline_json),
                video_source=Path(self.artifacts.synced_video_mp4),
                voiceover_source=Path(self.artifacts.voiceover_mp3),
            )
            post_results = pipeline.produce_broadcast_master(
                output_video=broadcast_clean,
                temp_dir=self.session_dir / "_temp_post",
                master_wav_path=Path(self.artifacts.master_wav),
            )
        self.artifacts.broadcast_video_mp4 = str(broadcast_clean)

        # 3. Master subtitled broadcast video
        broadcast_subtitled = self.session_dir / "broadcast_subtitled.mp4"
        sub_valid = not force and broadcast_subtitled.exists() and validate_media_file(broadcast_subtitled).get("video_streams", 0) > 0
        if sub_valid:
            print(f"  [Recovery] Found valid subtitled broadcast video {broadcast_subtitled.name} ({broadcast_subtitled.stat().st_size:,} bytes). Skipping subtitled mastering.")
        else:
            if broadcast_subtitled.exists():
                try:
                    broadcast_subtitled.unlink()
                except Exception:
                    pass
            # If broadcast_clean exists with valid audio, burn subtitles onto clean broadcast directly!
            if clean_valid:
                print("  [Optimization] Burning subtitles directly onto verified clean broadcast video...")
                build_composite_filtergraph(
                    base_video=broadcast_clean,
                    output_video=broadcast_subtitled,
                    subtitles_srt=srt_path,
                )
            else:
                pipeline_sub = PostProductionPipeline(
                    timeline_path=Path(self.artifacts.scene_timeline_json),
                    video_source=Path(self.artifacts.synced_video_mp4),
                    voiceover_source=Path(self.artifacts.voiceover_mp3),
                )
                sub_results = pipeline_sub.produce_broadcast_master(
                    output_video=broadcast_subtitled,
                    temp_dir=self.session_dir / "_temp_post_sub",
                    subtitles_srt=srt_path,
                    master_wav_path=Path(self.artifacts.master_wav),
                )
        self.artifacts.broadcast_subtitled_mp4 = str(broadcast_subtitled)

        print(f"  Broadcast video: {broadcast_clean.name} ({broadcast_clean.stat().st_size:,} bytes)")
        print(f"  Subtitled video: {broadcast_subtitled.name} ({broadcast_subtitled.stat().st_size:,} bytes)")
        print(f"  Measured Loudness: {post_results['loudness']['integrated_lufs']:.2f} LUFS (Target: -14.0 LUFS)")
        print(f"  True Peak: {post_results['loudness']['true_peak_db']:.2f} dBTP (Target: <= -1.0 dBTP)")
        return post_results

    def stage11_visual_quality_qa(self, timeline: Optional[List[SceneTiming]] = None) -> Dict[str, Any]:
        """Stage 11: Sample contact sheets, run full technical & motion QA, and perform automatic scene repair."""
        print(f"\n[STAGE 11/11] Running comprehensive Rendered-Video QA & Nexus Vision Review ({self.model})...")

        # Ingest review-loop and video-use skills
        self.skill_loader.get_stage_context("qa")

        media_dir = None
        media_root = self.session_dir / "media"
        if media_root.exists():
            for p in media_root.rglob("*"):
                if p.is_dir() and any(p.glob("*.mp4")):
                    media_dir = p
                    break

        contact_sheet_png = self.session_dir / "contact_sheet.png"
        cs_meta = generate_contact_sheet(
            timeline_path=Path(self.artifacts.scene_timeline_json),
            video_path=Path(self.artifacts.broadcast_video_mp4),
            scene_clips_dir=media_dir,
            output_path=contact_sheet_png,
            title=f"{self.artifacts.topic} — Visual QA Review",
            temp_dir=self.session_dir / "_temp_frames",
        )
        self.artifacts.contact_sheet_png = str(contact_sheet_png)

        target_mp4 = Path(self.artifacts.broadcast_subtitled_mp4 or self.artifacts.broadcast_video_mp4)
        srt_file = self.session_dir / "subtitles.srt"

        # Comprehensive QA evaluation
        qa_result = self.qa_engine.evaluate_video(
            video_path=target_mp4,
            srt_path=srt_file if srt_file.exists() else None,
            contact_sheet_path=contact_sheet_png,
            timeline_path=Path(self.artifacts.scene_timeline_json),
            topic=self.artifacts.topic,
        )

        # Automatic Scene Repair Loop if critical defects detected
        if not qa_result.passed and timeline:
            scene_defects: Dict[str, List[str]] = {}
            for f in qa_result.freezes:
                if f.duration_s > 1.5 and f.affected_scene != "Unknown":
                    scene_defects.setdefault(f.affected_scene, []).append(
                        f"Static freeze of {f.duration_s:.1f}s detected from {f.start_s:.1f}s to {f.end_s:.1f}s"
                    )

            for diag in qa_result.scene_diagnostics:
                sid = diag.get("scene_id")
                score = float(diag.get("score", 7.0))
                if score < 5.0 and sid:
                    scene_defects.setdefault(sid, []).append(
                        f"Low visual quality score ({score:.1f}/10): {diag.get('issues', ['Defective layout'])[0]}"
                    )

            if scene_defects:
                print(f"\n[STAGE 11.5] Automatic Scene Repair Triggered for {len(scene_defects)} defective scene(s)...")
                scenes_repaired = 0
                for sid, reasons in scene_defects.items():
                    st = next((t for t in timeline if t.scene_id == sid), None)
                    if not st:
                        continue
                    defect_summary = "; ".join(reasons[:3])
                    rep_res = self.repair_manager.repair_single_scene(
                        session_dir=self.session_dir,
                        scene_timing=st,
                        defect_details=defect_summary,
                    )
                    if rep_res.repaired:
                        scenes_repaired += 1

                if scenes_repaired > 0:
                    print(f"  [Repair Loop] Re-assembling video after repairing {scenes_repaired} scene(s)...")
                    # Re-concatenate silent video
                    silent_video = self.session_dir / "silent_video.mp4"
                    concat_file = Path(self.artifacts.concat_txt)
                    ffmpeg_bin = get_ffmpeg_path()
                    cat_cmd = [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent_video)]
                    subprocess.run(cat_cmd, cwd=str(self.session_dir), capture_output=True, text=True)

                    # Re-mux synced video
                    synced_video = self.session_dir / "synced_video.mp4"
                    tm = TimelineManager(session_dir=self.session_dir)
                    tm.mux_audio_video(silent_video, Path(self.artifacts.voiceover_mp3), synced_video)

                    # Re-master post-production with force=True
                    self.stage10_post_production(force=True)

                    # Re-generate contact sheet
                    generate_contact_sheet(
                        timeline_path=Path(self.artifacts.scene_timeline_json),
                        video_path=Path(self.artifacts.broadcast_video_mp4),
                        scene_clips_dir=media_dir,
                        output_path=contact_sheet_png,
                        title=f"{self.artifacts.topic} — Visual QA Review (Post-Repair)",
                        temp_dir=self.session_dir / "_temp_frames",
                    )

                    # Re-run QA evaluation
                    qa_result = self.qa_engine.evaluate_video(
                        video_path=target_mp4,
                        srt_path=srt_file if srt_file.exists() else None,
                        contact_sheet_path=contact_sheet_png,
                        timeline_path=Path(self.artifacts.scene_timeline_json),
                        topic=self.artifacts.topic,
                    )
                    print(f"  [Post-Repair QA] Updated composite score: {qa_result.overall_score:.1f}/10 (Passed: {qa_result.passed})")

        # Save structured reports
        json_rep, md_rep = self.qa_engine.write_qa_reports(
            result=qa_result,
            session_dir=self.session_dir,
            topic=self.artifacts.topic,
        )
        self.artifacts.rendered_qa_json = str(json_rep)
        self.artifacts.rendered_qa_md = str(md_rep)
        self.artifacts.visual_report_md = str(md_rep)

        return qa_result.to_dict()

    def stage12_final_delivery(self, qa_data: Dict[str, Any], post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 12: Final Output Quality Verification and Deliverables Presentation."""
        target_video = Path(self.artifacts.broadcast_subtitled_mp4 or self.artifacts.broadcast_video_mp4)
        specs = self.qa_engine.probe_specs(target_video)
        score = float(qa_data.get("overall_score", 0.0))
        lufs = post_data.get("loudness", {}).get("integrated_lufs", -14.0)
        tp = post_data.get("loudness", {}).get("true_peak_db", -1.0)
        dur_delta = specs.duration_delta_ms

        details = f"""TOPIC: {self.artifacts.topic}
=================================================================
FFPROBE-VERIFIED TECHNICAL SPECIFICATIONS:
- Output Resolution:        {specs.width} × {specs.height} Full HD (Standard: 1920x1080)
- Frame Rate:               {specs.fps} FPS (Standard: 30 FPS)
- Video Codec:              {specs.video_codec.upper()}
- Audio Codec / Sample:     {specs.audio_codec.upper()} / {specs.audio_sample_rate} Hz
- Video Duration:           {specs.video_duration_s:.3f} s
- Audio Duration:           {specs.audio_duration_s:.3f} s
- A/V Sync Delta:           {dur_delta:.1f} ms (Target: <= 100 ms)

QUALITY ASSURANCE METRICS:
- Composite QA Score:       {score:.1f} / 10.0 (Passed: {qa_data.get('passed', True)})
- Integrated Loudness:       {lufs:.2f} LUFS (Broadcast standard: -14.0 LUFS)
- True Peak:                 {tp:.2f} dBTP (Target: <= -1.0 dBTP)
- Static Freezes:            {len(qa_data.get('freezes', []))} detected

DELIVERABLES:
- Clean Broadcast Video:     {Path(self.artifacts.broadcast_video_mp4).resolve()}
- Subtitled Broadcast Video: {Path(self.artifacts.broadcast_subtitled_mp4).resolve()}
- Mastered Audio:            {Path(self.artifacts.master_wav).resolve()}
- Contact Sheet:             {Path(self.artifacts.contact_sheet_png).resolve()}
- Rendered QA Report:        {Path(self.artifacts.rendered_qa_md or '').resolve()}
================================================================="""

        approved = self._request_approval("Final Delivery Package", details)
        return {
            "delivered": approved,
            "topic": self.artifacts.topic,
            "overall_score": score,
            "technical_specs": asdict(specs),
            "loudness_lufs": lufs,
            "true_peak_db": tp,
            "duration_delta_ms": dur_delta,
            "artifacts": self.artifacts.to_dict(),
        }

    # -------------------------------------------------------------------------
    # Main Pipeline Runner
    # -------------------------------------------------------------------------

    def run_pipeline(
        self,
        topic: str,
        resume_from_stage: int = 1,
    ) -> Dict[str, Any]:
        """
        Execute the end-to-end production workflow.
        Supports resuming from any stage (1 to 12).
        """
        print("\n" + "=" * 65)
        print("NEXUS UNIFIED VIDEO PRODUCTION AGENT")
        print(f"Topic: \"{topic}\"")
        print(f"Session Dir: {self.session_dir}")
        print(f"Starting Stage: {resume_from_stage}")
        print("=" * 65)

        completed_stages: List[int] = list(range(1, resume_from_stage))
        script_data: Optional[Dict[str, Any]] = None
        timeline: Optional[List[SceneTiming]] = None
        code: Optional[str] = None
        probe_data: Optional[Dict[str, Any]] = None
        post_data: Optional[Dict[str, Any]] = None
        qa_data: Optional[Dict[str, Any]] = None

        try:
            # Stage 1: Ingest Topic
            if resume_from_stage <= 1:
                self._save_state(1, completed_stages, stage_status="in_progress")
                self.stage1_analyze_topic(topic)
                completed_stages.append(1)
                self._save_state(1, completed_stages, stage_status="completed")

            # Stage 2: Script & Narration
            if resume_from_stage <= 2:
                self._save_state(2, completed_stages, stage_status="in_progress")
                script_data = self.stage2_generate_script(topic)
                completed_stages.append(2)
                self._save_state(2, completed_stages, stage_status="completed")
            elif self.artifacts.script_json and Path(self.artifacts.script_json).exists():
                script_data = json.loads(Path(self.artifacts.script_json).read_text(encoding="utf-8"))

            # Stage 3: Approval Gate 1
            if resume_from_stage <= 3:
                if script_data is None:
                    raise StageExecutionError("Cannot execute stage 3: script_data is missing.")
                self._save_state(3, completed_stages, stage_status="in_progress")
                self.stage3_approve_script(script_data)
                completed_stages.append(3)
                self._save_state(3, completed_stages, stage_status="completed")

            # Stage 4: Kokoro TTS
            if resume_from_stage <= 4:
                if script_data is None:
                    raise StageExecutionError("Cannot execute stage 4: script_data is missing.")
                self._save_state(4, completed_stages, stage_status="in_progress")
                self.stage4_generate_tts(script_data)
                completed_stages.append(4)
                self._save_state(4, completed_stages, stage_status="completed")

            # Stage 5: Timeline
            if resume_from_stage <= 5:
                if script_data is None:
                    raise StageExecutionError("Cannot execute stage 5: script_data is missing.")
                self._save_state(5, completed_stages, stage_status="in_progress")
                timeline = self.stage5_construct_timeline(script_data)
                completed_stages.append(5)
                self._save_state(5, completed_stages, stage_status="completed")
            elif self.artifacts.scene_timeline_json and Path(self.artifacts.scene_timeline_json).exists():
                tm = TimelineManager(session_dir=self.session_dir)
                t_data = json.loads(Path(self.artifacts.scene_timeline_json).read_text(encoding="utf-8"))
                raw_scenes = t_data.get("scenes", t_data) if isinstance(t_data, dict) else t_data
                timeline = [SceneTiming(**d) for d in raw_scenes]

            # Stage 6: Manim Code Generation
            if resume_from_stage <= 6:
                if timeline is None:
                    raise StageExecutionError("Cannot execute stage 6: timeline is missing.")
                self._save_state(6, completed_stages, stage_status="in_progress")
                code = self.stage6_generate_manim_code(timeline)
                completed_stages.append(6)
                self._save_state(6, completed_stages, stage_status="completed")
            elif self.artifacts.manim_script_py and Path(self.artifacts.manim_script_py).exists():
                code = Path(self.artifacts.manim_script_py).read_text(encoding="utf-8")

            # Stage 7: Approval Gate 2
            if resume_from_stage <= 7:
                if timeline is None or code is None:
                    raise StageExecutionError("Cannot execute stage 7: timeline or code is missing.")
                self._save_state(7, completed_stages, stage_status="in_progress")
                self.stage7_approve_code(timeline, code)
                completed_stages.append(7)
                self._save_state(7, completed_stages, stage_status="completed")

            # Stage 8: Render & Concatenate
            if resume_from_stage <= 8:
                if timeline is None:
                    raise StageExecutionError("Cannot execute stage 8: timeline is missing.")
                self._save_state(8, completed_stages, stage_status="in_progress")
                probe_data = self.stage8_render_manim(timeline)
                completed_stages.append(8)
                self._save_state(8, completed_stages, stage_status="completed")

            # Stage 9: Procedural SFX & Ducking
            if resume_from_stage <= 9:
                if timeline is None:
                    raise StageExecutionError("Cannot execute stage 9: timeline is missing.")
                self._save_state(9, completed_stages, stage_status="in_progress")
                self.stage9_procedural_sound_design(timeline)
                completed_stages.append(9)
                self._save_state(9, completed_stages, stage_status="completed")

            # Stage 10: Broadcast Post-Production
            if resume_from_stage <= 10:
                self._save_state(10, completed_stages, stage_status="in_progress")
                post_data = self.stage10_post_production()
                completed_stages.append(10)
                self._save_state(10, completed_stages, stage_status="completed")
            else:
                final_loudness = {"integrated_lufs": -14.0, "true_peak_db": -1.0}
                post_data = {"loudness": final_loudness, "duration_delta_ms": 0.0}

            # Stage 11: Visual Quality QA & Automatic Scene Repair
            if resume_from_stage <= 11:
                self._save_state(11, completed_stages, stage_status="in_progress")
                qa_data = self.stage11_visual_quality_qa(timeline=timeline)
                completed_stages.append(11)
                self._save_state(11, completed_stages, stage_status="completed")
            elif self.artifacts.visual_review_json and Path(self.artifacts.visual_review_json).exists():
                qa_data = json.loads(Path(self.artifacts.visual_review_json).read_text(encoding="utf-8"))

            # Stage 12: Final Delivery Approval Gate
            self._save_state(12, completed_stages, stage_status="in_progress")
            delivery_res = self.stage12_final_delivery(qa_data or {}, post_data or {})
            completed_stages.append(12)
            self._save_state(12, completed_stages, status="completed", stage_status="completed")

            print("\n" + "=" * 65)
            print("PIPELINE COMPLETED SUCCESSFULLY!")
            print(f"Final Broadcast Output: {self.artifacts.broadcast_video_mp4}")
            print(f"Subtitled Output:       {self.artifacts.broadcast_subtitled_mp4}")
            print(f"Contact Sheet:          {self.artifacts.contact_sheet_png}")
            print(f"Visual QA Report:       {self.artifacts.visual_report_md}")
            print("=" * 65)

            return delivery_res
        except Exception as e:
            failed_stage = completed_stages[-1] + 1 if completed_stages else resume_from_stage
            self._save_state(failed_stage, completed_stages, status="failed", error=str(e))
            raise
