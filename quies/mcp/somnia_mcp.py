#!/usr/bin/env python3
"""
Somnia MCP Server

Provides memory tools to Claude via Model Context Protocol.
Thin layer over the Somnia Flask API (running on the same container).

Tools:
  somnia_remember   - Add an observation to the inbox
  somnia_recall     - Search for relevant memories by topic
  somnia_pin        - Pin or unpin a node
  somnia_session    - Layer 0 awareness dashboard
  somnia_status     - Quick health/state check
  somnia_journal    - Review dreams from recent inactive periods
  somnia_analytics  - Generate analytics report
  somnia_provision  - Provision storage for a pinned node; regenerate portal manifest
  somnia_dream      - Trigger a dream cycle (optionally force-bypassing readiness checks)
"""

import json
import glob
import logging
import os
import re
import requests
from datetime import datetime, timezone
from pathlib import Path
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = "http://localhost:8010"

# Filesystem roots — these are the canonical paths inside the container,
# which map to the shared /data volume accessible by all Constellation services.
DATA_ROOT      = Path("/data")
DOCUMENTS_ROOT = DATA_ROOT / "documents"
OUTPUTS_ROOT   = DATA_ROOT / "outputs"
SOLO_WORK_DIR  = DATA_ROOT / "somnia" / "solo-work"
MANIFEST_PATH  = OUTPUTS_ROOT / "portal-manifest.json"

mcp = FastMCP("Somnia")


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

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
        pass  # Best-effort, never break tool calls


def _get_pinned_node(node_id: str) -> dict | None:
    """
    Fetch a specific pinned node from the graph by ID.
    Returns the node dict or None if not found / not pinned.
    """
    result = _api_get("/search", params={"q": node_id, "limit": 20})
    if "error" in result:
        return None
    for node in result.get("nodes", []):
        if node.get("id") == node_id and node.get("pinned"):
            return node
    return None


def _get_all_pinned_nodes() -> list[dict]:
    """Return all currently pinned nodes from the session endpoint."""
    result = _api_get("/session")
    if "error" in result:
        return []
    return result.get("pinned", [])


def _read_solo_work_summary(filepath: Path) -> dict:
    """
    Extract metadata from a solo-work markdown file.
    Returns a dict with filename, date, time, summary, findings_count,
    max_significance, and pinned_nodes_reviewed.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError:
        return {}

    fname = filepath.name
    # Filename format: solo-work-YYYY-MM-DD_HHMM.md
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4})", fname)
    date_str = date_match.group(1) if date_match else ""
    time_str = date_match.group(2) if date_match else ""

    # Extract Summary line
    summary = ""
    m = re.search(r"\*\*Summary:\*\*\s*(.+)", text)
    if m:
        summary = m.group(1).strip()

    # Extract pinned nodes reviewed
    pinned_reviewed = []
    m = re.search(r"\*\*Pinned nodes reviewed:\*\*\s*(.+)", text)
    if m:
        pinned_reviewed = [x.strip() for x in m.group(1).split(",")]

    # Count finding blocks (## headers that aren't the title)
    findings = re.findall(r"^##\s+[^#]", text, re.MULTILINE)
    findings_count = len(findings)

    # Determine max significance
    significance_order = {"critical": 4, "important": 3, "interesting": 2, "minor": 1}
    found_significances = re.findall(r"\*\*Significance:\*\*\s*(\w+)", text)
    max_sig = max(
        (significance_order.get(s.lower(), 0) for s in found_significances),
        default=0
    )
    sig_reverse = {v: k for k, v in significance_order.items()}
    max_significance = sig_reverse.get(max_sig, "")

    return {
        "filename": fname,
        "path": f"somnia/solo-work/{fname}",
        "date": date_str,
        "time": time_str,
        "summary": summary,
        "pinned_nodes_reviewed": pinned_reviewed,
        "findings_count": findings_count,
        "max_significance": max_significance,
    }


def _build_manifest(source: str = "somnia_provision") -> dict:
    """
    Build the full portal manifest by gathering:
    - All pinned nodes with portal_visible=true and provisioned=true
    - Current Somnia health stats
    - Last 14 days of solo-work files
    - Last 10 dream summaries
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── Pinned nodes ──────────────────────────────────────────────────
    all_pinned = _get_all_pinned_nodes()
    portal_nodes = []
    for p in all_pinned:
        props = p.get("properties", {})
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}

        # Only include nodes that are explicitly provisioned and portal-visible
        if not props.get("portal_visible", False):
            continue
        if not props.get("provisioned", False):
            continue

        portal_nodes.append({
            "id": p.get("id", ""),
            "name": props.get("name", p.get("id", "").replace("-", " ").title()),
            "description": props.get("description", p.get("summary", "")),
            "icon": props.get("icon", "📁"),
            "status": props.get("status", ""),
            "decay": round(p.get("decay", 1.0), 2),
            "portal_visible": True,
            # has_collab_space: controls whether Files/Reports buttons appear.
            # True (default) = node has its own document workspace.
            # False = memory-only node; docs live under another domain.
            "has_collab_space": props.get("has_collab_space", True),
            "needs_store": props.get("needs_store", False),
            "store_ready": props.get("store_ready", False),
            "provisioned": True,
            "docs_path": props.get("docs_path", f"documents/{p.get('id','')}"),
            "store_domain": props.get("store_domain", ""),
            "last_activity": (p.get("last_touched") or now)[:10],
            "last_provisioned": props.get("last_provisioned", ""),
        })

    # ── Somnia health ─────────────────────────────────────────────────
    status = _api_get("/status")
    graph = status.get("graph", {})
    budget = status.get("budget", {})
    last_dream = status.get("last_dream") or {}
    health = {
        "node_count": graph.get("node_count", 0),
        "edge_count": graph.get("edge_count", 0),
        "inbox_depth": graph.get("inbox_pending", 0),
        "pinned_count": graph.get("pinned_count", 0),
        "avg_decay": round(graph.get("avg_decay", 1.0), 3),
        "errors_24h": status.get("errors_24h", 0),
        "warnings_24h": status.get("warnings_24h", 0),
        "last_dream_at": last_dream.get("ended_at", ""),
        "daily_cost_usd": round(budget.get("daily_cost", 0), 4),
        "daily_cap_usd": round(budget.get("daily_cap", 2.0), 2),
    }

    # Count dreams last 7 days
    dreams_result = _api_get("/dreams", params={"limit": 50})
    recent_dreams_list = dreams_result.get("dreams", [])
    dreams_7d = 0
    dream_summaries = []
    for d in recent_dreams_list:
        ended = d.get("ended_at", "")
        if ended:
            try:
                dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - dt
                if delta.days <= 7:
                    dreams_7d += 1
            except Exception:
                pass
        if len(dream_summaries) < 10:
            dream_summaries.append({
                "dream_id": d.get("id", ""),
                "mode": d.get("mode", "process"),
                "ended_at": ended,
                "duration_seconds": d.get("duration_seconds", 0),
                "summary": (d.get("summary") or "")[:200],
                "nodes_created": len(d.get("nodes_created", [])),
                "edges_created": len(d.get("edges_created", [])),
            })
    health["dreams_last_7d"] = dreams_7d

    # ── Solo-work (last 14 days) ───────────────────────────────────────
    solo_work_entries = []
    if SOLO_WORK_DIR.exists():
        files = sorted(SOLO_WORK_DIR.glob("solo-work-*.md"), reverse=True)
        cutoff = datetime.now(timezone.utc)
        for f in files:
            summary = _read_solo_work_summary(f)
            if not summary:
                continue
            # Include if within 14 days
            date_str = summary.get("date", "")
            if date_str:
                try:
                    file_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                    if (cutoff - file_date).days > 14:
                        break  # Files are sorted newest-first; stop when we go past 14d
                except Exception:
                    pass
            solo_work_entries.append(summary)

    return {
        "schema_version": "1.1",
        "generated_at": now,
        "generated_by": source,
        "pinned_nodes": portal_nodes,
        "somnia_health": health,
        "solo_work": solo_work_entries,
        "recent_dreams": dream_summaries,
    }


