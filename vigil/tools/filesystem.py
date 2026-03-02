"""
Filesystem tools — read, write, list, copy, move, delete, mkdir, rmdir, append.
"""

import shutil
from fastmcp import FastMCP

from core.paths import validate


def register(mcp: FastMCP):

    @mcp.tool()
    def fs_list(path: str = ".") -> str:
        """List directory contents."""
        target = validate(path)
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
    def fs_read(path: str) -> str:
        """Read file contents."""
        target = validate(path)
        if not target.exists():
            return f"❌ File does not exist: {path}"
        if not target.is_file():
            return f"❌ Not a file: {path}"
        try:
            return target.read_text()
        except UnicodeDecodeError:
            return f"❌ Cannot read binary file: {path}"

    @mcp.tool()
    def fs_write(path: str, content: str) -> str:
        """Write content to file. Creates parent directories if needed."""
        target = validate(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"✅ Written: {path} ({len(content)} bytes)"

    @mcp.tool()
    def fs_append(path: str, content: str) -> str:
        """Append content to file. Creates file if it doesn't exist."""
        target = validate(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a") as f:
            f.write(content)
        return f"✅ Appended to: {path} ({len(content)} bytes)"

    @mcp.tool()
    def fs_delete(path: str) -> str:
        """Delete a file."""
        target = validate(path)
        if not target.exists():
            return f"❌ Does not exist: {path}"
        if target.is_dir():
            return f"❌ Is a directory (use fs_rmdir): {path}"
        target.unlink()
        return f"✅ Deleted: {path}"

    @mcp.tool()
    def fs_mkdir(path: str) -> str:
        """Create directory (including parents)."""
        target = validate(path)
        target.mkdir(parents=True, exist_ok=True)
        return f"✅ Created directory: {path}"

    @mcp.tool()
    def fs_rmdir(path: str, force: bool = False) -> str:
        """Remove directory."""
        target = validate(path)
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
    def fs_move(source: str, destination: str) -> str:
        """Move or rename file/directory."""
        src = validate(source)
        dst = validate(destination)
        if not src.exists():
            return f"❌ Source does not exist: {source}"
        src.rename(dst)
        return f"✅ Moved: {source} → {destination}"

    @mcp.tool()
    def fs_copy(source: str, destination: str) -> str:
        """Copy file or directory."""
        src = validate(source)
        dst = validate(destination)
        if not src.exists():
            return f"❌ Source does not exist: {source}"

        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return f"✅ Copied: {source} → {destination}"
