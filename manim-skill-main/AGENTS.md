# AGENTS.md — AI Coding Assistant Guide

Welcome. This file gives AI assistants (ChatGPT, Claude Code, Antigravity, Codex, and similar agents) the exact mental model, file map, safety boundaries, and verification workflow for the **Nexus Manim Video Production Engine**.

> [!IMPORTANT]
> **Cost & Security Rules (Always Enforced)**
> - **Never expose, print, copy, or commit `.env` or API keys** (`NEXUS_API_KEY`, `ANTHROPIC_API_KEY`, `sk-...` tokens).
> - **Do not execute paid Nexus API calls or launch full video generation** (`create_educational_video`, `cli.py` full runs, `test_nexus.py` `test_02+`, `test_vision.py`, `run_acceptance_test.py`) unless the user explicitly asks to spend credits/run a live render.
> - **Default to the 51 offline unit tests** when validating changes.

---

## 1. AI Assistant Onboarding

### What to Read First
Read in this order to get full context with minimal token usage:
1. [`README.md`](README.md) — Project purpose, required software, and quickstart commands.
2. [`AGENTS.md`](AGENTS.md) *(this file)* — Safety rules, pipeline contracts, and validation workflow.
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Visual Mermaid diagrams, lifecycle stages, module boundaries, and a complete traced execution example.
4. [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md) — Canonical tables for CLI flags, MCP schemas, environment variables, session artifacts, and the full test matrix.

### Where to Find Implementation Details
- **Structured Pipeline:** [`nexus_agent/orchestrator.py`](nexus_agent/orchestrator.py) (`VideoProductionOrchestrator`, `lint_manim_code`)
- **Entrypoints:** [`nexus_agent/cli.py`](nexus_agent/cli.py) (CLI) and [`nexus_agent/mcp_server.py`](nexus_agent/mcp_server.py) (stdio MCP server, 5 tools)
- **Legacy Loop & Sandboxed Tooling:** [`nexus_agent/agent.py`](nexus_agent/agent.py) (`--legacy-loop`), [`nexus_agent/tools.py`](nexus_agent/tools.py) (`SafeToolExecutor`)
- **Executable Discovery & Config:** [`nexus_agent/tools_resolver.py`](nexus_agent/tools_resolver.py), [`nexus_agent/config.py`](nexus_agent/config.py)
- **Visual Direction, Timing, QA & Repair:** [`nexus_agent/visual_director.py`](nexus_agent/visual_director.py), [`nexus_agent/sync.py`](nexus_agent/sync.py), [`nexus_agent/contact_sheet.py`](nexus_agent/contact_sheet.py), [`nexus_agent/rendered_video_qa.py`](nexus_agent/rendered_video_qa.py), [`nexus_agent/vision_review.py`](nexus_agent/vision_review.py), [`nexus_agent/scene_repair.py`](nexus_agent/scene_repair.py)
- **Audio & FFmpeg Mastering:** [`nexus_agent/audio/`](nexus_agent/audio/) and [`nexus_agent/compositor/`](nexus_agent/compositor/)
- **Stage Prompts / Skills:** [`nexus_agent/skill_loader.py`](nexus_agent/skill_loader.py) and [`skills/`](skills/)

### Tracing a Video-Generation Request Through the Pipeline
1. **MCP Path:** Claude/client calls `create_educational_video(...)` in `nexus_agent/mcp_server.py` -> validates inputs & creates `<topic-slug>-YYYYMMDD-HHMMSS` inside `sessions/` -> spawns `python -m nexus_agent.cli ... --auto-approve` -> writes `mcp_metadata.json`.
2. **CLI Path:** `nexus_agent/cli.py:main()` loads config via `load_nexus_config()`, builds `VideoProductionOrchestrator`, and calls `orchestrator.run_pipeline(topic, resume_from_stage=...)`.
3. **Orchestration (`orchestrator.py`):** Runs `stage1_analyze_topic` through `stage12_final_delivery`, calling `TopicAwareVisualDirector` (`stage5_5`), AST `lint_manim_code()`, per-scene Manim renders, `SoundDesignEngine`, `PostProductionPipeline` (two-pass `-14.0 LUFS` loudnorm + burned `.srt` subtitles), and `RenderedVideoQA` + `VisualQualityReviewer` + `SceneRepairManager` (`STAGE 11.5` auto-repair when needed).

