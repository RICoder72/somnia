"""
Instructions tools — get/set domain or global instructions.

Domain context (context_load, context_list, context_get, context_update)
has been superseded by Somnia recall + filesystem tools. Use fs_read to
load domain markdown files directly, and somnia_recall for associative context.
"""

from fastmcp import FastMCP

from config import WORKSPACES_DIR


def register(mcp: FastMCP):

    @mcp.tool()
    def instructions_get(domain: str = "") -> str:
        """Get instructions for a domain or global instructions.

        Args:
            domain: Domain name, or empty string for global instructions"""
        if domain:
            instructions_file = WORKSPACES_DIR / domain / "INSTRUCTIONS.md"
            label = f"Domain '{domain}'"
        else:
            from config import DATA_ROOT
            instructions_file = DATA_ROOT / "INSTRUCTIONS.md"
            label = "Global"

        if not instructions_file.exists():
            return f"📋 No {label.lower()} instructions found. Use instructions_set to create them."

        try:
            content = instructions_file.read_text().strip()
            return f"📋 {label} Instructions\n{'─' * 30}\n{content}"
        except Exception as e:
            return f"❌ Error reading instructions: {e}"

    @mcp.tool()
    def instructions_set(content: str, domain: str = "") -> str:
        """Set instructions for a domain or global instructions.

        Args:
            content: The instruction content (markdown)
            domain: Domain name, or empty string for global instructions"""
        if domain:
            domain_path = WORKSPACES_DIR / domain
            if not domain_path.exists():
                return f"❌ Domain '{domain}' does not exist"
            instructions_file = domain_path / "INSTRUCTIONS.md"
            label = f"domain '{domain}'"
        else:
            from config import DATA_ROOT
            instructions_file = DATA_ROOT / "INSTRUCTIONS.md"
            label = "global"

        try:
            instructions_file.write_text(content.strip() + "\n")
            return f"✅ Updated {label} instructions ({len(content)} bytes)"
        except Exception as e:
            return f"❌ Error writing instructions: {e}"
