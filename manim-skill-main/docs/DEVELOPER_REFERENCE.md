# Developer & Maintainer Reference

This document is the canonical technical reference for the **Nexus Manim Video Production Engine**. Student onboarding lives in [`docs/SETUP.md`](SETUP.md) and [`docs/CONFIGURATION.md`](CONFIGURATION.md); system diagrams live in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Repository & Package Structure

### Root Files
| File / Folder | Purpose |
| :--- | :--- |
| [`README.md`](../README.md) | Overview, software prerequisites table, Windows quickstart |
| [`AGENTS.md`](../AGENTS.md) | AI assistant onboarding, safety boundaries, and doc-sync rules |
| [`requirements.txt`](../requirements.txt) & [`pyproject.toml`](../pyproject.toml) | Python dependencies (`>=3.10`, Manim, Kokoro, OpenAI SDK, Pydantic, Pillow, MCP SDK) |
| [`.env.example`](../.env.example) | Template for local `.env` credentials (`NEXUS_API_*`, optional `MANIM_SKILL_HOME`) |
| [`.mcp.json`](../.mcp.json) | Stdio MCP server definition for `nexus-video-production` |
| [`.claude/settings.json`](../.claude/settings.json) | Project MCP permission allowlist (`ping`, `create_educational_video`, `get_video_status`, `get_video_outputs`) |
| [`.claude-plugin/`](../.claude-plugin/) | Upstream Claude Code plugin metadata (`manim-skill`, MIT) |
| [`test_manim.py`](../test_manim.py) | Minimal no-LaTeX `SquareToCircle` Manim smoke scene |
| [`manim_skill.gif`](../manim_skill.gif) | Sample README animation asset |

### `nexus_agent/` Modules
| Module | Responsibility |
| :--- | :--- |
| `cli.py` | CLI entrypoint (`main()`, `slugify()`), loads `NexusConfig`, runs `VideoProductionOrchestrator` or legacy `NexusAgent` |
| `mcp_server.py` | `MCPServer("nexus-video-production")` exposing 5 stdio MCP tools and spawning background CLI workers |
| `orchestrator.py` | `VideoProductionOrchestrator` (Stages 1–12 + sub-stages `5.5` & `11.5`), `ProductionArtifacts`, AST `lint_manim_code()` |
| `config.py` | `NexusConfig` dataclass and `load_nexus_config()` + `masked_api_key()` secret masking |
| `tools_resolver.py` | Cross-platform resolver (`validate_required_executables()`, `resolve_executable()`, `get_manim_cmd()`, `validate_media_file()`) |
| `skill_loader.py` | `SkillLoader` & `LoadedSkill` mapping production stages to `skills/*/SKILL.md` and `references/` |
| `visual_director.py` | `TopicAwareVisualDirector` & `validate_visual_plan()` structured storyboard generator (Stage 5.5) |
| `sync.py` | `TimelineManager`, `TeachingBeat`, `SceneTiming`, `decompose_narration_into_beats()`, `generate_manim_template()` |
| `rendered_video_qa.py` | `RenderedVideoQA` (`ffprobe` stream specs, `freezedetect` static-freeze checks, SRT order checks, `RENDERED_VIDEO_QA_REPORT.md`) |
| `vision_review.py` | `VisualQualityReviewer` & `run_visual_quality_review_pipeline()` (multimodal contact-sheet & high-res frame inspection) |
| `contact_sheet.py` | `generate_contact_sheet()` & `extract_high_res_frame()` via FFmpeg + Pillow overlays |
| `scene_repair.py` | `SceneRepairManager` (`extract_scene_class_code()`, `repair_single_scene()`) surgical single-scene repair loop |
| `agent.py` & `tools.py` | Legacy freeform OpenAI tool-calling loop (`NexusAgent`, `NEXUS_TOOL_DEFINITIONS`) + `SafeToolExecutor` sandboxing |
| `run_acceptance_test.py` | Live end-to-end MCP acceptance runner (`run_conversational_acceptance_test`) |
| `audio/` | `dsp.py` (pure-Python oscillators/filters/reverb), `sfx_synth.py` (`click`, `whoosh`, `chime`, etc.), `mixer.py` (`AudioMixer` speech ducking & ITU-R BS.1770), `engine.py` (`SoundDesignEngine`) |
| `compositor/` | `pipeline.py` (`PostProductionPipeline`), `loudnorm.py` (`TwoPassLoudnorm` `-14.0 LUFS` / `-1.0 dBTP`), `subtitles.py` (`build_master_srt_from_timeline`), `overlays.py` (`concat_video_segments`, `build_composite_filtergraph`), `boundary_fades.py` (30ms edge fades) |

