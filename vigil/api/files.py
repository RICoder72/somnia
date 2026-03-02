"""
File management REST API for the outputs directory.

Provides listing and deletion of published files, organized by
domain and category (files, docs, apps).

External: GET /api/files, DELETE /api/files
Internal: Same paths on Vigil's port 8000
"""

import os
import shutil
from pathlib import Path
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from config import OUTPUTS_DIR


def register(mcp: FastMCP):
    """Register file management API routes."""

    @mcp.custom_route("/api/files", methods=["GET"])
    async def api_list_files(request: Request) -> JSONResponse:
        """List published files. Query params: domain (optional), category (optional).
        Returns nested structure: domain → category → files."""
        domain_filter = request.query_params.get("domain")
        category_filter = request.query_params.get("category")

        result = {}

        if not OUTPUTS_DIR.exists():
            return JSONResponse({"domains": result})

        # Walk the outputs directory
        for domain_dir in sorted(OUTPUTS_DIR.iterdir()):
            if not domain_dir.is_dir():
                # Root-level files (no domain)
                continue
            if domain_filter and domain_dir.name != domain_filter:
                continue

            domain_name = domain_dir.name
            categories = {}

            for item in sorted(domain_dir.iterdir()):
                if item.is_dir() and item.name in ("files", "docs", "apps"):
                    cat_name = item.name
                    if category_filter and cat_name != category_filter:
                        continue
                    categories[cat_name] = _list_dir(item, f"{domain_name}/{cat_name}")
                elif item.is_file():
                    # Legacy: files directly in domain dir (uncategorized)
                    if "uncategorized" not in categories:
                        categories["uncategorized"] = []
                    categories["uncategorized"].append(_file_info(item, domain_name))

            if categories:
                result[domain_name] = categories

        return JSONResponse({"domains": result})

    @mcp.custom_route("/api/files/{path:path}", methods=["DELETE"])
    async def api_delete_file(request: Request) -> JSONResponse:
        """Delete a published file. Path is relative to outputs root."""
        rel_path = request.path_params["path"]

        # Safety: resolve and ensure it's within OUTPUTS_DIR
        target = (OUTPUTS_DIR / rel_path).resolve()
        if not str(target).startswith(str(OUTPUTS_DIR.resolve())):
            return JSONResponse({"error": "path traversal denied"}, status_code=403)

        if not target.exists():
            return JSONResponse({"error": "not found"}, status_code=404)

        if target.is_file():
            target.unlink()
            return JSONResponse({"deleted": rel_path})
        elif target.is_dir():
            # Only delete empty dirs or dirs explicitly
            shutil.rmtree(target)
            return JSONResponse({"deleted": rel_path, "type": "directory"})

        return JSONResponse({"error": "unknown file type"}, status_code=400)


def _list_dir(directory: Path, prefix: str) -> list[dict]:
    """List files in a directory recursively."""
    files = []
    for item in sorted(directory.iterdir()):
        if item.is_file():
            files.append(_file_info(item, prefix))
        elif item.is_dir():
            # Include subdirectories' files with nested path
            files.extend(_list_dir(item, f"{prefix}/{item.name}"))
    return files


def _file_info(path: Path, prefix: str) -> dict:
    """Build file info dict."""
    stat = path.stat()
    return {
        "name": path.name,
        "path": f"{prefix}/{path.name}",
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "extension": path.suffix.lstrip("."),
    }
