"""Storage service MCP tools."""

import json
import logging
from pathlib import Path

from config import CONFIG_DIR
from core.paths import validate
from .manager import StorageManager
from .adapters.gdrive import GoogleDriveProvider

logger = logging.getLogger(__name__)

STORAGE_CONFIG = CONFIG_DIR / "storage_accounts.json"

storage_manager: StorageManager = None


def register(mcp) -> None:
    """Register storage tools with the MCP server."""
    global storage_manager

    try:
        storage_manager = StorageManager(STORAGE_CONFIG)
        storage_manager.register_provider_type("gdrive", GoogleDriveProvider)
        logger.info("✅ Storage service initialized")
    except Exception as e:
        logger.error(f"❌ Storage service failed to initialize: {e}")
        return

    @mcp.tool()
    async def storage_list_files(account: str, path: str = "/") -> str:
        """List files in a storage account."""
        files = await storage_manager.list_files(account, path)
        if not files:
            return f"No files found at {path}"
        lines = [f"Files at {account}:{path}", "-" * 40]
        for f in files:
            icon = "📁" if f.is_directory else "📄"
            lines.append(f"{icon} {f.name}")
        return "\n".join(lines)

    @mcp.tool()
    async def storage_upload(account: str, local_path: str, remote_path: str) -> str:
        """Upload a file to cloud storage."""
        local = validate(local_path)
        return await storage_manager.upload(account, local, remote_path)

    @mcp.tool()
    async def storage_download(account: str, remote_path: str, local_path: str) -> str:
        """Download a file from cloud storage."""
        local = validate(local_path)
        return await storage_manager.download(account, remote_path, local)

    logger.info("✅ Registered 3 storage tools")
