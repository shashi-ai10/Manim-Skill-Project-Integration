"""Configuration loader for Navigate Labs Nexus API integration."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

DEFAULT_NEXUS_API_URL = "https://nexusapi.navigatelabs.ai/v1"


@dataclass
class NexusConfig:
    api_url: str
    api_key: str
    model: str
    session_root: Path

    def masked_api_key(self) -> str:
        """Return masked key for logging without exposing credentials."""
        if not self.api_key:
            return "<UNSET>"
        if len(self.api_key) <= 8:
            return "***"
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"


def load_nexus_config(
    env_file: Optional[Path] = None,
    session_slug: str = "nexus-session"
) -> NexusConfig:
    """
    Load Nexus configuration from environment variables or .env file.
    Never hardcodes credentials or exposes the secret key.
    """
    if env_file and env_file.exists():
        load_dotenv(dotenv_path=env_file)
    else:
        # Search current working directory and user home
        load_dotenv()
        local_env = Path(".env")
        if local_env.exists():
            load_dotenv(dotenv_path=local_env)

    api_url = os.getenv("NEXUS_API_URL", DEFAULT_NEXUS_API_URL).rstrip("/")
    api_key = os.getenv("NEXUS_API_KEY", "")
    model = os.getenv("NEXUS_MODEL", "")

    # Session root defaults to ./sessions/<session_slug> or MANIM_SKILL_HOME
    manim_skill_home = os.getenv("MANIM_SKILL_HOME")
    if manim_skill_home:
        session_root = Path(manim_skill_home) / session_slug
    else:
        session_root = Path.cwd() / "sessions" / session_slug

    return NexusConfig(
        api_url=api_url,
        api_key=api_key,
        model=model,
        session_root=session_root
    )

