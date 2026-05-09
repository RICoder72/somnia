"""
Somnia Secrets — unified secret management with pluggable backends.

Usage:
    from shared.secrets import get_secret, set_secret, list_secrets

    password = get_secret("db.password")
    set_secret("hooks.gmail.refresh_token", token)
    keys = list_secrets("hooks.gmail.")

Backend is selected by SOMNIA_SECRETS_BACKEND env var:
    env        — reads from environment variables (default)
    file       — encrypted file at /data/config/secrets.enc
    1password  — 1Password via SDK or CLI

All three backends implement the same interface. Services import this
module and never think about which backend is active.
"""

import os
import logging
from typing import Optional

from .interface import SecretsBackend
from .config import create_backend

logger = logging.getLogger(__name__)

# Module-level singleton — initialized on first use
_backend: Optional[SecretsBackend] = None


def _get_backend() -> SecretsBackend:
    global _backend
    if _backend is None:
        _backend = create_backend()
    return _backend


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a secret by key. Returns default if not found."""
    try:
        value = _get_backend().get(key)
        return value if value is not None else default
    except Exception as e:
        logger.warning(f"secrets.get({key}) failed: {e}")
        return default


def require_secret(key: str) -> str:
    """Get a secret by key. Raises if not found."""
    value = get_secret(key)
    if value is None:
        raise RuntimeError(f"Required secret '{key}' not found")
    return value


def set_secret(key: str, value: str) -> None:
    """Set a secret. Not all backends support this (env is read-only)."""
    _get_backend().set(key, value)


def delete_secret(key: str) -> None:
    """Delete a secret."""
    _get_backend().delete(key)


def list_secrets(prefix: str = "") -> list[str]:
    """List secret keys, optionally filtered by prefix."""
    return _get_backend().list(prefix)


def exists(key: str) -> bool:
    """Check if a secret exists."""
    return _get_backend().exists(key)


def backend_info() -> dict:
    """Return info about the active backend (for diagnostics)."""
    return _get_backend().info()


def reset_backend() -> None:
    """Force re-initialization (for testing or config reload)."""
    global _backend
    _backend = None
