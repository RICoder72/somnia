"""Relationship tools — part of the Somnia/Vigil Store.

Like entities.py, these accept `domain=""` to defer to the active
workspace's default_domain, and `workspace=""` to override which
workspace to resolve scope against.
"""

import json
import uuid
from fastmcp import FastMCP, Context
from core.db import get_pool
from core.scope import resolve_domain


def register(mcp: FastMCP):

    @mcp.tool()
    async def store_relate_entities(
        ctx: Context,
        source_id: str,
        target_id: str,
        relationship_type: str,
        properties: str = "{}",
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Create a relationship between two entities.

        Args:
            source_id: UUID of the source entity
            target_id: UUID of the target entity
            relationship_type: Type of relationship (e.g. "depends_on", "contains")
            properties: JSON string of relationship properties
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        src = uuid.UUID(source_id)
        tgt = uuid.UUID(target_id)

        check = await pool.fetch(
            "SELECT id FROM entities WHERE id = ANY($1) AND domain = $2",
            [src, tgt], resolved_domain,
        )
        found_ids = {row["id"] for row in check}
        if src not in found_ids:
            return json.dumps({"error": f"Source entity {source_id} not found in domain {resolved_domain}"})
        if tgt not in found_ids:
            return json.dumps({"error": f"Target entity {target_id} not found in domain {resolved_domain}"})

        props = json.loads(properties)
        row = await pool.fetchrow(
            """
            INSERT INTO relationships (domain, source_id, target_id, relationship_type, properties)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (domain, source_id, target_id, relationship_type)
            DO UPDATE SET properties = relationships.properties || EXCLUDED.properties
            RETURNING id, domain, source_id, target_id, relationship_type,
                      properties, created_at, updated_at
            """,
            resolved_domain, src, tgt, relationship_type, json.dumps(props),
        )
        return json.dumps(_rel_to_dict(row), default=str)

    @mcp.tool()
    async def store_remove_relationship(
        ctx: Context,
        relationship_id: str,
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Remove a relationship by ID.

        Args:
            relationship_id: UUID of the relationship
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        row = await pool.fetchrow(
            "DELETE FROM relationships WHERE id = $1 AND domain = $2 RETURNING id",
            uuid.UUID(relationship_id), resolved_domain,
        )
        if not row:
            return json.dumps({"error": "Relationship not found"})
        return json.dumps({"deleted": str(row["id"])})

    @mcp.tool()
    async def store_get_related(
        ctx: Context,
        entity_id: str,
        relationship_type: str | None = None,
        direction: str = "both",
        domain: str = "",
        workspace: str = "",
    ) -> str:
        """Get entities related to a given entity.

        Args:
            entity_id: UUID of the entity
            relationship_type: Filter by relationship type (optional)
            direction: "outgoing", "incoming", or "both" (default "both")
            domain: Domain scope (empty → workspace default)
            workspace: Override active workspace for scope resolution"""
        resolved_domain = await resolve_domain(ctx, domain, workspace_override=workspace or None)
        if not resolved_domain:
            return json.dumps({"error": "No domain: pass domain= explicitly or activate a workspace"})
        pool = await get_pool()
        eid = uuid.UUID(entity_id)
        results = []

        if direction in ("outgoing", "both"):
            conditions = ["r.domain = $1", "r.source_id = $2"]
            params: list = [resolved_domain, eid]
            idx = 3
            if relationship_type is not None:
                conditions.append(f"r.relationship_type = ${idx}"); params.append(relationship_type)
            query = f"""
                SELECT e.*, r.id as rel_id, r.relationship_type, r.properties as rel_properties
                FROM relationships r JOIN entities e ON e.id = r.target_id
                WHERE {' AND '.join(conditions)}
            """
            for row in await pool.fetch(query, *params):
                results.append({
                    "direction": "outgoing",
                    "relationship_id": str(row["rel_id"]),
                    "relationship_type": row["relationship_type"],
                    "relationship_properties": json.loads(row["rel_properties"])
                    if isinstance(row["rel_properties"], str) else dict(row["rel_properties"]),
                    "entity": _entity_from_row(row),
                })

        if direction in ("incoming", "both"):
            conditions = ["r.domain = $1", "r.target_id = $2"]
            params = [resolved_domain, eid]
            idx = 3
            if relationship_type is not None:
                conditions.append(f"r.relationship_type = ${idx}"); params.append(relationship_type)
            query = f"""
                SELECT e.*, r.id as rel_id, r.relationship_type, r.properties as rel_properties
                FROM relationships r JOIN entities e ON e.id = r.source_id
                WHERE {' AND '.join(conditions)}
            """
            for row in await pool.fetch(query, *params):
                results.append({
                    "direction": "incoming",
                    "relationship_id": str(row["rel_id"]),
                    "relationship_type": row["relationship_type"],
                    "relationship_properties": json.loads(row["rel_properties"])
                    if isinstance(row["rel_properties"], str) else dict(row["rel_properties"]),
                    "entity": _entity_from_row(row),
                })

        return json.dumps(results, default=str)


def _entity_from_row(row) -> dict:
    return {
        "id": str(row["id"]), "domain": row["domain"],
        "entity_type": row["entity_type"], "name": row["name"],
        "properties": json.loads(row["properties"])
        if isinstance(row["properties"], str) else dict(row["properties"]),
        "archived": row["archived"],
        "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]),
    }


def _rel_to_dict(row) -> dict:
    return {
        "id": str(row["id"]), "domain": row["domain"],
        "source_id": str(row["source_id"]), "target_id": str(row["target_id"]),
        "relationship_type": row["relationship_type"],
        "properties": json.loads(row["properties"])
        if isinstance(row["properties"], str) else dict(row["properties"]),
        "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]),
    }
