"""
1Password secrets backend.

Two modes, tried in order:
1. credentials-service HTTP API (if CREDENTIALS_SERVICE_URL is set)
2. Direct `op` CLI calls (if `op` binary is available)

Key mapping:
    Dot keys map to 1Password items in the configured vault.
    db.password → vault/db.password/credential
    hooks.gmail.refresh_token → vault/hooks.gmail.refresh_token/credential

The field is always "credential" unless the key ends with a known
suffix like .username, .url, .notes — then the suffix becomes the field.
"""

import json
import os
import subprocess
import logging
import urllib.request
import urllib.parse
from typing import Optional

from ..interface import SecretsBackend

logger = logging.getLogger(__name__)

# Known field suffixes that map to 1Password fields
_FIELD_SUFFIXES = {
    ".username": "username",
    ".url": "url",
    ".notes": "notes",
}


def _split_key_field(key: str) -> tuple[str, str]:
    """Split a key into item name and field. Default field is 'credential'."""
    for suffix, field in _FIELD_SUFFIXES.items():
        if key.endswith(suffix):
            return key[: -len(suffix)], field
    return key, "credential"


class OnePasswordBackend(SecretsBackend):

    def __init__(self, vault: str = "Somnia"):
        self._vault = vault
        self._creds_url = os.environ.get("CREDENTIALS_SERVICE_URL", "")
        self._creds_api_key = os.environ.get(
            "CREDENTIALS_API_KEY",
            os.environ.get("INTERNAL_API_KEY", ""),
        )
        self._mode = self._detect_mode()

    def _detect_mode(self) -> str:
        """Determine whether to use credentials-service or op CLI."""
        if self._creds_url:
            try:
                req = urllib.request.Request(
                    f"{self._creds_url}/health", method="GET"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return "http"
            except Exception:
                logger.warning(
                    "credentials-service not reachable, falling back to op CLI"
                )

        try:
            result = subprocess.run(
                ["op", "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return "cli"
        except FileNotFoundError:
            pass

        raise RuntimeError(
            "1Password backend requires either credentials-service "
            "(set CREDENTIALS_SERVICE_URL) or the op CLI binary"
        )

    def _http_get(self, item: str, field: str) -> Optional[str]:
        """Fetch via credentials-service HTTP API."""
        vault_enc = urllib.parse.quote(self._vault, safe="")
        item_enc = urllib.parse.quote(item, safe="")
        field_enc = urllib.parse.quote(field, safe="")
        url = f"{self._creds_url}/secret/{vault_enc}/{item_enc}/{field_enc}"

        headers = {}
        if self._creds_api_key:
            headers["Authorization"] = f"Bearer {self._creds_api_key}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("value")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except Exception as e:
            logger.error(f"credentials-service request failed: {e}")
            return None

    def _cli_get(self, item: str, field: str) -> Optional[str]:
        """Fetch via op CLI."""
        ref = f"op://{self._vault}/{item}/{field}"
        try:
            result = subprocess.run(
                ["op", "read", ref],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception as e:
            logger.error(f"op read failed: {e}")
            return None

    def _cli_set(self, item: str, field: str, value: str) -> None:
        """Create or update via op CLI."""
        result = subprocess.run(
            ["op", "item", "get", item, f"--vault={self._vault}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            subprocess.run(
                ["op", "item", "edit", item, f"--vault={self._vault}",
                 f"{field}={value}"],
                capture_output=True, text=True, timeout=30, check=True,
            )
        else:
            subprocess.run(
                ["op", "item", "create", f"--vault={self._vault}",
                 "--category=login", f"--title={item}",
                 f"{field}={value}"],
                capture_output=True, text=True, timeout=30, check=True,
            )

    def get(self, key: str) -> Optional[str]:
        item, field = _split_key_field(key)
        if self._mode == "http":
            return self._http_get(item, field)
        return self._cli_get(item, field)

    def set(self, key: str, value: str) -> None:
        if self._mode == "http":
            raise NotImplementedError(
                "1Password HTTP backend is read-only. "
                "Use op CLI mode for writes."
            )
        item, field = _split_key_field(key)
        self._cli_set(item, field, value)

    def delete(self, key: str) -> None:
        if self._mode != "cli":
            raise NotImplementedError("Delete requires op CLI mode")
        item, _ = _split_key_field(key)
        subprocess.run(
            ["op", "item", "delete", item, f"--vault={self._vault}", "--archive"],
            capture_output=True, text=True, timeout=30,
        )

    def list(self, prefix: str = "") -> list[str]:
        if self._mode == "http":
            url = f"{self._creds_url}/list/{urllib.parse.quote(self._vault)}"
            if prefix:
                url += f"?prefix={urllib.parse.quote(prefix)}"
            headers = {}
            if self._creds_api_key:
                headers["Authorization"] = f"Bearer {self._creds_api_key}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    return sorted(data.get("items", []))
            except Exception as e:
                logger.error(f"list failed: {e}")
                return []
        else:
            try:
                result = subprocess.run(
                    ["op", "item", "list", f"--vault={self._vault}",
                     "--format=json"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    return []
                items = json.loads(result.stdout) if result.stdout else []
                titles = [i.get("title", "") for i in items]
                if prefix:
                    titles = [t for t in titles if t.startswith(prefix)]
                return sorted(titles)
            except Exception as e:
                logger.error(f"op list failed: {e}")
                return []

    def info(self) -> dict:
        return {
            "type": "1password",
            "mode": self._mode,
            "vault": self._vault,
            "writable": self._mode == "cli",
            "description": f"1Password backend ({self._mode} mode)",
        }
