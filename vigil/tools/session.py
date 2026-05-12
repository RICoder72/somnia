"""
Session tools — session_start and ping.
"""

import json
import logging
import sqlite3

import requests
from datetime import datetime
from pathlib import Path
from fastmcp import FastMCP, Context

from config import WORKSPACES_DIR, DATA_ROOT, DOMAIN_TRIGGERS_FILE

logger = logging.getLogger("vigil.session")
SOMNIA_ROUTE_URL = "http://quies:8010/route"
from core.bindings import set_active_workspace

SOMNIA_DB = DATA_ROOT / "somnia" / "db" / "somnia.db"




def _route_via_somnia(message: str) -> list[dict] | None:
    """Call somnia_route stored procedure for graph-native workspace routing.

    Returns list of candidates [{workspace, confidence, reason, ...}]
    or None if Somnia is unreachable.
    """
    if not message:
        return None
    try:
        r = requests.get(SOMNIA_ROUTE_URL, params={"message": message, "limit": 5}, timeout=3)
        if r.status_code == 200:
            return r.json().get("candidates", [])
        logger.warning(f"somnia_route returned {r.status_code}")
        return None
    except requests.RequestException as e:
        logger.warning(f"somnia_route unreachable: {e}")
        return None


def _load_domain_config() -> dict:
    if DOMAIN_TRIGGERS_FILE.exists():
        try:
            return json.loads(DOMAIN_TRIGGERS_FILE.read_text())
        except Exception:
            pass
    return {}


def _detect_domain(text: str, keywords: dict) -> str | None:
    """Detect domain from text based on keywords."""
    text_lower = text.lower()
    for domain, kws in keywords.items():
        for kw in kws:
            if kw in text_lower:
                return domain
    return None


def _get_available_domains(config: dict) -> list[dict]:
    if not WORKSPACES_DIR.exists():
        return []

    domains = []
    for item in sorted(WORKSPACES_DIR.iterdir()):
        if item.is_dir() and not item.name.startswith("_"):
            cfg = config.get(item.name, {})
            domains.append({
                "name": item.name,
                "description": cfg.get("description", ""),
                "keywords": cfg.get("triggers", []),
            })
    return domains


def _get_somnia_digest() -> str | None:
    """Query Somnia DB for dreams since last user interaction.

    Returns a formatted digest string, or None if no dreams occurred
    or Somnia DB is unavailable.
    """
    if not SOMNIA_DB.exists():
        return None

    try:
        conn = sqlite3.connect(str(SOMNIA_DB))
        conn.row_factory = sqlite3.Row

        # Find last user interaction
        cursor = conn.execute(
            "SELECT timestamp FROM activity "
            "WHERE type IN ('recall', 'remember', 'status') "
            "ORDER BY timestamp DESC LIMIT 1"
        )
        row = cursor.fetchone()
        last_interaction = row["timestamp"] if row else None

        if not last_interaction:
            conn.close()
            return None

        # Get dreams since last interaction
        cursor = conn.execute(
            "SELECT started_at, ended_at, summary, reflections, "
            "       nodes_created, edges_created, edges_reinforced "
            "FROM dream_log "
            "WHERE started_at > ? AND ended_at IS NOT NULL "
            "ORDER BY started_at ASC",
            (last_interaction,),
        )
        dreams = [dict(r) for r in cursor.fetchall()]

        # Get graph stats
        cursor = conn.execute("SELECT COUNT(*) as n FROM nodes")
        node_count = cursor.fetchone()["n"]
        cursor = conn.execute("SELECT COUNT(*) as n FROM edges")
        edge_count = cursor.fetchone()["n"]
        cursor = conn.execute(
            "SELECT COUNT(*) as n FROM inbox WHERE processed = 0"
        )
        inbox_depth = cursor.fetchone()["n"]

        conn.close()

        if not dreams:
            return None

        # Calculate time since last interaction
        last_dt = datetime.fromisoformat(last_interaction)
        gap_hours = round(
            (datetime.now() - last_dt).total_seconds() / 3600, 1
        )

        # Build digest
        lines = [
            "☾ **Somnia Dream Digest**",
            f"   {len(dreams)} cycle(s) while you were away ({gap_hours}h)",
            "",
        ]

        consolidations = []
        ruminations = []

        for d in dreams:
            summary = d["summary"] or ""
            nodes = json.loads(d["nodes_created"]) if d["nodes_created"] else []
            edges = json.loads(d["edges_created"]) if d["edges_created"] else []
            reinforced = (
                json.loads(d["edges_reinforced"]) if d["edges_reinforced"] else []
            )

            is_rumination = (
                summary.startswith("[ruminate]") or len(nodes) == 0
            )

            entry = {
                "summary": summary.replace("[process] ", "").replace("[ruminate] ", ""),
                "reflections": d["reflections"] or "",
                "nodes": len(nodes),
                "edges": len(edges),
                "reinforced": len(reinforced),
            }

            if is_rumination:
                ruminations.append(entry)
            else:
                consolidations.append(entry)

        if consolidations:
            lines.append(f"   **Dreams** ({len(consolidations)}):")
            for c in consolidations:
                lines.append(f"   • {c['summary']}")
                if c["nodes"]:
                    lines.append(
                        f"     ({c['nodes']} nodes, {c['edges']} edges created)"
                    )
            lines.append("")

        if ruminations:
            lines.append(f"   **Ruminations** ({len(ruminations)}):")
            for r in ruminations:
                lines.append(f"   • {r['summary']}")
            lines.append("")

        # Flag anything particularly interesting
        # (dreams with reflections that contain strong signals)
        notable_reflections = []
        for d in dreams:
            refl = d["reflections"] or ""
            if any(
                kw in refl.lower()
                for kw in [
                    "fascinating",
                    "struck",
                    "significant",
                    "surprising",
                    "curious",
                    "tension",
                    "pattern",
                    "missing",
                    "interesting",
                ]
            ):
                notable_reflections.append(refl)

        if notable_reflections:
            lines.append("   💭 **Notable reflection:**")
            # Just include the most recent notable one to keep it concise
            lines.append(f"   {notable_reflections[-1][:300]}")
            if len(notable_reflections[-1]) > 300:
                lines.append("   (...)")
            lines.append("")

        lines.append(
            f"   📊 Graph: {node_count} nodes, {edge_count} edges"
            f" | Inbox: {inbox_depth} pending"
        )
        lines.append("")
        lines.append(
            "   ↳ If any dreams seem interesting, mention them naturally "
            "at the start of the conversation."
        )

        return "\n".join(lines)

    except Exception as e:
        return f"☾ Somnia: digest unavailable ({e})"


