"""Topic-Aware Visual Director and Storyboard Choreographer.

Replaces rigid, cookie-cutter five-zone layouts with dynamic, topic-tailored
visual storytelling inspired by 3Blue1Brown, Kurzgesagt, and modern technical explainers.
Generates a detailed, validated visual plan for every scene before code synthesis.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import NexusConfig, load_nexus_config
from .skill_loader import SkillLoader
from .sync import SceneTiming

logger = logging.getLogger("nexus_agent.visual_director")


@dataclass
class SceneVisualPlan:
    scene_id: str
    learning_objective: str
    core_visual_metaphor: str
    main_visual_composition: str
    objects_and_relationships: List[Dict[str, str]]
    animation_sequence: List[Dict[str, Any]]
    camera_movement: str
    transitions: str
    colour_palette: Dict[str, str]
    typography_and_labels: List[str]
    narration_synchronization: List[Dict[str, Any]]
    approximate_screen_occupancy: str
    expected_visual_state_at_end: str
    is_3d: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_visual_plan(plan_data: Dict[str, Any], timeline: List[SceneTiming]) -> List[str]:
    """Validate completeness, depth, and quality of generated visual direction."""
    issues: List[str] = []
    scenes = plan_data.get("scenes", [])
    if not scenes:
        return ["Visual plan contains no scenes."]

    expected_ids = {t.scene_id for t in timeline}
    actual_ids = {s.get("scene_id") for s in scenes}

    missing = expected_ids - actual_ids
    if missing:
        issues.append(f"Visual plan is missing required scene(s): {', '.join(missing)}")

    for idx, s in enumerate(scenes, 1):
        sid = s.get("scene_id", f"Scene_{idx}")
        # Validate required fields
        required_fields = [
            "learning_objective",
            "core_visual_metaphor",
            "main_visual_composition",
            "animation_sequence",
            "objects_and_relationships",
            "transitions",
            "typography_and_labels",
            "approximate_screen_occupancy",
            "expected_visual_state_at_end",
        ]
        for f in required_fields:
            val = s.get(f)
            if not val:
                issues.append(f"Scene '{sid}' missing required visual plan field: '{f}'")

        # Validate visual sequence has multiple beats matching timeline
        seq = s.get("animation_sequence", [])
        if len(seq) < 2:
            issues.append(f"Scene '{sid}' has only {len(seq)} animation sequence steps (minimum 2 required).")

        # Check screen occupancy
        occ = str(s.get("approximate_screen_occupancy", ""))
        m_pct = re.search(r"(\d+)%", occ)
        if m_pct and int(m_pct.group(1)) < 50:
            issues.append(f"Scene '{sid}' specifies low screen occupancy ({occ}). Target is 60%-85%.")

    return issues


class TopicAwareVisualDirector:
    """Topic-Aware Visual Director planning visual architecture for Manim animations."""

    def __init__(
        self,
        config: Optional[NexusConfig] = None,
        skill_loader: Optional[SkillLoader] = None,
    ):
        self.config = config or load_nexus_config()
        self.client = OpenAI(
            base_url=self.config.api_url,
            api_key=self.config.api_key or "EMPTY",
        )
        self.model = self.config.model or "gemini-3.1-flash-lite"
        self.skill_loader = skill_loader or SkillLoader()

    def generate_visual_plan(
        self,
        topic: str,
        timeline: List[SceneTiming],
        script_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate a complete, topic-aware visual direction blueprint."""
        print(f"\n[VisualDirector] Generating topic-aware visual plan for: \"{topic}\"...")

        # Ingest original motion design and visual storytelling skills
        skill_context = self.skill_loader.get_stage_context("visual_planning")

        timeline_summary = []
        for t in timeline:
            timeline_summary.append({
                "scene_id": t.scene_id,
                "narration_text": t.narration_text,
                "duration_s": round(t.duration_s, 3),
                "is_3d": getattr(t, "is_3d", False),
                "visual_elements": t.visual_elements,
                "beats": [
                    {
                        "beat_index": b.beat_index,
                        "narration_phrase": b.narration_phrase,
                        "duration_s": round(b.duration_s, 3),
                        "animation_action": b.animation_action,
                    }
                    for b in getattr(t, "beats", [])
                ],
            })

        system_msg = (
            "You are an Executive Visual Director and Master Motion Graphics Lead for premier educational animations "
            "(in the style of 3Blue1Brown, Kurzgesagt, and Apple technical keynotes).\n"
            "You craft visual-first, professional-grade explanations tailored specifically to the conceptual essence of the subject.\n"
            "YOU MUST AVOID:\n"
            "- Rigid, repetitive template boxes or 5-zone grids.\n"
            "- Tiny diagrams surrounded by vast empty black voids (aim for 70%+ screen occupancy).\n"
            "- Static scenes with little to no motion (every 2-4 seconds MUST have a meaningful visual development).\n"
            "- Repeating the same animation patterns for different concepts.\n"
            "YOU MUST IMPLEMENT:\n"
            "- Dynamic layouts: matrix attention heatmaps, vector embeddings, bipartite graph streams, neural signal flows, and progressive stacks.\n"
            "- Purposeful, continuous motion (reveal, morph, unroll, connect, flow).\n"
            "- Deep visual hierarchy (focal vs. supporting architecture).\n"
            "- Meaningful visual metaphors that teach the concept through action.\n"
            "- Smooth, professional-grade transitions (morphing, not abrupt popping)."
        )

        user_prompt = f"""Design a topic-aware Visual Direction Blueprint for an educational video explaining:
"{topic}"

INGESTED MOTION DESIGN & VISUAL TASTE RULES:
{skill_context}

SCENE TIMELINE & BEATS:
{json.dumps(timeline_summary, indent=2)}

REQUIREMENTS FOR EACH SCENE:
1. "learning_objective": What specific intuitive breakthrough this scene accomplishes.
2. "core_visual_metaphor": Concrete visual metaphor tailored to "{topic}" (e.g., for attention: soft glowing connection rays linking tokens with weights; for embeddings: high-dimensional vector coordinates; for feedforward: parallel linear transformations). NEVER use generic placeholders.
3. "main_visual_composition": Describe the natural spatial layout suited to this concept (e.g. dual-column matrix comparison, layered vertical transformer stack, radial projection, continuous vector flow). Do NOT force a rigid 5-zone box layout!
4. "objects_and_relationships": List 4-8 concrete visual objects with relative positions, styling, and structural connections (e.g. token capsules, weight matrices, query/key vector arrows, softmax probability gauge).
5. "animation_sequence": Beat-by-beat visual choreography matching each teaching beat's duration. Every beat must have an explicit visual transformation (enter, pulse, morph, unroll, connect, flow, highlight).
6. "camera_movement": Subtle camera movement, framing changes, or 3D angle shifts (if 3D scene).
7. "transitions": How this scene enters from the previous concept and transitions into the next (e.g., morphing vectors into a matrix, zooming into a sub-module, sliding state).
8. "colour_palette": Semantic palette (primary, secondary, accent for focal concept, text, background).
9. "typography_and_labels": Readable typography rules, labels, and mathematical badge Text(...) strings.
10. "narration_synchronization": How animations align with spoken words (the see-then-hear principle: visual change begins slightly before or right on the spoken word).
11. "approximate_screen_occupancy": Target percentage (e.g. "70% coverage with balanced peripheral margins").
12. "expected_visual_state_at_end": The exact visual state at the end of the scene.

Respond strictly with a valid JSON object matching this schema:
{{
  "topic": "{topic}",
  "global_design_system": {{
    "canvas_dimensions": "1920x1080",
    "framerate": 30,
    "theme": "modern dark technical",
    "color_palette": {{
      "bg_color": "#0f172a",
      "primary": "#38bdf8",
      "secondary": "#818cf8",
      "accent": "#f43f5e",
      "success": "#10b981",
      "warning": "#fbbf24",
      "text": "#f8fafc",
      "muted": "#64748b"
    }}
  }},
  "scenes": [
    {{
      "scene_id": "...",
      "is_3d": false,
      "learning_objective": "...",
      "core_visual_metaphor": "...",
      "main_visual_composition": "...",
      "objects_and_relationships": [
        {{"name": "...", "type": "...", "role": "...", "position": "..."}}
      ],
      "animation_sequence": [
        {{"beat_index": 1, "duration_s": 4.5, "action": "...", "easing": "ease_out_cubic"}}
      ],
      "camera_movement": "...",
      "transitions": "...",
      "colour_palette": {{"focal": "#f43f5e", "structural": "#38bdf8"}},
      "typography_and_labels": ["..."],
      "narration_synchronization": [
        {{"phrase": "...", "trigger_time_s": 0.0, "visual_event": "..."}}
      ],
      "approximate_screen_occupancy": "75%",
      "expected_visual_state_at_end": "..."
    }}
  ]
}}
Do NOT include markdown backticks or commentary outside the JSON object.
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
            plan_data = json.loads(content)
        except Exception:
            match = re.search(r"(\{.*\})", content, re.DOTALL)
            if match:
                plan_data = json.loads(match.group(1))
            else:
                raise ValueError(f"Failed to parse Visual Director response as JSON: {content[:300]}")

        # Validate generated plan
        issues = validate_visual_plan(plan_data, timeline)
        if issues:
            print(f"  [VisualDirector] Quality warnings ({len(issues)}): {issues[0]}")
        else:
            print(f"  [VisualDirector] Visual plan verified: {len(plan_data.get('scenes', []))} scenes planned with high visual depth.")

        return plan_data
