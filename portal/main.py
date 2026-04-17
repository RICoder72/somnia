"""
Somnia Portal — collaborative document and reports portal.
Serves at /portal, behind OAuth via nginx auth_request.

The landing page reads live from Quies's /portal/bundle endpoint on every
request. If Quies is unreachable, Portal falls back to the cached
portal-manifest.json that Quies writes after every dream cycle — so a
Quies outage degrades gracefully to last-dream staleness instead of a
blank page.

Per-domain lookups (file browser, reports, data pages) read label/icon/
docs_path from the cached manifest, since those change rarely and
per-request round-trips would be wasteful. The manifest stays as the
durable cache; the landing page is what needed live data, and now has it.

Routes:
  GET  /portal/                         Landing page (live from Quies)
  GET  /portal/files/{domain}           File browser (HTML)
  GET  /portal/api/files/{domain}       List files (JSON), ?path= for subdirs
  GET  /portal/api/download/{domain}    Download file, ?path=
  POST /portal/api/upload/{domain}      Upload file, ?path= for target subdir
  GET  /portal/files/{domain}/view      View a file inline, ?path=
  GET  /portal/reports/{domain}         Reports gallery (HTML)
  GET  /portal/reports/{domain}/view    Serve a report, ?file=
  GET  /portal/api/reports/{domain}     List reports (JSON)
  GET  /portal/data/{domain}            Data query UI (HTML)
  GET  /portal/api/data/{domain}        Run a saved query, ?query_id=
  GET  /portal/api/data/{domain}/list   List available queries (JSON)
  GET  /portal/health                   Health check

Manifest schema v1.1 additions:
  has_collab_space  bool  If False, node card shows no Files/Reports buttons.
                          Docs for this node live under another domain's folder.
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from pathlib import Path
from datetime import datetime, timezone
import json
import mimetypes
import os
import re
import asyncpg
import httpx

app = FastAPI(title="Somnia Portal")

# Filesystem roots
DATA_ROOT       = Path("/data")
WORKSPACES_ROOT = DATA_ROOT / "workspaces"
OUTPUTS_ROOT    = DATA_ROOT / "outputs"
MANIFEST_PATH  = OUTPUTS_ROOT / "portal-manifest.json"

# Share publish system (public /p/{uuid} route)
PUBLISH_ROOT = Path("/data/publish")
SHARES_FILE  = PUBLISH_ROOT / "_shares.json"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

CONFIG_FILE    = Path("/data/config/portal.json")
QUERIES_FILE   = Path("/data/config/portal-queries.json")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
DB_URL = os.environ.get(
    "PORTAL_DB_URL",
    "postgresql://portal_reader:PortalRead2026!@constellation-postgres:5432/constellation"
)

ALLOWED_EXTENSIONS = {
    ".pdf", ".md", ".txt", ".docx", ".xlsx", ".xls",
    ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".gif",
    ".csv", ".html", ".json", ".zip",
}

# Internal Quies Flask API (reachable on mcp-net)
QUIES_API = os.environ.get("QUIES_API_URL", "http://quies:8010")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_manifest_file() -> dict:
    """Load the cached portal-manifest.json. Emergency fallback only.

    Quies writes this after every dream cycle. The live /portal/bundle
    endpoint is the preferred source for the landing page; this file is
    what Portal reads when Quies is unreachable, and what get_domain_info
    reads for per-domain label/icon/docs_path lookups (since those change
    rarely and a round-trip per request would be wasteful).
    """
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"pinned_nodes": [], "somnia_health": {}, "solo_work": [], "recent_dreams": []}


async def fetch_portal_bundle() -> tuple[dict, str]:
    """Fetch the live landing-page bundle from Quies.

    Returns (bundle, source) where source is one of:
      - "live"   — fresh from Quies
      - "cached" — read from manifest file because Quies was unreachable
      - "empty"  — neither Quies nor the cached manifest was available
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{QUIES_API}/portal/bundle")
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data, "live"
    except Exception:
        pass  # fall through to cached manifest

    cached = load_manifest_file()
    if cached.get("pinned_nodes") or cached.get("somnia_health"):
        return cached, "cached"
    return cached, "empty"


def load_queries() -> list:
    try:
        return json.loads(QUERIES_FILE.read_text(encoding="utf-8")).get("queries", [])
    except Exception:
        return []


def get_domain_info(domain: str) -> dict:
    """
    Look up a domain from the cached manifest.

    Per-domain fields (label, icon, docs_path) change rarely, so this
    stays synchronous and reads the cached manifest file rather than
    hitting Quies on every file-browser click. Fresh landing data lives
    on the live bundle path; this is the stable-reference path.

    Falls back to portal.json for backwards compatibility.
    Raises 404 if not found in either source.
    """
    manifest = load_manifest_file()
    for node in manifest.get("pinned_nodes", []):
        if node["id"] == domain:
            return {
                "id": node["id"],
                "label": node.get("name", node["id"].replace("-", " ").title()),
                "icon": node.get("icon", "📁"),
                "description": node.get("description", ""),
                "docs_path": node.get("docs_path", f"workspaces/{domain}"),
                "needs_store": node.get("needs_store", False),
                "has_collab_space": node.get("has_collab_space", True),
                "status": node.get("status", ""),
            }
    # Fallback: portal.json (legacy)
    try:
        config = json.loads(CONFIG_FILE.read_text())
        for d in config.get("exposed_domains", []):
            if d["id"] == domain:
                return {**d, "label": d.get("label", d["id"]), "docs_path": f"workspaces/{domain}", "has_collab_space": True}
    except Exception:
        pass
    raise HTTPException(404, f"Domain '{domain}' not found")


def safe_resolve(base: Path, rel: str = "") -> Path:
    """Resolve path within base, rejecting traversal attempts."""
    target = (base / rel).resolve() if rel else base.resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "Path traversal not permitted")
    return target


def fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.1f} GB"


def relative_time(iso: str) -> str:
    """Return human-readable relative time from ISO timestamp."""
    if not iso:
        return ""
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        hours = delta.seconds // 3600
        if days == 0:
            if hours == 0:
                return "just now"
            return f"{hours}h ago"
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days}d ago"
        if days < 30:
            return f"{days // 7}w ago"
        return f"{days // 30}mo ago"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Shared HTML shell
# ---------------------------------------------------------------------------

