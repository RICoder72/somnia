"""
Backend factory — creates the right SecretsBackend from configuration.

Configuration via environment:
    SOMNIA_SECRETS_BACKEND   — "env" | "file" | "1password" (default: "env")
    SOMNIA_SECRETS_FILE      — vault file path (default: /data/config/secrets.enc)
    SOMNIA_MASTER_KEY        — Fernet key for file backend (generated at bootstrap)
    SOMNIA_1P_VAULT          — 1Password vault name (default: "Somnia")
    OP_SERVICE_ACCOUNT_TOKEN — 1Password service account token
"""

import os
import logging

from .interface import SecretsBackend

logger = logging.getLogger(__name__)


def create_backend(backend_type: str | None = None) -> SecretsBackend:
    """Create a secrets backend from configuration."""
    backend_type = (
        backend_type
        or os.environ.get("SOMNIA_SECRETS_BACKEND", "env")
    ).lower().strip()

    if backend_type == "env":
        from .backends.env import EnvBackend
        backend = EnvBackend()

    elif backend_type == "file":
        from .backends.file import FileBackend
        vault_path = os.environ.get(
            "SOMNIA_SECRETS_FILE", "/data/config/secrets.enc"
        )
        master_key = os.environ.get("SOMNIA_MASTER_KEY")
        if not master_key:
            raise RuntimeError(
                "SOMNIA_MASTER_KEY required for file backend. "
                "Run bootstrap.sh to generate one."
            )
        backend = FileBackend(vault_path=vault_path, master_key=master_key)

    elif backend_type == "1password":
        from .backends.onepassword import OnePasswordBackend
        vault = os.environ.get("SOMNIA_1P_VAULT", "Somnia")
        backend = OnePasswordBackend(vault=vault)

    else:
        raise ValueError(
            f"Unknown secrets backend: '{backend_type}'. "
            f"Expected: env, file, 1password"
        )

    logger.info(f"Secrets backend: {backend.info().get('type', backend_type)}")
    return backend
