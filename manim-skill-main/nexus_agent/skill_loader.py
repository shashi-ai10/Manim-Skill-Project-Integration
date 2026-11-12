"""Runtime Skill Discovery and Loader for Nexus Video Production Engine.

Dynamically discovers, parses, and injects original skill instructions
at the appropriate production stages:
- Visual Planning: Motion Design & Manim Video (Visual Storytelling)
- Code Generation: Manim Skill & Manim Video & Nexus References
- Audio Post-Production: Sound Design
- Post-Production & QA: Video Use & Review Loop
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger("nexus_agent.skill_loader")


@dataclass
class LoadedSkill:
    """Represents a parsed SKILL.md with metadata and original instructions."""
    name: str
    path: Path
    description: str = ""
    version: str = "1.0.0"
    raw_content: str = ""
    body: str = ""
    sections: Dict[str, str] = field(default_factory=dict)

    def extract_sections(self, section_names: List[str], max_chars: int = 2500) -> str:
        """Extract specific markdown sections by heading name."""
        matches = []
        for name in section_names:
            norm_name = name.lower().strip()
            for h_title, h_body in self.sections.items():
                if norm_name in h_title.lower():
                    matches.append(f"### {h_title}\n{h_body.strip()}")
                    break

        if matches:
            combined = "\n\n".join(matches)
            return combined[:max_chars] if len(combined) > max_chars else combined
        return self.body[:max_chars]


class SkillLoader:
    """Discovers, validates, and serves skill instructions dynamically at runtime."""

    STAGE_SKILL_MAPPING = {
        "visual_planning": ["motion-design", "manim-video"],
        "visual_direction": ["motion-design", "manim-video"],
        "script_generation": ["manim-video", "motion-design"],
        "manim_generation": ["manim-skill", "manim-video"],
        "audio": ["sound-design"],
        "sound_design": ["sound-design"],
        "post_production": ["video-use", "review-loop"],
        "review_loop": ["review-loop", "video-use"],
        "qa": ["review-loop", "video-use"],
    }

    def __init__(self, skills_root: Optional[Path | str] = None):
        if skills_root is None:
            self.skills_root = Path(__file__).resolve().parent.parent / "skills"
        else:
            self.skills_root = Path(skills_root).resolve()

        self._skills_cache: Dict[str, LoadedSkill] = {}
        self._reference_cache: Dict[str, str] = {}
        self.loaded_log: List[Dict[str, str]] = []
        self._discover_skills()
        self._discover_references()

    def _discover_skills(self) -> None:
        """Scan skills_root for SKILL.md files and parse frontmatter + sections."""
        if not self.skills_root.exists():
            logger.warning(f"Skills directory does not exist: {self.skills_root}")
            return

        for skill_file in self.skills_root.rglob("SKILL.md"):
            try:
                content = skill_file.read_text(encoding="utf-8", errors="replace")
                skill = self._parse_skill_file(skill_file, content)
                self._skills_cache[skill.name] = skill
                # Also index by directory name
                dir_name = skill_file.parent.name
                if dir_name not in self._skills_cache:
                    self._skills_cache[dir_name] = skill
            except Exception as e:
                logger.warning(f"Failed to parse skill at {skill_file}: {e}")

    def _discover_references(self) -> None:
        """Scan references in skills/nexus-video-production/references."""
        ref_dir = self.skills_root / "nexus-video-production" / "references"
        if not ref_dir.exists():
            return

        for ref_file in ref_dir.glob("*.md"):
            try:
                text = ref_file.read_text(encoding="utf-8", errors="replace")
                self._reference_cache[ref_file.stem] = text
            except Exception as e:
                logger.warning(f"Failed to read reference file {ref_file}: {e}")

    @staticmethod
    def _parse_skill_file(path: Path, content: str) -> LoadedSkill:
        """Parse YAML frontmatter and markdown sections from a SKILL.md file."""
        frontmatter = {}
        body = content

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if fm_match:
            fm_text, body = fm_match.group(1), fm_match.group(2)
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip().lower()] = v.strip().strip("\"'")

        name = frontmatter.get("name") or path.parent.name
        description = frontmatter.get("description") or ""
        version = frontmatter.get("version") or "1.0.0"

        # Split into sections by H1/H2/H3
        sections: Dict[str, str] = {}
        cur_heading = "Overview"
        cur_lines: List[str] = []

        for line in body.splitlines():
            h_match = re.match(r"^(#{1,3})\s+(.*)$", line)
            if h_match:
                if cur_lines:
                    sections[cur_heading] = "\n".join(cur_lines)
                    cur_lines = []
                cur_heading = h_match.group(2).strip()
            else:
                cur_lines.append(line)

        if cur_lines:
            sections[cur_heading] = "\n".join(cur_lines)

        return LoadedSkill(
            name=name,
            path=path,
            description=description,
            version=version,
            raw_content=content,
            body=body,
            sections=sections,
        )

    def get_skill(self, name: str) -> Optional[LoadedSkill]:
        """Retrieve a loaded skill by name or folder alias."""
        return self._skills_cache.get(name.lower().strip())

    def get_reference(self, name: str) -> Optional[str]:
        """Retrieve a reference document from nexus-video-production/references."""
        return self._reference_cache.get(name.lower().strip())

    def list_available_skills(self) -> List[Dict[str, str]]:
        """List all discovered skills with metadata."""
        seen: Set[str] = set()
        result = []
        for name, skill in self._skills_cache.items():
            if skill.name in seen:
                continue
            seen.add(skill.name)
            result.append({
                "name": skill.name,
                "path": str(skill.path),
                "description": skill.description[:120] if skill.description else "No description",
                "version": skill.version,
            })
        return result

    def get_stage_context(self, stage_name: str, max_chars_per_skill: int = 1800) -> str:
        """Produce curated, stage-specific original skill instructions.
        
        Logs the exact skills loaded and their file paths for verification.
        """
        stage_key = stage_name.lower().strip()
        skill_names = self.STAGE_SKILL_MAPPING.get(stage_key, [])
        if not skill_names:
            for k in self.STAGE_SKILL_MAPPING:
                if k in stage_key or stage_key in k:
                    skill_names = self.STAGE_SKILL_MAPPING[k]
                    break

        instructions = []
        for name in skill_names:
            skill = self.get_skill(name)
            if not skill:
                logger.warning(f"Skill '{name}' requested for stage '{stage_name}' was not found.")
                continue

            # Log loaded skill path
            log_entry = {
                "stage": stage_name,
                "skill": skill.name,
                "path": str(skill.path),
            }
            self.loaded_log.append(log_entry)
            print(f"  [SkillLoader] Ingesting '{skill.name}' from {skill.path.name} for stage '{stage_name}'")

            # Extract highest-value sections per skill and stage
            if name == "motion-design":
                extracted = skill.extract_sections(
                    ["Timing", "Easing", "Typography", "Continuity", "Texture", "Anti-patterns"],
                    max_chars=max_chars_per_skill,
                )
            elif name == "manim-video":
                extracted = skill.extract_sections(
                    ["Creative Standard", "Creative Direction", "Color Palettes", "Animation Speed"],
                    max_chars=max_chars_per_skill,
                )
            elif name == "manim-skill":
                extracted = skill.extract_sections(
                    ["Contract", "Continuous Animated Storytelling", "Timing"],
                    max_chars=max_chars_per_skill,
                )
            elif name == "sound-design":
                extracted = skill.extract_sections(
                    ["Visual event -> sound", "Levels and space", "Verify"],
                    max_chars=max_chars_per_skill,
                )
            elif name == "video-use":
                extracted = skill.extract_sections(
                    ["Hard Rules", "Principle", "Cut craft"],
                    max_chars=max_chars_per_skill,
                )
            elif name == "review-loop":
                extracted = skill.extract_sections(
                    ["Checklist", "Loop", "Report"],
                    max_chars=max_chars_per_skill,
                )
            else:
                extracted = skill.body[:max_chars_per_skill]

            instructions.append(
                f"--- [SKILL: {skill.name.upper()} ({skill.path.name})] ---\n"
                f"{extracted.strip()}"
            )

        # Attach topic-relevant reference guide snippets for code generation
        if "generation" in stage_key or "code" in stage_key:
            ref_snippet = self._get_relevant_code_references()
            if ref_snippet:
                instructions.append(f"--- [MANIM REFERENCE GUIDES] ---\n{ref_snippet}")

        return "\n\n".join(instructions)

    def _get_relevant_code_references(self, max_chars: int = 1500) -> str:
        """Select core Manim recipes from reference guides."""
        recipes = []
        for ref_key in ("animations", "visual-design", "updaters-and-trackers"):
            content = self.get_reference(ref_key)
            if content:
                # Grab first 400 chars of each
                recipes.append(content[:450].strip())
        return "\n\n".join(recipes)[:max_chars]
