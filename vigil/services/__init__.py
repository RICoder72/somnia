"""
Vigil Services — Platform-agnostic external integrations.

Each service follows the adapter pattern:
- interface.py: ABC + dataclasses defining the contract
- manager.py: Account CRUD, adapter registry, routing
- adapters/: Platform-specific implementations
- tools.py: register(mcp) function for MCP tool registration
"""

from pathlib import Path
import json

CONFIG_DIR = Path("/data/config")
USER_SETTINGS_FILE = CONFIG_DIR / "user_settings.json"

DEFAULT_USER_SETTINGS = {
    "timezone": "America/New_York",
    "locale": "en-US",
    "date_format": "12h"
}


def get_user_timezone() -> str:
    """Get user's configured timezone."""
    try:
        if USER_SETTINGS_FILE.exists():
            settings = json.loads(USER_SETTINGS_FILE.read_text())
            return settings.get("timezone", DEFAULT_USER_SETTINGS["timezone"])
    except Exception:
        pass
    return DEFAULT_USER_SETTINGS["timezone"]


def get_user_settings() -> dict:
    """Get all user settings."""
    try:
        if USER_SETTINGS_FILE.exists():
            return json.loads(USER_SETTINGS_FILE.read_text())
    except Exception:
        pass
    return DEFAULT_USER_SETTINGS.copy()
