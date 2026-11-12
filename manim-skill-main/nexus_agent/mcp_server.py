"""Nexus Video Production MCP Server for Claude Integration.

Exposes tools allowing Claude to autonomously plan, render, master,
and retrieve broadcast-grade educational videos using the existing
Nexus-powered Manim production engine.
"""

import os
import sys
import json
import re
import time
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Annotated
from pydantic import Field
from mcp.server.mcpserver import MCPServer

# Initialize MCP Server
server = MCPServer("nexus-video-production")

# Workspace root
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = WORKSPACE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# Active process registry: session_id -> subprocess.Popen
_ACTIVE_JOBS: Dict[str, subprocess.Popen] = {}


def _sanitize_slug(text: str) -> str:
    """Generate a clean, filesystem-safe session slug."""
    clean = re.sub(r"[^a-zA-Z0-9_-]", "-", text.lower().strip())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean[:35] if clean else "video-session"


def _parse_duration_seconds(duration_str: Optional[str]) -> Optional[int]:
    """Parse natural language duration (e.g. '2 minutes', '90 seconds', '3m') into seconds."""
    if not duration_str:
        return None
    m_min = re.search(r"(\d+)\s*(?:-|–)?\s*(?:min|minute)s?", duration_str, re.IGNORECASE)
    m_sec = re.search(r"(\d+)\s*(?:-|–)?\s*(?:sec|second)s?", duration_str, re.IGNORECASE)
    if m_min:
        return int(m_min.group(1)) * 60
    if m_sec:
        return int(m_sec.group(1))
    try:
        return int(duration_str)
    except ValueError:
        return None


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Check if process with given PID is currently alive on the host OS."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            res = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            # STILL_ACTIVE = 259
            return bool(res and exit_code.value == 259)
        except Exception:
            return False
    else:
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def _validate_session_path(session_id: str) -> Path:
    """Validate that session_id resolves safely inside SESSIONS_DIR."""
    # Prevent path traversal attacks
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(f"Invalid session_id: path traversal forbidden: {session_id}")
    session_dir = (SESSIONS_DIR / session_id).resolve()
    if not str(session_dir).startswith(str(SESSIONS_DIR.resolve())):
        raise ValueError(f"Access denied: session path outside sessions directory: {session_id}")
    return session_dir


