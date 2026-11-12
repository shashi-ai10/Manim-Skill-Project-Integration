"""End-to-End Acceptance Test for Claude MCP Video Production Integration.

Executes the exact conversational user request:
'Create a 2-minute educational video explaining Convolutional Neural Networks for beginners.
Include detailed animations, narration, sound effects and subtitles.'
"""

import time
import json
from pathlib import Path
from nexus_agent.mcp_server import (
    create_educational_video,
    get_video_status,
    get_video_outputs
)


def run_conversational_acceptance_test():
    print("=" * 70)
    print("CLAUDE MCP INTEGRATION: CONVERSATIONAL ACCEPTANCE TEST")
    print("User Prompt: 'Create a 2-minute educational video explaining")
    print("Convolutional Neural Networks for beginners. Include detailed")
    print("animations, narration, sound effects and subtitles.'")
    print("=" * 70)

    # 1. Claude calls create_educational_video
    print("\n[Step 1] Claude invokes tool: create_educational_video(...)")
    start_res = create_educational_video(
        topic="Convolutional Neural Networks — Explain input image representation, convolution filters and kernels, feature maps, pooling layers, and final classification.",
        duration="2 minutes",
        audience="beginners",
        visual_style="clean dark-mode modern",
        narration_style="clear, engaging educational pacing",
        subtitles=True,
        scenes_count=5,
        wait_for_completion=False,
    )
    print(f"  Response: {json.dumps(start_res, indent=2)}")
    
    session_id = start_res.get("session_id")
    assert session_id, "Failed to obtain session_id from create_educational_video"
    print(f"\n  Active Session ID: {session_id}")

    # 2. Claude polls get_video_status
    print("\n[Step 2] Claude monitors production via get_video_status(...)...")
    last_stage = None
    max_wait_seconds = 360  # 6 minutes timeout
    start_time = time.time()
    completed = False

    while time.time() - start_time < max_wait_seconds:
        status_res = get_video_status(session_id)
        current_stage = status_res.get("current_stage")
        stage_desc = status_res.get("stage_description")
        status = status_res.get("status")
        pct = status_res.get("progress_percent")

        if current_stage != last_stage:
            elapsed = int(time.time() - start_time)
            print(f"  [{elapsed:3d}s] Progress: {pct:2d}% | {stage_desc} (Status: {status})")
            last_stage = current_stage

        if status == "completed":
            print(f"\n  [SUCCESS] Job completed in {int(time.time() - start_time)} seconds!")
            completed = True
            break
        elif status == "failed":
            print(f"\n  [ERROR] Job failed: {status_res}")
            recent_logs = status_res.get("recent_logs", [])
            print("  Recent Logs:")
            for l in recent_logs:
                print(f"    {l}")
            raise RuntimeError(f"Video production failed for session {session_id}")

        time.sleep(10)

    if not completed:
        raise TimeoutError(f"Production timed out after {max_wait_seconds} seconds.")

    # 3. Claude calls get_video_outputs
    print("\n[Step 3] Claude retrieves deliverables via get_video_outputs(...)...")
    out_res = get_video_outputs(session_id)
    print(f"  Outputs Ready: {out_res.get('ready')}")
    files = out_res.get("files", {})
    metrics = out_res.get("metrics", {})

    print("\n  Verified Final Deliverables:")
    for k, f_info in files.items():
        print(f"    • {k:25s}: {f_info['filename']} ({f_info['size_bytes']:,} bytes)")

    print("\n  Production Metrics:")
    for k, v in metrics.items():
        print(f"    • {k:25s}: {v}")

    # Assertions
    assert "subtitled_video_mp4" in files, "Master subtitled video missing!"
    assert "broadcast_video_mp4" in files or "clean_video_mp4" in files, "Clean video missing!"
    assert "contact_sheet_png" in files, "Contact sheet missing!"
    assert "visual_qa_report_md" in files, "Visual QA report missing!"
    
    subtitled_path = Path(files["subtitled_video_mp4"]["path"])
    assert subtitled_path.exists(), f"File does not exist: {subtitled_path}"
    assert subtitled_path.stat().st_size > 500_000, "Subtitled video file size too small!"

    print("\n" + "=" * 70)
    print("CONVERSATIONAL ACCEPTANCE TEST PASSED SUCCESSFULLY!")
    print(f"Master Video File: {subtitled_path}")
    print("=" * 70)
    return session_id, out_res


if __name__ == "__main__":
    run_conversational_acceptance_test()

