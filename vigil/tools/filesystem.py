"""
Filesystem tools — read, write, list, copy, move, delete, mkdir, rmdir, append.

All tools accept an optional `workspace=""` override. When set, scope
resolution uses that workspace instead of the session's active one —
useful for cross-workspace operations (reading from _shared, migrations,
etc.) without having to switch the active workspace.

Relative paths resolve against the scope's default writable root when a
workspace is active; otherwise against DATA_ROOT (legacy behavior).
Absolute paths are taken as-is. All paths remain DATA_ROOT-sandboxed.

Scope enforcement mode is controlled by config.SCOPE_MODE:
  advisory (default) — violations log a warning, tool proceeds.
  enforce            — violations raise ScopeViolationError, tool fails.
"""

import shutil
from fastmcp import FastMCP, Context

from core.scope import validate_fs_read, validate_fs_write


def register(mcp: FastMCP):

    @mcp.tool()
    async def fs_list(ctx: Context, path: str = ".", workspace: str = "") -> str:
        """List directory contents."""
        target = await validate_fs_read(ctx, path, workspace_override=workspace or None)
        if not target.exists():
            return f"❌ Path does not exist: {path}"
        if not target.is_dir():
            return f"❌ Not a directory: {path}"

        items = []
        for item in sorted(target.iterdir()):
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                items.append(f"📄 {item.name} ({size} bytes)")

        header = f"📂 {path}\n" + "─" * 40
        listing = "\n".join(items) if items else "(empty)"
        return f"{header}\n{listing}"

    @mcp.tool()
    async def fs_read(ctx: Context, path: str, workspace: str = "") -> str:
        """Read file contents."""
        target = await validate_fs_read(ctx, path, workspace_override=workspace or None)
        if not target.exists():
            return f"❌ File does not exist: {path}"
        if not target.is_file():
            return f"❌ Not a file: {path}"
        try:
            return target.read_text()
        except UnicodeDecodeError:
            return f"❌ Cannot read binary file: {path}"

    @mcp.tool()
    async def fs_write(ctx: Context, path: str, content: str, workspace: str = "") -> str:
        """Write content to file. Creates parent directories if needed."""
        target = await validate_fs_write(ctx, path, workspace_override=workspace or None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"✅ Written: {path} ({len(content)} bytes)"

    @mcp.tool()
    async def fs_append(ctx: Context, path: str, content: str, workspace: str = "") -> str:
        """Append content to file. Creates file if it doesn't exist."""
        target = await validate_fs_write(ctx, path, workspace_override=workspace or None)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a") as f:
            f.write(content)
        return f"✅ Appended to: {path} ({len(content)} bytes)"

    @mcp.tool()
    async def fs_delete(ctx: Context, path: str, workspace: str = "") -> str:
        """Delete a file."""
        target = await validate_fs_write(ctx, path, workspace_override=workspace or None)
        if not target.exists():
            return f"❌ Does not exist: {path}"
        if target.is_dir():
            return f"❌ Is a directory (use fs_rmdir): {path}"
        target.unlink()
        return f"✅ Deleted: {path}"

    @mcp.tool()
    async def fs_mkdir(ctx: Context, path: str, workspace: str = "") -> str:
        """Create directory (including parents)."""
        target = await validate_fs_write(ctx, path, workspace_override=workspace or None)
        target.mkdir(parents=True, exist_ok=True)
        return f"✅ Created directory: {path}"

    @mcp.tool()
    async def fs_rmdir(ctx: Context, path: str, force: bool = False, workspace: str = "") -> str:
        """Remove directory."""
        target = await validate_fs_write(ctx, path, workspace_override=workspace or None)
        if not target.exists():
            return f"❌ Does not exist: {path}"
        if not target.is_dir():
            return f"❌ Not a directory: {path}"

        if force:
            shutil.rmtree(target)
            return f"✅ Removed directory and contents: {path}"
        else:
            if any(target.iterdir()):
                return f"❌ Directory not empty (use force=True): {path}"
            target.rmdir()
            return f"✅ Removed directory: {path}"

    @mcp.tool()
    async def fs_move(ctx: Context, source: str, destination: str, workspace: str = "") -> str:
        """Move or rename file/directory.

        Both source and destination are scope-checked for write (since a move
        invalidates the old path and creates the new one).
        """
        src = await validate_fs_write(ctx, source, workspace_override=workspace or None)
        dst = await validate_fs_write(ctx, destination, workspace_override=workspace or None)
        if not src.exists():
            return f"❌ Source does not exist: {source}"
        src.rename(dst)
        return f"✅ Moved: {source} → {destination}"

    @mcp.tool()
    async def fs_replace(ctx: Context, path: str, old: str, new: str, workspace: str = "") -> str:
        """Replace a unique string in a file (must appear exactly once)."""
        target = await validate_fs_write(ctx, path, workspace_override=workspace or None)
        if not target.exists():
            return f"❌ File does not exist: {path}"
        if not target.is_file():
            return f"❌ Not a file: {path}"
        try:
            content = target.read_text()
        except UnicodeDecodeError:
            return f"❌ Cannot read binary file: {path}"
        count = content.count(old)
        if count == 0:
            return f"❌ String not found in {path}"
        if count > 1:
            return f"❌ Found {count} occurrences (need exactly 1)"
        target.write_text(content.replace(old, new))
        return f"✅ Replaced in {path}"

    @mcp.tool()
    async def fs_copy(ctx: Context, source: str, destination: str, workspace: str = "") -> str:
        """Copy file or directory.

        Source is scope-checked for read, destination for write — supports
        the common case of copying from _shared or another readable root
        into the active workspace.
        """
        src = await validate_fs_read(ctx, source, workspace_override=workspace or None)
        dst = await validate_fs_write(ctx, destination, workspace_override=workspace or None)
        if not src.exists():
            return f"❌ Source does not exist: {source}"

        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return f"✅ Copied: {source} → {destination}"
