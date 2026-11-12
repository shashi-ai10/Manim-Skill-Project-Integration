"""Tool definitions and safety validation for Nexus Agent Runtime."""

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Set

DEFAULT_TIMEOUT_S = 60
MAX_OUTPUT_CHARS = 10000

# Type for user approval callback: takes (action_type, description) -> returns bool
ApprovalCallback = Callable[[str, str], bool]


class SecurityError(Exception):
    """Raised when a tool action violates path traversal or safety boundaries."""
    pass


class ApprovalDeniedError(Exception):
    """Raised when an action is rejected by the approval gate."""
    pass


def mask_secrets(text: str, secret_patterns: Optional[Set[str]] = None) -> str:
    """Mask known secrets and API keys from text outputs."""
    if not text:
        return ""
    masked = text
    if secret_patterns:
        for secret in secret_patterns:
            if secret and len(secret) >= 4:
                masked = masked.replace(secret, "[REDACTED_SECRET]")

    # Mask common API key formats (sk-..., Bearer ...)
    masked = re.sub(r"(sk-[a-zA-Z0-9_-]{20,})", r"sk-[REDACTED]", masked)
    masked = re.sub(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}", r"\1[REDACTED]", masked)
    return masked


class SafeToolExecutor:
    """
    Executes tool actions with path validation, approval gates, timeouts,
    and secret redaction.
    """

    def __init__(
        self,
        session_dir: Path,
        repo_dir: Path,
        approval_callback: Optional[ApprovalCallback] = None,
        secret_patterns: Optional[Set[str]] = None,
        command_timeout_s: int = DEFAULT_TIMEOUT_S,
        max_output_chars: int = MAX_OUTPUT_CHARS,
    ):
        self.session_dir = session_dir.resolve()
        self.repo_dir = repo_dir.resolve()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.approval_callback = approval_callback or self._default_prompt_approval
        self.secret_patterns = secret_patterns or set()
        self.command_timeout_s = command_timeout_s
        self.max_output_chars = max_output_chars

    @staticmethod
    def _default_prompt_approval(action_type: str, details: str) -> bool:
        """Interactive prompt for human approval."""
        print(f"\n[APPROVAL REQUIRED - {action_type.upper()}]")
        print(details)
        try:
            choice = input("Approve this action? [y/N]: ").strip().lower()
            return choice in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def _validate_path_for_read(self, raw_path: str) -> Path:
        """Validate that path exists and is within allowed boundaries (session or repo)."""
        target = Path(raw_path)
        if not target.is_absolute():
            # If relative, prefer session_dir, fallback to repo_dir
            if (self.session_dir / target).exists():
                target = (self.session_dir / target).resolve()
            else:
                target = (self.repo_dir / target).resolve()
        else:
            target = target.resolve()

        # Path traversal check
        is_in_session = self.session_dir in target.parents or target == self.session_dir
        is_in_repo = self.repo_dir in target.parents or target == self.repo_dir

        if not (is_in_session or is_in_repo):
            raise SecurityError(
                f"Path traversal denied: '{raw_path}' is outside session ({self.session_dir}) and repo ({self.repo_dir})"
            )
        return target

    def _validate_path_for_write(self, raw_path: str) -> Path:
        """
        Validate that file write is strictly confined to session directory.
        Never allow writing into repository or arbitrary filesystem locations.
        """
        target = Path(raw_path)
        if not target.is_absolute():
            target = (self.session_dir / target).resolve()
        else:
            target = target.resolve()

        if self.session_dir not in target.parents and target != self.session_dir:
            raise SecurityError(
                f"Write access restricted: file writes are strictly confined to session directory ({self.session_dir}). "
                f"Attempted: '{raw_path}'"
            )
        return target

    def read_file(self, path: str) -> str:
        """Safely read text file with UTF-8 encoding and output clipping."""
        validated_path = self._validate_path_for_read(path)
        if not validated_path.is_file():
            return f"Error: File '{path}' does not exist."

        try:
            content = validated_path.read_text(encoding="utf-8", errors="replace")
            content = mask_secrets(content, self.secret_patterns)
            if len(content) > self.max_output_chars:
                return content[:self.max_output_chars] + f"\n... [Truncated: {len(content)} total characters]"
            return content
        except Exception as e:
            return f"Error reading file '{path}': {e}"

    def write_file(self, path: str, content: str) -> str:
        """Write text file into session directory with explicit user approval."""
        validated_path = self._validate_path_for_write(path)

        preview = content[:200] + ("..." if len(content) > 200 else "")
        details = f"Target File: {validated_path}\nBytes to write: {len(content.encode('utf-8'))}\nPreview:\n{preview}"

        if not self.approval_callback("write_file", details):
            raise ApprovalDeniedError(f"User denied approval to write '{validated_path.name}'")

        try:
            validated_path.parent.mkdir(parents=True, exist_ok=True)
            validated_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} characters to '{validated_path}'."
        except Exception as e:
            return f"Error writing file '{path}': {e}"

    def list_files(self, directory: Optional[str] = None) -> List[str]:
        """Safely list files inside session or allowed repo directory."""
        if directory:
            target = self._validate_path_for_read(directory)
        else:
            target = self.session_dir

        if not target.is_dir():
            return [f"Error: '{target}' is not a directory."]

        results = []
        try:
            for item in sorted(target.iterdir()):
                rel = item.relative_to(target)
                suffix = "/" if item.is_dir() else ""
                results.append(f"{rel}{suffix}")
            return results
        except Exception as e:
            return [f"Error listing directory '{target}': {e}"]

    def run_command(self, command: str, working_dir: Optional[str] = None) -> str:
        """
        Execute command with user approval, timeout, and output sanitization.
        Note: Setting working_dir does NOT sandbox the shell. Command safety relies on
        explicit human approval gate and timeout monitoring.
        """
        cwd = self._validate_path_for_read(working_dir) if working_dir else self.session_dir

        details = f"Command: {command}\nWorking Directory: {cwd}\nTimeout: {self.command_timeout_s}s"
        if not self.approval_callback("run_command", details):
            raise ApprovalDeniedError(f"User denied approval to execute command: '{command}'")

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["HF_HUB_OFFLINE"] = "1"

        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_s,
                env=env,
                encoding="utf-8",
                errors="replace",
            )
            raw_output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                raw_output += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                raw_output += f"STDERR:\n{result.stderr}\n"

            cleaned = mask_secrets(raw_output, self.secret_patterns)
            if len(cleaned) > self.max_output_chars:
                return cleaned[:self.max_output_chars] + f"\n... [Output truncated at {self.max_output_chars} chars]"
            return cleaned
        except subprocess.TimeoutExpired:
            return f"Command execution timed out after {self.command_timeout_s} seconds."
        except Exception as e:
            return f"Command execution error: {e}"

