# Nexus Manim Video Production Engine

![Demo Animation](manim_skill.gif)

An autonomous educational video production engine powered by **Nexus AI**, **Manim Community Edition**, **Kokoro TTS**, **FFmpeg**, and **Claude Code MCP**. Given a topic and target duration, the engine writes a pedagogical script, synthesizes voiceover narration, plans beat-synchronized scenes, renders animations without requiring LaTeX, mixes procedural sound design, masters audio to broadcast loudness (`-14.0 LUFS`), burns captions, and performs automated visual QA.

---

## Key Features

- **End-to-End Video Pipeline:** Topic -> Script -> Kokoro TTS -> Beat Timeline -> Visual Direction -> Manim Rendering -> Procedural SFX -> EBU R128 Mastering (`-14.0 LUFS`) -> Subtitles -> Rendered & Vision QA.
- **No-LaTeX Resilient:** AST linting enforces pure Manim geometry and Pango `Text(...)`—no MiKTeX or TeX Live needed.
- **Granular Self-Healing:** Automatically isolates, repairs, and re-renders failing or frozen scenes (`Stage 11.5`) without restarting healthy scenes.
- **Claude Code MCP Integration:** Exposes 5 tools (`ping`, `create_educational_video`, `get_video_status`, `get_video_outputs`, `launch_video_viewer`) via [`.mcp.json`](.mcp.json).

---

## Required vs. Optional Software

| Software | Requirement | Purpose |
| :--- | :--- | :--- |
| **Python 3.10 or 3.11 (64-bit)** | **Required** | Runs `nexus_agent`, Manim, Kokoro TTS, and the MCP server |
| **FFmpeg & FFprobe** | **Required** | Scene concatenation, two-pass audio mastering (`-14 LUFS`), subtitle burn-in |
| **Git** | **Required** | Cloning and managing the repository |
| **Claude Code CLI** | *Optional* | Trigger and monitor videos conversationally via MCP |
| **LaTeX (MiKTeX / TeX Live)** | *Optional (Not Required)* | Only needed if you manually write custom `MathTex` / `Tex` scripts |

---

## Quick Start (Windows PowerShell)

```powershell
# 1. Clone the repository and enter the folder
git clone <your-repo-url>
cd manim-skill-main

# 2. Install FFmpeg (skip if already installed)
winget install Gyan.FFmpeg

# 3. Create and activate a Python virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 4. Install Python dependencies (Manim, Kokoro, OpenAI SDK, MCP SDK)
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Create .env from template and set NEXUS_API_KEY + NEXUS_MODEL
Copy-Item .env.example .env
notepad .env

# 6. Verify environment & run offline unit tests (51 tests, no paid API calls)
python -c "from nexus_agent.tools_resolver import validate_required_executables; print(validate_required_executables())"
python -m unittest nexus_agent.test_mcp nexus_agent.test_upgraded_engine nexus_agent.test_visual_engine nexus_agent.test_ffmpeg_recovery nexus_agent.test_unified_engine -v
```

---

## Generate Your First Video

### Via CLI
```powershell
.\.venv\Scripts\Activate.ps1
python -m nexus_agent.cli "Explain Gradient Descent to beginners" --scenes 5 --target-duration 90 --auto-approve
```

### Via Claude Code (MCP)
Activate `.venv` first so `python` resolves inside `.venv`, then start Claude Code:
```powershell
.\.venv\Scripts\Activate.ps1
claude
```
Then ask Claude:
> *"Use `create_educational_video` to create a 90-second beginner video explaining Gradient Descent."*

Deliverables (`broadcast_subtitled.mp4`, `broadcast.mp4`, `subtitles.srt`, `contact_sheet.png`, QA reports) are saved under `sessions/`.

---

## Documentation Navigation

| Audience | Start Here |
| :--- | :--- |
| **Students / New Users** | [`docs/SETUP.md`](docs/SETUP.md) · [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) · [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) |
| **Developers / Maintainers** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md) |
| **AI Coding Assistants** | [`AGENTS.md`](AGENTS.md) *(onboarding, safety rules, test matrix, doc-sync triggers)* |

---

## Project Structure

```text
├── README.md                   # Quickstart & navigation
├── AGENTS.md                   # AI assistant onboarding & guardrails
├── LICENSE
├── .gitignore
├── .env.example
├── .mcp.json                   # MCP server launch config (nexus-video-production)
├── requirements.txt
├── pyproject.toml
├── test_manim.py               # No-LaTeX Manim smoke scene (SquareToCircle)
├── manim_skill.gif
├── .claude/                    # Project MCP allowlist (settings.json)
├── .claude-plugin/             # Upstream manim-skill plugin metadata (MIT)
├── docs/                       # Setup, Config, Architecture, Developer Reference, Troubleshooting
├── nexus_agent/                # Orchestrator, CLI, MCP server, audio/, compositor/, QA & tests
└── skills/                     # Stage prompt skills & tools (loaded by SkillLoader)
```
