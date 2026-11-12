# Setup Guide (Windows)

This guide walks you through installing prerequisites and verifying the Nexus Video Production Engine on a fresh Windows machine using PowerShell.

---

## 1. Prerequisites: Required vs. Optional

### Required
1. **Python 3.10 or 3.11 (64-bit)**
   - Recommended over 3.12+ for prebuilt scientific/TTS wheels (`kokoro`, `torch`, `pycairo`).
   - Download from [python.org/downloads](https://www.python.org/downloads/) and check **"Add python.exe to PATH"**.
   - Verify:
     ```powershell
     python --version
     ```
     Expected output: `Python 3.10.x` or `Python 3.11.x`.

2. **FFmpeg (includes `ffprobe`)**
   - Required for concatenating Manim scenes, two-pass `-14.0 LUFS` audio mastering, and burning subtitles.
   - Install via Windows Package Manager (`winget`):
     ```powershell
     winget install Gyan.FFmpeg
     ```
   - Open a **new** PowerShell window so Windows reloads `PATH`, then verify:
     ```powershell
     ffmpeg -version
     ffprobe -version
     ```

3. **Git**
   - Required to clone the repository:
     ```powershell
     winget install Git.Git
     ```

### Optional
- **Claude Code CLI:** Needed only if you want to trigger and monitor video production conversationally via MCP (`npm install -g @anthropic-ai/claude-code`).
- **LaTeX (MiKTeX / TeX Live):** **Not required.** The engine enforces pure Manim geometry and Pango `Text(...)` so scenes render cleanly without LaTeX.

---

## 2. Clone & Create a Virtual Environment

Open PowerShell in your target workspace folder:

```powershell
git clone <your-repo-url>
cd manim-skill-main

python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> **Note:** If PowerShell blocks `Activate.ps1`, allow local scripts once for your user account:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

---

## 3. Install Python Dependencies

With `(.venv)` active:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This installs:
- **Manim Community Edition** (`manim>=0.18.0,<=0.22.1`) — 2D/3D animation renderer (bundles Windows Cairo/Pango wheels).
- **Kokoro TTS & Audio Processing** (`kokoro`, `soundfile`, `numpy`, `scipy`, `srt`) — neural voiceover and procedural audio DSP.
- **Nexus API & Image QA** (`openai`, `pydantic`, `python-dotenv`, `pillow`) — LLM orchestration and contact-sheet inspection.
- **MCP Server SDK** (`mcp>=1.0.0`) — stdio Model Context Protocol integration for Claude Code / Antigravity.

*(Note: Kokoro downloads its neural voice model weights automatically from Hugging Face on the first TTS synthesis run; subsequent runs use the local cache.)*

---

## 4. Verify Installation (Offline / Free)

Run these checks inside your activated `.venv`. None of these commands consume Nexus API credits:

1. **Verify executable discovery (should resolve `python` and `manim_cmd` inside `.venv\Scripts\`):**
   ```powershell
   python -c "from nexus_agent.tools_resolver import validate_required_executables; res = validate_required_executables(); [print(f'{k}: {v}') for k, v in res.items()]"
   ```
   Expected output includes valid paths for `python`, `ffmpeg`, `ffprobe`, `manim_cmd`, `git`, and:
   `latex: Not installed (MathTex/Tex/BarChart LaTeX mode will be replaced with Text/geometry)` *(normal when no LaTeX is installed)*.

2. **Render the included no-LaTeX Manim smoke scene:**
   ```powershell
   manim -ql test_manim.py SquareToCircle
   ```
   Expected result: renders `media\videos\test_manim\480p15\SquareToCircle.mp4` (`Played 3 animations`).

3. **Verify Kokoro TTS & MCP imports:**
   ```powershell
   python -c "from kokoro import KPipeline; import mcp; print('Kokoro & MCP imports OK')"
   ```

4. **Run the complete offline unit test suite (51 tests, no paid API calls):**
   ```powershell
   python -m unittest nexus_agent.test_mcp nexus_agent.test_upgraded_engine nexus_agent.test_visual_engine nexus_agent.test_ffmpeg_recovery nexus_agent.test_unified_engine -v
   ```
   Expected output: `Ran 51 tests ... OK`.

> [!NOTE]
> **Pre-Publish Verification Scope**
> - **Verified after repository cleanup:** all **51** offline unit tests (`unittest`), fresh-clone `.venv` dependency installation (`pip install -r requirements.txt`), and the `SquareToCircle` Manim smoke render (`test_manim.py`).
> - **Not yet re-verified after cleanup:** live Kokoro TTS synthesis, a fresh Claude Code MCP session, and an end-to-end video-generation run.
> - **First-run Kokoro note:** the very first TTS run downloads Kokoro model weights from Hugging Face and caches them locally.

Next, proceed to [`CONFIGURATION.md`](CONFIGURATION.md) to set up your Nexus API key and generate your first video.
