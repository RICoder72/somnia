"""
Environment variable secrets backend.

Maps dot-separated keys to env var names:
    db.password        → SOMNIA_DB_PASSWORD
    claude.api_key     → SOMNIA_CLAUDE_API_KEY
    hooks.gmail.token  → SOMNIA_HOOKS_GMAIL_TOKEN

Also checks legacy env var names for backward compatibility:
    db.password        → POSTGRES_PASSWORD
    claude.api_key     → ANTHROPIC_API_KEY
    claude.oauth_token → CLAUDE_CODE_OAUTH_TOKEN

Read-only: set() raises NotImplementedError. Use the file backend
if you need runtime writes.
"""

import os
from ..interface import SecretsBackend

# Legacy env var mappings for backward compat with pre-OSS configs.
# Keys here are checked AFTER the SOMNIA_-prefixed name, so the new
# name always wins if both are set.
_LEGACY_MAP: dict[str, list[str]] = {
    "db.password":           ["POSTGRES_PASSWORD"],
    "db.user":               ["POSTGRES_USER"],
    "db.url":                ["SOMNIA_DATABASE_URL", "DATABASE_URL"],
    "claude.api_key":        ["ANTHROPIC_API_KEY"],
    "claude.oauth_token":    ["CLAUDE_CODE_OAUTH_TOKEN"],
    "auth.jwt_secret":       ["JWT_SECRET"],
    "auth.internal_api_key": ["CREDENTIALS_API_KEY", "INTERNAL_API_KEY"],
    "onepassword.token":     ["OP_SERVICE_ACCOUNT_TOKEN"],
}


def _key_to_env(key: str) -> str:
    """Convert dot key to SOMNIA_-prefixed env var name."""
    return "SOMNIA_" + key.upper().replace(".", "_")


class EnvBackend(SecretsBackend):

    def get(self, key: str) -> str | None:
        # Try canonical SOMNIA_-prefixed name first
        val = os.environ.get(_key_to_env(key))
        if val:
            return val
        # Try legacy names
        for legacy in _LEGACY_MAP.get(key, []):
            val = os.environ.get(legacy)
            if val:
                return val
        return None

    def set(self, key: str, value: str) -> None:
        raise NotImplementedError(
            "EnvBackend is read-only. Use SOMNIA_SECRETS_BACKEND=file "
            "for runtime secret management."
        )

    def delete(self, key: str) -> None:
        raise NotImplementedError("EnvBackend is read-only.")

    def list(self, prefix: str = "") -> list[str]:
        """List keys that have values set in the environment."""
        found = []
        somnia_prefix = "SOMNIA_"
        target = somnia_prefix + prefix.upper().replace(".", "_")
        for var in os.environ:
            if var.startswith(target):
                # Reverse: SOMNIA_DB_PASSWORD → db.password
                key = var[len(somnia_prefix):].lower().replace("_", ".")
                found.append(key)
        # Also check legacy mappings
        for key, legacy_vars in _LEGACY_MAP.items():
            if key.startswith(prefix):
                for lv in legacy_vars:
                    if os.environ.get(lv):
                        if key not in found:
                            found.append(key)
                        break
        return sorted(found)

    def info(self) -> dict:
        return {
            "type": "env",
            "writable": False,
            "description": "Environment variable backend (read-only)",
        }
