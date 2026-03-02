"""
Read-only REST API for Store entities.

These are plain HTTP routes (NOT MCP tools) exposed via FastMCP's
custom_route decorator. Invisible to MCP discovery, gated by the
same nginx auth layer as everything else.

External: GET /api/entities, /api/entities/{id}, /api/entities/{id}/related
Internal: Same paths on Vigil's port 8000 (custom routes mount at root)
"""

import json
import uuid
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from core.db import get_pool


def register(mcp: FastMCP):
    """Register Store REST API routes on the FastMCP server."""

    @mcp.custom_route("/api/entities", methods=["GET"])
    async def api_list_entities(request: Request) -> JSONResponse:
        """List/query entities. Query params: domain (required), type, status, limit, offset."""
        domain = request.query_params.get("domain")
        if not domain:
            return JSONResponse({"error": "domain parameter required"}, status_code=400)

        entity_type = request.query_params.get("type")
        status = request.query_params.get("status")
        limit = min(int(request.query_params.get("limit", "100")), 500)
        offset = int(request.query_params.get("offset", "0"))
        include_archived = request.query_params.get("archived", "false").lower() == "true"

        pool = await get_pool()
        conditions = ["domain = $1"]
        params: list = [domain]
        idx = 2

        if not include_archived:
            conditions.append("archived = FALSE")
        if entity_type:
            conditions.append(f"entity_type = ${idx}")
            params.append(entity_type)
            idx += 1
        if status:
            conditions.append(f"properties->>'status' = ${idx}")
            params.append(status)
            idx += 1

        params.extend([limit, offset])
        query = f"""
            SELECT * FROM entities
            WHERE {' AND '.join(conditions)}
            ORDER BY entity_type, name
            LIMIT ${idx} OFFSET ${idx + 1}
        """

        rows = await pool.fetch(query, *params)
        entities = [_row_to_dict(r) for r in rows]
        return JSONResponse({"entities": entities, "count": len(entities)})

    @mcp.custom_route("/api/entities/{entity_id}", methods=["GET"])
    async def api_get_entity(request: Request) -> JSONResponse:
        """Get a single entity by ID. Query params: domain (required)."""
        domain = request.query_params.get("domain")
        entity_id = request.path_params["entity_id"]

        if not domain:
            return JSONResponse({"error": "domain parameter required"}, status_code=400)

        try:
            eid = uuid.UUID(entity_id)
        except ValueError:
            return JSONResponse({"error": "invalid entity_id"}, status_code=400)

        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM entities WHERE id = $1 AND domain = $2",
            eid, domain,
        )
        if not row:
            return JSONResponse({"error": "not found"}, status_code=404)

        return JSONResponse(_row_to_dict(row))

    @mcp.custom_route("/api/entities/{entity_id}/related", methods=["GET"])
    async def api_get_related(request: Request) -> JSONResponse:
        """Get entities related to the given entity.
        Query params: domain (required), type (relationship_type filter), direction (outgoing|incoming|both)."""
        domain = request.query_params.get("domain")
        entity_id = request.path_params["entity_id"]
        rel_type = request.query_params.get("type")
        direction = request.query_params.get("direction", "both")

        if not domain:
            return JSONResponse({"error": "domain parameter required"}, status_code=400)

        try:
            eid = uuid.UUID(entity_id)
        except ValueError:
            return JSONResponse({"error": "invalid entity_id"}, status_code=400)

        pool = await get_pool()
        results = []

        if direction in ("outgoing", "both"):
            conditions = ["r.domain = $1", "r.source_id = $2"]
            params: list = [domain, eid]
            idx = 3
            if rel_type:
                conditions.append(f"r.relationship_type = ${idx}")
                params.append(rel_type)
            query = f"""
                SELECT e.*, r.id as rel_id, r.relationship_type,
                       r.properties as rel_properties
                FROM relationships r JOIN entities e ON e.id = r.target_id
                WHERE {' AND '.join(conditions)}
            """
            for row in await pool.fetch(query, *params):
                results.append({
                    "direction": "outgoing",
                    "relationship_id": str(row["rel_id"]),
                    "relationship_type": row["relationship_type"],
                    "entity": _row_to_dict(row),
                })

        if direction in ("incoming", "both"):
            conditions = ["r.domain = $1", "r.target_id = $2"]
            params = [domain, eid]
            idx = 3
            if rel_type:
                conditions.append(f"r.relationship_type = ${idx}")
                params.append(rel_type)
            query = f"""
                SELECT e.*, r.id as rel_id, r.relationship_type,
                       r.properties as rel_properties
                FROM relationships r JOIN entities e ON e.id = r.source_id
                WHERE {' AND '.join(conditions)}
            """
            for row in await pool.fetch(query, *params):
                results.append({
                    "direction": "incoming",
                    "relationship_id": str(row["rel_id"]),
                    "relationship_type": row["relationship_type"],
                    "entity": _row_to_dict(row),
                })

        return JSONResponse({"related": results, "count": len(results)})

    @mcp.custom_route("/api/types", methods=["GET"])
    async def api_list_types(request: Request) -> JSONResponse:
        """List type schemas. Query params: domain (required), type (optional filter)."""
        domain = request.query_params.get("domain")
        if not domain:
            return JSONResponse({"error": "domain parameter required"}, status_code=400)

        entity_type = request.query_params.get("type")
        pool = await get_pool()

        if entity_type:
            rows = await pool.fetch(
                "SELECT * FROM type_schemas WHERE domain = $1 AND entity_type = $2",
                domain, entity_type,
            )
        else:
            rows = await pool.fetch(
                "SELECT * FROM type_schemas WHERE domain = $1 ORDER BY entity_type",
                domain,
            )

        types = []
        for r in rows:
            types.append({
                "domain": r["domain"],
                "entity_type": r["entity_type"],
                "schema": json.loads(r["schema"]) if isinstance(r["schema"], str)
                          else dict(r["schema"]),
                "description": r.get("description", ""),
            })

        return JSONResponse({"types": types, "count": len(types)})


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record to a JSON-friendly dict."""
    return {
        "id": str(row["id"]),
        "domain": row["domain"],
        "entity_type": row["entity_type"],
        "name": row["name"],
        "properties": json.loads(row["properties"])
        if isinstance(row["properties"], str)
        else dict(row["properties"]),
        "archived": row["archived"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }
