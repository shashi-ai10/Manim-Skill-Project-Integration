"""Executable and tool resolution utilities for FFmpeg, FFprobe, and Manim.

Guarantees robust binary discovery across environments, detailed subprocess diagnostics,
and media integrity validation.
"""

import os
import sys
import shutil
import subprocess
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

_RESOLVED_CACHE: Dict[str, str] = {}


def _ensure_in_path(bin_path: Path) -> None:
    """Prepend executable's parent directory to os.environ['PATH'] so DLLs and binaries are discoverable."""
    if not bin_path.exists():
        return
    bin_dir = str(bin_path.parent.resolve())
    curr_path = os.environ.get("PATH", "")
    paths = curr_path.split(os.pathsep)
    if bin_dir not in paths:
        os.environ["PATH"] = bin_dir + os.pathsep + curr_path


def resolve_executable(name: str) -> str:
    """Resolve full path to executable, checking PATH and common installation paths.
    
    Caches results to avoid repeated filesystem traversal.
    """
    if name in _RESOLVED_CACHE:
        return _RESOLVED_CACHE[name]

    # 1. Prefer the active Python environment's Scripts/bin directory (important for venvs invoked via python.exe)
    py_dir = Path(sys.executable).parent
    py_scripts = py_dir / "Scripts"
    if sys.platform == "win32":
        venv_candidates = [py_scripts / f"{name}.exe", py_dir / f"{name}.exe", py_scripts / name, py_dir / name]
    else:
        venv_candidates = [py_dir / name, py_scripts / name]
    for p in venv_candidates:
        if p.exists():
            resolved = str(p.resolve())
            _ensure_in_path(p)
            _RESOLVED_CACHE[name] = resolved
            return resolved

    # 2. Standard PATH resolution
    found = shutil.which(name)
    if found and Path(found).exists():
        resolved = str(Path(found).resolve())
        _ensure_in_path(Path(resolved))
        _RESOLVED_CACHE[name] = resolved
        return resolved

    # 3. Windows .exe suffix fallback
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        found = shutil.which(f"{name}.exe")
        if found and Path(found).exists():
            resolved = str(Path(found).resolve())
            _ensure_in_path(Path(resolved))
            _RESOLVED_CACHE[name] = resolved
            return resolved

    # 4. Common Windows installation paths (WinGet, Chocolatey, Program Files, Git)
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        
        candidates = [
            Path(local_app_data) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.0.2-full_build-shared" / "bin" / f"{name}.exe",
            Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe",
            Path(program_files) / "ffmpeg" / "bin" / f"{name}.exe",
            Path(program_files_x86) / "ffmpeg" / "bin" / f"{name}.exe",
            Path("C:\\ffmpeg\\bin") / f"{name}.exe",
            Path("C:\\ProgramData\\chocolatey\\bin") / f"{name}.exe",
            Path(program_files) / "Git" / "cmd" / f"{name}.exe",
            Path(program_files) / "Git" / "bin" / f"{name}.exe",
        ]
        
        # Also check winget package wildcards
        winget_pkg_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkg_dir.exists():
            candidates.extend(list(winget_pkg_dir.glob(f"**/bin/{name}.exe")))

        for c in candidates:
            if c.exists():
                resolved = str(c.resolve())
                _ensure_in_path(c)
                _RESOLVED_CACHE[name] = resolved
                return resolved

    # Fallback to bare name
    _RESOLVED_CACHE[name] = name
    return name


def get_python_path() -> str:
    """Return verified path to current Python executable."""
    return sys.executable


def get_ffmpeg_path() -> str:
    """Return verified full path to ffmpeg binary."""
    return resolve_executable("ffmpeg")


def get_ffprobe_path() -> str:
    """Return verified full path to ffprobe binary."""
    return resolve_executable("ffprobe")


def get_manim_cmd() -> List[str]:
    """Return command prefix for invoking Manim reliably across platforms.
    
    Prefers resolved manim executable, falling back to [sys.executable, "-m", "manim"].
    """
    manim_bin = resolve_executable("manim")
    if manim_bin and Path(manim_bin).exists() and not manim_bin.endswith(".py"):
        _ensure_in_path(Path(manim_bin))
        return [manim_bin]
    return [sys.executable, "-m", "manim"]


def get_git_path() -> Optional[str]:
    """Return path to git binary if available."""
    git_bin = resolve_executable("git")
    if git_bin and Path(git_bin).exists():
        return git_bin
    return None


def check_latex_available() -> bool:
    """Check if a working LaTeX distribution is installed and discoverable in PATH."""
    for compiler in ("latex", "pdflatex", "xelatex"):
        found = shutil.which(compiler) or (shutil.which(f"{compiler}.exe") if sys.platform == "win32" else None)
        if found and Path(found).exists():
            return True
    return False


