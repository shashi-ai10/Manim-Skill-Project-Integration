"""Contact sheet generator for automated visual quality review of Manim scenes.

Inspired by Claude Motion, this module extracts representative settled frames
from rendered Manim scenes and composites them into a labeled 4x2 contact sheet
for visual evaluation by multimodal LLMs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .tools_resolver import get_ffmpeg_path, get_ffprobe_path, run_command_diagnostics


def _run_ffmpeg(cmd: List[str]) -> None:
    run_command_diagnostics(cmd)


def get_duration(video_file: Path | str) -> float:
    try:
        probe = get_ffprobe_path()
        cmd = [probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_file)]
        out = subprocess.check_output(cmd, text=True).strip()
        return float(out)
    except Exception:
        return 1000.0


def extract_frame(
    video_path: Path | str,
    timestamp_s: float,
    output_path: Path | str,
    scale_w: Optional[int] = None,
    scale_h: Optional[int] = None,
) -> Path:
    """Extract a single frame from video at timestamp_s."""
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = get_ffmpeg_path()
    cmd = [
        ffmpeg_bin, "-y",
        "-ss", f"{timestamp_s:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
    ]
    if scale_w and scale_h:
        cmd.extend(["-vf", f"scale={scale_w}:{scale_h}"])
    elif scale_w:
        cmd.extend(["-vf", f"scale={scale_w}:-2"])
    elif scale_h:
        cmd.extend(["-vf", f"scale=-2:{scale_h}"])

    cmd.append(str(output_path))
    _run_ffmpeg(cmd)

    if not output_path.exists() or output_path.stat().st_size == 0:
        # Fallback to early timestamp seeking before -i
        cmd_fallback = [
            ffmpeg_bin, "-y",
            "-ss", "0.500",
            "-i", str(video_path),
            "-frames:v", "1",
        ]
        if scale_w:
            cmd_fallback.extend(["-vf", f"scale={scale_w}:-2"])
        cmd_fallback.append(str(output_path))
        _run_ffmpeg(cmd_fallback)

    if not output_path.exists() or output_path.stat().st_size == 0:
        img = Image.new("RGB", (scale_w or 426, 240), color=(15, 23, 42))
        img.save(str(output_path))

    return output_path


def load_font(size: int = 16) -> ImageFont.ImageFont:
    """Load default or system monospace font."""
    font_candidates = [
        "arial.ttf",
        "consola.ttf",
        "segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
    ]
    for candidate in font_candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_tile_overlay(
    image: Image.Image,
    scene_number: int,
    scene_id: str,
    timestamp_s: float,
    duration_s: float,
    is_3d: bool = False,
    beat_info: Optional[str] = None,
) -> Image.Image:
    """Draw a readable header badge and timestamp footer badge on the tile."""
    tile = image.copy()
    draw = ImageDraw.Draw(tile)

    w, h = tile.size
    font_header = load_font(size=max(12, int(h * 0.065)))
    font_footer = load_font(size=max(10, int(h * 0.055)))

    # Clean display name from SceneX_CamelCase
    clean_name = scene_id
    if "_" in clean_name:
        clean_name = clean_name.split("_", 1)[1]
    # Insert spaces into CamelCase
    clean_name = "".join(f" {c}" if c.isupper() and i > 0 else c for i, c in enumerate(clean_name)).strip()

    threed_tag = " [3D]" if is_3d else ""
    header_text = f"Scene {scene_number}: {clean_name}{threed_tag}"
    footer_text = f"t={timestamp_s:5.2f}s | dur={duration_s:4.2f}s"
    if beat_info:
        footer_text += f" | {beat_info}"

    # Header banner (dark translucent background)
    banner_h = max(24, int(h * 0.11))
    draw.rectangle([(0, 0), (w, banner_h)], fill=(15, 17, 23, 220))
    header_color = (120, 220, 255) if is_3d else (255, 255, 255)
    draw.text((8, 3), header_text, fill=header_color, font=font_header)

    # Footer banner
    footer_h = max(20, int(h * 0.09))
    draw.rectangle([(0, h - footer_h), (w, h)], fill=(10, 12, 16, 210))
    draw.text((8, h - footer_h + 2), footer_text, fill=(160, 200, 255), font=font_footer)

    # Subtle border around tile
    draw.rectangle([(0, 0), (w - 1, h - 1)], outline=(70, 80, 100), width=1)

    return tile


def generate_contact_sheet(
    timeline_path: Path | str,
    video_path: Optional[Path | str] = None,
    scene_clips_dir: Optional[Path | str] = None,
    output_path: Path | str = "neural_network_contact_sheet.png",
    cols: int = 4,
    rows: int = 2,
    tile_width: int = 480,
    padding: int = 12,
    temp_dir: Optional[Path | str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a contact sheet from rendered scenes.

    Extracts settled frames after active animation completes to ensure
    readable hold states are reviewed.
    """
    timeline_path = Path(timeline_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if temp_dir is None:
        temp_dir = output_path.parent / "_temp_frames"
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    with open(timeline_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
        timeline: List[Dict[str, Any]] = (
            raw_data.get("scenes", raw_data)
            if isinstance(raw_data, dict)
            else raw_data
        )

    num_scenes = len(timeline)
    # Dynamically adjust grid if default 4x2 doesn't fit scene count
    if num_scenes <= 4 and (cols == 4 and rows == 2):
        cols = 2
        rows = (num_scenes + 1) // 2
    elif num_scenes in (5, 6) and (cols == 4 and rows == 2):
        cols = 3
        rows = 2
    elif num_scenes > 8 and (cols == 4 and rows == 2):
        cols = 4
        rows = (num_scenes + 3) // 4

    if len(timeline) != cols * rows:
        print(f"Notice: timeline has {len(timeline)} scenes; grid is {cols}x{rows} = {cols*rows}")

    scene_images: List[Tuple[Image.Image, Dict[str, Any]]] = []
    extracted_metadata: List[Dict[str, Any]] = []

    for i, scene in enumerate(timeline, start=1):
        scene_id = scene["scene_id"]
        duration = float(scene.get("duration_s", 4.0))
        anim_duration = float(scene.get("anim_duration_s", duration * 0.4))
        hold_duration = float(scene.get("hold_duration_s", duration - anim_duration))
        beats = scene.get("beats", [])
        is_3d = bool(scene.get("is_3d", False))

        # Sample at representative dynamic or settled state
        if beats and len(beats) > 1:
            # Sample across representative active teaching beats (mid-to-late dynamic progression)
            sample_offset_in_scene = min(duration * 0.65, max(0.5, duration - 0.4))
            beat_info = f"{len(beats)} beats"
        else:
            sample_offset_in_scene = anim_duration + min(1.0, hold_duration * 0.5)
            sample_offset_in_scene = min(sample_offset_in_scene, duration - 0.2)
            beat_info = None

        frame_file = temp_dir / f"frame_{i:02d}_{scene_id}.png"

        # Strategy 1: Extract from individual scene file if available
        extracted = False
        if scene_clips_dir:
            scene_clip = Path(scene_clips_dir) / f"{scene_id}.mp4"
            if scene_clip.exists():
                clip_dur = get_duration(scene_clip)
                t_offset = min(sample_offset_in_scene, max(0.1, clip_dur - 0.4))
                extract_frame(scene_clip, t_offset, frame_file, scale_w=tile_width)
                if frame_file.exists() and frame_file.stat().st_size > 0:
                    extracted = True
                    source_video = str(scene_clip)
                    global_t = float(scene.get("audio_start_s", 0.0)) + t_offset

        # Strategy 2: Extract from full stitched video using audio_start_s
        if not extracted and video_path:
            video_file = Path(video_path)
            if video_file.exists():
                vid_dur = get_duration(video_file)
                audio_start = float(scene.get("audio_start_s", 0.0))
                global_t = min(audio_start + sample_offset_in_scene, max(0.1, vid_dur - 0.5))
                extract_frame(video_file, global_t, frame_file, scale_w=tile_width)
                if frame_file.exists() and frame_file.stat().st_size > 0:
                    extracted = True
                    source_video = str(video_file)

        if not extracted:
            raise FileNotFoundError(f"Could not extract frame for {scene_id}: no valid video source.")

        raw_tile = Image.open(frame_file).convert("RGB")
        labeled_tile = render_tile_overlay(
            raw_tile,
            scene_number=i,
            scene_id=scene_id,
            timestamp_s=global_t,
            duration_s=duration,
            is_3d=is_3d,
            beat_info=beat_info,
        )
        scene_images.append((labeled_tile, scene))

        extracted_metadata.append({
            "scene_number": i,
            "scene_id": scene_id,
            "is_3d": is_3d,
            "beats_count": len(beats),
            "sample_time_s": round(global_t, 3),
            "duration_s": round(duration, 3),
            "frame_path": str(frame_file),
            "visual_elements": scene.get("visual_elements", []),
        })

    # Assemble grid canvas
    tile_w, tile_h = scene_images[0][0].size
    canvas_w = cols * tile_w + (cols + 1) * padding
    header_title_h = 60
    canvas_h = rows * tile_h + (rows + 1) * padding + header_title_h

    canvas = Image.new("RGB", (canvas_w, canvas_h), color=(14, 16, 22))
    draw = ImageDraw.Draw(canvas)

    # Main contact sheet title header
    font_main = load_font(size=22)
    font_sub = load_font(size=14)
    sheet_title = title or "Nexus + Manim Visual Quality Contact Sheet"
    draw.text((padding, 12), sheet_title, fill=(255, 255, 255), font=font_main)
    draw.text(
        (padding, 38),
        f"{len(timeline)} Scenes | Sampling at settled holds | Resolution per tile: {tile_w}x{tile_h}",
        fill=(140, 160, 190),
        font=font_sub,
    )

    # Place tiles in grid
    for idx, (tile, meta) in enumerate(scene_images):
        col = idx % cols
        row = idx // cols
        x = padding + col * (tile_w + padding)
        y = header_title_h + padding + row * (tile_h + padding)
        canvas.paste(tile, (x, y))

    canvas.save(output_path, quality=95)
    print(f"Contact sheet generated successfully: {output_path} ({canvas_w}x{canvas_h})")

    return {
        "contact_sheet_path": str(output_path),
        "dimensions": (canvas_w, canvas_h),
        "cols": cols,
        "rows": rows,
        "scenes": extracted_metadata,
    }


def extract_high_res_frame(
    timeline_path: Path | str,
    scene_id: str,
    output_path: Path | str,
    video_path: Optional[Path | str] = None,
    scene_clips_dir: Optional[Path | str] = None,
    scale_w: int = 1920,
    scale_h: int = 1080,
) -> Path:
    """Extract a high-resolution frame of a single scene for deep inspection."""
    timeline_path = Path(timeline_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(timeline_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
        timeline: List[Dict[str, Any]] = (
            raw_data.get("scenes", raw_data)
            if isinstance(raw_data, dict)
            else raw_data
        )

    target_scene = None
    for s in timeline:
        if s["scene_id"] == scene_id or s.get("segment_index") == scene_id:
            target_scene = s
            break

    if not target_scene:
        raise ValueError(f"Scene '{scene_id}' not found in timeline.")

    duration = float(target_scene.get("duration_s", 4.0))
    anim_duration = float(target_scene.get("anim_duration_s", duration * 0.4))
    hold_duration = float(target_scene.get("hold_duration_s", duration - anim_duration))
    sample_offset = min(anim_duration + min(1.0, hold_duration * 0.5), duration - 0.2)

    if scene_clips_dir:
        clip_path = Path(scene_clips_dir) / f"{scene_id}.mp4"
        if clip_path.exists():
            return extract_frame(clip_path, sample_offset, output_path, scale_w=scale_w, scale_h=scale_h)

    if video_path:
        audio_start = float(target_scene.get("audio_start_s", 0.0))
        return extract_frame(video_path, audio_start + sample_offset, output_path, scale_w=scale_w, scale_h=scale_h)

    raise FileNotFoundError(f"No video source available for high-res extraction of {scene_id}.")

