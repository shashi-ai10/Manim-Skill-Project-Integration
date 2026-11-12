"""Lossless video concatenation and PTS-shifted overlay filtergraph compositing.

Adapted from Video Use (MIT License), this module guarantees:
1. Subtitles are applied LAST in the filter chain (Hard Rule 1).
2. Per-segment extract -> lossless -c copy concat (Hard Rule 2).
3. Overlays use setpts=PTS-STARTPTS+T/TB to correctly shift frame 0 (Hard Rule 4).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from nexus_agent.tools_resolver import get_ffmpeg_path, run_command_diagnostics


def concat_video_segments(
    segment_paths: List[Path | str],
    output_path: Path | str,
    temp_dir: Optional[Path | str] = None,
) -> Path:
    """
    Perform lossless video concatenation using FFmpeg concat demuxer (-c copy).
    No re-encoding of video streams.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if temp_dir is None:
        temp_dir = output_path.parent
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    concat_file = temp_dir / "_concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in segment_paths:
            resolved = Path(p).resolve()
            f.write(f"file '{resolved}'\n")

    ffmpeg_bin = get_ffmpeg_path()
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    run_command_diagnostics(cmd)
    concat_file.unlink(missing_ok=True)
    return output_path


def build_composite_filtergraph(
    base_video: Path | str,
    output_video: Path | str,
    overlays: Optional[List[Dict[str, Any]]] = None,
    subtitles_srt: Optional[Path | str] = None,
    subtitle_style: str = (
        "FontName=Arial,FontSize=18,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=90"
    ),
) -> Path:
    """
    Chain overlays on top of base video with PTS-shifting, applying subtitles LAST.

    Overlays schema:
    [
        {"file": "overlay1.mp4", "start_s": 2.5, "duration_s": 3.0, "x": 50, "y": 50}
    ]
    """
    base_video = Path(base_video)
    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)

    overlays = overlays or []
    has_subs = subtitles_srt is not None and Path(subtitles_srt).exists()

    if not overlays and not has_subs:
        # Lossless copy if no compositing is needed
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [ffmpeg_bin, "-y", "-i", str(base_video), "-c", "copy", "-movflags", "+faststart", str(output_video)]
        run_command_diagnostics(cmd)
        return output_video

    inputs: List[str] = ["-i", str(base_video)]
    for ov in overlays:
        inputs.extend(["-i", str(ov["file"])])

    filter_parts: List[str] = []

    # 1. Shift PTS of each overlay so its frame 0 lands at start_s
    for idx, ov in enumerate(overlays, start=1):
        t_start = float(ov["start_s"])
        filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t_start}/TB[ov_{idx}]")

    # 2. Chain overlays sequentially on top of base
    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t_start = float(ov["start_s"])
        dur = float(ov["duration_s"])
        t_end = t_start + dur
        x = ov.get("x", "(W-w)/2")
        y = ov.get("y", "(H-h)/2")
        next_label = f"[v{idx}]"
        filter_parts.append(
            f"{current}[ov_{idx}]overlay=x={x}:y={y}:enable='between(t,{t_start:.3f},{t_end:.3f})'{next_label}"
        )
        current = next_label

    # 3. Subtitles applied strictly LAST (Hard Rule 1 from Video Use)
    if has_subs:
        subs_path = Path(subtitles_srt).resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
        filter_parts.append(
            f"{current}subtitles='{subs_path}':force_style='{subtitle_style}'[outv]"
        )
        out_v = "[outv]"
    else:
        out_v = current

    filter_complex = ";".join(filter_parts)

    ffmpeg_bin = get_ffmpeg_path()
    cmd = [
        ffmpeg_bin, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", out_v,
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_video),
    ]
    run_command_diagnostics(cmd)
    return output_video
