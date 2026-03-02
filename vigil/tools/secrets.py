"""
User Secrets — user-facing password and credential storage.

For the USER's passwords (firewall logins, vendor creds, system passwords).
NOT for infrastructure secrets (API keys, OAuth tokens) — those go through
core/credentials.py → Credentials Service.
"""

import json
import subprocess
import logging
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

CONFIG_DIR = Path("/data/config")
ACCOUNTS_CONFIG = CONFIG_DIR / "user_secrets_accounts.json"


def _load_accounts() -> dict:
    if not ACCOUNTS_CONFIG.exists():
        return {}
    try:
        config = json.loads(ACCOUNTS_CONFIG.read_text())
        return config.get("accounts", {})
    except Exception as e:
        logger.error(f"Failed to load accounts: {e}")
        return {}


def _run_op(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["op"] + args, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return False, "op CLI not found"
    except Exception as e:
        return False, str(e)


def _available_accounts(accounts: dict) -> str:
    return ", ".join(accounts.keys()) or "none"


def register(mcp: FastMCP):

    @mcp.tool()
    def secrets_list(account: str, search: Optional[str] = None) -> str:
        """List secrets in an account. Optional search filter."""
        accounts = _load_accounts()
        if account not in accounts:
            return f"❌ Account '{account}' not found. Available: {_available_accounts(accounts)}"
        vault = accounts[account]["vault"]
        ok, out = _run_op(["item", "list", f"--vault={vault}", "--format=json"])
        if not ok:
            return f"❌ Failed to list: {out}"
        try:
            items = json.loads(out) if out else []
        except json.JSONDecodeError:
            return "❌ Invalid response from op CLI"
        if not items:
            return f"🔐 No secrets in '{account}'"
        if search:
            sl = search.lower()
            items = [i for i in items if sl in i.get("title", "").lower()]
        lines = [f"🔐 Secrets in '{account}'", "─" * 40]
        for item in items:
            lines.append(f"  • {item.get('title', 'Untitled')} ({item.get('category', '')})")
        if search:
            lines.append(f"\n(filtered by: '{search}')")
        return "\n".join(lines)

    @mcp.tool()
    def secrets_get(account: str, item_name: str, field: str = "password") -> str:
        """Get a secret value. Common fields: password, username, url, notes."""
        accounts = _load_accounts()
        if account not in accounts:
            return f"❌ Account '{account}' not found. Available: {_available_accounts(accounts)}"
        vault = accounts[account]["vault"]
        ref = f"op://{vault}/{item_name}/{field}"
        ok, out = _run_op(["read", ref])
        if not ok:
            if "isn't an item" in out or "could not be found" in out:
                return f"❌ Item '{item_name}' not found in '{account}'"
            if "isn't a field" in out:
                return f"❌ Field '{field}' not found in '{item_name}'"
            return f"❌ Failed to read: {out}"
        return out

    @mcp.tool()
    def secrets_set(
        account: str,
        item_name: str,
        password: str,
        username: Optional[str] = None,
        url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        """Create or update a secret."""
        accounts = _load_accounts()
        if account not in accounts:
            return f"❌ Account '{account}' not found. Available: {_available_accounts(accounts)}"
        vault = accounts[account]["vault"]

        exists, _ = _run_op(["item", "get", item_name, f"--vault={vault}"])

        if exists:
            args = ["item", "edit", item_name, f"--vault={vault}", f"password={password}"]
            if username:
                args.append(f"username={username}")
            if url:
                args.append(f"url={url}")
            if notes:
                args.append(f"notesPlain={notes}")
            ok, out = _run_op(args)
            action = "Updated"
        else:
            args = [
                "item", "create", f"--vault={vault}", "--category=login",
                f"--title={item_name}", f"password={password}",
            ]
            if username:
                args.append(f"username={username}")
            if url:
                args.append(f"url={url}")
            if notes:
                args.append(f"notesPlain={notes}")
            ok, out = _run_op(args)
            action = "Created"

        if ok:
            return f"✅ {action} '{item_name}' in '{account}'"
        return f"❌ Failed to save: {out}"

    @mcp.tool()
    def secrets_delete(account: str, item_name: str) -> str:
        """Delete (archive) a secret."""
        accounts = _load_accounts()
        if account not in accounts:
            return f"❌ Account '{account}' not found. Available: {_available_accounts(accounts)}"
        vault = accounts[account]["vault"]
        ok, out = _run_op(["item", "delete", item_name, f"--vault={vault}", "--archive"])
        if ok:
            return f"✅ Archived '{item_name}' from '{account}'"
        if "isn't an item" in out or "could not be found" in out:
            return f"❌ Item '{item_name}' not found in '{account}'"
        return f"❌ Failed to delete: {out}"
