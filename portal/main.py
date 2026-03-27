"""
Constellation Portal — collaborative document and reports portal.
Serves at /portal, behind OAuth via nginx auth_request.

Routes:
  GET  /portal/                         Landing page
  GET  /portal/files/{domain}           File browser (HTML)
  GET  /portal/api/files/{domain}       List files (JSON), ?path= for subdirs
  GET  /portal/api/download/{domain}    Download file, ?path=
  POST /portal/api/upload/{domain}      Upload file, ?path= for target subdir
  GET  /portal/reports/{domain}         Reports gallery (HTML)
  GET  /portal/reports/{domain}/view    Serve a report, ?file=
  GET  /portal/api/reports/{domain}     List reports (JSON)
  GET  /portal/data/{domain}            Data query UI (HTML)
  GET  /portal/api/data/{domain}        Run a saved query, ?query_id=
  GET  /portal/api/data/{domain}/list   List available queries (JSON)
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pathlib import Path
from datetime import datetime
import json
import mimetypes
import os
import asyncpg

app = FastAPI(title="Constellation Portal")

DOMAINS_ROOT = Path("/data/domains")
CONFIG_FILE = Path("/data/config/portal.json")
QUERIES_FILE = Path("/data/config/portal-queries.json")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
DB_URL = os.environ.get("PORTAL_DB_URL", "postgresql://portal_reader:PortalRead2026!@constellation-postgres:5432/constellation")

ALLOWED_EXTENSIONS = {
    ".pdf", ".md", ".txt", ".docx", ".xlsx", ".xls",
    ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".gif",
    ".csv", ".html", ".json", ".zip",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"exposed_domains": []}


def load_queries() -> list:
    try:
        with open(QUERIES_FILE) as f:
            return json.load(f).get("queries", [])
    except Exception:
        return []


def get_domain_info(domain: str) -> dict:
    config = load_config()
    for d in config.get("exposed_domains", []):
        if d["id"] == domain:
            return d
    raise HTTPException(404, f"Domain '{domain}' not found or not exposed")


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


# ---------------------------------------------------------------------------
# Shared HTML shell
# ---------------------------------------------------------------------------

def html_shell(title: str, body: str, extra_head: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Constellation</title>
  {extra_head}
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
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
    }}
    html, body {{ height: 100%; background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.6; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .topbar {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
    }}
    .topbar-logo {{ font-family: var(--mono); font-size: 16px; font-weight: 700; color: var(--accent); letter-spacing: -0.5px; }}
    .topbar-logo span {{ color: var(--muted); font-weight: 400; }}
    .topbar-nav {{ margin-left: auto; display: flex; gap: 20px; font-size: 13px; color: var(--muted); }}
    .topbar-nav a {{ color: var(--muted); }}
    .topbar-nav a:hover {{ color: var(--text); }}
    .container {{ max-width: 960px; margin: 0 auto; padding: 32px 24px; }}
    .breadcrumb {{ font-size: 13px; color: var(--muted); margin-bottom: 20px; display: flex; align-items: center; gap: 6px; }}
    .breadcrumb a {{ color: var(--muted); }}
    .breadcrumb a:hover {{ color: var(--accent); }}
    .breadcrumb .sep {{ color: var(--border); }}
    h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 6px; }}
    h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: var(--muted); font-family: var(--mono); text-transform: uppercase; letter-spacing: 1px; font-size: 11px; }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 16px;
      margin-top: 24px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      cursor: pointer;
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    .card:hover {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent)22; }}
    .card-icon {{ font-size: 28px; margin-bottom: 10px; }}
    .card-title {{ font-size: 15px; font-weight: 600; margin-bottom: 4px; }}
    .card-desc {{ font-size: 12px; color: var(--muted); }}
    .card-links {{ display: flex; gap: 10px; margin-top: 14px; }}
    .btn {{
      display: inline-flex; align-items: center; gap: 6px;
      padding: 6px 14px; border-radius: var(--radius);
      font-size: 12px; font-weight: 500; cursor: pointer;
      border: 1px solid transparent; transition: all 0.15s;
      text-decoration: none;
    }}
    .btn-primary {{ background: var(--accent); color: #000; }}
    .btn-primary:hover {{ background: #79c0ff; text-decoration: none; }}
    .btn-secondary {{ background: transparent; border-color: var(--border); color: var(--text); }}
    .btn-secondary:hover {{ border-color: var(--accent); color: var(--accent); text-decoration: none; }}
    .btn-danger {{ background: transparent; border-color: var(--danger)88; color: var(--danger); }}
    .btn-danger:hover {{ background: var(--danger)22; text-decoration: none; }}
    .section-header {{
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
    }}
    .file-table {{ width: 100%; border-collapse: collapse; }}
    .file-table th {{ text-align: left; padding: 8px 12px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }}
    .file-table td {{ padding: 9px 12px; border-bottom: 1px solid var(--border)66; font-size: 13px; }}
    .file-table tr:hover td {{ background: var(--surface); }}
    .file-table tr:last-child td {{ border-bottom: none; }}
    .file-icon {{ margin-right: 6px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-family: var(--mono); }}
    .badge-blue {{ background: var(--accent)22; color: var(--accent); }}
    .badge-green {{ background: var(--accent2)22; color: var(--accent2); }}
    .upload-zone {{
      border: 2px dashed var(--border); border-radius: var(--radius);
      padding: 32px; text-align: center; cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      margin-top: 20px;
    }}
    .upload-zone:hover, .upload-zone.dragover {{ border-color: var(--accent); background: var(--accent)08; }}
    .upload-zone p {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
    .alert {{ padding: 10px 16px; border-radius: var(--radius); font-size: 13px; margin: 12px 0; }}
    .alert-success {{ background: var(--accent2)22; border: 1px solid var(--accent2)44; color: var(--accent2); }}
    .alert-error {{ background: var(--danger)22; border: 1px solid var(--danger)44; color: var(--danger); }}
    .report-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; margin-top: 16px; }}
    .report-card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 16px;
      transition: border-color 0.15s;
    }}
    .report-card:hover {{ border-color: var(--accent2); }}
    .report-card-title {{ font-size: 14px; font-weight: 600; margin-bottom: 4px; }}
    .report-card-meta {{ font-size: 11px; color: var(--muted); font-family: var(--mono); }}
    #toast {{
      position: fixed; bottom: 24px; right: 24px;
      padding: 10px 18px; border-radius: var(--radius);
      font-size: 13px; font-weight: 500;
      background: var(--surface); border: 1px solid var(--border);
      display: none; z-index: 1000;
    }}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-logo">✦ constellation <span>/ portal</span></div>
    <nav class="topbar-nav">
      <a href="/portal">Home</a>
      <a href="/dashboard" target="_blank">Somnia Dashboard ↗</a>
    </nav>
  </div>
  <div class="container">
    {body}
  </div>
  <div id="toast"></div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

@app.get("/portal", response_class=HTMLResponse)
@app.get("/portal/", response_class=HTMLResponse)
async def landing():
    config = load_config()
    domains = config.get("exposed_domains", [])

    domain_cards = ""
    for d in domains:
        domain_cards += f"""
        <div class="card">
          <div class="card-icon">{d.get('icon', '📁')}</div>
          <div class="card-title">{d.get('label', d['id'])}</div>
          <div class="card-desc">{d.get('description', '')}</div>
          <div class="card-links">
            <a href="/portal/files/{d['id']}" class="btn btn-secondary">📄 Files</a>
            <a href="/portal/reports/{d['id']}" class="btn btn-secondary">📊 Reports</a>
            <a href="/portal/data/{d['id']}" class="btn btn-secondary">🗄 Data</a>
          </div>
        </div>"""

    body = f"""
    <div style="margin-bottom: 32px;">
      <h1>Constellation Portal</h1>
      <p style="color: var(--muted); margin-top: 6px;">Collaborative workspace — documents, reports, and dashboards.</p>
    </div>

    <div style="margin-bottom: 40px;">
      <h2>Quick Links</h2>
      <div class="card-grid">
        <a href="/dashboard" target="_blank" style="text-decoration:none;">
          <div class="card">
            <div class="card-icon">🧠</div>
            <div class="card-title">Somnia Dashboard</div>
            <div class="card-desc">Memory graph, analytics, and system health</div>
          </div>
        </a>
      </div>
    </div>

    <div>
      <h2>Active Projects</h2>
      <div class="card-grid">
        {domain_cards if domain_cards else '<p style="color:var(--muted)">No domains configured.</p>'}
      </div>
    </div>"""

    return html_shell("Portal", body)


# ---------------------------------------------------------------------------
# File browser
# ---------------------------------------------------------------------------

@app.get("/portal/files/{domain}", response_class=HTMLResponse)
async def file_browser_page(domain: str):
    info = get_domain_info(domain)
    body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <span>{info.get('label', domain)}</span>
      <span class="sep">/</span>
      <span>Documents</span>
    </div>
    <div class="section-header">
      <h1>{info.get('icon','📁')} {info.get('label', domain)} — Documents</h1>
    </div>

    <div id="alert-area"></div>

    <!-- Breadcrumb path nav -->
    <div id="path-nav" style="font-family:var(--mono);font-size:12px;color:var(--muted);margin-bottom:16px;"></div>

    <!-- File listing -->
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

    <!-- Upload zone -->
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
          rows += `<tr>
            <td><a href="#" onclick="loadFiles('${{parent}}');return false;" style="color:var(--muted)">📂 ..</a></td>
            <td></td><td></td><td></td></tr>`;
        }}
        for (const item of data.items) {{
          if (item.type === 'dir') {{
            rows += `<tr>
              <td><a href="#" onclick="loadFiles('${{item.path}}');return false;">📁 ${{item.name}}</a></td>
              <td style="color:var(--muted)">${{item.modified}}</td>
              <td></td>
              <td style="text-align:right;"></td></tr>`;
          }} else {{
            const ext = item.name.split('.').pop().toLowerCase();
            const icon = {{pdf:'📄',md:'📝',docx:'📄',xlsx:'📊',pptx:'📊',png:'🖼',jpg:'🖼',jpeg:'🖼',gif:'🖼',csv:'📊',html:'🌐',json:'{{}}',zip:'📦'}}[ext] || '📄';
            rows += `<tr>
              <td>${{icon}} ${{item.name}}</td>
              <td style="color:var(--muted);font-family:var(--mono);font-size:12px">${{item.modified}}</td>
              <td style="color:var(--muted);font-family:var(--mono);font-size:12px">${{fmtSize(item.size)}}</td>
              <td style="text-align:right;display:flex;gap:6px;justify-content:flex-end;align-items:center;">
                <a href="/portal/files/${{DOMAIN}}/view?path=${{encodeURIComponent(item.path)}}" target="_blank" class="btn btn-secondary" style="padding:4px 10px;font-size:11px;">👁 View</a>
                <a href="/portal/api/download/${{DOMAIN}}?path=${{encodeURIComponent(item.path)}}" class="btn btn-primary" style="padding:4px 10px;font-size:11px;">↓ Download</a>
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
        const units = ['B','KB','MB','GB'];
        let i = 0;
        while (b >= 1024 && i < units.length - 1) {{ b /= 1024; i++; }}
        return b.toFixed(i ? 1 : 0) + ' ' + units[i];
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
          if (res.ok) {{
            showAlert(`✓ Uploaded ${{file.name}}`, 'success');
          }} else {{
            const err = await res.json().catch(() => ({{detail:'Upload failed'}}));
            showAlert(`✗ ${{err.detail || 'Upload failed'}}`, 'error');
          }}
        }}
        prog.innerHTML = '';
        loadFiles(currentPath);
      }}

      // Upload zone
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

    return html_shell(f"{info.get('label', domain)} — Files", body)


# ---------------------------------------------------------------------------
# File API
# ---------------------------------------------------------------------------

@app.get("/portal/api/files/{domain}")
async def api_list_files(domain: str, path: str = Query("")):
    info = get_domain_info(domain)
    docs = DOMAINS_ROOT / domain / "files"
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
    docs = DOMAINS_ROOT / domain / "files"
    target = safe_resolve(docs, path)
    if not target.is_file():
        raise HTTPException(404, "File not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, filename=target.name, media_type=mime or "application/octet-stream")


@app.get("/portal/files/{domain}/view", response_class=HTMLResponse)
async def view_file(domain: str, path: str = Query(...)):
    """View a file inline in the browser. Markdown files are rendered as HTML."""
    info = get_domain_info(domain)
    docs = DOMAINS_ROOT / domain / "files"
    target = safe_resolve(docs, path)
    if not target.is_file():
        raise HTTPException(404, "File not found")

    ext = target.suffix.lower()

    # Markdown: render client-side using marked.js
    if ext == ".md":
        raw = target.read_text(encoding="utf-8", errors="replace")
        # Escape backticks and template literals for JS embedding
        escaped = raw.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        label = info.get("label", domain)
        breadcrumb_path = str(Path(path).parent) if "/" in path else ""
        back_url = f"/portal/files/{domain}{'?path=' + breadcrumb_path if breadcrumb_path else ''}"
        body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <a href="/portal/files/{domain}">{label}</a>
      <span class="sep">/</span>
      <span>{target.name}</span>
    </div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
      <a href="{back_url}" class="btn btn-secondary" style="font-size:12px;">← Back</a>
      <a href="/portal/api/download/{domain}?path={path}" class="btn btn-secondary" style="font-size:12px;">↓ Download</a>
    </div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:32px 40px;max-width:860px;">
      <div id="md-content"></div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
    <style>
      #md-content h1,#md-content h2,#md-content h3 {{ color:var(--text);margin:1.2em 0 0.4em; }}
      #md-content h1 {{ font-size:1.6em;border-bottom:1px solid var(--border);padding-bottom:0.3em; }}
      #md-content h2 {{ font-size:1.25em;border-bottom:1px solid var(--border)55;padding-bottom:0.2em; }}
      #md-content h3 {{ font-size:1.05em; }}
      #md-content p {{ margin:0.7em 0;color:var(--text); }}
      #md-content ul,#md-content ol {{ margin:0.5em 0 0.5em 1.5em;color:var(--text); }}
      #md-content li {{ margin:0.2em 0; }}
      #md-content code {{ background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:1px 6px;font-family:var(--mono);font-size:0.9em;color:var(--accent); }}
      #md-content pre {{ background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:16px;overflow-x:auto;margin:1em 0; }}
      #md-content pre code {{ background:none;border:none;padding:0;color:var(--text); }}
      #md-content table {{ border-collapse:collapse;width:100%;margin:1em 0;font-size:13px; }}
      #md-content th {{ background:var(--bg);color:var(--muted);text-align:left;padding:8px 12px;border:1px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:0.5px; }}
      #md-content td {{ padding:8px 12px;border:1px solid var(--border)66;color:var(--text); }}
      #md-content tr:hover td {{ background:var(--bg); }}
      #md-content blockquote {{ border-left:3px solid var(--accent);margin:1em 0;padding:0.5em 1em;color:var(--muted); }}
      #md-content a {{ color:var(--accent); }}
      #md-content hr {{ border:none;border-top:1px solid var(--border);margin:1.5em 0; }}
      #md-content strong {{ color:var(--text);font-weight:600; }}
    </style>
    <script>
      const raw = `{escaped}`;
      document.getElementById('md-content').innerHTML = marked.parse(raw);
    </script>"""
        return html_shell(target.name, body)

    # All other types: serve inline so the browser handles it (PDF viewer, image, etc.)
    mime, _ = mimetypes.guess_type(str(target))
    from starlette.responses import Response
    content = target.read_bytes()
    return Response(
        content=content,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f"inline; filename=\"{target.name}\""}
    )


