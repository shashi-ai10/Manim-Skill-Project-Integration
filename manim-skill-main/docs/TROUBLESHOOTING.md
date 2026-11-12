# Troubleshooting Guide (Windows)

---

### 1. `ffmpeg` or `ffprobe` Not Found (`WinError 2`)
- **Symptom:** `FileNotFoundError: [WinError 2] The system cannot find the file specified` during Stage 1, 8, or 10.
- **Fix:**
  ```powershell
  winget install Gyan.FFmpeg
  ```
  Open a **new** PowerShell terminal so `PATH` reloads, then verify `ffmpeg -version` and `ffprobe -version`.

---

### 2. `ModuleNotFoundError: No module named 'mcp'` (or `manim` / `kokoro`)
- **Symptom:** Running `python -m nexus_agent.mcp_server` or Claude Code's MCP server fails with missing package errors.
- **Cause:** `.venv` was not activated or dependencies were installed globally/partially. Note that [`.mcp.json`](../.mcp.json) invokes bare `python`, which resolves from your active shell `PATH`.
- **Fix:** Always activate `.venv` before starting `claude` or CLI commands, and reinstall dependencies inside `.venv`:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```

---

### 3. `manim.exe` Uses Global Python Instead of `.venv`
- **Symptom:** Calling `.\.venv\Scripts\python.exe` prints a global `manim.exe` path in `validate_required_executables()`.
- **Fix:** Ensure you have the current [`nexus_agent/tools_resolver.py`](../nexus_agent/tools_resolver.py) (prioritizes `.venv\Scripts\` via `sys.platform == "win32"` before falling back to global `PATH`).

---

### 4. HTTP `402 RESOURCE_EXHAUSTED` from Nexus API
- **Symptom:** `Your prepayment credits are depleted` (for example on `gemini-3.1-flash-lite`, which is also the code's fallback if `NEXUS_MODEL` is left empty).
- **Fix:** Set `NEXUS_MODEL=claude-haiku-4-5` (or another funded model from `test_01_api_connectivity`) explicitly in `.env` and in `~/.claude/settings.json`.

---

### 5. Claude Code Hangs / Retries with HTTP `500 BedrockException`
- **Symptom:** Interactive Claude Code retries repeatedly when pointed at models like `gemma-4-e2b`.
- **Fix:** Some hosted models do not support Claude Code's full tool-calling payload. Set `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_HAIKU_MODEL` in `~/.claude/settings.json` to `claude-haiku-4-5`.

---

### 6. `JSONDecodeError: Unexpected UTF-8 BOM` in Claude Settings
- **Symptom:** Tools fail to parse `~/.claude/settings.json` at line 1 column 1.
- **Fix:** Strip UTF-8 BOM in place using Python:
  ```powershell
  python -c "from pathlib import Path; p = Path.home()/'.claude'/'settings.json'; p.write_text(p.read_bytes().decode('utf-8-sig'), encoding='utf-8')"
  ```

---

### 7. Manim `latex` / `compile_tex` Errors
- **Symptom:** Manim fails trying to invoke `latex` or `pdflatex`.
- **Fix:** The pipeline enforces no-LaTeX rendering via `lint_manim_code()` (replaces `MathTex`/`Tex` with Pango `Text(...)`). For custom scenes outside the orchestrator, replace `MathTex(...)` and `Tex(...)` with `Text(...)` and geometry.

---

### 8. Kokoro TTS First-Run Download Timeout or Python 3.12+ Wheel Issues
- **Symptom:** Stage 4 fails downloading Kokoro weights or `pip install -r requirements.txt` fails compiling packages on Python 3.12/3.13.
- **Fix:**
  - Use **64-bit Python 3.10 or 3.11** (tested environments for prebuilt Manim/Kokoro/PyTorch wheels).
  - Ensure internet access to Hugging Face on the very first Kokoro TTS run so voice weights cache locally.

---

### 9. PowerShell Blocks `Activate.ps1`
- **Symptom:** `running scripts is disabled on this system`.
- **Fix:**
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```

---

### 10. MCP Server Shows `Pending approval` or Session Stuck as `failed`
- **`Pending approval` in `claude mcp list`:** Start `claude` interactively from the project root (with `.venv` active) and choose **Approve** when prompted for `nexus-video-production`.
- **Session shows `status: "failed"` after a crash:** `get_video_status` detects dead worker PIDs automatically when deliverables are incomplete. Inspect `sessions/<id>/production.log` and `session_state.json`, then resume from the interrupted stage (`--resume-stage <N>` via CLI).