### Identifying Dependencies Before Modifying Code
Before editing any module:
- Check imports inside `nexus_agent/` and runtime subprocess calls (`manim`, `ffmpeg`, `ffprobe`, `video_viewer.py` in `skills/manim-skill/tools/`).
- Check `SkillLoader.STAGE_SKILL_MAPPING` in [`nexus_agent/skill_loader.py`](nexus_agent/skill_loader.py) before renaming/removing any folder under `skills/`.
- Check [`.mcp.json`](.mcp.json) and [`.claude/settings.json`](.claude/settings.json) if changing MCP server module names, tool names, or signatures.
- Check [`requirements.txt`](requirements.txt) and [`pyproject.toml`](pyproject.toml) together whenever adding/updating Python packages.

### Safe Validation Without Consuming Nexus Credits
Run the **51 offline `unittest` cases** (zero LLM inference calls; mocks `SESSIONS_DIR` and sets `SKIP_NEXUS_API=1` where needed):
```powershell
python -m unittest nexus_agent.test_mcp nexus_agent.test_upgraded_engine nexus_agent.test_visual_engine nexus_agent.test_ffmpeg_recovery nexus_agent.test_unified_engine -v
```
Optional free environment/syntax checks:
```powershell
python -m nexus_agent.cli --help
python -c "from nexus_agent.tools_resolver import validate_required_executables; print(validate_required_executables())"
manim -ql test_manim.py SquareToCircle
```
Free metadata-only API connectivity check (lists `/models`, generates no completions):
```powershell
python -m unittest nexus_agent.test_nexus.TestNexusIntegration.test_01_api_connectivity
```

### Actions Requiring Explicit User Approval
Always confirm with the user before:
- Running paid API tests (`test_nexus.py` `test_02+`, `test_vision.py`, `run_acceptance_test.py`) or triggering `create_educational_video` / `cli.py` full video runs.
- Editing [`nexus_agent/orchestrator.py`](nexus_agent/orchestrator.py), [`nexus_agent/mcp_server.py`](nexus_agent/mcp_server.py), [`nexus_agent/tools_resolver.py`](nexus_agent/tools_resolver.py), [`.mcp.json`](.mcp.json), or `.claude/` config files.
- Editing `.env`, `~/.claude/settings.json`, or installing packages/changing pinned dependencies.
- Initializing Git, modifying `.gitignore`, or pushing to a remote repository.

---

## 2. Architecture & Contract Highlights (Verify Against Code)

- **Stage Numbering:** `orchestrator.py` prints `[STAGE 1/11]`–`[STAGE 11/11]` in console logs, executes sub-stages `Stage 5.5/11` (Visual Direction inside Stage 6) and `STAGE 11.5` (Scene Repair inside Stage 11), and writes `current_stage: 12` (`"Production Complete"`) in `session_state.json` via `stage12_final_delivery()`.
- **No-LaTeX Environment:** Most Windows user machines lack MiKTeX/TeX Live. `lint_manim_code()` forbids `MathTex` and `Tex`; generated code must use Pango `Text(...)` and pure geometry (`Circle`, `Square`, `RoundedRectangle`, `Line`, `Arrow`, `VGroup`, `ThreeDAxes`, etc.).
- **No Static Freezes:** `lint_manim_code()` flags `self.wait(...) > 1.5s`; every beat requires active `self.play(...)` motion.
- **Session IDs vs. Task IDs:** Video session IDs follow `<topic-slug>-YYYYMMDD-HHMMSS` (or `"latest"`). `mcp_server.py` explicitly rejects internal agent task IDs or numeric PIDs.

