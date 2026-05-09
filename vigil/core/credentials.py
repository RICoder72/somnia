"""
Credentials client — fetches secrets via the unified secrets interface.

Migration shim: preserves the get_credential() API that existing Vigil
services call, but routes through shared.secrets instead of directly
hitting the credentials-service HTTP API.

The backend (env, file, 1password) is selected by SOMNIA_SECRETS_BACKEND.
For 1password backend, the OnePasswordBackend handles the HTTP/CLI
communication internally — no change needed in calling code.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Try shared secrets module first; fall back to direct env/HTTP for
# backward compat if the shared module isn't mounted.
_USE_SHARED = False
try:
    import sys
    if '/app' not in sys.path:
        sys.path.insert(0, '/app')
    from shared.secrets import get_secret as _get_secret
    _USE_SHARED = True
except ImportError:
    logger.warning("shared.secrets not available — using legacy credential path")
    _get_secret = None


# Legacy HTTP client (kept as fallback)
CREDENTIALS_URL = os.environ.get(
    "CREDENTIALS_SERVICE_URL", "http://credentials-service:3100"
)
CREDENTIALS_API_KEY = os.environ.get("CREDENTIALS_API_KEY", "")


def _legacy_get(item_name: str, field: str, vault: str) -> str:
    """Legacy path: fetch via credentials-service HTTP API."""
    import urllib.request
    import urllib.parse
    import json

    vault_enc = urllib.parse.quote(vault, safe="")
    item_enc = urllib.parse.quote(item_name, safe="")
    field_enc = urllib.parse.quote(field, safe="")
    url = f"{CREDENTIALS_URL}/secret/{vault_enc}/{item_enc}/{field_enc}"

    headers = {}
    if CREDENTIALS_API_KEY:
        headers["Authorization"] = f"Bearer {CREDENTIALS_API_KEY}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        return data["value"]


def get_credential(item_name: str, field: str = "credential",
                   vault: str = "Key Vault") -> str:
    """
    Get a secret. Routes through shared.secrets when available.

    Args:
        item_name: Secret identifier (e.g., "Somnia - Gmail OAuth")
        field: Field to retrieve (default: "credential")
        vault: Vault name (only used by legacy/1password path)

    Returns:
        The secret value as a string.
    """
    if _USE_SHARED:
        # Map legacy item_name to dot-key convention
        # e.g., "Somnia - Gmail OAuth" with field "refresh_token"
        # → "hooks.gmail.refresh_token" (caller should migrate to dot keys)
        #
        # For now, try the item_name as-is as a dot key first,
        # then fall back to the legacy HTTP path
        value = _get_secret(item_name)
        if value is not None:
            return value

        # Try with field suffix for compound lookups
        if field != "credential":
            value = _get_secret(f"{item_name}.{field}")
            if value is not None:
                return value

        # Fall through to legacy if shared module didn't find it
        logger.debug(f"Shared secrets miss for '{item_name}', trying legacy path")

    try:
        return _legacy_get(item_name, field, vault)
    except Exception as e:
        logger.error(f"Failed to get credential '{item_name}': {e}")
        raise RuntimeError(f"Failed to get credential '{item_name}': {e}")