@app.post("/portal/api/upload/{domain}")
async def api_upload(domain: str, file: UploadFile = File(...), path: str = Query("")):
    info = get_domain_info(domain)
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type '{ext}' is not permitted")
    docs = DOMAINS_ROOT / domain / "files"
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
    body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <span>{info.get('label', domain)}</span>
      <span class="sep">/</span>
      <span>Reports</span>
    </div>
    <div class="section-header">
      <h1>{info.get('icon','📁')} {info.get('label', domain)} — Reports & Dashboards</h1>
    </div>
    <p style="color:var(--muted);font-size:13px;margin-bottom:20px;">
      HTML dashboards published by Claude — todo lists, contact directories, vendor lists, project summaries, and more.
    </p>

    <div id="report-grid" class="report-grid">
      <p style="color:var(--muted)">Loading…</p>
    </div>

    <script>
      const DOMAIN = '{domain}';

      async function loadReports() {{
        const grid = document.getElementById('report-grid');
        const res = await fetch('/portal/api/reports/' + DOMAIN);
        if (!res.ok) {{ grid.innerHTML = '<p style="color:var(--danger)">Error loading reports</p>'; return; }}
        const data = await res.json();
        if (!data.reports.length) {{
          grid.innerHTML = '<p style="color:var(--muted)">No reports published yet. Ask Claude to generate one!</p>';
          return;
        }}
        grid.innerHTML = data.reports.map(r => `
          <a href="/portal/reports/${{DOMAIN}}/view?file=${{encodeURIComponent(r.filename)}}" style="text-decoration:none;">
            <div class="report-card">
              <div style="font-size:24px;margin-bottom:8px;">📊</div>
              <div class="report-card-title">${{r.name}}</div>
              <div class="report-card-meta">${{r.modified}} &nbsp;·&nbsp; ${{fmtSize(r.size)}}</div>
            </div>
          </a>`).join('');
      }}

      function fmtSize(b) {{
        const units = ['B','KB','MB'];
        let i = 0;
        while (b >= 1024 && i < units.length - 1) {{ b /= 1024; i++; }}
        return b.toFixed(i ? 1 : 0) + ' ' + units[i];
      }}

      loadReports();
    </script>"""

    return html_shell(f"{info.get('label', domain)} — Reports", body)


@app.get("/portal/reports/{domain}/view", response_class=HTMLResponse)
async def view_report(domain: str, file: str = Query(...)):
    info = get_domain_info(domain)
    rdir = DOMAINS_ROOT / domain / "reports"
    target = safe_resolve(rdir, file)
    if not target.is_file() or target.suffix not in (".html", ".htm"):
        raise HTTPException(404, "Report not found")
    return HTMLResponse(content=target.read_text())


@app.get("/portal/api/reports/{domain}")
async def api_list_reports(domain: str):
    info = get_domain_info(domain)
    rdir = DOMAINS_ROOT / domain / "reports"
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
    queries = [q for q in load_queries() if q.get("domain") == domain]

    query_options = "".join(
        f'<option value="{q["id"]}">{q["label"]} — {q["description"]}</option>'
        for q in queries
    )

    # Build this outside the f-string to avoid {{}} escaping issues
    queries_js = json.dumps([
        {"id": q["id"], "label": q["label"], "description": q["description"]}
        for q in queries
    ])

    body = f"""
    <div class="breadcrumb">
      <a href="/portal">Portal</a>
      <span class="sep">/</span>
      <span>{info.get('label', domain)}</span>
      <span class="sep">/</span>
      <span>Data</span>
    </div>
    <div class="section-header">
      <h1>{info.get('icon','📁')} {info.get('label', domain)} — Data</h1>
    </div>
    <p style="color:var(--muted);font-size:13px;margin-bottom:24px;">
      Saved queries against the Burrillville project store. Read-only. Ask Claude to add new queries.
    </p>

    <div style="display:flex;gap:12px;align-items:flex-end;margin-bottom:20px;flex-wrap:wrap;">
      <div style="flex:1;min-width:280px;">
        <label style="display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">Query</label>
        <select id="query-select" style="width:100%;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:13px;cursor:pointer;">
          <option value="">— select a query —</option>
          {query_options}
        </select>
      </div>
      <button id="run-btn" class="btn btn-primary" style="padding:8px 20px;font-size:13px;" onclick="runQuery()">▶ Run</button>
      <button id="csv-btn" class="btn btn-secondary" style="padding:8px 16px;font-size:13px;display:none;" onclick="exportCSV()">↓ CSV</button>
    </div>

    <div id="query-desc" style="font-size:12px;color:var(--muted);margin-bottom:16px;min-height:18px;"></div>
    <div id="result-meta" style="font-size:12px;color:var(--muted);margin-bottom:8px;font-family:var(--mono);"></div>

    <div id="result-area" style="overflow-x:auto;">
      <p style="color:var(--muted);text-align:center;padding:40px 0;">Select a query and click Run.</p>
    </div>

    <script>
      const DOMAIN = '{domain}';
      let currentData = null;
      let currentColumns = null;

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
        btn.textContent = '⏳ Running…';
        btn.disabled = true;
        document.getElementById('result-area').innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px 0;">Running query…</p>';
        document.getElementById('result-meta').textContent = '';
        document.getElementById('csv-btn').style.display = 'none';

        try {{
          const res = await fetch(`/portal/api/data/${{DOMAIN}}?query_id=${{encodeURIComponent(id)}}`);
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Query failed');

          currentData = data.rows;
          currentColumns = data.columns;

          const elapsed = data.elapsed_ms ? ` · ${{data.elapsed_ms}}ms` : '';
          document.getElementById('result-meta').textContent =
            `${{data.rows.length}} row${{data.rows.length !== 1 ? 's' : ''}}${{elapsed}}`;

          if (!data.rows.length) {{
            document.getElementById('result-area').innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px 0;">No results.</p>';
          }} else {{
            document.getElementById('result-area').innerHTML = buildTable(data.columns, data.rows);
            document.getElementById('csv-btn').style.display = 'inline-flex';
          }}
        }} catch(e) {{
          document.getElementById('result-area').innerHTML =
            `<div class="alert alert-error">Error: ${{e.message}}</div>`;
        }} finally {{
          btn.textContent = '▶ Run';
          btn.disabled = false;
        }}
      }}

      function buildTable(cols, rows) {{
        const header = cols.map(c =>
          `<th style="cursor:pointer;user-select:none;" onclick="sortTable(this, '${{c}}')">${{c}} <span style="color:var(--border)">⇅</span></th>`
        ).join('');
        const body = rows.map(row =>
          '<tr>' + cols.map(c => {{
            const val = row[c];
            if (val === null || val === undefined || val === '') return '<td style="color:var(--border)">—</td>';
            // Pretty-print JSON arrays
            if (typeof val === 'string' && val.startsWith('[')) {{
              try {{
                const arr = JSON.parse(val);
                return `<td>${{arr.map(x => `<span class="badge badge-blue" style="margin-right:3px">${{x}}</span>`).join('')}}</td>`;
              }} catch(e) {{}}
            }}
            return `<td>${{String(val)}}</td>`;
          }}).join('') + '</tr>'
        ).join('');
        return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
          <table class="file-table" style="min-width:100%">
            <thead><tr>${{header}}</tr></thead>
            <tbody>${{body}}</tbody>
          </table></div>`;
      }}

      let sortDir = {{}};
      function sortTable(th, col) {{
        if (!currentData) return;
        sortDir[col] = sortDir[col] === 'asc' ? 'desc' : 'asc';
        const sorted = [...currentData].sort((a, b) => {{
          const va = a[col] ?? '';
          const vb = b[col] ?? '';
          return sortDir[col] === 'asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
        }});
        document.getElementById('result-area').innerHTML = buildTable(currentColumns, sorted);
      }}

      function exportCSV() {{
        if (!currentData || !currentColumns) return;
        const rows = [currentColumns.join(',')].concat(
          currentData.map(row => currentColumns.map(c => {{
            const v = row[c] ?? '';
            return `"${{String(v).replace(/"/g,'""')}}"`;
          }}).join(','))
        );
        const blob = new Blob([rows.join('\\n')], {{type: 'text/csv'}});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${{DOMAIN}}-${{document.getElementById('query-select').value}}-${{new Date().toISOString().slice(0,10)}}.csv`;
        a.click();
      }}

      // Auto-run if query param in URL
      const params = new URLSearchParams(window.location.search);
      if (params.get('q')) {{
        document.getElementById('query-select').value = params.get('q');
        document.getElementById('query-select').dispatchEvent(new Event('change'));
        runQuery();
      }}
    </script>"""

    return html_shell(f"{info.get('label', domain)} — Data", body)


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
        raise HTTPException(404, f"Query '{query_id}' not found for domain '{domain}'")

    # Safety: only SELECT statements, no domain escape
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
        result = []
        for row in rows:
            result.append({col: row[col].isoformat() if hasattr(row[col], 'isoformat') else row[col] for col in columns})

        return {"columns": columns, "rows": result, "elapsed_ms": elapsed, "query": query["label"]}

    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/portal/health")
async def health():
    return {"status": "ok", "service": "portal"}