---

## 3. Safe-to-Edit vs. Handle-with-Care Files

| Risk Level | Files / Directories | Guidance |
| :--- | :--- | :--- |
| **Low (Safe with offline test run)** | `docs/*.md`, `README.md`, `nexus_agent/test_*.py`, `nexus_agent/audio/*`, `nexus_agent/compositor/*`, `skills/*/SKILL.md` | Keep skill directory names aligned with `SkillLoader.STAGE_SKILL_MAPPING`; keep unit tests self-contained with temp dirs (never depend on local `sessions/`). |
| **Medium (Check callers carefully)** | `nexus_agent/cli.py`, `nexus_agent/config.py`, `nexus_agent/skill_loader.py`, `nexus_agent/sync.py`, `nexus_agent/visual_director.py`, `nexus_agent/rendered_video_qa.py`, `nexus_agent/vision_review.py`, `nexus_agent/scene_repair.py`, `requirements.txt`, `pyproject.toml` | Verify JSON schemas produced for downstream stages and keep `requirements.txt` + `pyproject.toml` synchronized. |
| **High (Careful review + approval)** | `nexus_agent/orchestrator.py`, `nexus_agent/mcp_server.py`, `nexus_agent/tools_resolver.py`, `nexus_agent/tools.py`, `.mcp.json`, `.claude/settings.json` | Core orchestration, path traversal guards, MCP return schema contracts (`session_id`/`id`/`job_id`), and Windows binary resolution live here. |
| **Protected (Do NOT modify/expose)** | `.env`, `.gitignore`, `.claude/settings.local.json`, local backups (`.zip`) | Never log, display, commit, or overwrite `.env` secrets. |

---

## 4. Coding Conventions & Common Failure Patterns

- **Windows OS checks:** Always use `sys.platform == "win32"` (never `os.name == "win32"`, because Python sets `os.name == "nt"` on Windows).
- **Virtualenv priority:** `tools_resolver.py` checks the active interpreter's `Scripts/` directory before global `PATH` so `.venv\Scripts\python.exe` uses `.venv\Scripts\manim.exe`.
- **Encoding:** Write all Python/JSON files as **UTF-8 without BOM** (`encoding="utf-8"`). `.mcp.json` sets `PYTHONIOENCODING=utf-8` so Manim/subprocess Unicode logs do not crash MCP stdio transport on Windows.
- **Legacy test scripts nuance:** `nexus_agent/test_phase4_audio.py` and `nexus_agent/test_phase4_2_post.py` are standalone `main()` scripts (not `unittest.TestCase` classes); see [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md#6-test-matrix) before citing them as `unittest` passes.
- **LLM Endpoint difference:** Python OpenAI SDK (`.env` `NEXUS_API_URL`) requires `/v1` (`https://nexusapi.navigatelabs.ai/v1`); Claude Code Anthropic env (`ANTHROPIC_BASE_URL`) omits `/v1`.

---

## 5. Keeping Docs Synchronized After Code Changes

When you change code, update the canonical doc listed in [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md) in the same task:
- Changed CLI flags (`cli.py`) -> [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md)
- Changed MCP tools/schemas (`mcp_server.py`) -> [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md) + [`.claude/settings.json`](.claude/settings.json) check
- Changed env vars (`config.py`, `vision_review.py`, etc.) -> [`.env.example`](.env.example), [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md), [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md)
- Changed pipeline stages or modules -> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) + [`AGENTS.md`](AGENTS.md)
- Changed test counts or dependencies -> [`README.md`](README.md), [`docs/SETUP.md`](docs/SETUP.md), [`docs/DEVELOPER_REFERENCE.md`](docs/DEVELOPER_REFERENCE.md)
