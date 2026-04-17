"""Entity CRUD tools — part of the Somnia/Vigil Store.

Store tools accept `domain=""` to defer to the active workspace's
default_domain, and `workspace=""` to override which workspace to
resolve against. When neither is set and there's no active workspace,
domain must be passed explicitly.

Scope enforcement mode is controlled by config.SCOPE_MODE.
"""

import json
import uuid
from fastmcp import FastMCP, Context
from core.db import get_pool
from core.scope import resolve_domain


def register(mcp: FastMCP):

    @mcp.tool()
    async def store_create_entity(
        ctx: Context,
        entity_type: str,
        name: str,
        properties: str = "{}",
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Create a new entity in a domain.

        Args:
            entity_type: Type of entity (e.g. "task", "item")
            name: Display name for the entity
            properties: JSON string of entity properties
            domain: Domain scope. Empty string defers to the active
                workspace's default_domain (scope resolution).
            workspace: Override which workspace scope resolves against."""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        props = json.loads(properties)
        pool = await get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO entities (domain, entity_type, name, properties)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id, domain, entity_type, name, properties,
                      archived, created_at, updated_at
            """,
            resolved_domain, entity_type, name, json.dumps(props),
        )
        return json.dumps(_entity_to_dict(row), default=str)

    @mcp.tool()
    async def store_get_entity(
        ctx: Context,
        entity_id: str,
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Get a single entity by ID.

        Args:
            entity_id: UUID of the entity
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM entities WHERE id = $1 AND domain = $2",
            uuid.UUID(entity_id), resolved_domain,
        )
        if not row:
            return json.dumps({"error": "Entity not found"})
        return json.dumps(_entity_to_dict(row), default=str)

    @mcp.tool()
    async def store_update_entity(
        ctx: Context,
        entity_id: str,
        name: str | None = None,
        properties: str | None = None,
        entity_type: str | None = None,
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Update an entity. Properties are shallow-merged.

        Args:
            entity_id: UUID of the entity
            name: New name (optional)
            properties: JSON string of properties to merge (optional)
            entity_type: New entity type (optional)
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        eid = uuid.UUID(entity_id)

        sets, params = [], [eid, resolved_domain]
        idx = 3

        if name is not None:
            sets.append(f"name = ${idx}"); params.append(name); idx += 1
        if entity_type is not None:
            sets.append(f"entity_type = ${idx}"); params.append(entity_type); idx += 1
        if properties is not None:
            sets.append(f"properties = properties || ${idx}::jsonb"); params.append(properties); idx += 1

        if not sets:
            return json.dumps({"error": "No fields to update"})

        query = f"""
            UPDATE entities SET {', '.join(sets)}
            WHERE id = $1 AND domain = $2 AND archived = FALSE
            RETURNING id, domain, entity_type, name, properties,
                      archived, created_at, updated_at
        """
        row = await pool.fetchrow(query, *params)
        if not row:
            return json.dumps({"error": "Entity not found or archived"})
        return json.dumps(_entity_to_dict(row), default=str)

    @mcp.tool()
    async def store_archive_entity(
        ctx: Context,
        entity_id: str,
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Soft-delete an entity by marking it as archived.

        Args:
            entity_id: UUID of the entity
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        row = await pool.fetchrow(
            """
            UPDATE entities SET archived = TRUE
            WHERE id = $1 AND domain = $2 AND archived = FALSE
            RETURNING id, domain, entity_type, name, properties,
                      archived, created_at, updated_at
            """,
            uuid.UUID(entity_id), resolved_domain,
        )
        if not row:
            return json.dumps({"error": "Entity not found or already archived"})
        return json.dumps(_entity_to_dict(row), default=str)

    @mcp.tool()
    async def store_query_entities(
        ctx: Context,
        entity_type: str | None = None,
        properties_filter: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Query entities with optional type and property filters.

        Args:
            entity_type: Filter by entity type (optional)
            properties_filter: JSON string for JSONB containment filter (optional)
            include_archived: Include archived entities (default false)
            limit: Max results (default 50)
            offset: Pagination offset (default 0)
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        conditions = ["domain = $1"]
        params: list = [resolved_domain]
        idx = 2

        if not include_archived:
            conditions.append("archived = FALSE")
        if entity_type is not None:
            conditions.append(f"entity_type = ${idx}"); params.append(entity_type); idx += 1
        if properties_filter is not None:
            conditions.append(f"properties @> ${idx}::jsonb"); params.append(properties_filter); idx += 1

        where_sql = " AND ".join(conditions)
        params.extend([limit, offset])
        limit_clause = f"LIMIT ${idx} OFFSET ${idx + 1}"

        query = f"""
            SELECT * FROM entities
            WHERE {where_sql}
            ORDER BY created_at DESC
            {limit_clause}
        """
        rows = await pool.fetch(query, *params)
        return json.dumps([_entity_to_dict(r) for r in rows], default=str)


def _entity_to_dict(row) -> dict:
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
