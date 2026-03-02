"""
Credentials client — fetches secrets from the Credentials Service.

The Credentials Service runs on mcp-net at http://credentials-service:3100.
It wraps 1Password and provides secrets via a simple HTTP API.

API: GET /secret/{vault}/{item}/{field}
Auth: Bearer token (CREDENTIALS_API_KEY)
"""

import os
import logging
import urllib.request
import urllib.parse
import json

logger = logging.getLogger(__name__)

CREDENTIALS_URL = os.environ.get(
    "CREDENTIALS_SERVICE_URL", "http://credentials-service:3100"
)
CREDENTIALS_API_KEY = os.environ.get("CREDENTIALS_API_KEY", "")


def get_credential(item_name: str, field: str = "credential", vault: str = "Key Vault") -> str:
    """
    Get a secret from the Credentials Service.

    Args:
        item_name: 1Password item name
        field: Field to retrieve (default: "credential")
        vault: 1Password vault (default: "Key Vault")

    Returns:
        The secret value as a string.

    Raises:
        RuntimeError: If the request fails.
    """
    vault_enc = urllib.parse.quote(vault, safe="")
    item_enc = urllib.parse.quote(item_name, safe="")
    field_enc = urllib.parse.quote(field, safe="")
    url = f"{CREDENTIALS_URL}/secret/{vault_enc}/{item_enc}/{field_enc}"

    headers = {}
    if CREDENTIALS_API_KEY:
        headers["Authorization"] = f"Bearer {CREDENTIALS_API_KEY}"

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["value"]
    except Exception as e:
        logger.error(f"Credentials Service request failed: {e}")
        raise RuntimeError(f"Failed to get credential '{item_name}': {e}")
