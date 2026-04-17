"""
Workspace Bindings — resolves resource types (email, storage, etc.) to
account names based on the active workspace's bindings.yaml.

This is Phase 1 of the workspace bindings design (v0.4). It does NOT:
  - Define accounts (those still live in fleet-level /data/config/*_accounts.json)
  - Enforce filesystem or datastore scope (Phase 2)
  - Handle abstract role names or workspace-local hooks.yaml (Phase 3)

It DOES:
  - Load workspaces/{name}/bindings.yaml if present
  - Track the active workspace per MCP session via FastMCP Context state
  - Resolve "what account does this workspace use for email?"
  - Raise a structured MissingBindingError when unbound
  - Stay out of the way when callers pass an explicit account

Session scoping:
  Active workspace is stored in ctx.set_state under ACTIVE_WS_KEY. This is
  session-scoped — each connected MCP client gets its own active workspace.
  A process-global would leak state across sessions, so we never use one.

Usage from a tool:

    from fastmcp import Context
    from core.bindings import resolve_account, MissingBindingError

    async def mail_list_messages(ctx: Context, account: str = "", ...):
        if not account:
            try:
                account = await resolve_account(ctx, "email")
            except MissingBindingError as e:
                return e.user_message()
        # ... proceed with account ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from fastmcp import Context

from config import WORKSPACES_DIR

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Session-scoped active workspace
# ────────────────────────────────────────────────────────────────

ACTIVE_WS_KEY = "bindings.active_workspace"


async def set_active_workspace(ctx: Context, name: Optional[str]) -> None:
    """Set (or clear) the active workspace for this MCP session.

    None clears the binding, which causes subsequent resolves to raise
    MissingBindingError with reason='no_active_workspace'.
    """
    await ctx.set_state(ACTIVE_WS_KEY, name)
    if name:
        logger.info(f"🎯 [session {ctx.session_id}] active workspace: {name}")
    else:
        logger.info(f"🎯 [session {ctx.session_id}] active workspace: cleared")


async def get_active_workspace(ctx: Context) -> Optional[str]:
    """Return the currently active workspace for this session, or None."""
    return await ctx.get_state(ACTIVE_WS_KEY)


# ────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────


@dataclass
class MissingBindingError(Exception):
    """Raised when a workspace has no binding for a requested resource type."""

    workspace: Optional[str]
    resource_type: str
    reason: str  # "no_active_workspace" | "no_bindings_file" | "no_binding_for_type"

    def __str__(self) -> str:
        return self.user_message()

    def user_message(self) -> str:
        """Human-readable message suitable for surfacing in chat."""
        if self.reason == "no_active_workspace":
            return (
                f"❌ No {self.resource_type} binding: no active workspace. "
                f"Pass account= explicitly, or run session_start with a "
                f"user_message that identifies a workspace."
            )
        if self.reason == "no_bindings_file":
            return (
                f"❌ No {self.resource_type} binding: workspace "
                f"'{self.workspace}' has no bindings.yaml. "
                f"Pass account= explicitly, or create "
                f"workspaces/{self.workspace}/bindings.yaml with an "
                f"identity.{self.resource_type} entry."
            )
        if self.reason == "no_binding_for_type":
            return (
                f"❌ No {self.resource_type} binding declared for workspace "
                f"'{self.workspace}'. Pass account= explicitly, or add "
                f"identity.{self.resource_type} to "
                f"workspaces/{self.workspace}/bindings.yaml."
            )
        return f"❌ No {self.resource_type} binding ({self.reason})"


# ────────────────────────────────────────────────────────────────
# Binding file loader (pure; no session state involvement)
# ────────────────────────────────────────────────────────────────


def _bindings_path(workspace: str) -> Path:
    return WORKSPACES_DIR / workspace / "bindings.yaml"


def load_bindings(workspace: str) -> Optional[dict]:
    """Load the raw bindings.yaml for a workspace. Returns None if absent."""
    path = _bindings_path(workspace)
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning(f"bindings.yaml for {workspace} is not a mapping; ignoring")
            return None
        return data
    except Exception as e:
        logger.error(f"Failed to load bindings.yaml for {workspace}: {e}")
        return None


def _normalize_binding(raw) -> Optional[dict]:
    """Normalize a binding value to {primary, fallbacks}.

    Bare string   ->  {"primary": "<str>", "fallbacks": []}
    Object form   ->  {"primary": raw["primary"], "fallbacks": raw.get("fallbacks", [])}
    Anything else ->  None
    """
    if isinstance(raw, str):
        return {"primary": raw, "fallbacks": []}
    if isinstance(raw, dict) and "primary" in raw:
        return {
            "primary": raw["primary"],
            "fallbacks": list(raw.get("fallbacks", []) or []),
        }
    return None


# ────────────────────────────────────────────────────────────────
# Resolution
# ────────────────────────────────────────────────────────────────


async def resolve_account(
    ctx: Context,
    resource_type: str,
    workspace: Optional[str] = None,
) -> str:
    """Resolve an account name for a resource type.

    Args:
        ctx: FastMCP Context (for session-scoped active workspace lookup).
        resource_type: "email" | "calendar" | "contacts" | "storage" | "git" | ...
        workspace: Explicit workspace override. Defaults to the session's
                   active workspace.

    Returns:
        The primary account name for the binding.

    Raises:
        MissingBindingError: If no binding can be resolved.

    Policy (v0.4):
      - Always returns the primary binding. Fallbacks are NOT auto-substituted.
      - Callers with an explicit account= value should bypass this function
        entirely.
    """
    ws = workspace or await get_active_workspace(ctx)
    if not ws:
        raise MissingBindingError(
            workspace=None,
            resource_type=resource_type,
            reason="no_active_workspace",
        )

    data = load_bindings(ws)
    if data is None:
        raise MissingBindingError(
            workspace=ws,
            resource_type=resource_type,
            reason="no_bindings_file",
        )

    identity = data.get("identity") or {}
    raw = identity.get(resource_type)
    binding = _normalize_binding(raw)
    if binding is None:
        raise MissingBindingError(
            workspace=ws,
            resource_type=resource_type,
            reason="no_binding_for_type",
        )

    return binding["primary"]


async def get_allowed_accounts(
    ctx: Context,
    resource_type: str,
    workspace: Optional[str] = None,
) -> list[str]:
    """Return [primary, *fallbacks] for a resource type. Empty if unbound.

    Used for validating explicit account= overrides. Phase 1 does NOT
    enforce this restriction; it's a helper for tools that want to warn.
    """
    ws = workspace or await get_active_workspace(ctx)
    if not ws:
        return []
    data = load_bindings(ws)
    if not data:
        return []
    identity = data.get("identity") or {}
    raw = identity.get(resource_type)
    binding = _normalize_binding(raw)
    if binding is None:
        return []
    return [binding["primary"], *binding["fallbacks"]]


async def describe_bindings(
    ctx: Context,
    workspace: Optional[str] = None,
) -> dict:
    """Return a human/portal-friendly view of a workspace's identity bindings.

    Shape:
      {
        "workspace": "burrillville",
        "has_bindings_file": True,
        "identity": {
          "email": {"primary": "zannim@bsd-ri.net", "fallbacks": []},
          "storage": {"primary": "personal", "fallbacks": []},
          ...
        }
      }

    Used by bindings_show (Phase 1) and the Config portal page (future).
    Safe to call with any workspace; returns has_bindings_file=False when absent.
    """
    ws = workspace or await get_active_workspace(ctx)
    result: dict = {
        "workspace": ws,
        "has_bindings_file": False,
        "identity": {},
    }
    if not ws:
        return result
    data = load_bindings(ws)
    if not data:
        return result
    result["has_bindings_file"] = True
    identity = data.get("identity") or {}
    for resource_type, raw in identity.items():
        normalized = _normalize_binding(raw)
        if normalized is not None:
            result["identity"][resource_type] = normalized
    return result
