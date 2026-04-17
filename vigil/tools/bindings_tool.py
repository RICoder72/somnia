"""
Bindings tools — inspection for workspace bindings.

Phase 1 exposes `bindings_show` and `bindings_resolve` for humans (and
portal Config) to see what the active (or a named) workspace has declared.

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


def register(mcp: FastMCP):

    @mcp.tool()
    async def bindings_show(ctx: Context, workspace: str = "") -> str:
        """Show a workspace's identity bindings.

        If workspace is omitted, uses the active workspace from session_start.
        """
        ws = workspace or await get_active_workspace(ctx)
        view = await describe_bindings(ctx, workspace=ws)

        lines = ["🔗 Workspace Bindings", "─" * 40]
        if not view["workspace"]:
            lines.append("  (no active workspace — run session_start first,")
            lines.append("   or pass workspace= explicitly)")
            return "\n".join(lines)

        lines.append(f"  Workspace: {view['workspace']}")

        if not view["has_bindings_file"]:
            lines.append(f"  bindings.yaml: (absent)")
            lines.append("")
            lines.append("  No identity bindings declared. Tools that")
            lines.append("  don't receive an explicit account= will error.")
            return "\n".join(lines)

        lines.append("  bindings.yaml: present")
        lines.append("")

        if not view["identity"]:
            lines.append("  No identity bindings declared (file is empty or")
            lines.append("  only contains scope settings).")
            return "\n".join(lines)

        lines.append("  Identity bindings:")
        for resource_type, binding in sorted(view["identity"].items()):
            primary = binding["primary"]
            fallbacks = binding.get("fallbacks") or []
            if fallbacks:
                fb = f" (fallbacks allowed: {', '.join(fallbacks)})"
            else:
                fb = ""
            lines.append(f"    {resource_type:10s} → {primary}{fb}")

        lines.append("")
        lines.append("  Note: fallbacks are allowed explicit overrides, NOT")
        lines.append("        automatic failover. Primary is always used")
        lines.append("        unless the caller passes account= explicitly.")
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
