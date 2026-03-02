"""
Shell execution utilities with safety guards.
"""

import subprocess
import re
import logging
from pathlib import Path

from config import DATA_ROOT

logger = logging.getLogger(__name__)

# Patterns that should never be executed
BLOCKED_PATTERNS = [
    r'\brm\s+-rf\s+/',
    r'\brm\s+-rf\s+~',
    r'\brm\s+-rf\s+\*',
    r'\brmdir\s+/',
    r'>\s*/dev/sd',
    r'\bmkfs\b',
    r'\bdd\s+.*of=/',
    r':\(\)\{.*\};:',
]


def is_blocked(command: str) -> tuple[bool, str]:
    """Check if a command matches any blocked pattern."""
    command_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command_lower):
            return True, f"Matches blocked pattern: {pattern}"
    return False, ""


def run(command: str, timeout: int = 30, cwd: Path = None) -> tuple[bool, str]:
    """Execute a shell command safely. Returns (success, output)."""
    if cwd is None:
        cwd = DATA_ROOT

    blocked, reason = is_blocked(command)
    if blocked:
        logger.warning(f"Blocked command: {command} - {reason}")
        return False, f"❌ Command blocked for safety: {reason}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return result.returncode == 0, output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return False, f"❌ Command timed out after {timeout}s"
    except Exception as e:
        return False, f"❌ Error: {e}"


def run_simple(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return just the output string."""
    _, output = run(command, timeout)
    return output
