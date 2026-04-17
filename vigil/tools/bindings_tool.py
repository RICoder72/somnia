"""
Bindings tools — inspection for workspace bindings and scope.

Phase 1 shipped `bindings_show` and `bindings_resolve` for identity
bindings. Phase 2 extends `bindings_show` to also display scope info
(filesystem writable/readable roots, datastore default domain and
allowed domains).

Setting bindings is deliberately NOT a tool: edit the YAML file directly.
The design calls for workspace-local configs that travel with workspace
backups; a tool would make it tempting to mutate state through an API
surface instead of through the workspace's own files.
"""

from fastmcp import FastMCP, Context

from core.bindings import (
    describe_bindings,
    get_active_workspace,
    MissingBindingError,
    resolve_account,
)
from core.scope import describe_scope


def register(mcp: FastMCP):

    @mcp.tool()
    async def bindings_show(ctx: Context, workspace: str = "") -> str:
        """Show a workspace's identity bindings and scope.

        If workspace is omitted, uses the active workspace from session_start
        or workspace_activate.
        """
        ws = workspace or await get_active_workspace(ctx)
        view = await describe_bindings(ctx, workspace=ws)
        scope_view = await describe_scope(ctx, workspace=ws)

        lines = ["🔗 Workspace Bindings & Scope", "─" * 40]
        if not view["workspace"]:
            lines.append("  (no active workspace — run session_start or")
            lines.append("   workspace_activate, or pass workspace= explicitly)")
            return "\n".join(lines)

        lines.append(f"  Workspace: {view['workspace']}")
        lines.append("")

        # ── Identity bindings ─────────────────────────────────────
        if not view["has_bindings_file"]:
            lines.append("  📬 Identity bindings: (no bindings.yaml)")
            lines.append("     Tools without explicit account= will error.")
        elif not view["identity"]:
            lines.append("  📬 Identity bindings: (none declared)")
        else:
            lines.append("  📬 Identity bindings:")
            for resource_type, binding in sorted(view["identity"].items()):
                primary = binding["primary"]
                fallbacks = binding.get("fallbacks") or []
                fb = f" (fallbacks: {', '.join(fallbacks)})" if fallbacks else ""
                lines.append(f"     {resource_type:10s} → {primary}{fb}")
            lines.append("     (fallbacks are allowed overrides, NOT auto-failover)")

        lines.append("")

        # ── Filesystem scope ──────────────────────────────────────
        fs = scope_view.get("filesystem") or {}
        source = fs.get("source", "?")
        lines.append(f"  📁 Filesystem scope ({source}):")
        for root in fs.get("writable_roots", []):
            lines.append(f"     writable: {root}")
        for root in fs.get("readable_roots", []):
            if root not in fs.get("writable_roots", []):
                lines.append(f"     readable: {root}")

        lines.append("")

        # ── Datastore scope ───────────────────────────────────────
        ds = scope_view.get("datastore") or {}
        source = ds.get("source", "?")
        lines.append(f"  🗄  Datastore scope ({source}):")
        lines.append(f"     default domain: {ds.get('default_domain', '?')}")
        allowed = ds.get("allowed_domains", [])
        if allowed and allowed != [ds.get("default_domain")]:
            lines.append(f"     allowed: {', '.join(allowed)}")

        return "\n".join(lines)

    @mcp.tool()
    async def bindings_resolve(
        ctx: Context, resource_type: str, workspace: str = ""
    ) -> str:
        """Show which account a resource_type resolves to.

        Useful for verifying what mail_list_messages() would use if called
        without an explicit account.
        """
        ws = workspace or await get_active_workspace(ctx)
        try:
            account = await resolve_account(ctx, resource_type, workspace=ws)
            return f"✅ {resource_type} → {account}  (workspace: {ws})"
        except MissingBindingError as e:
            return e.user_message()