STYLES = """
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg:       #0d1117;
      --surface:  #161b22;
      --border:   #30363d;
      --text:     #e6edf3;
      --muted:    #8b949e;
      --accent:   #58a6ff;
      --accent2:  #3fb950;
      --danger:   #f85149;
      --warn:     #d29922;
      --radius:   8px;
      --font:     -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      --mono:     "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
    }
    html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.6; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .topbar {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .topbar-logo {
      font-family: var(--mono); font-size: 16px; font-weight: 700;
      color: var(--accent); letter-spacing: -0.5px;
      text-decoration: none;
    }
    .topbar-logo:hover { text-decoration: none; color: var(--accent); }
    .topbar-nav { margin-left: auto; display: flex; gap: 4px; font-size: 13px; }
    .topbar-link {
      color: var(--muted);
      text-decoration: none;
      padding: 6px 14px;
      border-radius: var(--radius);
      transition: color 0.15s, background 0.15s;
    }
    .topbar-link:hover { color: var(--text); text-decoration: none; background: #ffffff08; }
    .topbar-link.active { color: var(--accent); background: #58a6ff15; font-weight: 500; }
    .topbar-link.active:hover { color: var(--accent); background: #58a6ff20; }
    .container { max-width: 1040px; margin: 0 auto; padding: 32px 24px; }
    .breadcrumb { font-size: 13px; color: var(--muted); margin-bottom: 20px; display: flex; align-items: center; gap: 6px; }
    .breadcrumb a { color: var(--muted); }
    .breadcrumb a:hover { color: var(--accent); }
    .breadcrumb .sep { color: var(--border); }
    h1 { font-size: 22px; font-weight: 600; margin-bottom: 6px; }
    .section-label {
      font-size: 11px; color: var(--muted); font-family: var(--mono);
      text-transform: uppercase; letter-spacing: 1px;
      margin-bottom: 12px; margin-top: 32px;
    }
    .card-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 16px;
      margin-top: 12px;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      transition: border-color 0.15s, box-shadow 0.15s;
      display: flex;
      flex-direction: column;
    }
    .card:hover { border-color: var(--accent); box-shadow: 0 0 0 1px #58a6ff22; }
    .card-icon { font-size: 26px; margin-bottom: 10px; }
    .card-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
    .card-desc { font-size: 12px; color: var(--muted); flex: 1; }
    .card-meta { margin-top: 10px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .card-links { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
    /* memory-only cards: slightly dimmer border, no cursor pointer */
    .card.memory-only { border-color: #30363d88; cursor: default; }
    .card.memory-only:hover { border-color: #58a6ff55; box-shadow: none; }
    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 6px 14px; border-radius: var(--radius);
      font-size: 12px; font-weight: 500; cursor: pointer;
      border: 1px solid transparent; transition: all 0.15s;
      text-decoration: none;
    }
    .btn:hover { text-decoration: none; }
    .btn-primary { background: var(--accent); color: #000; }
    .btn-primary:hover { background: #79c0ff; }
    .btn-secondary { background: transparent; border-color: var(--border); color: var(--text); }
    .btn-secondary:hover { border-color: var(--accent); color: var(--accent); }
    .btn-danger { background: transparent; border-color: #f8514988; color: var(--danger); }
    .btn-danger:hover { background: #f8514922; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-family: var(--mono); }
    .badge-blue  { background: #58a6ff22; color: var(--accent); }
    .badge-green { background: #3fb95022; color: var(--accent2); }
    .badge-warn  { background: #d2992222; color: var(--warn); }
    .badge-muted { background: #8b949e22; color: var(--muted); }
    .section-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
    }
    .file-table { width: 100%; border-collapse: collapse; }
    .file-table th { text-align: left; padding: 8px 12px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
    .file-table td { padding: 9px 12px; border-bottom: 1px solid #30363d66; font-size: 13px; }
    .file-table tr:hover td { background: var(--surface); }
    .file-table tr:last-child td { border-bottom: none; }
    .upload-zone {
      border: 2px dashed var(--border); border-radius: var(--radius);
      padding: 32px; text-align: center; cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      margin-top: 20px;
    }
    .upload-zone:hover, .upload-zone.dragover { border-color: var(--accent); background: #58a6ff08; }
    .upload-zone p { color: var(--muted); font-size: 13px; margin-top: 8px; }
    .alert { padding: 10px 16px; border-radius: var(--radius); font-size: 13px; margin: 12px 0; }
    .alert-success { background: #3fb95022; border: 1px solid #3fb95044; color: var(--accent2); }
    .alert-error   { background: #f8514922; border: 1px solid #f8514944; color: var(--danger); }
    .report-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; margin-top: 16px; }
    .report-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 16px;
      transition: border-color 0.15s;
    }
    .report-card:hover { border-color: var(--accent2); }
    .report-card-title { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
    .report-card-meta { font-size: 11px; color: var(--muted); font-family: var(--mono); }
    /* Health widget */
    .health-bar {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .health-stat {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px 16px;
    }
    .health-stat-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; font-family: var(--mono); }
    .health-stat-value { font-size: 20px; font-weight: 700; font-family: var(--mono); margin-top: 2px; }
    .health-stat-clickable { cursor: pointer; transition: border-color 0.15s, background 0.15s; }
    .health-stat-clickable:hover { border-color: #f85149; background: #f8514908; }
    .health-stat-clickable .health-stat-label { color: #f85149aa; }
    /* Error log modal */
    .modal-overlay {
      display: none; position: fixed; inset: 0;
      background: #010409cc; z-index: 2000;
      align-items: center; justify-content: center;
    }
    .modal-overlay.open { display: flex; }
    .modal-box {
      background: var(--bg); border: 1px solid var(--border);
      border-radius: var(--radius); width: 90%; max-width: 780px;
      max-height: 80vh; display: flex; flex-direction: column;
      box-shadow: 0 8px 32px #00000066;
    }
    .modal-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 14px 20px; border-bottom: 1px solid var(--border);
    }
    .modal-title { font-size: 14px; font-weight: 600; color: var(--danger); }
    .modal-close {
      background: none; border: none; color: var(--muted);
      font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1;
    }
    .modal-close:hover { color: var(--text); }
    .modal-body { overflow-y: auto; padding: 16px 20px; flex: 1; }
    .log-entry {
      border-bottom: 1px solid #30363d55; padding: 10px 0;
      font-size: 12px; font-family: var(--mono);
    }
    .log-entry:last-child { border-bottom: none; }
    .log-ts { color: var(--muted); margin-bottom: 3px; font-size: 11px; }
    .log-source { display: inline-block; background: #f8514922; color: #f85149; border-radius: 3px; padding: 1px 6px; font-size: 10px; margin-right: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
    .log-msg { color: var(--text); margin-top: 3px; word-break: break-word; line-height: 1.5; }
    /* Solo-work feed */
    .findings-list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
    .finding-row {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 12px 16px;
      display: flex; align-items: flex-start; gap: 12px;
    }
    .finding-meta { font-size: 11px; color: var(--muted); font-family: var(--mono); white-space: nowrap; min-width: 90px; padding-top: 2px; }
    .finding-summary { font-size: 13px; flex: 1; }
    .finding-sig { margin-left: auto; align-self: center; }
    #toast {
      position: fixed; bottom: 24px; right: 24px;
      padding: 10px 18px; border-radius: var(--radius);
      font-size: 13px; font-weight: 500;
      background: var(--surface); border: 1px solid var(--border);
      display: none; z-index: 1000;
    }
"""