def _write_manifest(source: str = "somnia_provision") -> str:
    """Build and write the portal manifest. Returns a status string."""
    try:
        manifest = _build_manifest(source=source)
        OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        node_count = len(manifest["pinned_nodes"])
        solo_count = len(manifest["solo_work"])
        return f"Manifest written: {node_count} portal node(s), {solo_count} solo-work entries → {MANIFEST_PATH}"
    except Exception as e:
        return f"Manifest write failed: {e}"


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

    return f"Noted: {content[:80]}{'...' if len(content) > 80 else ''}"


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
    lines = [f"Found {len(nodes)} memory/memories for '{query}' "
             f"({stm_count} recent, {ltm_count} long-term, {sltm_count} faded):\n"]

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
    overwritten). After pinning a new node you intend to work on, call
    somnia_provision to create its storage footprint and portal card.

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
    return (
        f"{action}: {id}" + (f" — {content[:60]}" if content else "") +
        "\n\nRun somnia_provision to create storage and portal card."
        if created else
        f"{action}: {id}" + (f" — {content[:60]}" if content else "")
    )


@mcp.tool()
def somnia_provision(
    id: str,
    needs_store: bool = False,
    portal_visible: bool = True,
    has_collab_space: bool = True,
    icon: str = "📁",
    name: str = "",
    description: str = "",
    refresh_manifest_only: bool = False,
) -> str:
    """
    Provision the storage footprint for a pinned node, then regenerate
    the portal manifest.

    Call this immediately after pinning a new node you want to work on —
    don't wait for a dream cycle. Also usable to update portal metadata
    (icon, description, visibility) on an existing node.

    What it does:
      1. Verifies the node is pinned in the graph
      2. Creates documents/{slug}/ if missing (skipped if has_collab_space=False)
      3. Writes documents/{slug}/README.md if missing (skipped if has_collab_space=False)
      4. Updates the node's properties: provisioned, docs_path, portal_visible,
         has_collab_space, needs_store, icon, name, description, last_provisioned
      5. Regenerates outputs/portal-manifest.json

    portal_visible vs has_collab_space:
      portal_visible=False  — node is memory-only; no card appears in the portal at all
      has_collab_space=False — card appears in the portal but without Files/Reports buttons;
                               docs live under a parent domain's folder

    If needs_store=True, the manifest will flag store_ready=False until
    you confirm the Store domain has been initialized via Vigil tools,
    then call somnia_provision again with the same id to refresh.

    Use refresh_manifest_only=True to skip filesystem/node changes and
    just regenerate the manifest from current state (e.g. after a dream
    cycle updates health data).

    Args:
        id:                   Pinned node ID (kebab-case)
        needs_store:          Whether this node requires a Store domain
        portal_visible:       Whether to show a portal card for this node at all
        has_collab_space:     Whether this node has its own document workspace
                              (Files/Reports buttons). Set False for nodes whose
                              docs live under another domain's folder.
        icon:                 Emoji icon for the portal card
        name:                 Display name (defaults to title-cased id)
        description:          Short description for the portal card
        refresh_manifest_only: Only regenerate manifest, skip other steps
    """
    _record_activity("provision", {"id": id, "refresh_only": refresh_manifest_only})

    # ── Manifest-only mode ────────────────────────────────────────────
    if refresh_manifest_only:
        msg = _write_manifest(source="somnia_provision:refresh")
        return f"Manifest refreshed.\n{msg}"

    # ── Verify node is pinned ─────────────────────────────────────────
    node = _get_pinned_node(id)
    if node is None:
        return (
            f"Node '{id}' not found or not pinned. "
            f"Pin it first with somnia_pin, then call somnia_provision."
        )

    steps = []
    display_name = name or id.replace("-", " ").title()
    desc_line = description or node.get("summary", "")

    # ── Create documents/{id}/ and README (only if has_collab_space) ──
    if has_collab_space:
        docs_path = DOCUMENTS_ROOT / id
        try:
            docs_path.mkdir(parents=True, exist_ok=True)
            steps.append(f"✅ documents/{id}/ — ready")
        except PermissionError as e:
            steps.append(
                f"⚠️  documents/{id}/ — permission error: {e}\n"
                f"   Workaround: create via Vigil fs_mkdir /data/documents/{id}"
            )

        readme_path = docs_path / "README.md"
        if not readme_path.exists():
            try:
                readme_content = (
                    f"# {display_name}\n\n{desc_line}\n\n"
                    f"---\n\n"
                    f"This folder is the canonical document store for the **{id}** "
                    f"track in Somnia.\n\n"
                    f"All files, reports, and designs related to {display_name} "
                    f"live here. The portal reads from this location.\n\n"
                    f"_Provisioned: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}_\n"
                )
                readme_path.write_text(readme_content, encoding="utf-8")
                steps.append(f"✅ documents/{id}/README.md — written")
            except PermissionError as e:
                steps.append(
                    f"⚠️  documents/{id}/README.md — permission error: {e}\n"
                    f"   Workaround: create via Vigil fs_write /data/documents/{id}/README.md"
                )
        else:
            steps.append(f"ℹ️  documents/{id}/README.md — already exists, skipped")
    else:
        steps.append(f"ℹ️  has_collab_space=False — skipping filesystem provisioning")

    # ── Update node properties ────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    new_props = {
        "provisioned": True,
        "portal_visible": portal_visible,
        "has_collab_space": has_collab_space,
        "needs_store": needs_store,
        "store_ready": False,   # Must be confirmed separately via Vigil store tools
        "docs_path": f"documents/{id}",
        "store_domain": id if needs_store else "",
        "icon": icon,
        "name": display_name,
        "description": desc_line,
        "last_provisioned": now_iso,
    }

    pin_result = _api_post(f"/nodes/{id}/pin", {"properties": new_props})
    if "error" in pin_result:
        steps.append(f"⚠️  Node property update failed: {pin_result['error']}")
    else:
        steps.append(
            f"✅ Node properties updated "
            f"(portal_visible={portal_visible}, has_collab_space={has_collab_space})"
        )

    # ── Store domain reminder ─────────────────────────────────────────
    if needs_store:
        steps.append(
            f"\n📋 Store domain needed: Run Vigil store_register_type for domain '{id}', "
            f"then call somnia_provision(id='{id}', needs_store=True) again to mark store_ready=True."
        )

    # ── Regenerate manifest ───────────────────────────────────────────
    manifest_msg = _write_manifest(source=f"somnia_provision:{id}")
    steps.append(f"✅ {manifest_msg}")

    return f"Provisioned: {id}\n\n" + "\n".join(steps)


