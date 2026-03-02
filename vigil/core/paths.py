"""
Path validation — ensure all file operations stay within the sandbox.
"""

from pathlib import Path

from config import DATA_ROOT


def validate(path: str) -> Path:
    """Resolve path and ensure it's within DATA_ROOT. Raises ValueError if not."""
    if path.startswith("/"):
        resolved = Path(path).resolve()
    else:
        resolved = (DATA_ROOT / path).resolve()

    if not str(resolved).startswith(str(DATA_ROOT)):
        raise ValueError(f"Path outside sandbox: {path}")
    return resolved
