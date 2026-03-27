#!/usr/bin/env python3
"""
Somnia MCP Server

Provides memory tools to Claude via Model Context Protocol.
Thin layer over the Somnia Flask API (running on the same container).

Tools:
  somnia_remember  - Add an observation to the inbox
  somnia_recall    - Search for relevant memories by topic
  somnia_status    - Quick health/state check
  somnia_journal   - Review dreams from recent inactive periods
"""

import json
import logging
import requests
from datetime import datetime, timezone
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = "http://localhost:8010"

mcp = FastMCP("Somnia")


def _api_get(path: str, params: dict = None) -> dict:
    """GET request to the internal Flask API."""
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def _api_post(path: str, data: dict = None) -> dict:
    """POST request to the internal Flask API."""
    try:
        resp = requests.post(f"{API_BASE}{path}", json=data or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def _record_activity(activity_type: str, metadata: dict = None):
    """Record a tool invocation as an interaction in the activity log."""
    try:
        requests.post(f"{API_BASE}/activity",
                      json={"type": activity_type, "metadata": metadata},
                      timeout=5)
    except Exception:
        pass  # Activity recording is best-effort, don't break tool calls


def _server_timestamp() -> str:
    """Return current server time as a compact ISO 8601 string with local offset."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool()
def somnia_remember(
    content: str,
    domain: str = "",
    source: str = ""
) -> str:
    """
    Add an observation to Somnia's inbox for later consolidation.

    Use this during conversations to note things worth remembering:
    decisions, preferences, patterns, surprises, interconnections.
    Don't filter for novelty — the dream cycle handles deduplication
    and reinforcement. Focus on quality of what's worth noting.

    Args:
        content: The observation to remember (be specific and concise)
        domain: Optional domain/topic tag (e.g., "burrillville", "somnia")
        source: Optional source identifier (e.g., conversation context)
    """
    data = {"content": content}
    if domain:
        data["domain"] = domain
    if source:
        data["source"] = source

    result = _api_post("/inbox", data)
    _record_activity("remember", {"content_preview": content[:80]})

    if "error" in result:
        return f"Failed to save: {result['error']}"

    return f"[{_server_timestamp()}] Noted: {content[:80]}{'...' if len(content) > 80 else ''}"


@mcp.tool()
def somnia_recall(
    query: str,
    limit: int = 10
) -> str:
    """
    Search Somnia's memory graph for relevant memories.

    Use this when a topic comes up and you want to know what you already
    know about it. Also use when the conversation topic shifts to load
    relevant context. Be liberal with recalls — they're cheap and the
    payoff is better, more informed responses.

    Args:
        query: Topic or keywords to search for
        limit: Maximum number of results (default 10)
    """
    result = _api_get("/search", params={"q": query, "limit": limit})
    _record_activity("recall", {"query": query})

    if "error" in result:
        return f"No memories found for '{query}'"

    nodes = result.get("nodes", [])
    if not nodes:
        return f"No memories found for '{query}'"

    stm_count = result.get("stm_count", 0)
    ltm_count = result.get("ltm_count", 0)
    sltm_count = result.get("sltm_count", 0)
    lines = [f"[{_server_timestamp()}] Found {len(nodes)} memory/memories for '{query}' ({stm_count} recent, {ltm_count} long-term, {sltm_count} faded):\n"]
    for node in nodes:
        memory_layer = node.get("memory_layer_result", node.get("memory_layer", "ltm"))
        content = node.get("content", "")

        if memory_layer == "stm":
            captured = node.get("captured_at", "")
            domain = node.get("domain", "")
            lines.append(f"[RECENT] {content}")
            extras = []
            if domain:
                extras.append(f"domain={domain}")
            if captured:
                extras.append(f"captured={captured}")
            if extras:
                lines.append(f"  ({', '.join(extras)})")
        elif memory_layer == "sltm":
            node_type = node.get("type", "unknown")
            lines.append(f"🌫️ [faded/{node_type}] {content}")
            lines.append(f"  (faded memory — recalled, warming back up)")
        else:
            node_type = node.get("type", "unknown")
            decay = node.get("decay_state", 1.0)
            reinforcement = node.get("reinforcement_count", 1)
            pinned = node.get("pinned", False)
            prefix = "📌 " if pinned else ""
            lines.append(f"{prefix}[{node_type}] {content}")

            extras = []
            if pinned:
                extras.append("PINNED")
            if reinforcement > 1 or decay < 0.8:
                extras.append(f"reinforced {reinforcement}x, decay {decay:.2f}")
            if extras:
                lines.append(f"  ({', '.join(extras)})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def somnia_pin(
    id: str,
    content: str = "",
    properties: str = "{}",
    unpin: bool = False
) -> str:
    """
    Pin or unpin a node. Creates the node if it doesn't exist.

    Pinned nodes are sovereign — the dream cycle can observe them and
    add edges but cannot merge, dissolve, or delete them. Use for
    projects, people, recurring topics, or anything worth not losing.

    Properties are merged on update (existing keys preserved unless
    overwritten). Convention properties include: status, domain_path,
    tags, repos, notebook.

    Args:
        id: Node ID (use kebab-case, e.g. "constellation", "burrillville")
        content: Description of what this node represents
        properties: JSON string of properties to set/merge
        unpin: Set True to unpin (node stays in graph, loses durability)
    """
    if unpin:
        result = _api_post(f"/nodes/{id}/unpin")
        if "error" in result:
            return f"Failed to unpin: {result['error']}"
        _record_activity("pin", {"id": id, "action": "unpin"})
        return f"Unpinned: {id}"

    try:
        props = json.loads(properties) if isinstance(properties, str) else properties
    except (json.JSONDecodeError, TypeError):
        props = {}

    data = {}
    if content:
        data["content"] = content
    if props:
        data["properties"] = props

    result = _api_post(f"/nodes/{id}/pin", data)
    _record_activity("pin", {"id": id, "action": "pin", "created": result.get("created", False)})

    if "error" in result:
        return f"Failed to pin: {result['error']}"

    created = result.get("created", False)
    action = "Created and pinned" if created else "Pinned"
    return f"{action}: {id}" + (f" — {content[:60]}" if content else "")


@mcp.tool()
def somnia_session() -> str:
    """
    Layer 0 awareness dashboard — returns all pinned nodes and nudges.

    Call at the start of conversations to know what's in flight.
    Returns pinned nodes with summaries, status, and staleness info,
    plus nudges from the dream cycle about things worth attention.
    Cheap and fast — most conversations only need this.

    For deeper context on a specific topic, follow up with somnia_recall.
    """
    result = _api_get("/session")
    _record_activity("session")

    if "error" in result:
        return f"Session unavailable: {result['error']}"

    pinned = result.get("pinned", [])
    nudges = result.get("nudges", [])
    graph = result.get("graph_summary", {})
    dreams_since = result.get("dreams_since_last", 0)
    recent_findings = result.get("recent_findings", [])
    system_health = result.get("system_health", {})

    lines = ["Somnia Session Dashboard"]
    lines.append(f"  Server time: {_server_timestamp()}")
    lines.append(f"  Graph: {graph.get('nodes', 0)} nodes, {graph.get('edges', 0)} edges, {graph.get('inbox', 0)} inbox")
    if dreams_since > 0:
        lines.append(f"  Dreams since last conversation: {dreams_since}")
    if recent_findings:
        lines.append(f"  Solo-work findings: {len(recent_findings)} recent")

    # Surface system health issues
    health_24h = system_health.get("last_24h", {})
    error_count = health_24h.get("error", 0)
    warning_count = health_24h.get("warning", 0)
    if error_count or warning_count:
        parts = []
        if error_count:
            parts.append(f"{error_count} errors")
        if warning_count:
            parts.append(f"{warning_count} warnings")
        lines.append(f"  ⚠️ System health (24h): {', '.join(parts)}")
    lines.append("")

    if pinned:
        lines.append(f"Pinned ({len(pinned)}):")
        for p in pinned:
            status = f" [{p['status']}]" if p.get('status') else ""
            lines.append(f"  📌 {p['id']}{status} — {p['summary']}")
            lines.append(f"     last: {p['last_touched']}, decay: {p['decay']:.2f}")
        lines.append("")
    else:
        lines.append("No pinned nodes yet.")
        lines.append("")

    if nudges:
        lines.append("Nudges:")
        for n in nudges:
            lines.append(f"  💡 [{n['id']}] {n['note']}")
        lines.append("")

    if recent_findings:
        lines.append("Recent Solo-Work Findings:")
        for f in recent_findings:
            lines.append(f"  📋 {f['filename']} ({f['modified'][:10]})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def somnia_status() -> str:
    """
    Check Somnia's current state — graph size, inbox depth, dream readiness.

    Use occasionally for diagnostics or when discussing Somnia itself.
    """
    result = _api_get("/status")
    _record_activity("status")

    if "error" in result:
        return f"Somnia status unavailable: {result['error']}"

    graph = result.get("graph", {})
    activity = result.get("activity", {})
    ready = result.get("ready_to_dream", False)
    reason = result.get("reason", "")
    can_ruminate = result.get("ready_to_ruminate", False)
    ruminate_reason = result.get("ruminate_reason", "")
    can_solo = result.get("ready_for_solo_work", False)
    solo_reason = result.get("solo_work_reason", "")
    budget = result.get("budget", {})

    lines = [
        "Somnia Status:",
        f"  Graph: {graph.get('node_count', 0)} nodes, {graph.get('edge_count', 0)} edges",
        f"  Pinned: {graph.get('pinned_count', 0)} nodes",
        f"  Inbox: {graph.get('inbox_pending', 0)} pending items",
        f"  Avg decay: {graph.get('avg_decay', 1.0):.2f}",
        f"  Dream ready: {'yes' if ready else 'no'} ({reason})",
        f"  Rumination ready: {'yes' if can_ruminate else 'no'} ({ruminate_reason})",
        f"  Solo-work ready: {'yes' if can_solo else 'no'} ({solo_reason})",
    ]

    if budget:
        lines.append(f"  Budget: ${budget.get('daily_cost', 0):.2f} / ${budget.get('daily_cap', 2.0):.2f} today"
                     f" (${budget.get('remaining', 0):.2f} remaining)")

    # Activity info
    dreams_since = activity.get('dreams_since_last_interaction', 0)
    last_interaction = activity.get('last_interaction')
    if last_interaction:
        lines.append(f"  Last interaction: {last_interaction.get('timestamp', 'unknown')}")
    lines.append(f"  Dreams since last interaction: {dreams_since}")

    last_dream_info = result.get("last_dream")
    if last_dream_info and last_dream_info.get("ended_at"):
        lines.append(f"  Last dream: {last_dream_info['ended_at']}")

    return "\n".join(lines)


@mcp.tool()
def somnia_journal(
    periods: int = 1,
    threshold: float = 2.0
) -> str:
    """
    Review Somnia's dream journal from recent inactive periods.

    Returns dreams and ruminations grouped by gap periods — stretches
    where the user was away and dreaming/rumination occurred. Use this
    when asked about dreams, what happened overnight, or what Somnia
    was thinking about while idle.

    Args:
        periods: Number of gap periods to return (default 1 = most recent)
        threshold: Minimum inactive hours to count as a gap (default 2.0)
    """
    result = _api_get("/journal", params={
        "periods": periods,
        "threshold": threshold,
    })
    _record_activity("journal", {"periods": periods})

    if "error" in result:
        return f"Journal unavailable: {result['error']}"

    entries = result.get("journal", [])
    if not entries:
        return "No dream activity found in recent inactive periods."

    lines = []
    for entry in entries:
        gap_start = entry.get("gap_start", "?")
        gap_end = entry.get("gap_end", "?")
        gap_hours = entry.get("gap_hours", 0)
        dream_count = entry.get("dream_count", 0)

        lines.append(f"Gap: {gap_start} → {gap_end} ({gap_hours:.1f}h, {dream_count} dream(s))")
        lines.append("")

        for dream in entry.get("dreams", []):
            mode = dream.get("mode", "process")
            ended = dream.get("ended_at", "?")
            summary = dream.get("summary")
            reflections = dream.get("reflections")
            duration = dream.get("duration_seconds", 0)

            lines.append(f"  [{mode}] {ended} ({duration}s)")
            if summary:
                lines.append(f"    Summary: {summary}")
            if reflections:
                refl = reflections if len(reflections) < 300 else reflections[:297] + "..."
                lines.append(f"    Reflections: {refl}")

            nodes_created = dream.get("nodes_created", [])
            edges_created = dream.get("edges_created", [])
            if nodes_created:
                lines.append(f"    Nodes created: {len(nodes_created)}")
            if edges_created:
                lines.append(f"    Edges created: {len(edges_created)}")

            resolved = dream.get("edges_created_resolved", [])
            for r in resolved:
                if "not found" not in r:
                    lines.append(f"      {r}")

            lines.append("")

    return "\n".join(lines)


@mcp.tool()
def somnia_analytics(
    days: int = 14
) -> str:
    """
    Generate a Somnia analytics report.

    Covers graph health, heat map distribution, dream activity,
    user interaction patterns, cost/token usage, pinned node status,
    connectivity analysis, and at-risk nodes. Returns markdown.

    Args:
        days: Number of days to cover (default 14, max 90)
    """
    try:
        resp = requests.get(f"{API_BASE}/analytics",
                            params={"days": min(days, 90), "format": "markdown"},
                            timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        return f"Analytics unavailable: {e}"


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import os
    port = int(os.environ.get("SOMNIA_MCP_PORT", "8011"))
    logger.info(f"Starting Somnia MCP server on port {port}")
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/somnia")