---

## 2. Environment Variables

| Variable | Required | Default | Used By | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `NEXUS_API_URL` | Yes | `https://nexusapi.navigatelabs.ai/v1` | `config.py`, `test_vision.py` | OpenAI-compatible Nexus endpoint (must include `/v1`) |
| `NEXUS_API_KEY` | Yes | `""` | `config.py`, `test_vision.py` | Navigate Labs Nexus bearer key |
| `NEXUS_MODEL` | Yes | `""` *(orchestrator falls back to `gemini-3.1-flash-lite` if empty)* | `config.py`, `orchestrator.py`, `vision_review.py`, `test_vision.py` | Model ID (e.g., `claude-haiku-4-5`) |
| `MANIM_SKILL_HOME` | No | `./sessions` | `config.py` | Overrides base output root for CLI runs (`<MANIM_SKILL_HOME>/<session_slug>/`) |
| `SKIP_NEXUS_API` | No | unset | `vision_review.py`, `test_visual_engine.py` | Set to `1` in offline tests/CI to bypass live Nexus Vision calls |
| `PYTHONIOENCODING` | No | system default | `.mcp.json` | Forced to `utf-8` for the MCP stdio subprocess on Windows |

> **Claude Code Environment Note:** `~/.claude/settings.json` sets `ANTHROPIC_BASE_URL` (no `/v1`), `ANTHROPIC_API_KEY`, and `ANTHROPIC_*_MODEL` separately for interactive Claude Code sessions. See [`docs/CONFIGURATION.md`](CONFIGURATION.md).

---

## 3. CLI Reference (`nexus_agent/cli.py`)

Invoke via:
```powershell
python -m nexus_agent.cli [prompt] [options]
```

| Flag | Default | Description |
| :--- | :--- | :--- |
| `prompt` | interactive input (`"Explain Gradient Descent to beginners."` on blank Enter) | Educational topic or animation prompt |
| `--session-slug` | `slugify(prompt)` (kebab-case, max 32 chars) | Custom folder name under `MANIM_SKILL_HOME` / `./sessions` |
| `--session-dir` | `None` | Explicit directory path for outputs (overrides `--session-slug` & `MANIM_SKILL_HOME`) |
| `--scenes` | `5` | Target number of educational scenes |
| `--target-duration`, `--duration` | `None` (auto/standard) | Target duration in seconds (e.g., `60`, `90`, `120`) |
| `--voice` | `af_heart` | Kokoro TTS voice identifier |
| `--resume-stage` | `1` | Resume existing session starting from stage `1`..`12` |
| `--model` | `.env` `NEXUS_MODEL` | Override `NEXUS_MODEL` for this run |
| `--env-file` | auto `.env` lookup | Custom path to an environment file |
| `--auto-approve` | `False` | Skip interactive approval gates at Stages 3 and 7 |
| `--legacy-loop` | `False` | Run `NexusAgent` freeform tool-calling loop instead of the structured pipeline |

---

## 4. MCP Server & Tool Contracts (`nexus_agent/mcp_server.py`)

Configured in [`.mcp.json`](../.mcp.json) as `nexus-video-production` running `python -m nexus_agent.mcp_server` with stdio transport. Session root is fixed at `WORKSPACE_DIR / "sessions"` (`SESSIONS_DIR`).

