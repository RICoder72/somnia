"""
Binding helpers for service tools — common boilerplate for resolving
optional `account=` parameters through workspace bindings.

Usage:

    from fastmcp import Context
    from core.binding_helpers import resolve_or_error

    @mcp.tool()
    async def mail_list_messages(ctx: Context, account: str = "", ...) -> str:
        account, err = await resolve_or_error(ctx, account, "email")
        if err:
            return err
        # ... use account ...

Keeps the pattern identical across mail/calendar/contacts/storage so
Phase 2 changes (fallback semantics, scope checks, etc.) happen in
one place, not six.
"""

from __future__ import annotations

from typing import Optional

from fastmcp import Context

from core.bindings import MissingBindingError, resolve_account


async def resolve_or_error(
    ctx: Context,
    account: str,
    resource_type: str,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve an account for a resource type.

    Args:
        ctx: FastMCP Context, for session-scoped active workspace lookup.
        account: Caller-supplied account. Non-empty wins, no resolution.
        resource_type: "email" | "calendar" | "contacts" | "storage" | ...

    Returns:
        (account, None) on success — use `account`.
        (None, error_message) on missing binding — return the error to the user.
    """
    if account:
        return account, None
    try:
        return await resolve_account(ctx, resource_type), None
    except MissingBindingError as e:
        return None, e.user_message()
