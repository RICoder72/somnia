"""
Vigil MCP Server — Somnia Core Tools

Everyday tools for filesystem, git, shell, domains, publishing,
database (entities/schemas/relationships), and external services
(mail, calendar, contacts, storage).

Part of the Somnia system.
"""

import asyncio
from fastmcp import FastMCP
import logging

# Database
from core.db import init_db

# Core tools
from tools.filesystem import register as register_fs
from tools.git import register as register_git
from tools.shell import register as register_shell
from tools.context import register as register_context
from tools.publishing import register as register_publishing
from tools.shares import register as register_shares
from tools.dashboard import register as register_dashboard
from tools.session import register as register_session
from tools.bindings_tool import register as register_bindings

# Entity store tools (absorbed from Store)
from tools.entities import register as register_entities
from tools.relationships import register as register_relationships
from tools.types import register as register_types

# REST API (non-MCP HTTP routes)
from api.store import register as register_store_api
from api.files import register as register_files_api
from api.browser import register as register_browser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Run database migrations before starting
asyncio.run(init_db())

# Initialize FastMCP
mcp = FastMCP("Vigil")

# ── Core tools ──────────────────────────────────────────────────────────────
register_fs(mcp)
register_git(mcp)
register_shell(mcp)
register_context(mcp)
register_publishing(mcp)
register_shares(mcp)
register_dashboard(mcp)
register_session(mcp)
register_bindings(mcp)

# ── Entity store (absorbed from Store) ──────────────────────────────────────
register_entities(mcp)
register_relationships(mcp)
register_types(mcp)

# ── REST API (non-MCP HTTP routes) ─────────────────────────────────────────
register_store_api(mcp)
register_files_api(mcp)
register_browser(mcp)

# ── Services (graceful — failures don't block startup) ──────────────────────

try:
    from services.mail.tools import register as register_mail
    register_mail(mcp)
except Exception as e:
    logger.warning(f"⚠️ Mail service unavailable: {e}")

try:
    from services.calendarservice.tools import register as register_calendar
    register_calendar(mcp)
except Exception as e:
    logger.warning(f"⚠️ Calendar service unavailable: {e}")

try:
    from services.contacts.tools import register as register_contacts
    register_contacts(mcp)
except Exception as e:
    logger.warning(f"⚠️ Contacts service unavailable: {e}")

try:
    from services.storage.tools import register as register_storage
    register_storage(mcp)
except Exception as e:
    logger.warning(f"⚠️ Storage service unavailable: {e}")

try:
    from services.supernote.tools import register as register_supernote
    register_supernote(mcp)
except Exception as e:
    logger.warning(f"⚠️ Supernote service unavailable: {e}")

# ── Additional tools (graceful) ─────────────────────────────────────────────

try:
    from tools.secrets import register as register_secrets
    register_secrets(mcp)
except Exception as e:
    logger.warning(f"⚠️ Secrets tools unavailable: {e}")

# ── Startup summary ────────────────────────────────────────────────────────
try:
    if hasattr(mcp, '_tool_manager') and hasattr(mcp._tool_manager, '_tools'):
        tools = sorted(mcp._tool_manager._tools.keys())
        logger.info(f"📋 Registered {len(tools)} tools: {', '.join(tools)}")
except Exception as e:
    logger.warning(f"Could not log tools: {e}")

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/vigil")