| Tool | Key Parameters & Defaults | Behavior & Return Highlights |
| :--- | :--- | :--- |
| `ping` | `message: Optional[str] = "pong"` | Instant health check (`status: "ok"`, `echo`, `timestamp`) |
| `create_educational_video` | `topic: str`, `duration="2-3 minutes"`, `audience="beginners"`, `visual_style="clean dark-mode"`, `narration_style="clear, engaging educational pacing"`, `subtitles=True`, `scenes_count=5`, `session_id=None`, `wait_for_completion=True`, `timeout_seconds=600`, `dry_run=False` | Creates `<slug>-YYYYMMDD-HHMMSS`, spawns `cli.py --auto-approve`. Returns `session_id`, `id`, `job_id`, `status`, `final_mp4_path`, `duration_s`, `vision_qa_score`, `outputs`. Ignores bogus Claude task IDs/PIDs cleanly |
| `get_video_status` | `session_id=None`, aliases `id`, `job_id`, `task_id` | Supports `"latest"`. Inspects live PID (`_is_pid_alive`), `session_state.json`, and `production.log`; auto-marks dead-PID incomplete jobs as `"failed"` |
| `get_video_outputs` | `session_id=None`, aliases `id`, `job_id`, `task_id` | Supports `"latest"`. Returns only physically existing files in `files` plus extracted metrics (`vision_qa_score`, `rendered_qa_composite_score`, `total_duration_s`, `duration_deviation_*`, FFmpeg loudness metrics) |
| `launch_video_viewer` | `session_id=None`, aliases `id`, `job_id`, `task_id`, `port=0`, `open_browser=True` | Launches [`skills/manim-skill/tools/video_viewer.py`](../skills/manim-skill/tools/video_viewer.py) on an available localhost port and returns `viewer_url` |

> **Allowlist Note:** Project [`.claude/settings.json`](../.claude/settings.json) currently pre-approves `ping`, `create_educational_video`, `get_video_status`, and `get_video_outputs`. `launch_video_viewer` will prompt for tool approval in Claude Code unless added to `.claude/settings.json`.

---

## 5. Session Directories, Deliverables & Debugging

- **CLI output path:** `MANIM_SKILL_HOME/<session_slug>/` (defaults to `./sessions/<session_slug>/`) unless `--session-dir` is given.
- **MCP output path:** `./sessions/<topic-slug>-YYYYMMDD-HHMMSS/`.

| Artifact | Producer | Purpose |
| :--- | :--- | :--- |
| `broadcast_subtitled.mp4` / `broadcast.mp4` | Stage 10 (`PostProductionPipeline`) | Final `1920x1080` `30fps` H.264/AAC outputs (with and without burned-in subtitles; `-14.0 LUFS`) |
| `narration.txt` & `voiceover.mp3` | Stage 4 (`tts-generate.py`) | Narration `SubtitleSpec` list and Kokoro-synthesized speech audio track |
| `audio_master.wav` | Stage 9 (`SoundDesignEngine`), Stage 10 | Mixed narration + speech-ducked procedural SFX master exported via `AudioMixer.export_wav()` |
| `subtitles.srt` | Stage 10 (`subtitles.py`) | Beat-aligned SRT captions (`MarginV=90` safe styling) |
| `script_breakdown.json` | Stage 2 | Topic scene list, narrations, and planned visuals |
| `timestamps.json` & `scene_timeline.json` | Stages 4–5 | Measured TTS durations and multi-beat `TeachingBeat` allocations |
| `visual_direction.json` | Stage 5.5 | Structured storyboard layout (`SceneVisualPlan`) |
| `script.py` | Stage 6 | Generated Manim Python scene classes |
| `contact_sheet.png` | Stage 11 (`contact_sheet.py`) | Multi-scene inspection grid with overlays |
| `RENDERED_VIDEO_QA_REPORT.md` / `rendered_video_qa.json` | Stage 11 (`RenderedVideoQA`) | `ffprobe` specs, freeze intervals, SRT validation, composite QA score |
| `VISUAL_QUALITY_REPORT.md` / `visual_quality_review.json` | Stage 11 (`VisualQualityReviewer`) | Vision LLM inspection scores & scene critiques |
| `session_state.json`, `mcp_metadata.json`, `production.log` | Orchestrator & MCP server | Live stage progress (`current_stage` `1..12`), subprocess PID, full execution logs |