def html_shell(title: str, body: str, extra_head: str = "", active: str = "portal") -> str:
    portal_cls = "topbar-link active" if active == "portal" else "topbar-link"
    dashboard_cls = "topbar-link active" if active == "dashboard" else "topbar-link"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Somnia</title>
  {extra_head}
  <style>{STYLES}</style>
</head>
<body>
  <div class="topbar">
    <a href="/portal" class="topbar-logo">✦ somnia</a>
    <nav class="topbar-nav">
      <a href="/portal" class="{portal_cls}">Portal</a>
      <a href="/dashboard" class="{dashboard_cls}">Dashboard</a>
    </nav>
  </div>
  <div class="container">
    {body}
  </div>
  <div id="toast"></div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Landing page — manifest-driven
# ---------------------------------------------------------------------------

@app.get("/portal/logs", response_class=JSONResponse)
async def portal_logs(level: str = "error", limit: int = 30):
    """Proxy recent system logs from Quies for dashboard drill-down."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{QUIES_API}/logs", params={"level": level, "limit": limit})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e), "logs": [], "count": 0}


@app.get("/portal", response_class=HTMLResponse)
@app.get("/portal/", response_class=HTMLResponse)
async def landing():
    bundle, source = await fetch_portal_bundle()
    nodes = bundle.get("pinned_nodes", [])
    health = bundle.get("somnia_health", {})
    solo_work = bundle.get("solo_work", [])
    generated_at = bundle.get("generated_at", "")
    generated_rel = relative_time(generated_at)

    # ── Node cards ────────────────────────────────────────────────────
    node_cards = ""
    for node in nodes:
        nid = node["id"]
        icon = node.get("icon", "📁")
        name = node.get("name", nid)
        desc = node.get("description", "")
        status = node.get("status", "")
        decay = node.get("decay", 1.0)
        last_activity = node.get("last_activity", "")
        has_collab = node.get("has_collab_space", True)

        # Status badge
        status_badge = ""
        if status:
            badge_class = "badge-green" if "active" in status.lower() else "badge-muted"
            status_badge = f'<span class="badge {badge_class}" style="font-size:10px">{status}</span>'

        # Decay indicator
        decay_pct = int(decay * 100)
        decay_color = "#3fb950" if decay > 0.7 else "#d29922" if decay > 0.4 else "#f85149"
        decay_badge = f'<span style="font-size:10px;color:{decay_color};font-family:var(--mono)" title="Memory decay">{decay_pct}%</span>'

        # Action buttons — only shown for nodes with their own workspace
        if has_collab:
            action_links = f"""
            <a href="/portal/files/{nid}" class="btn btn-secondary" style="font-size:11px;padding:5px 10px">📄 Files</a>
            <a href="/portal/reports/{nid}" class="btn btn-secondary" style="font-size:11px;padding:5px 10px">📊 Reports</a>
            {'<a href="/portal/data/' + nid + '" class="btn btn-secondary" style="font-size:11px;padding:5px 10px">🗄 Data</a>' if node.get("needs_store") else ''}"""
        else:
            # Memory-only node: no workspace, just a reference card
            action_links = '<span style="font-size:11px;color:var(--muted);font-style:italic">memory node — docs in parent domain</span>'

        card_class = "card" if has_collab else "card memory-only"

        node_cards += f"""
        <div class="{card_class}">
          <div class="card-icon">{icon}</div>
          <div class="card-title">{name}</div>
          <div class="card-desc">{desc}</div>
          <div class="card-meta">
            {status_badge}
            {decay_badge}
            {f'<span style="font-size:10px;color:var(--muted)">{last_activity}</span>' if last_activity else ''}
          </div>
          <div class="card-links">
            {action_links}
          </div>
        </div>"""

    if not node_cards:
        node_cards = '<p style="color:var(--muted);font-size:13px">No nodes provisioned yet. Run somnia_provision on pinned nodes to populate the portal.</p>'

    # ── Health stats ──────────────────────────────────────────────────
    def health_stat(label, value, color=None):
        style = f'color:{color}' if color else 'color:var(--text)'
        return f"""
        <div class="health-stat">
          <div class="health-stat-label">{label}</div>
          <div class="health-stat-value" style="{style}">{value}</div>
        </div>"""

    errors = health.get("errors_24h", 0)
    err_color = "#f85149" if errors > 0 else "#3fb950"
    daily_cost = health.get("daily_cost_usd", 0)
    daily_cap = health.get("daily_cap_usd", 2.0)
    cost_pct = int((daily_cost / daily_cap * 100) if daily_cap else 0)

    # Errors stat: clickable when > 0, opens log drill-down modal
    if errors > 0:
        err_stat_html = f"""
        <div class="health-stat health-stat-clickable" onclick="openErrorModal()" title="Click to view error log">
          <div class="health-stat-label">Errors 24h ↗</div>
          <div class="health-stat-value" style="color:{err_color}">{errors}</div>
        </div>"""
    else:
        err_stat_html = f"""
        <div class="health-stat">
          <div class="health-stat-label">Errors 24h</div>
          <div class="health-stat-value" style="color:{err_color}">{errors}</div>
        </div>"""

    health_html = f"""
    <div class="health-bar">
      {health_stat("Nodes", health.get("node_count", "—"))}
      {health_stat("Edges", health.get("edge_count", "—"))}
      {health_stat("Pinned", health.get("pinned_count", "—"))}
      {health_stat("Inbox", health.get("inbox_depth", "—"))}
      {health_stat("Dreams / 7d", health.get("dreams_last_7d", "—"))}
      {err_stat_html}
      {health_stat("Daily cost", f'${daily_cost:.3f} ({cost_pct}%)')}
    </div>"""

    # ── Solo-work feed ────────────────────────────────────────────────
    sig_colors = {
        "critical":    ("#f85149", "badge-blue"),
        "important":   ("#d29922", "badge-warn"),
        "interesting": ("#58a6ff", "badge-blue"),
        "minor":       ("#8b949e", "badge-muted"),
    }
    findings_html = ""
    for f in solo_work[:8]:
        date = f.get("date", "")
        summary = f.get("summary", "(no summary)")
        sig = f.get("max_significance", "")
        count = f.get("findings_count", 0)
        _, sig_badge_class = sig_colors.get(sig, ("#8b949e", "badge-muted"))
        sig_label = f'<span class="badge {sig_badge_class}">{sig}</span>' if sig else ""
        count_label = f'<span style="font-size:10px;color:var(--muted);font-family:var(--mono)">{count} finding{"s" if count != 1 else ""}</span>' if count else ""
        findings_html += f"""
        <div class="finding-row">
          <div class="finding-meta">{date}</div>
          <div class="finding-summary">{summary}</div>
          <div class="finding-sig" style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
            {count_label}
            {sig_label}
          </div>
        </div>"""
    if not findings_html:
        findings_html = '<p style="color:var(--muted);font-size:13px;padding:8px 0">No recent solo-work findings.</p>'

    # ── Data source indicator ─────────────────────────────────────────
    # When source=="live", the page was rendered from a fresh Quies call.
    # When source=="cached", Quies was unreachable and we fell back to the
    # manifest file written at the last dream cycle — show a warning so
    # the user knows the data may be stale.
    stale_warn = ""
    if source == "cached":
        stale_warn = (
            f'<div class="alert" style="background:var(--surface);border:1px solid var(--warn)44;color:var(--warn);font-size:12px;margin-top:16px">'
            f'⚠ Quies is unreachable — showing cached manifest from {generated_rel or "unknown time"}. '
            f'The landing page will refresh automatically once Quies is back.'
            f'</div>'
        )
    elif source == "empty":
        stale_warn = (
            '<div class="alert" style="background:var(--surface);border:1px solid var(--danger)44;color:var(--danger);font-size:12px;margin-top:16px">'
            '⚠ No portal data available — Quies is unreachable and no cached manifest was found. '
            'Check container health via <code>fleet_status</code>.'
            '</div>'
        )

    # Source label shown next to the title: "live" | "cached {time}" | "unavailable"
    if source == "live":
        source_label = '<span style="color:var(--accent2);font-size:12px;font-family:var(--mono)" title="Read live from Quies">● live</span>'
    elif source == "cached":
        source_label = f'<span style="color:var(--warn);font-size:12px;font-family:var(--mono)" title="Quies unreachable; cached manifest from {generated_at}">● cached {generated_rel}</span>'
    else:
        source_label = '<span style="color:var(--danger);font-size:12px;font-family:var(--mono)">● unavailable</span>'

    body = f"""
    <div style="margin-bottom: 8px; display:flex; align-items:baseline; gap:12px;">
      <h1>Somnia Portal</h1>
      {source_label}
    </div>
    <p style="color:var(--muted);margin-bottom:4px;font-size:13px">Documents, reports, and memory — all in one place.</p>
    {stale_warn}

    <div class="section-label">System Health</div>
    {health_html}

    <div class="section-label">Active Workspaces ({len(nodes)})</div>
    <div class="card-grid">
      {node_cards}
    </div>

    <div class="section-label">Solo-Work Activity</div>
    <div class="findings-list">
      {findings_html}
    </div>

    <!-- Error log modal -->
    <div class="modal-overlay" id="error-modal" onclick="if(event.target===this)closeErrorModal()">
      <div class="modal-box">
        <div class="modal-header">
          <span class="modal-title">⚠ Error Log — Last 24 Hours</span>
          <button class="modal-close" onclick="closeErrorModal()">×</button>
        </div>
        <div class="modal-body" id="error-modal-body">
          <p style="color:var(--muted);font-size:13px">Loading…</p>
        </div>
      </div>
    </div>

    <script>
    async function openErrorModal() {{
      document.getElementById('error-modal').classList.add('open');
      const body = document.getElementById('error-modal-body');
      body.innerHTML = '<p style="color:var(--muted);font-size:13px;font-family:var(--mono)">Fetching logs…</p>';
      try {{
        const r = await fetch('/portal/logs?level=error&limit=30');
        const data = await r.json();
        if (data.error) {{
          body.innerHTML = '<p style="color:var(--danger);font-size:12px;font-family:var(--mono)">Error: ' + data.error + '</p>';
          return;
        }}
        const logs = data.logs || [];
        if (!logs.length) {{
          body.innerHTML = '<p style="color:var(--muted);font-size:13px">No errors found.</p>';
          return;
        }}
        const summary = data.summary || {{}};
        let html = '<div style="font-size:11px;color:var(--muted);margin-bottom:14px;font-family:var(--mono)">';
        html += logs.length + ' most recent errors';
        if (summary.error) html += ' &nbsp;·&nbsp; ' + summary.error + ' total in log';
        html += '</div>';
        for (const e of logs) {{
          const ts = e.timestamp ? e.timestamp.replace('T',' ').slice(0,19) + ' UTC' : '—';
          const src = e.source || '?';
          const msg = e.message || '';
          let meta = '';
          if (e.metadata) {{
            try {{
              const m = typeof e.metadata === 'string' ? JSON.parse(e.metadata) : e.metadata;
              const parts = Object.entries(m).filter(([k]) => k !== 'dream_id').map(([k,v]) => k + '=' + JSON.stringify(v));
              if (parts.length) meta = '<div style="color:#8b949e;margin-top:3px;font-size:10px">' + parts.join('  ') + '</div>';
            }} catch(ex) {{}}
          }}
          html += '<div class="log-entry">';
          html += '<div class="log-ts">' + ts + '</div>';
          html += '<div><span class="log-source">' + src + '</span></div>';
          html += '<div class="log-msg">' + msg + '</div>';
          html += meta;
          html += '</div>';
        }}
        body.innerHTML = html;
      }} catch(ex) {{
        body.innerHTML = '<p style="color:var(--danger);font-size:12px;font-family:var(--mono)">Failed to load: ' + ex.message + '</p>';
      }}
    }}
    function closeErrorModal() {{
      document.getElementById('error-modal').classList.remove('open');
    }}
    document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeErrorModal(); }});
    </script>
    """

    return html_shell("Somnia Portal", body)


# ---------------------------------------------------------------------------
# File browser
# ---------------------------------------------------------------------------

@app.get("/portal/files/{domain}", response_class=HTMLResponse)
async def file_browser_page(domain: str):
    info = get_domain_info(domain)
    label = info.get("label", domain)
    icon = info.get("icon", "📁")

    body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <a href="/portal">{label}</a>
      <span class="sep">/</span>
      <span>Documents</span>
    </div>
    <div class="section-header">
      <h1>{icon} {label} — Documents</h1>
    </div>

    <div id="alert-area"></div>
    <div id="path-nav" style="font-family:var(--mono);font-size:12px;color:var(--muted);margin-bottom:16px;"></div>

    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
      <table class="file-table" id="file-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Modified</th>
            <th>Size</th>
            <th style="text-align:right;">Actions</th>
          </tr>
        </thead>
        <tbody id="file-tbody">
          <tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;">Loading…</td></tr>
        </tbody>
      </table>
    </div>

    <div class="upload-zone" id="upload-zone">
      <div style="font-size:24px;">⬆</div>
      <p>Drag & drop files here, or click to select</p>
      <p style="font-size:11px;margin-top:4px;">PDF, Word, Excel, PowerPoint, Markdown, images — max 50 MB</p>
      <input type="file" id="file-input" style="display:none" multiple accept=".pdf,.md,.txt,.docx,.xlsx,.xls,.pptx,.ppt,.png,.jpg,.jpeg,.gif,.csv,.html,.json,.zip">
    </div>
    <div id="upload-progress" style="margin-top:12px;"></div>

    <script>
      const DOMAIN = '{domain}';
      let currentPath = '';

      async function loadFiles(path) {{
        currentPath = path || '';
        const tbody = document.getElementById('file-tbody');
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;">Loading…</td></tr>';
        updatePathNav(currentPath);
        const res = await fetch('/portal/api/files/' + DOMAIN + '?path=' + encodeURIComponent(currentPath));
        if (!res.ok) {{ tbody.innerHTML = '<tr><td colspan="4" style="color:var(--danger)">Error loading files</td></tr>'; return; }}
        const data = await res.json();
        let rows = '';
        if (currentPath) {{
          const parent = currentPath.includes('/') ? currentPath.split('/').slice(0,-1).join('/') : '';
          rows += `<tr><td><a href="#" onclick="loadFiles('${{parent}}');return false;" style="color:var(--muted)">📂 ..</a></td><td></td><td></td><td></td></tr>`;
        }}
        for (const item of data.items) {{
          if (item.type === 'dir') {{
            rows += `<tr>
              <td><a href="#" onclick="loadFiles('${{item.path}}');return false;">📁 ${{item.name}}</a></td>
              <td style="color:var(--muted)">${{item.modified}}</td>
              <td></td><td style="text-align:right;"></td></tr>`;
          }} else {{
            const ext = item.name.split('.').pop().toLowerCase();
            const icon = {{pdf:'📄',md:'📝',docx:'📄',xlsx:'📊',pptx:'📊',png:'🖼',jpg:'🖼',jpeg:'🖼',gif:'🖼',csv:'📊',html:'🌐',json:'{{}}',zip:'📦'}}[ext] || '📄';
            rows += `<tr>
              <td>${{icon}} ${{item.name}}</td>
              <td style="color:var(--muted);font-family:var(--mono);font-size:12px">${{item.modified}}</td>
              <td style="color:var(--muted);font-family:var(--mono);font-size:12px">${{fmtSize(item.size)}}</td>
              <td style="text-align:right;">
                <span style="display:inline-flex;gap:6px;">
                  <a href="/portal/files/${{DOMAIN}}/view?path=${{encodeURIComponent(item.path)}}" target="_blank" class="btn btn-secondary" style="padding:4px 10px;font-size:11px;">👁 View</a>
                  <a href="/portal/api/download/${{DOMAIN}}?path=${{encodeURIComponent(item.path)}}" class="btn btn-primary" style="padding:4px 10px;font-size:11px;">↓</a>
                </span>
              </td></tr>`;
          }}
        }}
        if (!rows) rows = '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;">No files here yet.</td></tr>';
        tbody.innerHTML = rows;
      }}

      function updatePathNav(path) {{
        const nav = document.getElementById('path-nav');
        let parts = [{{label: '{domain}', path: ''}}];
        if (path) {{
          let acc = '';
          for (const seg of path.split('/')) {{
            acc = acc ? acc + '/' + seg : seg;
            parts.push({{label: seg, path: acc}});
          }}
        }}
        nav.innerHTML = parts.map((p, i) =>
          i < parts.length - 1
            ? `<a href="#" onclick="loadFiles('${{p.path}}');return false;" style="color:var(--muted)">${{p.label}}</a> / `
            : `<strong style="color:var(--text)">${{p.label}}</strong>`
        ).join('');
      }}

      function fmtSize(b) {{
        if (b == null) return '';
        const u = ['B','KB','MB','GB']; let i = 0;
        while (b >= 1024 && i < u.length-1) {{ b /= 1024; i++; }}
        return b.toFixed(i?1:0) + ' ' + u[i];
      }}

      function showAlert(msg, type) {{
        const el = document.getElementById('alert-area');
        el.innerHTML = `<div class="alert alert-${{type}}">${{msg}}</div>`;
        setTimeout(() => el.innerHTML = '', 4000);
      }}

      async function uploadFiles(files) {{
        const prog = document.getElementById('upload-progress');
        for (const file of files) {{
          prog.innerHTML = `<div class="alert" style="background:var(--surface);border:1px solid var(--border)">Uploading ${{file.name}}…</div>`;
          const fd = new FormData();
          fd.append('file', file);
          const url = '/portal/api/upload/' + DOMAIN + '?path=' + encodeURIComponent(currentPath);
          const res = await fetch(url, {{method:'POST', body:fd}});
          if (res.ok) showAlert('✓ Uploaded ' + file.name, 'success');
          else {{
            const err = await res.json().catch(() => ({{detail:'Upload failed'}}));
            showAlert('✗ ' + (err.detail || 'Upload failed'), 'error');
          }}
        }}
        prog.innerHTML = '';
        loadFiles(currentPath);
      }}

      const zone = document.getElementById('upload-zone');
      const input = document.getElementById('file-input');
      zone.addEventListener('click', () => input.click());
      input.addEventListener('change', () => uploadFiles(Array.from(input.files)));
      zone.addEventListener('dragover', e => {{ e.preventDefault(); zone.classList.add('dragover'); }});
      zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
      zone.addEventListener('drop', e => {{
        e.preventDefault(); zone.classList.remove('dragover');
        uploadFiles(Array.from(e.dataTransfer.files));
      }});

      loadFiles('');
    </script>"""

    return html_shell(f"{label} — Files", body)