def _list_recent_sessions(limit: int = 5) -> List[str]:
    """Return names of the most recently modified session directories."""
    if not SESSIONS_DIR.exists():
        return []
    dirs = [
        d for d in SESSIONS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return [d.name for d in dirs[:limit]]


def _resolve_session_id(
    session_id: Optional[str] = None,
    id: Optional[str] = None,
    job_id: Optional[str] = None,
    task_id: Optional[str] = None
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve and validate a session identifier from multiple possible argument aliases.
    
    Protects Claude from confusing internal task IDs (e.g. 'kgk308nzk') or OS PIDs
    with Nexus video production session identifiers.
    
    Returns:
        (resolved_session_id, error_dict)
    """
    raw_val = session_id or id or job_id or task_id
    if not raw_val or not str(raw_val).strip():
        recent = _list_recent_sessions(limit=5)
        return None, {
            "error": "Missing required session_id parameter.",
            "hint": "Pass the session_id returned by create_educational_video (format: '<topic-slug>-YYYYMMDD-HHMMSS').",
            "recent_sessions": recent
        }

    raw = str(raw_val).strip()

    # Support special aliases like 'latest', 'current', 'last'
    if raw.lower() in ("latest", "current", "last"):
        all_recent = _list_recent_sessions(limit=10)
        completed_recent = [
            s for s in all_recent
            if (SESSIONS_DIR / s / "broadcast_subtitled.mp4").exists()
            or (SESSIONS_DIR / s / "broadcast.mp4").exists()
        ]
        if completed_recent:
            return completed_recent[0], None
        if all_recent:
            return all_recent[0], None
        return None, {"error": "No sessions found in sessions directory."}

    # Prevent path traversal
    if ".." in raw or "/" in raw or "\\" in raw:
        return None, {"error": f"Invalid session_id: path traversal forbidden: {raw}"}

    session_dir = (SESSIONS_DIR / raw).resolve()
    if not str(session_dir).startswith(str(SESSIONS_DIR.resolve())):
        return None, {"error": f"Access denied: session path outside sessions directory: {raw}"}

    if not session_dir.exists():
        # Check if caller passed an internal Claude task ID (e.g. 'kgk308nzk'), PID (numeric), or task prefix
        is_task_id = bool(re.match(r"^[a-z0-9]{6,12}$", raw, re.IGNORECASE))
        is_pid = raw.isdigit()
        is_task_prefixed = raw.lower().startswith("task_") or raw.lower().startswith("task-")

        recent = _list_recent_sessions(limit=5)
        active = list(_ACTIVE_JOBS.keys())

        if is_task_id or is_pid or is_task_prefixed:
            err_msg = (
                f"Session '{raw}' not found. '{raw}' appears to be an internal Claude task ID or process ID, "
                f"NOT a video production session ID. "
                f"The Nexus video engine requires the exact session_id returned by create_educational_video "
                f"(format: '<topic-slug>-YYYYMMDD-HHMMSS')."
            )
        else:
            err_msg = f"Session '{raw}' not found in sessions directory."

        res: Dict[str, Any] = {
            "error": err_msg,
            "hint": "Use the exact session_id returned by create_educational_video, or pass 'latest' for the most recent session.",
            "recent_sessions": recent
        }
        if active:
            res["active_jobs"] = active
        return None, res

    return raw, None


@server.tool()
def ping(message: Optional[str] = "pong") -> Dict[str, str]:
    """Harmless test tool to verify instant MCP responsiveness without side effects.
    
    Args:
        message: Optional message to echo back.
        
    Returns:
        Dictionary with status ok, echoed message, and server timestamp.
    """
    return {
        "status": "ok",
        "echo": message or "pong",
        "timestamp": str(time.time()),
        "server": "nexus-video-production"
    }


@server.tool()
def create_educational_video(
    topic: Annotated[
        str,
        Field(description="The educational concept to explain (e.g. 'Convolutional Neural Networks', 'Transformer Attention').")
    ],
    duration: Annotated[
        Optional[str],
        Field(default="2-3 minutes", description="Target video duration (e.g. '2 minutes', '90 seconds').")
    ] = "2-3 minutes",
    audience: Annotated[
        Optional[str],
        Field(default="beginners", description="Target audience level ('beginners', 'intermediate', 'advanced').")
    ] = "beginners",
    visual_style: Annotated[
        Optional[str],
        Field(default="clean dark-mode", description="Aesthetic guidelines ('clean dark-mode', 'blueprint', etc.).")
    ] = "clean dark-mode",
    narration_style: Annotated[
        Optional[str],
        Field(default="clear, engaging educational pacing", description="Voice tone instructions for script generation.")
    ] = "clear, engaging educational pacing",
    subtitles: Annotated[
        Optional[bool],
        Field(default=True, description="Whether to burn synchronized subtitles into the final broadcast video.")
    ] = True,
    scenes_count: Annotated[
        Optional[int],
        Field(default=5, description="Number of structured scenes (default 5).")
    ] = 5,
    session_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Optional existing session ID to resume or inspect (format: '<topic-slug>-YYYYMMDD-HHMMSS'). Never pass internal task IDs or PIDs."
        )
    ] = None,
    wait_for_completion: Annotated[
        Optional[bool],
        Field(
            default=True,
            description="When True (default), waits for production to finish and returns final MP4 deliverable paths, duration, and QA score directly. No polling required."
        )
    ] = True,
    timeout_seconds: Annotated[
        Optional[int],
        Field(default=600, description="Maximum seconds to wait before returning status (default 600).")
    ] = 600,
    dry_run: Annotated[
        Optional[bool],
        Field(default=False, description="Run a lightweight diagnostic simulation without API calls or rendering.")
    ] = False
) -> Dict[str, Any]:
    """Start autonomous educational video production using the Nexus engine.
    
    Executes end-to-end video production (planning, TTS, Manim rendering, procedural audio,
    EBU R128 loudness mastering, subtitle burning, and visual QA review).
    When wait_for_completion=True (default), waits for production to finish and directly
    returns the verified MP4 paths, duration, and QA metrics.

    Args:
        topic: The educational concept to explain (e.g. 'Convolutional Neural Networks', 'Transformer Attention').
        duration: Target video duration (e.g. '2 minutes', '90 seconds').
        audience: Target audience level ('beginners', 'intermediate', 'advanced').
        visual_style: Aesthetic guidelines ('clean dark-mode', 'blueprint', etc.).
        narration_style: Voice tone instructions for script generation.
        subtitles: Whether to burn synchronized subtitles into the final broadcast video.
        scenes_count: Number of structured scenes (default 5).
        session_id: Optional existing session ID to resume, inspect, or retrieve.
        wait_for_completion: Whether to wait until production completes and return final deliverable paths (default True).
        timeout_seconds: Maximum seconds to wait before returning status (default 600).
        dry_run: Run a lightweight diagnostic without API calls.

    Returns:
        Dictionary containing session_id, terminal status, final video paths, and metrics.
    """
    if not topic or not topic.strip():
        return {"error": "Topic cannot be empty."}

    # If an existing session_id is provided, check if it already exists and is completed
    if session_id:
        try:
            session_dir = _validate_session_path(session_id)
            if session_dir.exists():
                st = get_video_status(session_id)
                if st.get("status") == "completed":
                    final_mp4 = st.get("final_mp4_path")
                    return {
                        "status": "completed",
                        "session_id": session_id,
                        "id": session_id,
                        "job_id": session_id,
                        "topic": topic,
                        "progress_percent": 100,
                        "current_stage": 12,
                        "stage_description": st.get("stage_description"),
                        "final_mp4_path": final_mp4,
                        "duration_s": st.get("duration_s"),
                        "vision_qa_score": st.get("vision_qa_score"),
                        "files": st.get("files", {}),
                        "metrics": st.get("metrics", {}),
                        "message": (
                            f"Session '{session_id}' verified completed on disk. "
                            f"Final output: {final_mp4} (Duration: {st.get('duration_s', 'N/A')}s, "
                            f"Vision QA Score: {st.get('vision_qa_score', 'N/A')}/10)."
                        )
                    }
                elif not wait_for_completion:
                    return st
            else:
                # If session_id was provided but doesn't exist, check if it's an erroneous task ID or PID
                is_task_id = bool(re.match(r"^[a-z0-9]{6,12}$", session_id, re.IGNORECASE))
                is_pid = session_id.isdigit()
                if is_task_id or is_pid or session_id.lower().startswith("task"):
                    session_id = None
        except ValueError as ve:
            return {"error": str(ve)}

    # Generate unique session slug if not provided
    if not session_id:
        slug_base = _sanitize_slug(topic)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        session_id = f"{slug_base}-{timestamp}"

    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Build rich instructional prompt
    enhanced_prompt = (
        f"{topic.strip()} — Target duration: {duration}. Audience: {audience}. "
        f"Visual style: {visual_style}. Narration style: {narration_style}."
    )

    # Prepare command to invoke existing production CLI with unbuffered I/O
    log_file_path = session_dir / "production.log"
    log_file = open(log_file_path, "a" if log_file_path.exists() else "w", encoding="utf-8")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "nexus_agent.cli",
        enhanced_prompt,
        "--session-slug",
        session_id,
        "--scenes",
        str(scenes_count or 5),
        "--auto-approve"
    ]

    dur_sec = _parse_duration_seconds(duration)
    if dur_sec:
        cmd.extend(["--target-duration", str(dur_sec)])

    # Environment with UTF-8 encoding
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    # Support safe dry-run / diagnostic invocation without external API calls
    if dry_run:
        log_file.write(f"[DRY-RUN] Diagnostic simulation for topic: {topic}\n")
        log_file.close()
        meta_file = session_dir / "mcp_metadata.json"
        meta_file.write_text(json.dumps({
            "session_id": session_id,
            "topic": topic,
            "enhanced_prompt": enhanced_prompt,
            "dry_run": True,
            "started_at": time.time(),
            "status": "dry_run_complete"
        }, indent=2), encoding="utf-8")
        return {
            "status": "dry_run_success",
            "session_id": session_id,
            "id": session_id,
            "job_id": session_id,
            "topic": topic,
            "scenes": scenes_count or 5,
            "message": f"Diagnostic dry-run verified for '{topic}'. Zero API or GPU resources consumed.",
            "log_file": str(log_file_path)
        }

    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(WORKSPACE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags
        )
        log_file.close()
        _ACTIVE_JOBS[session_id] = proc

        # Write initial metadata state
        meta_file = session_dir / "mcp_metadata.json"
        meta_file.write_text(json.dumps({
            "session_id": session_id,
            "topic": topic,
            "enhanced_prompt": enhanced_prompt,
            "pid": proc.pid,
            "started_at": time.time(),
            "status": "running",
            "progress_percent": 0,
            "current_stage": 1,
            "stage_description": "Stage 1/11: Initializing production workspace"
        }, indent=2), encoding="utf-8")

        # Asynchronous dispatch mode (when explicitly requested)
        if not wait_for_completion:
            return {
                "status": "started",
                "session_id": session_id,
                "id": session_id,
                "job_id": session_id,
                "topic": topic,
                "scenes": scenes_count or 5,
                "message": (
                    f"Video production job started successfully for session_id '{session_id}'. "
                    f"Poll get_video_status(session_id='{session_id}') to monitor progress. "
                    f"Note: Always use session_id, never pass internal Claude task IDs or process PIDs."
                ),
                "log_file": str(log_file_path)
            }

        # Synchronous / Terminal completion mode (Default)
        start_wait = time.time()
        timeout = timeout_seconds or 600
        while time.time() - start_wait < timeout:
            time.sleep(2.0)
            st = get_video_status(session_id)
            if st.get("status") == "completed":
                final_mp4 = st.get("final_mp4_path")
                return {
                    "status": "completed",
                    "session_id": session_id,
                    "id": session_id,
                    "job_id": session_id,
                    "topic": topic,
                    "progress_percent": 100,
                    "current_stage": 12,
                    "stage_description": st.get("stage_description"),
                    "final_mp4_path": final_mp4,
                    "duration_s": st.get("duration_s"),
                    "vision_qa_score": st.get("vision_qa_score"),
                    "files": st.get("files", {}),
                    "metrics": st.get("metrics", {}),
                    "message": (
                        f"Video production completed successfully! "
                        f"Master broadcast video: {final_mp4} "
                        f"(Duration: {st.get('duration_s', 'N/A')}s, "
                        f"Vision QA Score: {st.get('vision_qa_score', 'N/A')}/10)."
                    )
                }
            elif st.get("status") == "failed":
                return {
                    "status": "failed",
                    "session_id": session_id,
                    "id": session_id,
                    "job_id": session_id,
                    "topic": topic,
                    "current_stage": st.get("current_stage"),
                    "stage_description": st.get("stage_description"),
                    "error": st.get("error") or "Pipeline execution failed.",
                    "recent_logs": st.get("recent_logs", []),
                    "message": f"Video production failed during {st.get('stage_description')}."
                }

        # Timeout elapsed
        current_st = get_video_status(session_id)
        return {
            "status": "running",
            "session_id": session_id,
            "id": session_id,
            "job_id": session_id,
            "topic": topic,
            "progress_percent": current_st.get("progress_percent", 50),
            "current_stage": current_st.get("current_stage", 1),
            "stage_description": current_st.get("stage_description"),
            "message": (
                f"Video production is still running after {timeout} seconds. "
                f"Call get_video_status(session_id='{session_id}') to inspect final deliverables. "
                f"Important: Do not use internal Claude task IDs or PIDs; use this exact session_id: '{session_id}'."
            ),
            "recent_logs": current_st.get("recent_logs", [])
        }

    except Exception as e:
        return {"error": f"Failed to launch video production: {str(e)}"}


@server.tool()
def get_video_status(
    session_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="The exact session_id string returned by create_educational_video (format: '<topic-slug>-YYYYMMDD-HHMMSS', or 'latest'). Never use internal Claude task IDs (e.g. kgk308nzk) or process PIDs."
        )
    ] = None,
    id: Annotated[
        Optional[str],
        Field(default=None, description="Alias for session_id.")
    ] = None,
    job_id: Annotated[
        Optional[str],
        Field(default=None, description="Alias for session_id.")
    ] = None,
    task_id: Annotated[
        Optional[str],
        Field(default=None, description="Alias for session_id if accidentally passed.")
    ] = None
) -> Dict[str, Any]:
    """Check the real-time production status and progress of a video job.

    Args:
        session_id: The exact session_id string returned by create_educational_video (format: 'topic-slug-YYYYMMDD-HHMMSS', or 'latest').
        id: Alias for session_id.
        job_id: Alias for session_id.
        task_id: Alias for session_id if accidentally passed instead of session_id.

    Returns:
        Dictionary containing current status (running, completed, failed),
        current stage name (1-11), progress percentage, deliverables, and log snippet.
    """
    resolved_id, err = _resolve_session_id(session_id=session_id, id=id, job_id=job_id, task_id=task_id)
    if err:
        return err

    session_id = resolved_id
    session_dir = SESSIONS_DIR / session_id

    # Inspect in-memory process state
    proc = _ACTIVE_JOBS.get(session_id)
    is_proc_running = False
    return_code = None
    if proc:
        poll_res = proc.poll()
        if poll_res is None:
            is_proc_running = True
        else:
            return_code = poll_res
            _ACTIVE_JOBS.pop(session_id, None)

    # Inspect mcp_metadata.json for PID if not active in memory
    meta_file = session_dir / "mcp_metadata.json"
    meta_data: Dict[str, Any] = {}
    if meta_file.exists():
        try:
            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not is_proc_running:
        meta_pid = meta_data.get("pid")
        if meta_pid:
            is_proc_running = _is_pid_alive(meta_pid)

    # Read session_state.json if available
    state_file = session_dir / "session_state.json"
    stage_num = 1
    completed_stages = []
    state_status = None
    state_error = None
    state_progress = None
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            stage_num = state_data.get("current_stage", 1)
            completed_stages = state_data.get("completed_stages", [])
            state_status = state_data.get("status")
            state_error = state_data.get("error")
            state_progress = state_data.get("progress_percent")
        except Exception:
            pass

    # Read recent logs and check completion/failure indicators
    log_file = session_dir / "production.log"
    recent_logs = []
    log_completed = False
    log_failed = False
    if log_file.exists():
        try:
            log_text = log_file.read_text(encoding="utf-8", errors="replace")
            lines = log_text.splitlines()
            recent_logs = lines[-12:]
            if "PIPELINE COMPLETED SUCCESSFULLY!" in log_text:
                log_completed = True
            if "[PIPELINE ERROR]" in log_text or "Traceback (most recent call last)" in log_text:
                log_failed = True
        except Exception:
            pass

    stage_descriptions = {
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
        12: "Production Complete: Master video and visual audit verified"
    }

    subtitled_video = session_dir / "broadcast_subtitled.mp4"
    clean_video = session_dir / "broadcast.mp4"
    has_subtitled = subtitled_video.exists() and subtitled_video.stat().st_size > 1000
    has_clean = clean_video.exists() and clean_video.stat().st_size > 1000
    has_video = has_subtitled or has_clean

    if has_video and (stage_num >= 11 or log_completed or state_status == "completed"):
        status = "completed"
        progress_pct = 100
        stage_num = 12
    elif not is_proc_running and (not has_video or return_code not in (0, None) or log_failed or state_status == "failed"):
        status = "failed"
        progress_pct = state_progress if state_progress is not None else int(min(stage_num / 11.0, 1.0) * 100)
    elif is_proc_running:
        status = "running"
        progress_pct = state_progress if state_progress is not None else int(min(stage_num / 11.0, 0.95) * 100)
    else:
        if has_video:
            status = "completed"
            progress_pct = 100
            stage_num = 12
        else:
            status = "failed"
            progress_pct = 0

    # Sync mcp_metadata.json on disk so it never remains stale
    if meta_file.exists() and meta_data.get("status") != status:
        try:
            meta_data["status"] = status
            meta_data["progress_percent"] = progress_pct
            meta_data["current_stage"] = stage_num
            meta_data["updated_at"] = time.time()
            if status == "failed" and state_error:
                meta_data["error"] = state_error
            meta_file.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    result: Dict[str, Any] = {
        "session_id": session_id,
        "id": session_id,
        "job_id": session_id,
        "status": status,
        "progress_percent": progress_pct,
        "current_stage": stage_num,
        "stage_description": stage_descriptions.get(stage_num, f"Stage {stage_num}"),
        "completed_stages": completed_stages,
        "process_alive": is_proc_running,
        "exit_code": return_code,
        "recent_logs": recent_logs,
    }
    if state_error:
        result["error"] = state_error

    # When completed, attach deliverables and metrics directly for immediate consumption
    if status == "completed":
        outputs = get_video_outputs(session_id)
        result["files"] = outputs.get("files", {})
        result["metrics"] = outputs.get("metrics", {})
        final_mp4 = (
            outputs.get("files", {}).get("subtitled_video_mp4", {}).get("path")
            or outputs.get("files", {}).get("clean_video_mp4", {}).get("path")
        )
        result["final_mp4_path"] = final_mp4
        result["duration_s"] = outputs.get("metrics", {}).get("total_duration_s")
        result["vision_qa_score"] = outputs.get("metrics", {}).get("vision_qa_score")

    return result


@server.tool()
def get_video_outputs(
    session_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="The exact session_id string returned by create_educational_video (format: '<topic-slug>-YYYYMMDD-HHMMSS', or 'latest'). Never use internal Claude task IDs (e.g. kgk308nzk) or process PIDs."
        )
    ] = None,
    id: Annotated[
        Optional[str],
        Field(default=None, description="Alias for session_id.")
    ] = None,
    job_id: Annotated[
        Optional[str],
        Field(default=None, description="Alias for session_id.")
    ] = None,
    task_id: Annotated[
        Optional[str],
        Field(default=None, description="Alias for session_id if accidentally passed.")
    ] = None
) -> Dict[str, Any]:
    """Retrieve actual verified output files for a completed video production session.

    Never returns fabricated paths. Only returns files that physically exist on disk.

    Args:
        session_id: The exact session_id string returned by create_educational_video (format: 'topic-slug-YYYYMMDD-HHMMSS', or 'latest').
        id: Alias for session_id.
        job_id: Alias for session_id.
        task_id: Alias for session_id if accidentally passed instead of session_id.

    Returns:
        Dictionary containing verified absolute paths to master subtitled video,
        clean video, contact sheet, visual QA report, and audio track.
    """
    resolved_id, err = _resolve_session_id(session_id=session_id, id=id, job_id=job_id, task_id=task_id)
    if err:
        return err

    session_id = resolved_id
    session_dir = SESSIONS_DIR / session_id

    outputs: Dict[str, Any] = {
        "session_id": session_id,
        "id": session_id,
        "job_id": session_id,
        "session_directory": str(session_dir.resolve()),
        "files": {},
        "metrics": {}
    }

    # Expected key deliverables
    deliverables = {
        "subtitled_video_mp4": "broadcast_subtitled.mp4",
        "clean_video_mp4": "broadcast.mp4",
        "contact_sheet_png": "contact_sheet.png",
        "rendered_qa_report_md": "RENDERED_VIDEO_QA_REPORT.md",
        "rendered_qa_json": "rendered_video_qa.json",
        "visual_qa_report_md": "VISUAL_QUALITY_REPORT.md",
        "visual_qa_review_json": "visual_quality_review.json",
        "visual_direction_json": "visual_direction.json",
        "master_subtitles_srt": "subtitles.srt",
        "master_audio_wav": "audio_master.wav",
        "manim_script_py": "script.py",
        "scene_timeline_json": "scene_timeline.json",
        "script_breakdown_json": "script_breakdown.json",
        "production_log": "production.log"
    }

    for key, filename in deliverables.items():
        file_path = session_dir / filename
        if file_path.exists():
            outputs["files"][key] = {
                "path": str(file_path.resolve()),
                "filename": filename,
                "size_bytes": file_path.stat().st_size
            }

    # Extract Rendered Video QA score if report exists
    r_qa_file = session_dir / "rendered_video_qa.json"
    if r_qa_file.exists():
        try:
            r_data = json.loads(r_qa_file.read_text(encoding="utf-8"))
            outputs["metrics"]["rendered_qa_composite_score"] = r_data.get("overall_score")
            outputs["metrics"]["technical_score"] = r_data.get("technical_score")
            outputs["metrics"]["motion_score"] = r_data.get("motion_score")
            outputs["metrics"]["qa_passed"] = r_data.get("passed")
            outputs["metrics"]["max_freeze_duration_s"] = r_data.get("max_freeze_duration_s")
        except Exception:
            pass

    # Extract Vision QA score if report exists
    qa_file = session_dir / "visual_quality_review.json"
    if qa_file.exists():
        try:
            qa_data = json.loads(qa_file.read_text(encoding="utf-8"))
            outputs["metrics"]["vision_qa_score"] = qa_data.get("overall_score")
            outputs["metrics"]["status"] = qa_data.get("status")
        except Exception:
            pass

    # Extract timeline duration if available
    tl_file = session_dir / "scene_timeline.json"
    if tl_file.exists():
        try:
            raw_tl = json.loads(tl_file.read_text(encoding="utf-8"))
            if isinstance(raw_tl, dict):
                outputs["metrics"]["total_duration_s"] = raw_tl.get("total_duration_s")
                outputs["metrics"]["scenes_count"] = len(raw_tl.get("scenes", []))
                dur_m = raw_tl.get("duration_metrics")
                if dur_m:
                    outputs["metrics"]["target_duration_s"] = dur_m.get("target_duration_s")
                    outputs["metrics"]["duration_deviation_s"] = dur_m.get("deviation_s")
                    outputs["metrics"]["duration_deviation_pct"] = dur_m.get("deviation_pct")
            elif isinstance(raw_tl, list):
                outputs["metrics"]["scenes_count"] = len(raw_tl)
                outputs["metrics"]["total_duration_s"] = round(sum(s.get("duration_s", 0.0) for s in raw_tl), 2)
        except Exception:
            pass

    # Fallback duration from timestamps.json
    if "total_duration_s" not in outputs["metrics"]:
        ts_file = session_dir / "timestamps.json"
        if ts_file.exists():
            try:
                ts_data = json.loads(ts_file.read_text(encoding="utf-8"))
                outputs["metrics"]["total_duration_s"] = ts_data.get("total_duration_s")
            except Exception:
                pass

    # Extract verified audio broadcast metrics from production.log
    prod_log = session_dir / "production.log"
    if prod_log.exists():
        try:
            log_text = prod_log.read_text(encoding="utf-8", errors="replace")
            loud_match = re.search(r"Measured Loudness:\s*([-\d\.]+)\s*LUFS", log_text)
            if loud_match:
                outputs["metrics"]["integrated_loudness_lufs"] = float(loud_match.group(1))
            tp_match = re.search(r"True Peak:\s*([-\d\.]+)\s*dB", log_text)
            if tp_match:
                outputs["metrics"]["true_peak_dbtp"] = float(tp_match.group(1))
            delta_match = re.search(r"Duration Delta:\s*([-\d\.]+)\s*ms", log_text)
            if delta_match:
                outputs["metrics"]["duration_delta_ms"] = float(delta_match.group(1))
        except Exception:
            pass

    has_video = "subtitled_video_mp4" in outputs["files"] or "clean_video_mp4" in outputs["files"]
    outputs["ready"] = has_video

    return outputs


@server.tool()
def launch_video_viewer(
    session_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="The exact session_id string (e.g. 'topic-YYYYMMDD-HHMMSS', or 'latest') to launch the interactive viewer for."
        )
    ] = None,
    id: Annotated[Optional[str], Field(default=None, description="Alias for session_id.")] = None,
    job_id: Annotated[Optional[str], Field(default=None, description="Alias for session_id.")] = None,
    task_id: Annotated[Optional[str], Field(default=None, description="Alias for session_id if accidentally passed.")] = None,
    port: Annotated[int, Field(default=0, description="Optional local port to bind the viewer server to (0 for automatic port selection).")] = 0,
    open_browser: Annotated[bool, Field(default=True, description="Whether to automatically launch the default web browser.")] = True,
) -> Dict[str, Any]:
    """Launch the interactive browser video viewer for human feedback and chapter navigation.

    Opens a local web player with chapter bookmarks, subtitles, and timestamped note capture.
    Pressing 't' or clicking 'Capture' generates precise feedback notes ready to paste back into Claude Code.

    Args:
        session_id: The session identifier (or 'latest').
        port: Local port number (0 for auto-assigning an available port).
        open_browser: Whether to open the default web browser.

    Returns:
        Status dictionary with active viewer URL and instructions.
    """
    resolved_id, err = _resolve_session_id(session_id=session_id, id=id, job_id=job_id, task_id=task_id)
    if err:
        return err

    session_id = resolved_id
    session_dir = SESSIONS_DIR / session_id

    # Locate deliverable video
    video_candidates = [
        session_dir / "broadcast_subtitled.mp4",
        session_dir / "broadcast.mp4",
        session_dir / "synced_video.mp4",
    ]
    video_file = next((v for v in video_candidates if v.exists() and v.stat().st_size > 1000), None)
    if not video_file:
        return {
            "status": "error",
            "session_id": session_id,
            "message": f"No completed video found in session '{session_id}'. Run create_educational_video first."
        }

    # Ensure order file exists
    concat_file = session_dir / "concat.txt"
    if not concat_file.exists():
        media_scenes = list((session_dir / "media").rglob("*.mp4")) if (session_dir / "media").exists() else []
        media_scenes = [s for s in media_scenes if not s.name.startswith("_") and "partial" not in str(s)]
        if media_scenes:
            concat_lines = [f"file '{s.resolve().as_posix()}'" for s in media_scenes]
            concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        else:
            # Fallback: single video entry
            concat_file.write_text(f"file '{video_file.resolve().as_posix()}'\n", encoding="utf-8")

    viewer_script = WORKSPACE_DIR / "skills" / "manim-skill" / "tools" / "video_viewer.py"
    if not viewer_script.exists():
        return {
            "status": "error",
            "session_id": session_id,
            "message": f"Video viewer script not found at {viewer_script}"
        }

    # Find open port if not specified
    if not port or port <= 0:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

    cmd = [
        sys.executable,
        str(viewer_script),
        str(video_file.resolve()),
        "--order", str(concat_file.resolve()),
        "--port", str(port),
    ]

    srt_file = session_dir / "subtitles.srt"
    if srt_file.exists():
        cmd.extend(["--srt", str(srt_file.resolve())])

    script_py = session_dir / "script.py"
    if script_py.exists():
        cmd.extend(["--script", str(script_py.resolve())])

    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS

    proc = subprocess.Popen(
        cmd,
        cwd=str(session_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags
    )

    url = f"http://localhost:{port}"
    if open_browser:
        import webbrowser
        import threading
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()

    _ACTIVE_JOBS[f"viewer_{session_id}"] = proc

    return {
        "status": "viewer_launched",
        "session_id": session_id,
        "id": session_id,
        "job_id": session_id,
        "viewer_url": url,
        "video_file": str(video_file.resolve()),
        "port": port,
        "pid": proc.pid,
        "message": (
            f"Interactive video viewer launched successfully at {url}.\n"
            f"Instructions:\n"
            f"- Watch the video in your browser with chapter marks.\n"
            f"- Press 't' or click 'Capture' at any moment to log timestamped notes.\n"
            f"- Click 'Copy' and paste feedback directly into Claude Code to request targeted scene revisions."
        )
    }


def main():
    """Run the MCP server via standard stdio transport for Claude."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