**Debugging failed sessions:**
1. Inspect `session_state.json` (`status`, `current_stage`, `error`) and tail `production.log`.
2. Verify external tools via `python -c "from nexus_agent.tools_resolver import validate_required_executables; print(validate_required_executables())"`.
3. Resume cleanly without redoing earlier stages via `python -m nexus_agent.cli "<topic>" --session-dir sessions/<id> --resume-stage <N> --auto-approve`.

---

## 6. Test Matrix

| Suite / Command | Test Count | Network / Cost | Notes |
| :--- | :--- | :--- | :--- |
| `python -m unittest nexus_agent.test_mcp nexus_agent.test_upgraded_engine nexus_agent.test_visual_engine nexus_agent.test_ffmpeg_recovery nexus_agent.test_unified_engine -v` | **51** `unittest` cases | **Offline / Free** | Uses temp dirs and mocked `SESSIONS_DIR`. Safe for CI and frequent runs |
| `python -m unittest nexus_agent.test_nexus.TestNexusIntegration.test_01_api_connectivity` | **1** case | **Online / Free** | Queries Nexus `/models` only; requires valid `.env` |
| `python -m unittest nexus_agent.test_nexus` (`test_02`–`test_10`) | **10** cases total (`9` beyond `test_01`) | **Live / Uses Credits (02–03)** | Tests completions, tool calling, `SafeToolExecutor`, Manim, Kokoro, and `video_viewer.py` |
| `python -m nexus_agent.test_vision` | **1** check | **Online / Uses Credits** | Sends a 50x50 test PNG to `NEXUS_MODEL` |
| `python -m nexus_agent.run_acceptance_test` | Live E2E check | **Online / Expensive** | Triggers a full MCP video generation job |
| `nexus_agent/test_phase4_audio.py`, `nexus_agent/test_phase4_2_post.py` | 3 & 4 script functions (`0` `TestCase`s) | Local script checks | Legacy `main()` scripts expecting standalone root sample files removed in cleanup; **do not** run cleanly as fresh-clone `unittest` suites |

---

## 7. Maintenance & Contribution Tasks

- **Adding a Skill:** Create `skills/<skill-name>/SKILL.md` (with optional `references/` or scripts), map it inside `SkillLoader.STAGE_SKILL_MAPPING` in [`nexus_agent/skill_loader.py`](../nexus_agent/skill_loader.py), and verify loading via `test_upgraded_engine.TestSkillLoader`.
- **Adding an MCP Tool:** Add an `@server.tool()` function in [`nexus_agent/mcp_server.py`](../nexus_agent/mcp_server.py), update [`test_unified_engine.py`](../nexus_agent/test_unified_engine.py) (`test_05_registered_mcp_tools`) and [`.claude/settings.json`](../.claude/settings.json), then update Section 4 above.
- **Modifying / Resuming Scenes:** Edit `sessions/<id>/script.py` or fix a scene manually, render just that class with `manim -qh script.py <SceneClass>`, and run `--resume-stage 8` or `10` to re-mux/re-master without calling Stage 2 or Stage 6 LLMs.
- **Switching LLM Models:** Update `NEXUS_MODEL` in `.env` (and `ANTHROPIC_*_MODEL` in `~/.claude/settings.json` for Claude Code), verify `/models` connectivity (`test_01_api_connectivity`), and verify multimodal support with `python -m nexus_agent.test_vision`.
- **Updating Dependencies:** Keep both [`requirements.txt`](../requirements.txt) and [`pyproject.toml`](../pyproject.toml) synchronized, then test installation in a clean temporary `.venv`.
- **Contribution Rules:** Preserve no-LaTeX compatibility, use `sys.platform == "win32"` (never `os.name == "win32"`), write UTF-8 without BOM, keep unit tests free of local `sessions/` fixtures, and run the 51 offline unit tests before committing.
