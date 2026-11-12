# Configuration & Usage Guide

This guide explains how to configure Nexus AI API credentials, optionally connect Claude Code via MCP, and generate your first educational video. For full CLI flags, MCP parameter schemas, and environment variables, see [`DEVELOPER_REFERENCE.md`](DEVELOPER_REFERENCE.md).

---

## 1. Configure Nexus API Access (`.env`)

1. Request a **Nexus API Key** from your Navigate Labs workspace/admin.
2. Copy the example template to `.env` in the project root:
   ```powershell
   Copy-Item .env.example .env
   ```
3. Edit `.env` (UTF-8, never commit it):
   ```env
   # Nexus API Base Endpoint (must include /v1 for the Python OpenAI SDK)
   NEXUS_API_URL=https://nexusapi.navigatelabs.ai/v1

   # Your Navigate Labs Nexus API Key
   NEXUS_API_KEY=your_nexus_api_key_here

   # Model identifier active on your Nexus account (e.g., claude-haiku-4-5)
   NEXUS_MODEL=claude-haiku-4-5
   ```
   *(Optional: set `MANIM_SKILL_HOME=./custom_sessions` if you want CLI sessions stored outside `./sessions`.)*

4. **Verify Nexus connectivity (free metadata check — lists available models without spending generation credits):**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   python -m unittest nexus_agent.test_nexus.TestNexusIntegration.test_01_api_connectivity -v
   ```
   Expected output: `Connected to https://nexusapi.navigatelabs.ai/v1. Found <N> models.`

   *(Optional paid checks: `test_02_basic_text_response` and `python -m nexus_agent.test_vision` verify live text completion and image QA support, and consume a small amount of upstream Nexus credits.)*

---

## 2. Configure Claude Code & MCP Server (Optional)

Skip this section if you only plan to run the Python CLI.

### A. Configure Claude Code to Use Nexus API
Create the Claude configuration folder if it does not exist yet:
```powershell
New-Item -ItemType Directory -Force "$HOME\.claude"
```
Create or edit `$HOME\.claude\settings.json` (save as **UTF-8 without BOM**):
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://nexusapi.navigatelabs.ai",
    "ANTHROPIC_API_KEY": "your_nexus_api_key_here",
    "ANTHROPIC_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5"
  }
}
```
> [!IMPORTANT]
> `ANTHROPIC_BASE_URL` for Claude Code must **not** end with `/v1` (Claude appends `/v1/messages`), whereas `NEXUS_API_URL` in `.env` **must** end with `/v1`.

### B. Launch Claude Code with the MCP Server Active
The repository includes [`.mcp.json`](../.mcp.json), which launches `python -m nexus_agent.mcp_server` (`PYTHONIOENCODING=utf-8`).

Always **activate `.venv` before running `claude`** from the project root so `python` resolves to `.venv\Scripts\python.exe`:
```powershell
.\.venv\Scripts\Activate.ps1
claude
```
When prompted to approve the project MCP server `nexus-video-production`, choose **Approve**.

### C. Verify All 5 MCP Tools
Inside Claude Code, test with:
> *"Call `ping` on `nexus-video-production` and list available Nexus video tools."*

Available tools:
1. `ping` — Harmless health check
2. `create_educational_video` — Runs the pipeline in a background CLI worker
3. `get_video_status` — Polls stage progress (`1..12`), PID health, and `production.log`
4. `get_video_outputs` — Returns verified deliverable file paths and QA metrics
5. `launch_video_viewer` — Opens the interactive local browser player with chapter markers *(prompts once on first use unless added to `.claude/settings.json`)*

*(Full parameter defaults and aliases live in the MCP Server section of [`DEVELOPER_REFERENCE.md`](DEVELOPER_REFERENCE.md).)*

---

## 3. Generate & Monitor Your First Video

### Option 1: Command Line
```powershell
.\.venv\Scripts\Activate.ps1
python -m nexus_agent.cli "Explain Gradient Descent to beginners" --scenes 5 --target-duration 90 --auto-approve
```
To resume an interrupted run from Stage 8 (rendering) without regenerating scripts or audio:
```powershell
python -m nexus_agent.cli "Explain Gradient Descent to beginners" --session-slug explain-gradient-descent-to-begi --resume-stage 8 --auto-approve
```

### Option 2: Claude Code Prompt
> *"Use `create_educational_video` to create a 90-second beginner educational video explaining Gradient Descent, and share the final `broadcast_subtitled.mp4` path and QA score."*

---

## 4. Finding Your Generated Outputs

- **CLI runs** write to `sessions/<session-slug>/` (default slug derived from topic, or set via `--session-slug` / `--session-dir`).
- **MCP runs** write to `sessions/<topic-slug>-YYYYMMDD-HHMMSS/`.

Key deliverables inside each session folder:
- `broadcast_subtitled.mp4` — Final `1920x1080` `30fps` video with mastered audio (`-14.0 LUFS`) and burned-in captions
- `broadcast.mp4` — Clean `1920x1080` video without burned-in subtitles
- `audio_master.wav` & `subtitles.srt` — Broadcast-mastered narration/SFX track and SRT captions
- `contact_sheet.png` — Scene preview grid
- `RENDERED_VIDEO_QA_REPORT.md` & `rendered_video_qa.json` — Technical `ffprobe`, freeze-detection, and Vision QA audit
- `session_state.json` & `production.log` — Stage progress (`1..12`) and diagnostic execution log
