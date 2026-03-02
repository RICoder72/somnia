"""Publishing tools — publish files for external access."""

import shutil
from fastmcp import FastMCP

from config import OUTPUTS_DIR, PUBLIC_BASE_URL
from core.paths import validate


def register(mcp: FastMCP):

    @mcp.tool()
    def publish(
        source: str,
        dest_name: str = None,
        domain: str = None,
        category: str = None,
    ) -> str:
        """Publish a file to the outputs directory for external access.

        Args:
            source: Path to the file to publish
            dest_name: Optional filename override
            domain: Domain subdirectory (e.g. "burrillville")
            category: Category within domain: "files" (ephemeral downloads),
                      "docs" (durable documents), "apps" (web pages/dashboards).
                      Defaults to "files" when domain is specified."""
        src = validate(source)
        if not src.exists():
            return f"❌ Source does not exist: {source}"
        if not src.is_file():
            return f"❌ Source is not a file: {source}"

        filename = dest_name or src.name

        # Build destination path: /outputs/{domain}/{category}/{filename}
        if domain:
            cat = category or "files"
            if cat not in ("files", "docs", "apps"):
                return f"❌ Invalid category: {cat}. Use files, docs, or apps."
            dest_dir = OUTPUTS_DIR / domain / cat
            url_path = f"{domain}/{cat}/{filename}"
        else:
            dest_dir = OUTPUTS_DIR
            url_path = filename

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        shutil.copy2(src, dest)

        url = f"{PUBLIC_BASE_URL}/{url_path}"
        return f"✅ Published: {url_path}\n📎 {url}"
