"""
File browser SPA — served at /api/browser.

Single-page app for browsing and managing published files.
Replaces nginx autoindex with a proper UI including delete capability.
"""

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse


def register(mcp: FastMCP):

    @mcp.custom_route("/api/browser", methods=["GET"])
    async def file_browser(request: Request) -> HTMLResponse:
        return HTMLResponse(BROWSER_HTML)


BROWSER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Somnia — Files</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0a0e17;
    color: #c9d1d9;
    min-height: 100vh;
    padding: 1.5rem;
  }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid #21262d;
  }
  .header h1 {
    font-size: 1.4rem;
    color: #e6edf3;
    font-weight: 500;
    letter-spacing: 0.03em;
  }
  .header h1 span { color: #58a6ff; }
  .header .logout { color: #8b949e; font-size: 0.85rem; }

  .domain-section {
    margin-bottom: 2rem;
  }
  .domain-header {
    font-size: 1.15rem;
    color: #e6edf3;
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .domain-header .icon { font-size: 1.1rem; }

  .category-section {
    margin-left: 0.5rem;
    margin-bottom: 1.25rem;
  }
  .category-label {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8b949e;
    margin-bottom: 0.4rem;
    padding-left: 0.3rem;
  }

  .file-list {
    border: 1px solid #21262d;
    border-radius: 8px;
    overflow: hidden;
    background: #0d1117;
  }
  .file-row {
    display: flex;
    align-items: center;
    padding: 0.55rem 0.8rem;
    border-bottom: 1px solid #21262d;
    gap: 0.75rem;
    transition: background 0.1s;
  }
  .file-row:last-child { border-bottom: none; }
  .file-row:hover { background: #161b22; }

  .file-icon { width: 1.2rem; text-align: center; flex-shrink: 0; font-size: 0.85rem; }
  .file-name { flex: 1; }
  .file-name a { color: #e6edf3; }
  .file-size {
    color: #8b949e;
    font-size: 0.8rem;
    min-width: 5rem;
    text-align: right;
    flex-shrink: 0;
  }
  .file-date {
    color: #8b949e;
    font-size: 0.8rem;
    min-width: 6rem;
    text-align: right;
    flex-shrink: 0;
  }
  .file-delete {
    background: none;
    border: 1px solid transparent;
    color: #8b949e;
    cursor: pointer;
    padding: 0.2rem 0.4rem;
    border-radius: 4px;
    font-size: 0.8rem;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .file-delete:hover {
    color: #f85149;
    border-color: rgba(248,81,73,0.4);
    background: rgba(248,81,73,0.1);
  }

  .empty {
    color: #8b949e;
    font-style: italic;
    padding: 2rem;
    text-align: center;
  }
  .loading {
    color: #8b949e;
    text-align: center;
    padding: 3rem;
  }

  .toast {
    position: fixed;
    bottom: 1.5rem;
    right: 1.5rem;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 0.7rem 1rem;
    font-size: 0.875rem;
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    opacity: 0;
    transform: translateY(10px);
    transition: all 0.2s;
    pointer-events: none;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  .toast.error { border-color: rgba(248,81,73,0.4); color: #f85149; }
  .toast.success { border-color: rgba(35,134,54,0.4); color: #3fb950; }

  @media (max-width: 640px) {
    .file-date { display: none; }
    .file-size { min-width: 3.5rem; }
  }
</style>
</head>
<body>

<div class="header">
  <h1><span>★</span> Somnia Files</h1>
  <a href="/logout" class="logout">Sign out</a>
</div>

<div id="content"><div class="loading">Loading…</div></div>
<div id="toast" class="toast"></div>

<script>
const ICONS = {
  pdf: '📄', html: '🌐', json: '📋', md: '📝', txt: '📝',
  png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', svg: '🖼️',
  docx: '📄', xlsx: '📊', pptx: '📊', csv: '📊',
  default: '📦'
};

const CAT_LABELS = {
  apps: '🌐 Apps',
  docs: '📁 Documents',
  files: '📎 Files',
  uncategorized: '📦 Uncategorized'
};

const CAT_ORDER = ['apps', 'docs', 'files', 'uncategorized'];

function icon(ext) { return ICONS[ext] || ICONS.default; }

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric'
  });
}

function toast(msg, type) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + type;
  setTimeout(() => el.className = 'toast', 2500);
}

async function deleteFile(path) {
  if (!confirm(`Delete ${path}?`)) return;
  try {
    const res = await fetch('/api/files/' + path, { method: 'DELETE' });
    if (res.ok) {
      toast('Deleted ' + path.split('/').pop(), 'success');
      loadFiles();
    } else {
      const data = await res.json();
      toast(data.error || 'Delete failed', 'error');
    }
  } catch (e) {
    toast('Network error', 'error');
  }
}

function renderFiles(data) {
  const el = document.getElementById('content');
  const domains = data.domains;

  if (!domains || Object.keys(domains).length === 0) {
    el.innerHTML = '<div class="empty">No published files yet.</div>';
    return;
  }

  let html = '';
  for (const [domain, categories] of Object.entries(domains).sort()) {
    html += `<div class="domain-section">`;
    html += `<div class="domain-header"><span class="icon">📂</span> ${domain}</div>`;

    const sortedCats = CAT_ORDER.filter(c => categories[c]);
    // Add any categories not in our predefined order
    for (const c of Object.keys(categories)) {
      if (!sortedCats.includes(c)) sortedCats.push(c);
    }

    for (const cat of sortedCats) {
      const files = categories[cat];
      if (!files || files.length === 0) continue;

      html += `<div class="category-section">`;
      html += `<div class="category-label">${CAT_LABELS[cat] || cat}</div>`;
      html += `<div class="file-list">`;

      for (const f of files) {
        const href = `/output/${f.path}`;
        html += `<div class="file-row">
          <span class="file-icon">${icon(f.extension)}</span>
          <span class="file-name"><a href="${href}" target="_blank">${f.name}</a></span>
          <span class="file-size">${formatSize(f.size)}</span>
          <span class="file-date">${formatDate(f.modified)}</span>
          <button class="file-delete" onclick="deleteFile('${f.path}')" title="Delete">✕</button>
        </div>`;
      }

      html += `</div></div>`;
    }

    html += `</div>`;
  }

  el.innerHTML = html;
}

async function loadFiles() {
  try {
    const res = await fetch('/api/files');
    if (res.status === 401) {
      window.location.href = '/login?redirect=/output/';
      return;
    }
    const data = await res.json();
    renderFiles(data);
  } catch (e) {
    document.getElementById('content').innerHTML =
      '<div class="empty">Failed to load files. <a href="" onclick="loadFiles();return false;">Retry</a></div>';
  }
}

loadFiles();
</script>
</body>
</html>"""