@mcp.tool()
def somnia_dream(
    mode: str = "process",
    force: bool = False,
) -> str:
    """
    Trigger a Somnia dream cycle.

    Modes:
      process    — Consolidate STM inbox into the graph (requires inbox items)
      ruminate   — Deep reflection on existing graph structure and connections
      solo_work  — Autonomous research and wondering session

    Set force=True to bypass readiness checks (e.g. insufficient inbox items,
    budget cooldown, recent dream). Useful immediately after pinning/provisioning
    a new node when you want Somnia to process it right away.

    Note: Dreams run synchronously and may take 30-120 seconds. The tool
    waits for completion and returns a summary.

    Args:
        mode:  Dream mode — process | ruminate | solo_work
        force: Bypass readiness checks (default False)
    """
    valid_modes = ("process", "ruminate", "solo_work")
    if mode not in valid_modes:
        return f"Invalid mode '{mode}'. Must be one of: {', '.join(valid_modes)}"

    _record_activity("dream_trigger", {"mode": mode, "force": force})

    payload = {"mode": mode, "force": force}

    try:
        resp = requests.post(
            f"{API_BASE}/consolidate",
            json=payload,
            timeout=180,   # Dreams can take up to 3 minutes
        )
        resp.raise_for_status()
        result = resp.json()
    except requests.Timeout:
        return (
            "Dream timed out waiting for response (>180s). "
            "It may still be running — check somnia_status or somnia_journal."
        )
    except requests.RequestException as e:
        return f"Dream request failed: {e}"

    if "error" in result:
        reason = result.get("reason", result.get("stderr", ""))
        return f"Dream rejected: {result['error']}\nReason: {reason}"

    ops = result.get("operations", {})
    gb = result.get("graph_before", {})
    ga = result.get("graph_after", {})
    dream_id = result.get("dream_id", "?")
    duration = result.get("duration_seconds", "?")
    summary = result.get("summary", "(no summary)")
    reflections = result.get("reflections", "")

    lines = [
        f"Dream complete [{mode}{'·forced' if force else ''}]",
        f"  ID:        {dream_id[:16]}...",
        f"  Duration:  {duration}s",
        f"  Nodes:     {gb.get('node_count','?')} → {ga.get('node_count','?')}  "
        f"(+{ops.get('nodes_created',0)} created)",
        f"  Edges:     {gb.get('edge_count','?')} → {ga.get('edge_count','?')}  "
        f"(+{ops.get('edges_created',0)} new, {ops.get('edges_reinforced',0)} reinforced)",
        f"  Inbox:     cleared {ops.get('inbox_processed',0)} items",
        "",
        f"  Summary: {summary}",
    ]
    if reflections:
        lines.append(f"  Reflections: {reflections[:300]}")
    if ops.get("errors"):
        lines.append(f"  ⚠️  Errors: {ops['errors']}")

    return "\n".join(lines)


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

    # Check system health
    status = _api_get("/status")
    errors = status.get("errors_24h", 0)
    warnings = status.get("warnings_24h", 0)

    lines = ["Somnia Session Dashboard"]
    lines.append(f"  Server time: {datetime.now(timezone.utc).isoformat()[:19]+'+0000'}")
    lines.append(f"  Graph: {graph.get('nodes', 0)} nodes, {graph.get('edges', 0)} edges, "
                 f"{graph.get('inbox', 0)} inbox")
    if dreams_since > 0:
        lines.append(f"  Dreams since last conversation: {dreams_since}")
    if recent_findings:
        lines.append(f"  Solo-work findings: {len(recent_findings)} recent")
    if errors or warnings:
        lines.append(f"  ⚠️ System health (24h): {errors} errors, {warnings} warnings")
    lines.append("")

    if pinned:
        lines.append(f"Pinned ({len(pinned)}):")
        for p in pinned:
            status_val = f" [{p['status']}]" if p.get('status') else ""
            lines.append(f"  📌 {p['id']}{status_val} — {p['summary']}")
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
        lines.append(
            f"  Budget: ${budget.get('daily_cost', 0):.2f} / ${budget.get('daily_cap', 2.0):.2f} today"
            f" (${budget.get('remaining', 0):.2f} remaining)"
        )

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
        resp = requests.get(
            f"{API_BASE}/analytics",
            params={"days": min(days, 90), "format": "markdown"},
            timeout=30
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        return f"Analytics unavailable: {e}"


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("SOMNIA_MCP_PORT", "8011"))
    logger.info(f"Starting Somnia MCP server on port {port}")
    mcp.run(transport="http", host="0.0.0.0", path="/somnia", port=port)
