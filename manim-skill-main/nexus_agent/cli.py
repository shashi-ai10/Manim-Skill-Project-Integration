"""Command-line interface for the Nexus Unified Video Production Agent."""

import argparse
import os
import re
import sys
from pathlib import Path

from .config import load_nexus_config
from .tools import SafeToolExecutor
from .agent import NexusAgent
from .orchestrator import VideoProductionOrchestrator


def slugify(text: str) -> str:
    """Convert prompt to kebab-case slug."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:32] if s else "video-project"


def main():
    parser = argparse.ArgumentParser(
        description="Nexus Unified Agent for Educational & Mathematical Manim Animations."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Animation topic or educational request. If omitted, you will be prompted interactively."
    )
    parser.add_argument(
        "--session-slug",
        default=None,
        help="Custom kebab-case project slug for session storage (default: derived from topic)."
    )
    parser.add_argument(
        "--session-dir",
        default=None,
        help="Explicit directory path for session storage (overrides --session-slug)."
    )
    parser.add_argument(
        "--scenes",
        type=int,
        default=5,
        help="Target number of sequential educational scenes (default: 5)."
    )
    parser.add_argument(
        "--target-duration",
        "--duration",
        type=int,
        default=None,
        help="Target video duration in seconds (e.g. 60, 120, 180)."
    )
    parser.add_argument(
        "--voice",
        default="af_heart",
        help="Kokoro TTS voice identifier (default: af_heart)."
    )
    parser.add_argument(
        "--resume-stage",
        type=int,
        default=1,
        help="Resume pipeline starting from specific stage index (1 to 12)."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override NEXUS_MODEL environment configuration."
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to custom .env file."
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Automatically approve gates and tool executions (useful for unattended testing)."
    )
    parser.add_argument(
        "--legacy-loop",
        action="store_true",
        help="Use legacy freeform tool-calling loop instead of the structured 11-stage pipeline."
    )

    args = parser.parse_args()

    # Interactive prompt if not supplied
    topic = args.prompt
    if not topic:
        print("\n" + "=" * 65)
        print("NEXUS EDUCATIONAL VIDEO PRODUCTION AGENT")
        print("=" * 65)
        try:
            topic = input("\nWhat educational topic should we create a video about?\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

        if not topic:
            topic = "Explain Gradient Descent to beginners."
            print(f"Using default topic: \"{topic}\"")

    # Determine session slug and folder
    slug = args.session_slug or slugify(topic)
    env_path = Path(args.env_file) if args.env_file else None
    config = load_nexus_config(env_file=env_path, session_slug=slug)
    if args.session_dir:
        config.session_root = Path(args.session_dir).resolve()
    if args.model:
        config.model = args.model

    print("\n" + "=" * 65)
    print("NEXUS PRODUCTION RUNTIME")
    print(f"API Endpoint:      {config.api_url}")
    print(f"API Key:           {config.masked_api_key()}")
    print(f"Model:             {config.model or '<NOT SPECIFIED>'}")
    print(f"Session Directory: {config.session_root}")
    print(f"Auto-Approve:      {args.auto_approve}")
    print(f"Target Scenes:     {args.scenes}")
    print(f"Kokoro Voice:      {args.voice}")
    if args.resume_stage > 1:
        print(f"Resuming From:     Stage {args.resume_stage}")
    print("=" * 65)

    if not config.api_key:
        print("\n[ERROR] NEXUS_API_KEY is not set.")
        print("Please configure NEXUS_API_KEY in your environment or a .env file.")
        sys.exit(1)

    if not config.model:
        print("\n[ERROR] NEXUS_MODEL is not set.")
        print("Please configure NEXUS_MODEL in your environment or specify via --model.")
        sys.exit(1)

    if args.legacy_loop:
        # Legacy freeform tool execution
        repo_dir = Path(__file__).resolve().parent.parent

        def legacy_approval(action_type: str, details: str) -> bool:
            if args.auto_approve:
                return True
            print(f"\n[APPROVAL GATE - {action_type.upper()}]")
            print(details)
            choice = input("Approve action? [y/N]: ").strip().lower()
            return choice in ("y", "yes")

        executor = SafeToolExecutor(
            session_dir=config.session_root,
            repo_dir=repo_dir,
            approval_callback=legacy_approval,
            secret_patterns={config.api_key},
        )
        agent = NexusAgent(config=config, executor=executor)
        result = agent.run(topic)
        print(f"Execution finished: Success={result.get('success')}")
        return

    # Unified 11-Stage Production Pipeline
    orchestrator = VideoProductionOrchestrator(
        config=config,
        session_dir=config.session_root,
        auto_approve=args.auto_approve,
        voice=args.voice,
        target_scenes=args.scenes,
        target_duration_s=args.target_duration,
    )

    try:
        results = orchestrator.run_pipeline(
            topic=topic,
            resume_from_stage=args.resume_stage,
        )
    except Exception as e:
        print(f"\n[PIPELINE ERROR] Execution failed: {e}")
        import traceback
        traceback.print_exc()
        # orchestrator.run_pipeline already persists exact failure stage & diagnostics to session_state.json.
        # Ensure state file is updated only if not already marked as failed.
        try:
            if not orchestrator.state_file.exists():
                orchestrator._save_state(
                    current_stage=args.resume_stage,
                    completed_stages=[],
                    status="failed",
                    error=str(e),
                )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
