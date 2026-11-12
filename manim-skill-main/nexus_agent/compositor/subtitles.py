"""Master SRT subtitle generation and formatting for educational videos."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def srt_timestamp(seconds: float) -> str:
    """Format seconds float into SRT timestamp format: HH:MM:SS,mmm."""
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_master_srt_from_timeline(
    timeline_path: Path | str,
    output_srt_path: Path | str = "neural_network_master.srt",
) -> Path:
    """
    Construct a master SRT subtitle file aligned to the output timeline
    using segment start/end timestamps and teaching beats from scene_timeline.json.
    
    When teaching beats are present, subtitle cues are synchronized at the 2-4 second
    phrase level for maximum educational clarity and reading comfort.
    """
    timeline_path = Path(timeline_path)
    output_srt_path = Path(output_srt_path)
    output_srt_path.parent.mkdir(parents=True, exist_ok=True)

    with open(timeline_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
        timeline: List[Dict[str, Any]] = (
            raw_data.get("scenes", raw_data)
            if isinstance(raw_data, dict)
            else raw_data
        )

    srt_lines: List[str] = []
    cue_idx = 1
    for scene in timeline:
        t_start = float(scene.get("audio_start_s", 0.0))
        t_end = float(scene.get("audio_end_s", t_start + scene.get("duration_s", 4.0)))
        beats = scene.get("beats", [])

        if beats and isinstance(beats, list) and len(beats) > 1:
            for b in beats:
                b_start = t_start + float(b.get("start_s", 0.0))
                b_dur = float(b.get("duration_s", 2.0))
                b_end = min(t_end, b_start + b_dur)
                b_text = b.get("narration_phrase", "").strip()
                if b_text and b_end > b_start:
                    srt_lines.append(str(cue_idx))
                    srt_lines.append(f"{srt_timestamp(b_start)} --> {srt_timestamp(b_end)}")
                    srt_lines.append(b_text)
                    srt_lines.append("")
                    cue_idx += 1
        else:
            text = scene.get("narration_text", "").strip()
            srt_lines.append(str(cue_idx))
            srt_lines.append(f"{srt_timestamp(t_start)} --> {srt_timestamp(t_end)}")
            srt_lines.append(text)
            srt_lines.append("")
            cue_idx += 1

    output_srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    print(f"Master SRT built successfully: {output_srt_path} ({cue_idx - 1} cues)")
    return output_srt_path