# ---------------------------------------------------------------------------
# File API — serves from documents/{domain}/ (canonical storage)
# ---------------------------------------------------------------------------

@app.get("/portal/api/files/{domain}")
async def api_list_files(domain: str, path: str = Query("")):
    info = get_domain_info(domain)
    docs = DATA_ROOT / info["docs_path"]
    docs.mkdir(parents=True, exist_ok=True)
    target = safe_resolve(docs, path)
    if not target.exists():
        raise HTTPException(404, "Path not found")
    if not target.is_dir():
        raise HTTPException(400, "Path is not a directory")
    items = []
    for item in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        s = item.stat()
        items.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": s.st_size if item.is_file() else None,
            "modified": datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "path": str(item.relative_to(docs)),
        })
    return {"items": items, "current": path, "domain": domain}


@app.get("/portal/api/download/{domain}")
async def api_download(domain: str, path: str = Query(...)):
    info = get_domain_info(domain)
    docs = DATA_ROOT / info["docs_path"]
    target = safe_resolve(docs, path)
    if not target.is_file():
        raise HTTPException(404, "File not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, filename=target.name, media_type=mime or "application/octet-stream")


@app.get("/portal/files/{domain}/view", response_class=HTMLResponse)
async def view_file(domain: str, path: str = Query(...)):
    info = get_domain_info(domain)
    docs = DATA_ROOT / info["docs_path"]
    target = safe_resolve(docs, path)
    if not target.is_file():
        raise HTTPException(404, "File not found")

    ext = target.suffix.lower()
    label = info.get("label", domain)
    back_path = str(Path(path).parent) if "/" in path else ""
    back_url = f"/portal/files/{domain}" + (f"?path={back_path}" if back_path else "")

    if ext == ".md":
        raw = target.read_text(encoding="utf-8", errors="replace")
        escaped = raw.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <a href="/portal/files/{domain}">{label}</a>
      <span class="sep">/</span>
      <span>{target.name}</span>
    </div>
    <div class="view-toolbar" style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
      <a href="{back_url}" class="btn btn-secondary" style="font-size:12px;">← Back</a>
      <a href="/portal/api/download/{domain}?path={path}" class="btn btn-secondary" style="font-size:12px;">↓ Download</a>
      <button onclick="window.print()" class="btn btn-secondary" style="font-size:12px;cursor:pointer;">🖨 Print</button>
    </div>
    <div class="md-card" style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:32px 40px;max-width:860px;">
      <div id="md-content"></div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
    <style>
      #md-content h1,#md-content h2,#md-content h3 {{ color:var(--text);margin:1.2em 0 0.4em; }}
      #md-content h1 {{ font-size:1.6em;border-bottom:1px solid var(--border);padding-bottom:0.3em; }}
      #md-content h2 {{ font-size:1.25em;border-bottom:1px solid #30363d55;padding-bottom:0.2em; }}
      #md-content p {{ margin:0.7em 0; }}
      #md-content ul,#md-content ol {{ margin:0.5em 0 0.5em 1.5em; }}
      #md-content li {{ margin:0.2em 0; }}
      #md-content code {{ background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:1px 6px;font-family:var(--mono);font-size:0.9em;color:var(--accent); }}
      #md-content pre {{ background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:16px;overflow-x:auto;margin:1em 0; }}
      #md-content pre code {{ background:none;border:none;padding:0;color:var(--text); }}
      #md-content table {{ border-collapse:collapse;width:100%;margin:1em 0;font-size:13px; }}
      #md-content th {{ background:var(--bg);color:var(--muted);text-align:left;padding:8px 12px;border:1px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:0.5px; }}
      #md-content td {{ padding:8px 12px;border:1px solid #30363d66; }}
      #md-content tr:hover td {{ background:var(--bg); }}
      #md-content blockquote {{ border-left:3px solid var(--accent);margin:1em 0;padding:0.5em 1em;color:var(--muted); }}
      #md-content a {{ color:var(--accent); }}
      #md-content hr {{ border:none;border-top:1px solid var(--border);margin:1.5em 0; }}
      #md-content strong {{ color:var(--text);font-weight:600; }}
      @media print {{
        .topbar, .breadcrumb, .view-toolbar, #toast {{ display:none !important; }}
        body {{ background:#fff !important; }}
        .container {{ padding:0 !important; max-width:none !important; }}
        .md-card {{
          background:#fff !important;
          border:none !important;
          border-radius:0 !important;
          padding:0 !important;
          max-width:none !important;
          box-shadow:none !important;
        }}
        #md-content, #md-content p, #md-content li {{ color:#111 !important; }}
        #md-content h1, #md-content h2, #md-content h3 {{ color:#000 !important; }}
        #md-content h1 {{ border-bottom:1px solid #ccc !important; }}
        #md-content h2 {{ border-bottom:1px solid #eee !important; }}
        #md-content code {{ background:#f5f5f5 !important; border-color:#ddd !important; color:#c7254e !important; }}
        #md-content pre {{ background:#f5f5f5 !important; border-color:#ddd !important; }}
        #md-content pre code {{ color:#333 !important; }}
        #md-content th {{ background:#f0f0f0 !important; color:#333 !important; border-color:#ccc !important; }}
        #md-content td {{ border-color:#ddd !important; }}
        #md-content blockquote {{ border-left-color:#999 !important; color:#555 !important; }}
        #md-content a {{ color:#0066cc !important; }}
        #md-content tr:hover td {{ background:none !important; }}
      }}
    </style>
    <script>
      document.getElementById('md-content').innerHTML = marked.parse(`{escaped}`);
    </script>"""
        return html_shell(target.name, body)

    mime, _ = mimetypes.guess_type(str(target))
    return Response(
        content=target.read_bytes(),
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{target.name}"'}
    )


@app.post("/portal/api/upload/{domain}")
async def api_upload(domain: str, file: UploadFile = File(...), path: str = Query("")):
    info = get_domain_info(domain)
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type '{ext}' not permitted")
    docs = DATA_ROOT / info["docs_path"]
    dest_dir = safe_resolve(docs, path)
    if not dest_dir.is_dir():
        raise HTTPException(400, "Target path is not a directory")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds 50 MB limit")
    dest = dest_dir / file.filename
    dest.write_bytes(content)
    return {"ok": True, "saved": str(dest.relative_to(docs)), "bytes": len(content)}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.get("/portal/reports/{domain}", response_class=HTMLResponse)
async def reports_page(domain: str):
    info = get_domain_info(domain)
    label = info.get("label", domain)
    icon = info.get("icon", "📁")

    body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <a href="/portal">{label}</a>
      <span class="sep">/</span>
      <span>Reports</span>
    </div>
    <div class="section-header">
      <h1>{icon} {label} — Reports</h1>
    </div>
    <p style="color:var(--muted);font-size:13px;margin-bottom:20px;">HTML dashboards and reports published by Claude.</p>
    <div id="report-grid" class="report-grid"><p style="color:var(--muted)">Loading…</p></div>
    <script>
      const DOMAIN = '{domain}';
      async function loadReports() {{
        const grid = document.getElementById('report-grid');
        const res = await fetch('/portal/api/reports/' + DOMAIN);
        if (!res.ok) {{ grid.innerHTML = '<p style="color:var(--danger)">Error</p>'; return; }}
        const data = await res.json();
        if (!data.reports.length) {{
          grid.innerHTML = '<p style="color:var(--muted)">No reports yet. Ask Claude to generate one!</p>';
          return;
        }}
        grid.innerHTML = data.reports.map(r => `
          <a href="/portal/reports/${{DOMAIN}}/view?file=${{encodeURIComponent(r.filename)}}" style="text-decoration:none;">
            <div class="report-card">
              <div style="font-size:24px;margin-bottom:8px;">📊</div>
              <div class="report-card-title">${{r.name}}</div>
              <div class="report-card-meta">${{r.modified}}</div>
            </div>
          </a>`).join('');
      }}
      loadReports();
    </script>"""
    return html_shell(f"{label} — Reports", body)


@app.get("/portal/reports/{domain}/view", response_class=HTMLResponse)
async def view_report(domain: str, file: str = Query(...)):
    info = get_domain_info(domain)
    rdir = DATA_ROOT / info["docs_path"] / "reports"
    target = safe_resolve(rdir, file)
    if not target.is_file() or target.suffix not in (".html", ".htm"):
        raise HTTPException(404, "Report not found")
    return HTMLResponse(content=target.read_text())


@app.get("/portal/api/reports/{domain}")
async def api_list_reports(domain: str):
    info = get_domain_info(domain)
    rdir = DATA_ROOT / info["docs_path"] / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    reports = []
    for f in sorted(rdir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix in (".html", ".htm"):
            s = f.stat()
            reports.append({
                "name": f.stem.replace("-", " ").replace("_", " ").title(),
                "filename": f.name,
                "modified": datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d"),
                "size": s.st_size,
            })
    return {"reports": reports, "domain": domain}


# ---------------------------------------------------------------------------
# Data — saved queries
# ---------------------------------------------------------------------------

@app.get("/portal/data/{domain}", response_class=HTMLResponse)
async def data_page(domain: str):
    info = get_domain_info(domain)
    label = info.get("label", domain)
    icon = info.get("icon", "📁")
    queries = [q for q in load_queries() if q.get("domain") == domain]
    query_options = "".join(
        f'<option value="{q["id"]}">{q["label"]} — {q["description"]}</option>'
        for q in queries
    )
    queries_js = json.dumps([{"id": q["id"], "label": q["label"], "description": q["description"]} for q in queries])

    body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <a href="/portal">{label}</a>
      <span class="sep">/</span>
      <span>Data</span>
    </div>
    <div class="section-header">
      <h1>{icon} {label} — Data</h1>
    </div>
    <p style="color:var(--muted);font-size:13px;margin-bottom:24px;">Saved queries — read-only. Ask Claude to add new queries.</p>
    <div style="display:flex;gap:12px;align-items:flex-end;margin-bottom:20px;flex-wrap:wrap;">
      <div style="flex:1;min-width:280px;">
        <label style="display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">Query</label>
        <select id="query-select" style="width:100%;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:13px;cursor:pointer;">
          <option value="">— select a query —</option>
          {query_options}
        </select>
      </div>
      <button id="run-btn" class="btn btn-primary" style="padding:8px 20px;" onclick="runQuery()">▶ Run</button>
      <button id="csv-btn" class="btn btn-secondary" style="padding:8px 16px;display:none;" onclick="exportCSV()">↓ CSV</button>
    </div>
    <div id="query-desc" style="font-size:12px;color:var(--muted);margin-bottom:16px;min-height:18px;"></div>
    <div id="result-meta" style="font-size:12px;color:var(--muted);margin-bottom:8px;font-family:var(--mono);"></div>
    <div id="result-area" style="overflow-x:auto;">
      <p style="color:var(--muted);text-align:center;padding:40px 0;">Select a query and click Run.</p>
    </div>
    <script>
      const DOMAIN = '{domain}';
      let currentData = null, currentColumns = null;
      const queries = {queries_js};
      document.getElementById('query-select').addEventListener('change', function() {{
        const q = queries.find(x => x.id === this.value);
        document.getElementById('query-desc').textContent = q ? q.description : '';
        document.getElementById('csv-btn').style.display = 'none';
        currentData = null;
      }});
      async function runQuery() {{
        const id = document.getElementById('query-select').value;
        if (!id) {{ alert('Select a query first'); return; }}
        const btn = document.getElementById('run-btn');
        btn.textContent = '⏳'; btn.disabled = true;
        document.getElementById('result-area').innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px 0;">Running…</p>';
        document.getElementById('result-meta').textContent = '';
        try {{
          const res = await fetch(`/portal/api/data/${{DOMAIN}}?query_id=${{encodeURIComponent(id)}}`);
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Query failed');
          currentData = data.rows; currentColumns = data.columns;
          document.getElementById('result-meta').textContent = `${{data.rows.length}} row${{data.rows.length !== 1 ? 's' : ''}}${{data.elapsed_ms ? ' · '+data.elapsed_ms+'ms' : ''}}`;
          if (!data.rows.length) {{
            document.getElementById('result-area').innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px 0;">No results.</p>';
          }} else {{
            document.getElementById('result-area').innerHTML = buildTable(data.columns, data.rows);
            document.getElementById('csv-btn').style.display = 'inline-flex';
          }}
        }} catch(e) {{
          document.getElementById('result-area').innerHTML = `<div class="alert alert-error">Error: ${{e.message}}</div>`;
        }} finally {{ btn.textContent = '▶ Run'; btn.disabled = false; }}
      }}
      function buildTable(cols, rows) {{
        const header = cols.map(c => `<th>${{c}}</th>`).join('');
        const body = rows.map(row =>
          '<tr>' + cols.map(c => {{
            const v = row[c];
            if (v === null || v === undefined || v === '') return '<td style="color:var(--border)">—</td>';
            return `<td>${{String(v)}}</td>`;
          }}).join('') + '</tr>').join('');
        return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;"><table class="file-table" style="min-width:100%"><thead><tr>${{header}}</tr></thead><tbody>${{body}}</tbody></table></div>`;
      }}
      function exportCSV() {{
        if (!currentData || !currentColumns) return;
        const rows = [currentColumns.join(',')].concat(
          currentData.map(row => currentColumns.map(c => `"${{String(row[c]??'').replace(/"/g,'""')}}"`).join(','))
        );
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([rows.join('\\n')], {{type:'text/csv'}}));
        a.download = `${{DOMAIN}}-${{new Date().toISOString().slice(0,10)}}.csv`;
        a.click();
      }}
    </script>"""
    return html_shell(f"{label} — Data", body)


@app.get("/portal/api/data/{domain}/list")
async def api_list_queries(domain: str):
    get_domain_info(domain)
    queries = [q for q in load_queries() if q.get("domain") == domain]
    return {"queries": [{"id": q["id"], "label": q["label"], "description": q["description"]} for q in queries]}


@app.get("/portal/api/data/{domain}")
async def api_run_query(domain: str, query_id: str = Query(...)):
    get_domain_info(domain)
    queries = load_queries()
    query = next((q for q in queries if q["id"] == query_id and q.get("domain") == domain), None)
    if not query:
        raise HTTPException(404, f"Query '{query_id}' not found")
    sql = query["sql"].strip()
    if not sql.upper().startswith("SELECT"):
        raise HTTPException(400, "Only SELECT queries are permitted")
    try:
        import time
        t0 = time.monotonic()
        conn = await asyncpg.connect(DB_URL)
        try:
            rows = await conn.fetch(sql)
        finally:
            await conn.close()
        elapsed = round((time.monotonic() - t0) * 1000)
        if not rows:
            return {"columns": [], "rows": [], "elapsed_ms": elapsed}
        columns = list(rows[0].keys())
        result = [{col: (row[col].isoformat() if hasattr(row[col], 'isoformat') else row[col]) for col in columns} for row in rows]
        return {"columns": columns, "rows": result, "elapsed_ms": elapsed}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Public share links — GET /p/{uuid}  (no auth — UUID is the credential)
# ---------------------------------------------------------------------------

def _load_shares_manifest() -> dict:
    try:
        return json.loads(SHARES_FILE.read_text())
    except Exception:
        return {"version": 1, "shares": {}}


def share_shell(title: str, body: str) -> str:
    """Minimal HTML shell for public share pages — no portal nav, no auth links."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>{STYLES}
  .share-hero {{
    max-width: 520px; margin: 80px auto; text-align: center; padding: 0 24px;
  }}
  .share-hero-icon {{ font-size: 52px; margin-bottom: 16px; line-height: 1; }}
  .share-hero h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 12px; }}
  .share-hero p {{ color: var(--muted); font-size: 14px; line-height: 1.7; }}
  .share-footer {{
    position: fixed; bottom: 20px; width: 100%; text-align: center;
    font-size: 11px; color: #30363d; font-family: var(--mono);
    pointer-events: none;
  }}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-logo">✦ somnia</div>
  </div>
  {body}
  <div class="share-footer">share link · somnia</div>
</body>
</html>"""


@app.get("/p/{uuid}")
async def serve_share(uuid: str):
    """Public share endpoint — UUID is the credential. No auth required.

    Returns the file with the correct Content-Type if the share is valid and
    not expired. Returns styled error pages (not bare HTTP errors) otherwise.
    """
    # ── 1. Validate UUID format ────────────────────────────────────────────
    if not _UUID_RE.match(uuid):
        body = """
  <div class="share-hero">
    <div class="share-hero-icon">🔍</div>
    <h1>Invalid link</h1>
    <p>This doesn't look like a valid Somnia share link.</p>
  </div>"""
        return HTMLResponse(share_shell("Invalid link — Somnia", body), status_code=400)

    # ── 2. Look up share entry ─────────────────────────────────────────────
    shares = _load_shares_manifest().get("shares", {})
    entry  = shares.get(uuid.lower()) or shares.get(uuid)

    if not entry:
        body = """
  <div class="share-hero">
    <div class="share-hero-icon">🔍</div>
    <h1>Link not found</h1>
    <p>This share link doesn't exist or has been revoked.<br>
    If someone sent you this link, ask them to generate a fresh one.</p>
  </div>"""
        return HTMLResponse(share_shell("Not found — Somnia", body), status_code=404)

    # ── 3. Check expiry ────────────────────────────────────────────────────
    try:
        expires_at = datetime.fromisoformat(entry["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            expired_date = entry.get("expires_at", "")[:10]
            fname = entry.get("filename", "this file")
            body = f"""
  <div class="share-hero">
    <div class="share-hero-icon">⌛</div>
    <h1>This link has expired</h1>
    <p>The share for <strong>{fname}</strong> expired on {expired_date}.<br>
    Contact the person who shared it with you to get a new link.</p>
  </div>"""
            return HTMLResponse(share_shell("Expired — Somnia", body), status_code=410)
    except Exception:
        pass  # malformed expiry — treat as non-expired, let the file check decide

    # ── 4. Resolve and verify file on disk ─────────────────────────────────
    rel_path  = entry.get("path", "")
    file_path = (PUBLISH_ROOT / rel_path).resolve()

    # Path traversal guard
    if not str(file_path).startswith(str(PUBLISH_ROOT.resolve())):
        return HTMLResponse(
            share_shell("Error — Somnia", '<div class="share-hero"><div class="share-hero-icon">⚠</div><h1>Error</h1><p>Invalid share path.</p></div>'),
            status_code=500,
        )

    if not file_path.exists() or not file_path.is_file():
        body = f"""
  <div class="share-hero">
    <div class="share-hero-icon">⚠</div>
    <h1>File unavailable</h1>
    <p>The link is valid, but the underlying file could not be found on the server.<br>
    The file may have been moved or deleted — contact the person who shared it.</p>
  </div>"""
        return HTMLResponse(share_shell("Unavailable — Somnia", body), status_code=410)

    # ── 5. Serve ───────────────────────────────────────────────────────────
    content_type = (
        entry.get("content_type")
        or mimetypes.guess_type(str(file_path))[0]
        or "application/octet-stream"
    )
    filename = entry.get("filename", file_path.name)

    # Inline types render in browser; everything else triggers download
    _INLINE_TYPES = {
        "application/pdf", "text/html", "text/plain",
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    }
    disposition = (
        "inline"
        if content_type in _INLINE_TYPES
        else f'attachment; filename="{filename}"'
    )

    return Response(
        content=file_path.read_bytes(),
        media_type=content_type,
        headers={"Content-Disposition": disposition},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/portal/health")
async def health():
    quies_ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{QUIES_API}/")
            quies_ok = r.status_code < 500
    except Exception:
        pass
    return {
        "status": "ok",
        "service": "portal",
        "manifest_cache": MANIFEST_PATH.exists(),
        "quies_reachable": quies_ok,
    }
