"""Utilities for shell command execution: allowlist, history, pending approvals."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default safe commands that can skip approval (read-only, low risk)
DEFAULT_SAFE_COMMANDS = ["ls", "df", "pwd", "whoami", "date", "uptime", "echo", "which", "type"]

# Pending exec requests for remote approval (user_id -> {command, cwd, timestamp})
_pending_exec: Dict[str, Dict[str, Any]] = {}

EXEC_HISTORY_PATH = Path.home() / ".grizzyclaw" / "exec_history.json"
EXEC_HISTORY_MAX = 20


def _base_command(cmd: str) -> str:
    """Extract base command name (first token, basename if path)."""
    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    parts = cmd.split()
    first = parts[0] if parts else ""
    if "/" in first:
        return Path(first).name
    return first


def is_safe_command(command: str, allowlist: Optional[List[str]] = None) -> bool:
    """Check if command is in the safe allowlist (by base command name)."""
    allowlist = allowlist or DEFAULT_SAFE_COMMANDS
    base = _base_command(command)
    return base.lower() in [a.lower() for a in allowlist]


def run_shell_command(command: str, cwd: Optional[str] = None, timeout: int = 60) -> str:
    """Run a shell command and return output. Used for allowlist and remote approval."""
    cwd_path = Path(cwd).expanduser() if cwd else Path.home()
    if not cwd_path.exists():
        cwd_path = Path.home()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd_path),
        )
        out = result.stdout or ""
        err = result.stderr or ""
        combined = (out + "\n" + err).strip() if err else out
        if result.returncode != 0:
            combined = f"(exit {result.returncode})\n{combined}"
        return combined or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds"
    except Exception as e:
        logger.exception("Exec command error")
        return f"Error: {e}"


def set_pending(user_id: str, command: str, cwd: Optional[str] = None) -> None:
    """Store a pending exec request for remote approval."""
    import time

    _pending_exec[user_id] = {
        "command": command,
        "cwd": cwd,
        "timestamp": time.time(),
    }


def get_and_clear_pending(user_id: str) -> Optional[Dict[str, Any]]:
    """Get and remove pending exec for user. Returns None if none."""
    return _pending_exec.pop(user_id, None)


def has_pending(user_id: str) -> bool:
    """Check if user has a pending exec request."""
    return user_id in _pending_exec


def add_to_history(command: str, cwd: Optional[str] = None) -> None:
    """Append command to history file."""
    EXEC_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    history: List[Dict[str, str]] = []
    if EXEC_HISTORY_PATH.exists():
        try:
            with open(EXEC_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    entry = {"command": command, "cwd": cwd or str(Path.home())}
    # Avoid duplicate of last
    if history and history[-1].get("command") == command and history[-1].get("cwd") == entry.get("cwd"):
        return
    history.append(entry)
    history = history[-EXEC_HISTORY_MAX:]
    try:
        with open(EXEC_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save exec history: %s", e)


def get_history() -> List[Dict[str, str]]:
    """Load command history (most recent last)."""
    if not EXEC_HISTORY_PATH.exists():
        return []
    try:
        with open(EXEC_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