def validate_required_executables() -> Dict[str, Any]:
    """Validate all core executables needed by the Nexus Video Production Engine.
    
    Verifies Python, Manim, FFmpeg, FFprobe, and Git.
    Raises RuntimeError with clear actionable diagnostic message if critical dependencies are missing.
    """
    python_path = get_python_path()
    ffmpeg_path = get_ffmpeg_path()
    ffprobe_path = get_ffprobe_path()
    manim_cmd = get_manim_cmd()
    git_path = get_git_path()
    latex_ok = check_latex_available()

    missing = []
    if not python_path or not Path(python_path).exists():
        missing.append("Python (sys.executable not found)")

    if not ffmpeg_path or not Path(ffmpeg_path).exists():
        missing.append("FFmpeg (install via winget install Gyan.FFmpeg or place in PATH)")

    if not ffprobe_path or not Path(ffprobe_path).exists():
        missing.append("FFprobe (install via winget install Gyan.FFmpeg or place in PATH)")

    # Test manim invocability quickly via --version
    try:
        check_cmd = manim_cmd + ["--version"]
        res = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            missing.append(f"Manim (executable failed with exit {res.returncode}: {res.stderr[:100]})")
    except Exception as e:
        missing.append(f"Manim (invocation failed: {e})")

    if missing:
        err_msg = (
            "Nexus Video Engine environment validation failed! The following required executables are missing or non-functional:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\nEnsure all dependencies are installed and available in PATH."
        )
        raise RuntimeError(err_msg)

    return {
        "python": python_path,
        "ffmpeg": ffmpeg_path,
        "ffprobe": ffprobe_path,
        "manim_cmd": manim_cmd,
        "git": git_path or "Not found (optional)",
        "latex": "Available" if latex_ok else "Not installed (MathTex/Tex/BarChart LaTeX mode will be replaced with Text/geometry)",
    }


def run_command_diagnostics(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    check: bool = True,
    env: Optional[Dict[str, str]] = None
) -> subprocess.CompletedProcess:
    """Execute command with resolved binaries, full diagnostic logging, and rich exceptions."""
    # Resolve leading command binary if ffmpeg/ffprobe/manim/python
    resolved_cmd = list(cmd)
    head = resolved_cmd[0].lower()
    if head in ("ffmpeg", "ffmpeg.exe"):
        resolved_cmd[0] = get_ffmpeg_path()
    elif head in ("ffprobe", "ffprobe.exe"):
        resolved_cmd[0] = get_ffprobe_path()
    elif head in ("python", "python.exe"):
        resolved_cmd[0] = get_python_path()
    elif head in ("manim", "manim.exe"):
        manim_prefix = get_manim_cmd()
        resolved_cmd = manim_prefix + resolved_cmd[1:]

    work_dir = str(cwd) if cwd else None
    exec_env = {**os.environ, **(env or {}), "PYTHONIOENCODING": "utf-8"}

    res = subprocess.run(
        resolved_cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=exec_env
    )

    if check and res.returncode != 0:
        cmd_str = " ".join(resolved_cmd)
        stderr_text = res.stderr.strip() or "<empty>"
        stdout_text = res.stdout.strip() or "<empty>"
        raise RuntimeError(
            f"Command execution failed with exit code {res.returncode}.\n"
            f"Command: {cmd_str}\n"
            f"Working Directory: {work_dir or os.getcwd()}\n"
            f"STDERR:\n{stderr_text}\n"
            f"STDOUT:\n{stdout_text}"
        )

    return res


def validate_media_file(file_path: Path, min_duration: float = 0.1) -> Dict[str, Any]:
    """Validate that media file exists, is non-empty, and has valid readable streams via ffprobe.
    
    Returns:
        Dictionary containing duration, width, height, codec info.
    Raises:
        RuntimeError if file is missing, empty, or unreadable by ffprobe.
    """
    file_path = Path(file_path).resolve()
    if not file_path.exists():
        raise RuntimeError(f"Media validation failed: file not found: {file_path}")
    
    size_bytes = file_path.stat().st_size
    if size_bytes == 0:
        raise RuntimeError(f"Media validation failed: file is zero bytes: {file_path}")

    ffprobe_bin = get_ffprobe_path()
    cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,duration,width,height",
        "-show_entries", "format=duration,size,bit_rate",
        "-of", "json",
        str(file_path)
    ]

    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if res.returncode != 0:
        raise RuntimeError(
            f"ffprobe validation failed for {file_path} (exit {res.returncode}):\n{res.stderr}"
        )

    try:
        info = json.loads(res.stdout)
    except Exception as e:
        raise RuntimeError(f"Failed to parse ffprobe JSON output for {file_path}: {e}") from e

    format_info = info.get("format", {})
    streams = info.get("streams", [])

    duration_str = format_info.get("duration")
    duration_s = float(duration_str) if duration_str else 0.0

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if duration_s < min_duration:
        raise RuntimeError(
            f"Media validation failed for {file_path}: duration {duration_s:.3f}s is below minimum {min_duration}s."
        )

    return {
        "file_path": str(file_path),
        "size_bytes": size_bytes,
        "duration_s": duration_s,
        "video_streams": len(video_streams),
        "audio_streams": len(audio_streams),
        "width": video_streams[0].get("width") if video_streams else None,
        "height": video_streams[0].get("height") if video_streams else None,
        "video_codec": video_streams[0].get("codec_name") if video_streams else None,
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
    }
