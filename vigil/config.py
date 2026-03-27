"""
Vigil configuration — paths, URLs, and constants.

All paths are from the container's perspective (/data mount).
"""

import os
from pathlib import Path

# Base paths
DATA_ROOT = Path("/data")
DOMAINS_DIR = DATA_ROOT / "domains"
CONFIG_DIR = DATA_ROOT / "config"
OUTPUTS_DIR = DATA_ROOT / "outputs"

# Docker network (Somnia)
DOCKER_NETWORK = "mcp-net"

# Public URL for published outputs (served via Somnia router)
PUBLIC_BASE_URL = "https://zanni.synology.me/output"

# Domain config
DOMAIN_TRIGGERS_FILE = CONFIG_DIR / "domain_triggers.json"

# Storage config (for Phase B — services)
STORAGE_CONFIG = CONFIG_DIR / "storage_accounts.json"

# Database (shared Somnia PostgreSQL)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://somnia:changeme@somnia-postgres:5432/somnia",
)
POOL_MIN_SIZE = int(os.environ.get("POOL_MIN_SIZE", "2"))
POOL_MAX_SIZE = int(os.environ.get("POOL_MAX_SIZE", "10"))
