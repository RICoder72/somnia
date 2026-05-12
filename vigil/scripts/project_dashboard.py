#!/usr/bin/env python3
"""
Somnia Project Dashboard Generator

Queries Store (PostgreSQL) for projects, contacts, and relationships,
then generates a self-contained HTML dashboard and publishes it.

Usage:
    python3 scripts/project_dashboard.py [--domain myworkspace] [--output /path/to/file.html]
"""

import asyncio
import asyncpg
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://constellation:FPCsUawkvlxe6O_lSt0_7AiEAJO8DVr4@constellation-postgres:5432/constellation",
)

OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", "/data/outputs"))


async def fetch_dashboard_data(domain: str) -> dict:
    """Pull all projects, contacts, and relationships from Store."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Projects
        projects = [dict(r) for r in await conn.fetch("""
            SELECT id, name, entity_type, properties, created_at, updated_at
            FROM entities
            WHERE domain = $1 AND entity_type = 'project' AND archived = false
            ORDER BY name
        """, domain)]

        # Contacts
        contacts = [dict(r) for r in await conn.fetch("""
            SELECT id, name, entity_type, properties, created_at, updated_at
            FROM entities
            WHERE domain = $1 AND entity_type = 'contact' AND archived = false
            ORDER BY name
        """, domain)]

        # All relationships in domain
        relationships = [dict(r) for r in await conn.fetch("""
            SELECT id, source_id, target_id, relationship_type, properties
            FROM relationships
            WHERE domain = $1
        """, domain)]

        return {
            "domain": domain,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "projects": projects,
            "contacts": contacts,
            "relationships": relationships,
        }
    finally:
        await conn.close()


def serialize_data(data: dict) -> str:
    """Convert to JSON-safe format for embedding in HTML."""
    def default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (asyncpg.Record,)):
            return dict(obj)
        if hasattr(obj, '__str__'):
            return str(obj)
        raise TypeError(f"Not serializable: {type(obj)}")
    return json.dumps(data, default=default)


def generate_html(data: dict) -> str:
    """Render the dashboard HTML with embedded data."""
    json_data = serialize_data(data)
    domain = data["domain"]
    generated = data["generated_at"]

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{domain.title()} — Project Dashboard</title>
<style>
:root {{
  --bg: #0d1117;
  --surface: #161b22;
  --surface-hover: #1c2129;
  --border: #30363d;
  --text: #e6edf3;
  --text-muted: #8b949e;
  --accent: #58a6ff;
  --accent-subtle: #1f3a5f;
  --green: #3fb950;
  --green-bg: #0d3117;
  --yellow: #d29922;
  --yellow-bg: #2d2000;
  --red: #f85149;
  --red-bg: #3d1117;
  --purple: #bc8cff;
  --purple-bg: #271c47;
  --orange: #f0883e;
  --orange-bg: #3d2200;
  --cyan: #39d2c0;
  --cyan-bg: #0d3d36;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}}

.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}

/* Header */
header {{
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 24px; border-bottom: 1px solid var(--border); margin-bottom: 24px;
}}
header h1 {{ font-size: 24px; font-weight: 600; }}
header h1 span {{ color: var(--accent); }}
.meta {{ color: var(--text-muted); font-size: 13px; }}

/* Stats row */
.stats {{
  display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px;
}}
.stat-card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px 20px; flex: 1; min-width: 140px;
}}
.stat-card .label {{ color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
.stat-card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
.stat-card .sub {{ color: var(--text-muted); font-size: 12px; margin-top: 2px; }}

/* Filters */
.filters {{
  display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; align-items: center;
}}
.filters label {{ color: var(--text-muted); font-size: 13px; margin-right: 4px; }}
.chip {{
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 12px; border-radius: 20px; font-size: 13px;
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-muted); cursor: pointer; transition: all 0.15s;
  user-select: none;
}}
.chip:hover {{ border-color: var(--accent); color: var(--text); }}
.chip.active {{ background: var(--accent-subtle); border-color: var(--accent); color: var(--accent); }}
.divider {{ width: 1px; height: 24px; background: var(--border); margin: 0 8px; }}

/* Phase sections */
.phase {{ margin-bottom: 32px; }}
.phase-header {{
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
}}
.phase-header h2 {{ font-size: 16px; font-weight: 600; }}
.phase-count {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 2px 10px; font-size: 12px; color: var(--text-muted);
}}

/* Project cards */
.cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 12px; }}

.card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; cursor: pointer; transition: all 0.15s;
}}
.card:hover {{ border-color: var(--accent); background: var(--surface-hover); }}
.card.expanded {{ grid-column: 1 / -1; }}

.card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
.card-title {{ font-size: 15px; font-weight: 600; flex: 1; }}

.badge {{
  display: inline-flex; align-items: center; padding: 2px 8px;
  border-radius: 12px; font-size: 11px; font-weight: 600;
  white-space: nowrap;
}}
.badge-progress {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }}
.badge-discovery {{ background: var(--cyan-bg); color: var(--cyan); border: 1px solid var(--cyan); }}
.badge-research {{ background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple); }}
.badge-planning {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow); }}
.badge-review {{ background: var(--orange-bg); color: var(--orange); border: 1px solid var(--orange); }}
.badge-parked {{ background: var(--surface); color: var(--text-muted); border: 1px solid var(--border); }}
.badge-notstarted {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }}

.area-tags {{ display: flex; gap: 4px; margin-top: 8px; flex-wrap: wrap; }}
.area-tag {{
  font-size: 11px; padding: 1px 6px; border-radius: 4px;
  background: var(--surface-hover); color: var(--text-muted); border: 1px solid var(--border);
}}
.area-tag.BSD {{ color: var(--green); border-color: var(--green); }}
.area-tag.BVL {{ color: var(--accent); border-color: var(--accent); }}
.area-tag.BPD {{ color: var(--red); border-color: var(--red); }}
.area-tag.BIT {{ color: var(--purple); border-color: var(--purple); }}

.priority-high {{ border-left: 3px solid var(--red); }}
.priority-low {{ border-left: 3px solid var(--text-muted); }}

/* Expanded details */
.card-details {{ display: none; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }}
.card.expanded .card-details {{ display: block; }}

.detail-section {{ margin-bottom: 12px; }}
.detail-section h4 {{ font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}

.person-chip {{
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 12px; font-size: 12px;
  background: var(--surface-hover); border: 1px solid var(--border); margin: 2px;
}}
.person-chip .role {{ color: var(--text-muted); font-size: 10px; }}

.dep-link {{
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 12px; font-size: 12px;
  background: var(--accent-subtle); border: 1px solid var(--accent);
  color: var(--accent); margin: 2px; cursor: pointer;
}}
.dep-link:hover {{ background: var(--accent); color: var(--bg); }}

.rel-type {{ font-size: 10px; color: var(--text-muted); }}

/* Relationship graph section */
.graph-section {{ margin-top: 32px; padding-top: 24px; border-top: 1px solid var(--border); }}
.graph-section h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; }}
#graph-container {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  height: 500px; position: relative;
}}

/* Responsive */
@media (max-width: 768px) {{
  .cards {{ grid-template-columns: 1fr; }}
  .stats {{ flex-direction: column; }}
}}
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1><span>⬡</span> {domain.title()} Project Dashboard</h1>
      <div class="meta">Safeguard. Support. Advance.</div>
    </div>
    <div class="meta" style="text-align:right">
      <div>Generated: <span id="gen-time"></span></div>
      <div id="counts-summary"></div>
    </div>
  </header>

  <div class="stats" id="stats-row"></div>

  <div class="filters" id="filters">
    <label>Area:</label>
    <div id="area-filters"></div>
    <div class="divider"></div>
    <label>Status:</label>
    <div id="status-filters"></div>
    <div class="divider"></div>
    <label>Role:</label>
    <div id="role-filters"></div>
  </div>

  <div id="phases"></div>

  <div class="graph-section">
    <h2>Relationship Map</h2>
    <div id="graph-container"></div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.6/vis-network.min.js"></script>
<script>
const RAW = {json_data};

// ── Parse & index ──────────────────────────────────────────────────────
const projects = RAW.projects.map(p => ({{
  ...p,
  props: typeof p.properties === 'string' ? JSON.parse(p.properties) : (p.properties || {{}})
}}));
const contacts = RAW.contacts.map(c => ({{
  ...c,
  props: typeof c.properties === 'string' ? JSON.parse(c.properties) : (c.properties || {{}})
}}));
const relationships = RAW.relationships.map(r => ({{
  ...r,
  props: typeof r.properties === 'string' ? JSON.parse(r.properties) : (r.properties || {{}})
}}));

const entityMap = {{}};
projects.forEach(p => entityMap[p.id] = p);
contacts.forEach(c => entityMap[c.id] = c);

// Build adjacency: project -> [{{contact, role, relType}}]
const projectStakeholders = {{}};
const projectDeps = {{}};

relationships.forEach(r => {{
  const src = entityMap[r.source_id];
  const tgt = entityMap[r.target_id];
  if (!src || !tgt) return;

  if (r.relationship_type === 'stakeholder') {{
    // source=project, target=contact
    if (!projectStakeholders[r.source_id]) projectStakeholders[r.source_id] = [];
    projectStakeholders[r.source_id].push({{ contact: tgt, role: r.props.role || '', relType: r.relationship_type }});
  }} else if (r.relationship_type === 'depends_on' || r.relationship_type === 'related_to') {{
    if (!projectDeps[r.source_id]) projectDeps[r.source_id] = [];
    projectDeps[r.source_id].push({{ project: tgt, relType: r.relationship_type, notes: r.props.notes || '' }});
    // Reverse too
    if (!projectDeps[r.target_id]) projectDeps[r.target_id] = [];
    projectDeps[r.target_id].push({{ project: src, relType: r.relationship_type, notes: r.props.notes || '' }});
  }}
}});

// ── Status ordering ────────────────────────────────────────────────────
const PHASE_ORDER = [
  {{ key: 'In Progress', label: 'In Progress', icon: '🟢' }},
  {{ key: 'Contract Review', label: 'Contract Review', icon: '📋' }},
  {{ key: 'Discovery', label: 'Discovery', icon: '🔍' }},
  {{ key: 'Research', label: 'Research', icon: '🔬' }},
  {{ key: 'Planning', label: 'Planning', icon: '📐' }},
  {{ key: 'Not Started', label: 'Not Started', icon: '⬜' }},
  {{ key: 'Parked', label: 'Parked', icon: '🅿️' }},
];

function statusBadgeClass(status) {{
  const map = {{
    'In Progress': 'badge-progress',
    'Contract Review': 'badge-review',
    'Discovery': 'badge-discovery',
    'Research': 'badge-research',
    'Planning': 'badge-planning',
    'Not Started': 'badge-notstarted',
    'Parked': 'badge-parked',
  }};
  return map[status] || 'badge-parked';
}}

// ── Filters ────────────────────────────────────────────────────────────
const activeFilters = {{ areas: new Set(), statuses: new Set(), roles: new Set() }};

const allAreas = [...new Set(projects.flatMap(p => p.props.areas || []))].sort();
const allStatuses = [...new Set(projects.map(p => p.props.status))];
const allRoles = [...new Set(contacts.map(c => c.props.role).filter(Boolean))].sort();

function renderChips(container, items, filterSet) {{
  const el = document.getElementById(container);
  el.innerHTML = '';
  items.forEach(item => {{
    const chip = document.createElement('span');
    chip.className = 'chip' + (filterSet.has(item) ? ' active' : '');
    chip.textContent = item;
    chip.onclick = () => {{
      if (filterSet.has(item)) filterSet.delete(item);
      else filterSet.add(item);
      render();
    }};
    el.appendChild(chip);
  }});
}}

function matchesFilters(project) {{
  const areas = project.props.areas || [];
  const status = project.props.status;
  if (activeFilters.areas.size > 0 && !areas.some(a => activeFilters.areas.has(a))) return false;
  if (activeFilters.statuses.size > 0 && !activeFilters.statuses.has(status)) return false;
  if (activeFilters.roles.size > 0) {{
    const stakeholders = projectStakeholders[project.id] || [];
    const contactIds = stakeholders.map(s => s.contact.id);
    const hasMatchingRole = contacts.some(c => contactIds.includes(c.id) && activeFilters.roles.has(c.props.role));
    if (!hasMatchingRole) return false;
  }}
  return true;
}}

// ── Render ──────────────────────────────────────────────────────────────
function render() {{
  renderChips('area-filters', allAreas, activeFilters.areas);
  renderChips('status-filters', allStatuses, activeFilters.statuses);
  renderChips('role-filters', allRoles, activeFilters.roles);

  const filtered = projects.filter(matchesFilters);

  // Stats
  const statsRow = document.getElementById('stats-row');
  const inProgress = filtered.filter(p => p.props.status === 'In Progress').length;
  const highPri = filtered.filter(p => p.props.priority === 'High').length;
  const totalContacts = contacts.length;
  const totalRels = relationships.length;
  statsRow.innerHTML = `
    <div class="stat-card"><div class="label">Projects</div><div class="value">${{filtered.length}}</div><div class="sub">of ${{projects.length}} total</div></div>
    <div class="stat-card"><div class="label">In Progress</div><div class="value" style="color:var(--green)">${{inProgress}}</div></div>
    <div class="stat-card"><div class="label">High Priority</div><div class="value" style="color:var(--red)">${{highPri}}</div></div>
    <div class="stat-card"><div class="label">People</div><div class="value">${{totalContacts}}</div><div class="sub">${{totalRels}} relationships</div></div>
  `;

  // Phases
  const phasesEl = document.getElementById('phases');
  phasesEl.innerHTML = '';

  PHASE_ORDER.forEach(phase => {{
    const phaseProjects = filtered.filter(p => p.props.status === phase.key);
    if (phaseProjects.length === 0) return;

    const section = document.createElement('div');
    section.className = 'phase';
    section.innerHTML = `
      <div class="phase-header">
        <h2>${{phase.icon}} ${{phase.label}}</h2>
        <span class="phase-count">${{phaseProjects.length}}</span>
      </div>
      <div class="cards" id="cards-${{phase.key.replace(/\\s/g, '-')}}"></div>
    `;
    phasesEl.appendChild(section);

    const cardsEl = section.querySelector('.cards');
    phaseProjects.sort((a, b) => {{
      const pa = a.props.priority === 'High' ? 0 : a.props.priority === 'Low' ? 2 : 1;
      const pb = b.props.priority === 'High' ? 0 : b.props.priority === 'Low' ? 2 : 1;
      return pa - pb || a.name.localeCompare(b.name);
    }}).forEach(p => {{
      const card = document.createElement('div');
      const priClass = p.props.priority === 'High' ? ' priority-high' : p.props.priority === 'Low' ? ' priority-low' : '';
      card.className = 'card' + priClass;
      card.onclick = (e) => {{
        if (e.target.classList.contains('dep-link')) return;
        card.classList.toggle('expanded');
      }};

      const areas = (p.props.areas || []).map(a => `<span class="area-tag ${{a}}">${{a}}</span>`).join('');
      const stakeholders = (projectStakeholders[p.id] || []).map(s =>
        `<span class="person-chip">${{s.contact.name}} <span class="role">${{s.role}}</span></span>`
      ).join('');
      const deps = [...new Set((projectDeps[p.id] || []).map(d => JSON.stringify(d)))].map(d => JSON.parse(d)).map(d =>
        `<span class="dep-link" onclick="scrollToProject('${{d.project.id}}')" title="${{d.notes}}">
          <span class="rel-type">${{d.relType === 'depends_on' ? '⬆ depends' : '↔ related'}}</span> ${{d.project.name}}
        </span>`
      ).join('');
      const parkedReason = p.props.parked_reason ? `<div class="detail-section"><h4>Parked Reason</h4><p style="color:var(--text-muted);font-size:13px">${{p.props.parked_reason}}</p></div>` : '';
      const priLabel = p.props.priority ? `<span class="badge" style="font-size:10px;margin-left:4px;background:${{p.props.priority === 'High' ? 'var(--red-bg)' : 'var(--surface)'}};color:${{p.props.priority === 'High' ? 'var(--red)' : 'var(--text-muted)'}};border:1px solid ${{p.props.priority === 'High' ? 'var(--red)' : 'var(--border)'}}">${{p.props.priority}}</span>` : '';

      card.innerHTML = `
        <div class="card-header">
          <span class="card-title">${{p.name}}</span>
          <span class="badge ${{statusBadgeClass(p.props.status)}}">${{p.props.status}}</span>
          ${{priLabel}}
        </div>
        <div class="area-tags">${{areas}}</div>
        <div class="card-details" id="card-${{p.id}}">
          ${{stakeholders ? `<div class="detail-section"><h4>Stakeholders</h4><div>${{stakeholders}}</div></div>` : ''}}
          ${{deps ? `<div class="detail-section"><h4>Related Projects</h4><div>${{deps}}</div></div>` : ''}}
          ${{parkedReason}}
          <div class="detail-section"><h4>Metadata</h4>
            <p style="color:var(--text-muted);font-size:12px">
              Created: ${{new Date(p.created_at).toLocaleDateString()}}
              ${{p.props.doc_path ? ' · Doc: ' + p.props.doc_path : ''}}
            </p>
          </div>
        </div>
      `;
      card.dataset.projectId = p.id;
      cardsEl.appendChild(card);
    }});
  }});

  renderGraph(filtered);
}}

function scrollToProject(id) {{
  const card = document.querySelector(`[data-project-id="${{id}}"]`);
  if (card) {{
    card.classList.add('expanded');
    card.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    card.style.outline = '2px solid var(--accent)';
    setTimeout(() => card.style.outline = '', 2000);
  }}
}}

// ── Vis.js Network Graph ───────────────────────────────────────────────
function renderGraph(filtered) {{
  const container = document.getElementById('graph-container');
  const filteredIds = new Set(filtered.map(p => p.id));

  // Only project-to-project relationships
  const projRels = relationships.filter(r =>
    (r.relationship_type === 'depends_on' || r.relationship_type === 'related_to') &&
    filteredIds.has(r.source_id) && filteredIds.has(r.target_id)
  );

  // Nodes: only projects with relationships
  const relatedIds = new Set(projRels.flatMap(r => [r.source_id, r.target_id]));
  const nodeColor = {{
    'In Progress': '#3fb950',
    'Discovery': '#39d2c0',
    'Research': '#bc8cff',
    'Planning': '#d29922',
    'Contract Review': '#f0883e',
    'Not Started': '#f85149',
    'Parked': '#8b949e',
  }};

  const nodes = filtered.filter(p => relatedIds.has(p.id)).map(p => ({{
    id: p.id,
    label: p.name.length > 30 ? p.name.slice(0, 28) + '…' : p.name,
    title: p.name + '\\n' + (p.props.status || ''),
    color: {{
      background: nodeColor[p.props.status] || '#8b949e',
      border: nodeColor[p.props.status] || '#8b949e',
      highlight: {{ background: '#58a6ff', border: '#58a6ff' }},
    }},
    font: {{ color: '#e6edf3', size: 12 }},
    shape: 'box',
    borderWidth: p.props.priority === 'High' ? 3 : 1,
  }}));

  const edges = projRels.map(r => {{
    const props = typeof r.properties === 'string' ? JSON.parse(r.properties) : (r.properties || {{}});
    return {{
      from: r.source_id,
      to: r.target_id,
      label: r.relationship_type === 'depends_on' ? 'depends' : 'related',
      arrows: r.relationship_type === 'depends_on' ? 'to' : '',
      dashes: r.relationship_type === 'related_to',
      color: {{ color: '#30363d', highlight: '#58a6ff' }},
      font: {{ color: '#8b949e', size: 10 }},
      title: props.notes || '',
    }};
  }});

  if (nodes.length === 0) {{
    container.innerHTML = '<p style="padding:40px;text-align:center;color:var(--text-muted)">No project relationships to display. Add depends_on or related_to links between projects.</p>';
    return;
  }}

  const data = {{ nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) }};
  const options = {{
    physics: {{ solver: 'forceAtlas2Based', forceAtlas2Based: {{ gravitationalConstant: -40, springLength: 150 }} }},
    interaction: {{ hover: true, tooltipDelay: 100 }},
    layout: {{ improvedLayout: true }},
  }};
  new vis.Network(container, data, options);
}}

// ── Init ────────────────────────────────────────────────────────────────
document.getElementById('gen-time').textContent = new Date(RAW.generated_at).toLocaleString();
document.getElementById('counts-summary').textContent =
  `${{projects.length}} projects · ${{contacts.length}} contacts · ${{relationships.length}} relationships`;
render();
</script>
</body>
</html>'''


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate project dashboard")
    parser.add_argument("--domain", default="", help="Domain to generate dashboard for (required)")
    parser.add_argument("--output", default=None, help="Output path (default: /data/outputs/project-dashboard.html)")
    args = parser.parse_args()

    print(f"Fetching data for domain: {args.domain}")
    data = await fetch_dashboard_data(args.domain)
    print(f"  {len(data['projects'])} projects, {len(data['contacts'])} contacts, {len(data['relationships'])} relationships")

    html = generate_html(data)

    output_path = Path(args.output) if args.output else OUTPUTS_DIR / "project-dashboard.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    print(f"Dashboard written to: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    asyncio.run(main())
