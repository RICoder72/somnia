"""Type schema tools — part of the Somnia/Vigil Store.

Accept `domain=""` to defer to the active workspace's default_domain,
and `workspace=""` to override which workspace to resolve against.
"""

import json
from fastmcp import FastMCP, Context
from core.db import get_pool
from core.scope import resolve_domain


def register(mcp: FastMCP):

    @mcp.tool()
    async def store_register_type(
        ctx: Context,
        entity_type: str,
        schema: str = "{}",
        description: str = "",
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Register or update a type schema for a domain.

        Args:
            entity_type: Type name to register
            schema: JSON string defining the type schema
            description: Human-readable description of this type
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        schema_obj = json.loads(schema)
        row = await pool.fetchrow(
            """
            INSERT INTO entity_type_schemas (domain, entity_type, schema, description)
            VALUES ($1, $2, $3::jsonb, $4)
            ON CONFLICT (domain, entity_type) DO UPDATE
            SET schema = EXCLUDED.schema, description = EXCLUDED.description
            RETURNING id, domain, entity_type, schema, description,
                      created_at, updated_at
            """,
            resolved_domain, entity_type, json.dumps(schema_obj), description,
        )
        return json.dumps(_type_to_dict(row), default=str)

    @mcp.tool()
    async def store_query_types(
        ctx: Context,
        entity_type: str | None = None,
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """List type schemas for a domain.

        Args:
            entity_type: Filter by specific type (optional)
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        if entity_type is not None:
            rows = await pool.fetch(
                "SELECT * FROM entity_type_schemas WHERE domain = $1 AND entity_type = $2 ORDER BY entity_type",
                resolved_domain, entity_type,
            )
        else:
            rows = await pool.fetch(
                "SELECT * FROM entity_type_schemas WHERE domain = $1 ORDER BY entity_type",
                resolved_domain,
            )
        return json.dumps([_type_to_dict(r) for r in rows], default=str)


def _type_to_dict(row) -> dict:
    return {
        "id": str(row["id"]), "domain": row["domain"],
        "entity_type": row["entity_type"],
        "schema": json.loads(row["schema"])
        if isinstance(row["schema"], str) else dict(row["schema"]),
        "description": row["description"],
        "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]),
    }
