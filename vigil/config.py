"""
Vigil configuration — paths, URLs, and constants.

All paths are from the container's perspective (/data mount).
"""

import os
from pathlib import Path

# Base paths
DATA_ROOT = Path("/data")
DOMAINS_DIR = DATA_ROOT / "domains"         # DEPRECATED - migrating to workspaces
WORKSPACES_DIR = DATA_ROOT / "workspaces"
CONFIG_DIR = DATA_ROOT / "config"
OUTPUTS_DIR = DATA_ROOT / "outputs"

# Docker network (Somnia)
DOCKER_NETWORK = "mcp-net"

# Public URL for published outputs (served via Somnia router)
# Set SOMNIA_PUBLIC_URL to your host's base URL (e.g. https://myhost.example.com)
_public_url = os.environ.get("SOMNIA_PUBLIC_URL", "http://localhost")
PUBLIC_BASE_URL = f"{_public_url}/output"

# Share publish system
PUBLISH_DIR         = DATA_ROOT / "publish"          # /data/publish (shared volume)
PUBLIC_SHARE_BASE_URL = f"{_public_url}/p"  # public /p/{uuid} route

# Domain config
DOMAIN_TRIGGERS_FILE = CONFIG_DIR / "domain_triggers.json"

# Storage config (for Phase B — services)
STORAGE_CONFIG = CONFIG_DIR / "storage_accounts.json"

# Database (shared Somnia PostgreSQL)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://somnia:changeme@somnia-postgres:5432/somnia",
)
# Scope enforcement mode (Phase 2 workspace bindings)
#   advisory  — scope violations log a warning but tool proceeds (default)
#   enforce   — scope violations raise and tool fails
# Flip to "enforce" once advisory has been observed to fire only on genuine
# violations (not false positives from cross-workspace work). Env override:
# SCOPE_MODE=enforce or SCOPE_MODE=advisory
SCOPE_MODE = os.environ.get("SCOPE_MODE", "advisory")

# Database pool sizing
POOL_MIN_SIZE = int(os.environ.get("POOL_MIN_SIZE", "2"))
POOL_MAX_SIZE = int(os.environ.get("POOL_MAX_SIZE", "10"))