def register(mcp: FastMCP):
    domain_config = _load_domain_config()
    domain_keywords = {
        d: c.get("triggers", []) for d, c in domain_config.items()
    }

    @mcp.tool()
    async def session_start(ctx: Context, user_message: str = "") -> str:
        """Initialize a Vigil session with plugin and domain detection."""
        lines = ["🔭 Vigil Session Started", "─" * 40, ""]

        # ── Somnia digest (dreams since last interaction) ──────────
        digest = _get_somnia_digest()
        if digest:
            lines.append(digest)
            lines.append("─" * 40)
            lines.append("")

        # ── Available domains ─────────────────────────────────────
        domains = _get_available_domains(domain_config)
        if domains:
            lines.append("📚 **Available Domains**")
            for d in domains:
                desc = f" - {d['description']}" if d["description"] else ""
                triggers = (
                    f" (triggers: {', '.join(d['keywords'][:3])})"
                    if d["keywords"]
                    else " ⚠️ no triggers"
                )
                lines.append(f"   • {d['name']}{desc}{triggers}")
            lines.append("")

        # ── Auto-detect domain (via Somnia graph routing) ─────────
        detected = None
        route_candidates = []
        if user_message:
            # Try graph-native routing first
            route_candidates = _route_via_somnia(user_message) or []
            if route_candidates:
                top = route_candidates[0]
                if top["confidence"] >= 0.5:
                    # High confidence — auto-activate
                    detected = top["workspace"]
                    lines.append(f"🎯 Auto-detected domain: {detected}")
                    lines.append("")
                else:
                    # Low confidence — show candidates but don't auto-activate
                    lines.append("🔍 **Possible workspaces** (low confidence):")
                    for c in route_candidates[:3]:
                        lines.append(
                            f"   • {c['workspace']} ({c['confidence']:.0%}) — {c['reason']}"
                        )
                    lines.append(
                        "   Use `workspace_activate` to set one explicitly."
                    )
                    lines.append("")
            else:
                # Fallback to legacy trigger matching if Somnia unreachable
                detected = _detect_domain(user_message, domain_keywords)
                if detected:
                    lines.append(f"🎯 Auto-detected domain: {detected} (via fallback)")
                    lines.append("")

        # ── Track active workspace for binding resolution ─────────
        await set_active_workspace(ctx, detected)

        if detected:
            domain_path = WORKSPACES_DIR / detected
            context_file = domain_path / f"{detected}.md"
            if context_file.exists():
                lines.append(f"📖 Loaded domain: {detected}")
                lines.append("")
                lines.append(context_file.read_text())
        else:
            lines.append(
                "💡 No specific domain detected. "
                "Mention a topic or use `fs_read` on a domain file. "
                "Call `workspace_activate` to set the active workspace explicitly."
            )

        # ── Global instructions ───────────────────────────────────
        instructions_file = DATA_ROOT / "INSTRUCTIONS.md"
        if instructions_file.exists():
            try:
                content = instructions_file.read_text().strip()
                if content:
                    lines.append("")
                    lines.append("📋 **Global Instructions**")
                    lines.append("─" * 30)
                    lines.append(content)
            except Exception:
                pass

        return "\n".join(lines)

    @mcp.tool()
    async def workspace_activate(ctx: Context, name: str = "") -> str:
        """Set (or clear) the active workspace for this session.

        Claude is the source of truth for which workspace a conversation
        is actually in. `session_start`'s trigger-word detection makes a
        best guess from the first user message, but conversations drift,
        span workspaces, or start with generic messages that don't match
        any triggers. Call this whenever you notice the conversation has
        moved — before fs or Store calls that should resolve relative
        paths and default domains against the new context.

        Args:
          name: Workspace to activate (e.g. "myworkspace", "somnia").
                Pass "" or "_clear" to unset the active workspace.

        Returns a confirmation plus a summary of the workspace's bindings
        and scope, so you can verify the switch did what you expected.
        """
        if not name or name == "_clear":
            await set_active_workspace(ctx, None)
            return "🎯 Active workspace cleared. No implicit scope or binding resolution."

        # Validate the workspace exists
        target = WORKSPACES_DIR / name
        if not target.exists() or not target.is_dir():
            available = sorted(
                p.name for p in WORKSPACES_DIR.iterdir()
                if p.is_dir() and not p.name.startswith("_")
            )
            return (
                f"❌ No workspace named '{name}'.\n"
                f"Available: {', '.join(available)}"
            )

        await set_active_workspace(ctx, name)

        # Show what just happened — bindings + scope summary
        from core.bindings import describe_bindings
        from core.scope import describe_scope

        bindings = await describe_bindings(ctx)
        scope = await describe_scope(ctx)

        lines = [f"🎯 Active workspace: **{name}**", ""]

        identity = bindings.get("identity", {})
        if bindings.get("has_bindings_file") and identity:
            lines.append("**Identity bindings:**")
            for rtype, binding in sorted(identity.items()):
                primary = binding.get("primary", "?")
                fallbacks = binding.get("fallbacks", [])
                extra = f" (fallbacks: {', '.join(fallbacks)})" if fallbacks else ""
                lines.append(f"  • {rtype}: {primary}{extra}")
        else:
            lines.append(
                "**Identity bindings:** none declared (explicit account= required for identity-bound tools)"
            )

        fs = scope.get("filesystem") or {}
        ds = scope.get("datastore") or {}
        lines.append("")
        lines.append(f"**Filesystem scope** ({fs.get('source', '?')}):")
        for root in fs.get("writable_roots", []):
            lines.append(f"  • writable: {root}")
        for root in fs.get("readable_roots", []):
            if root not in fs.get("writable_roots", []):
                lines.append(f"  • readable: {root}")
        lines.append("")
        lines.append(f"**Datastore scope** ({ds.get('source', '?')}):")
        lines.append(f"  • default domain: {ds.get('default_domain', '?')}")
        if ds.get("allowed_domains", []) != [ds.get("default_domain")]:
            lines.append(f"  • allowed: {', '.join(ds.get('allowed_domains', []))}")

        return "\n".join(lines)

    @mcp.tool()
    async def workspace_show(ctx: Context) -> str:
        """Show the currently active workspace and its bindings/scope.

        Useful when you're about to do something that depends on workspace
        context (file writes, Store operations) and want to confirm what
        the resolver will see.
        """
        from core.bindings import get_active_workspace, describe_bindings
        from core.scope import describe_scope

        ws = await get_active_workspace(ctx)
        if not ws:
            return (
                "🎯 No active workspace. Scope is advisory-only and bindings "
                "resolution will fail with MissingBindingError unless you pass "
                "account= / workspace= explicitly.\n"
                "\n"
                "Call `workspace_activate(name)` to set one."
            )

        bindings = await describe_bindings(ctx)
        scope = await describe_scope(ctx)

        lines = [f"🎯 Active workspace: **{ws}**", ""]

        identity = bindings.get("identity", {})
        lines.append("**Identity bindings:**")
        if identity:
            for rtype, binding in sorted(identity.items()):
                primary = binding.get("primary", "?")
                fallbacks = binding.get("fallbacks", [])
                extra = f" (fallbacks: {', '.join(fallbacks)})" if fallbacks else ""
                lines.append(f"  • {rtype}: {primary}{extra}")
        else:
            lines.append("  (none)")

        fs = scope.get("filesystem") or {}
        ds = scope.get("datastore") or {}
        lines.append("")
        lines.append(f"**Filesystem scope** ({fs.get('source', '?')}):")
        for root in fs.get("writable_roots", []):
            lines.append(f"  • writable: {root}")
        for root in fs.get("readable_roots", []):
            if root not in fs.get("writable_roots", []):
                lines.append(f"  • readable: {root}")
        lines.append("")
        lines.append(f"**Datastore scope** ({ds.get('source', '?')}):")
        lines.append(f"  • default domain: {ds.get('default_domain', '?')}")
        if ds.get("allowed_domains", []) != [ds.get("default_domain")]:
            lines.append(f"  • allowed: {', '.join(ds.get('allowed_domains', []))}")

        return "\n".join(lines)

    @mcp.tool()
    def ping() -> str:
        """Health check. Returns pong if Vigil is running."""
        return "pong from Vigil 🔭"
