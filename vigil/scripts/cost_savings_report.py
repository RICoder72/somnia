#!/usr/bin/env python3
"""
Somnia Cost & Savings Report Generator

Queries Store for accomplishments with financial_impact set, plus a hand-curated
list of accomplishments that warrant financial assessment but don't have it yet,
and renders a self-contained HTML report.

Usage:
    python3 scripts/cost_savings_report.py [--domain myworkspace] [--output /path/to/file.html]
"""

import asyncio
import asyncpg
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://constellation:FPCsUawkvlxe6O_lSt0_7AiEAJO8DVr4@constellation-postgres:5432/constellation",
)

OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", "/data/outputs"))

# Hand-curated list of accomplishment names that should eventually have
# financial_impact populated. Shown in the "Awaiting Assessment" section.
AWAITING_ASSESSMENT = {
    "Intercom & Emergency Communications Research": "State safety funding eligible (50% reimbursement) + E-Rate eligibility. Wahsega Carina deployment cost vs reimbursement = real recoverable funding.",
    "Structured Cabling Audit — Reframed": "Reframing as audit-and-gap-fill (vs ground-up rewire) is a major cost avoidance. Quantify once gap analysis complete.",
    "OSHEAN Initial Meeting": "Internet consolidation across BSD/BVL/BPD. Net annual savings TBD when carrier spend baseline + OSHEAN pricing finalized.",
    "Centralized Technology Acquisition Model Created": "Volume pricing leverage and vendor consolidation across three orgs. Soft savings — quantify on first major joint procurement.",
    "Print Management Research": "PaperCut Hive/MF unified solution vs current per-org spend. Net impact TBD.",
    "SIS Evaluation Project Created": "Skyward replacement decision will have material recurring impact (positive or negative) depending on selected platform.",
    "NUC Standardization Project Created": "Standardization on N100/N200 + i3 tiers should yield volume pricing and reduce one-off purchases. Quantify after first batch.",
    "KnowBe4 Gap Identified": "Expanding KnowBe4 to BSD/BPD will add cost — currently grant-funded for BVL only. Quote and grant strategy pending.",
}

# Items flagged in budget analysis that need investigation (not in Store yet)
PENDING_INVESTIGATIONS = [
    {"item": "Screencastify (FY25 dual entries)",
     "question": "Duplicate, split payment, or two products?",
     "potential": "Up to $4,416/yr if duplicate"},
    {"item": "Adobe Creative Cloud + Adobe (additional)",
     "question": "$9,000 + $2,520 — same product or different tiers?",
     "potential": "Up to $2,520/yr if duplicate"},
    {"item": "Backup duplication",
     "question": "Altaro ($2,000) + Barracuda ($5,000) — running two solutions?",
     "potential": "Up to $2,000/yr if Altaro retired"},
    {"item": "Gopher × 2 (FY25)",
     "question": "Single product or two?",
     "potential": "Up to $800/yr if duplicate"},
    {"item": "Airwatch MDM",
     "question": "Still needed at $2,700/yr or replaced by Intune?",
     "potential": "Up to $2,700/yr if retirable"},
]


async def fetch_data(domain: str) -> dict:
    """Pull all accomplishments for the domain."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        accomplishments = [dict(r) for r in await conn.fetch("""
            SELECT id, name, properties, created_at, updated_at
            FROM entities
            WHERE domain = $1 AND entity_type = 'accomplishment' AND archived = false
            ORDER BY (properties->>'date') DESC, created_at DESC
        """, domain)]

        return {
            "domain": domain,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "accomplishments": accomplishments,
        }
    finally:
        await conn.close()


def serialize(data: dict) -> str:
    def default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, '__str__'):
            return str(obj)
        raise TypeError(f"Not serializable: {type(obj)}")
    return json.dumps(data, default=default)


def generate_html(data: dict) -> str:
    json_data = serialize(data)
    domain = data["domain"]
    awaiting_json = json.dumps(AWAITING_ASSESSMENT)
    pending_json = json.dumps(PENDING_INVESTIGATIONS)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{domain.title()} — Cost & Savings Report</title>
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

header {{
  display: flex; justify-content: space-between; align-items: flex-end;
  padding-bottom: 24px; border-bottom: 1px solid var(--border); margin-bottom: 24px;
  flex-wrap: wrap; gap: 16px;
}}
header h1 {{ font-size: 24px; font-weight: 600; }}
header h1 .icon {{ color: var(--green); margin-right: 8px; }}
header .subtitle {{ color: var(--text-muted); font-size: 13px; margin-top: 4px; }}
.meta {{ color: var(--text-muted); font-size: 13px; text-align: right; }}

/* Top stats */
.stats {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px; margin-bottom: 24px;
}}
.stat-card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px 20px;
}}
.stat-card .label {{ color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; }}
.stat-card .value-row {{ display: flex; align-items: baseline; gap: 12px; margin-top: 6px; flex-wrap: wrap; }}
.stat-card .value {{ font-size: 26px; font-weight: 700; }}
.stat-card .value.recurring {{ color: var(--green); }}
.stat-card .value.onetime {{ color: var(--accent); }}
.stat-card .value.cost {{ color: var(--red); }}
.stat-card .value.muted {{ color: var(--text-muted); }}
.stat-card .unit {{ color: var(--text-muted); font-size: 12px; }}
.stat-card .sub {{ color: var(--text-muted); font-size: 12px; margin-top: 6px; }}

/* Filters */
.filters {{
  display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; align-items: center;
}}
.filters label {{ color: var(--text-muted); font-size: 13px; margin-right: 4px; }}
.chip {{
  display: inline-flex; align-items: center;
  padding: 4px 12px; border-radius: 20px; font-size: 13px;
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-muted); cursor: pointer; transition: all 0.15s;
  user-select: none;
}}
.chip:hover {{ border-color: var(--accent); color: var(--text); }}
.chip.active {{ background: var(--accent-subtle); border-color: var(--accent); color: var(--accent); }}
.divider {{ width: 1px; height: 24px; background: var(--border); margin: 0 8px; }}

/* Section headers */
.section {{ margin-bottom: 32px; }}
.section-header {{
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
}}
.section-header h2 {{ font-size: 16px; font-weight: 600; }}
.section-count {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 2px 10px; font-size: 12px; color: var(--text-muted);
}}
.section-totals {{ margin-left: auto; font-size: 13px; color: var(--text-muted); }}
.section-totals .pos {{ color: var(--green); font-weight: 600; }}
.section-totals .neg {{ color: var(--red); font-weight: 600; }}
.section-desc {{ color: var(--text-muted); font-size: 13px; margin-bottom: 12px; }}

/* Entry rows */
.entries {{ display: flex; flex-direction: column; gap: 8px; }}
.entry {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px; transition: all 0.15s;
}}
.entry:hover {{ border-color: var(--accent); background: var(--surface-hover); }}
.entry-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }}
.entry-title-block {{ flex: 1; min-width: 240px; }}
.entry-title {{ font-size: 14px; font-weight: 600; }}
.entry-date {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; }}
.entry-amounts {{ display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }}
.amount-block {{ text-align: right; }}
.amount-label {{ font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
.amount {{ font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.amount.savings {{ color: var(--green); }}
.amount.avoidance {{ color: var(--cyan); }}
.amount.cost {{ color: var(--red); }}
.amount.zero {{ color: var(--text-muted); }}
.amount.tbd {{ color: var(--yellow); font-size: 13px; font-weight: 600; font-style: italic; }}

.entry-tags {{ display: flex; gap: 4px; margin-top: 8px; flex-wrap: wrap; align-items: center; }}
.tag {{
  font-size: 10px; padding: 2px 8px; border-radius: 4px;
  background: var(--surface-hover); color: var(--text-muted);
  border: 1px solid var(--border); text-transform: uppercase; letter-spacing: 0.4px;
}}
.tag.BSD {{ color: var(--green); border-color: var(--green); }}
.tag.BVL {{ color: var(--accent); border-color: var(--accent); }}
.tag.BPD {{ color: var(--red); border-color: var(--red); }}
.tag.BIT {{ color: var(--purple); border-color: var(--purple); }}

.status {{ font-size: 10px; padding: 2px 8px; border-radius: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
.status.realized {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }}
.status.projected {{ background: var(--accent-subtle); color: var(--accent); border: 1px solid var(--accent); }}
.status.estimate {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow); }}
.status.todo {{ background: var(--surface); color: var(--text-muted); border: 1px dashed var(--text-muted); }}

.confidence {{ font-size: 10px; color: var(--text-muted); margin-left: 4px; }}
.confidence.high::before {{ content: "● ● ●"; color: var(--green); margin-right: 4px; }}
.confidence.medium::before {{ content: "● ● ○"; color: var(--yellow); margin-right: 4px; }}
.confidence.low::before {{ content: "● ○ ○"; color: var(--red); margin-right: 4px; }}

.entry-detail {{
  margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);
  font-size: 13px; color: var(--text-muted); line-height: 1.6;
}}
.entry-detail strong {{ color: var(--text); }}
.source-link {{
  font-size: 11px; color: var(--accent); margin-top: 6px; display: inline-block;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
}}

/* Pending investigations table */
table.pending {{
  width: 100%; border-collapse: collapse;
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; font-size: 13px;
}}
table.pending th {{
  text-align: left; padding: 10px 12px; background: var(--surface-hover);
  color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;
  font-size: 11px; font-weight: 600;
}}
table.pending td {{ padding: 10px 12px; border-top: 1px solid var(--border); }}
table.pending td.potential {{ color: var(--yellow); font-weight: 600; white-space: nowrap; }}

footer {{
  margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border);
  color: var(--text-muted); font-size: 12px;
}}
footer code {{
  background: var(--surface); padding: 2px 6px; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
  font-size: 11px;
}}

@media (max-width: 768px) {{
  .stats {{ grid-template-columns: 1fr; }}
  .entry-header {{ flex-direction: column; }}
  .entry-amounts {{ width: 100%; justify-content: flex-start; }}
}}
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1><span class="icon">$</span>{domain.title()} Cost &amp; Savings Report</h1>
      <div class="subtitle">Financial impact ledger — Realized, projected, and pending</div>
    </div>
    <div class="meta">
      <div>Generated: <span id="gen-time"></span></div>
      <div id="counts-summary"></div>
    </div>
  </header>

  <div class="stats" id="stats-row"></div>

  <div class="filters" id="filters">
    <label>Status:</label>
    <div id="status-filters"></div>
    <div class="divider"></div>
    <label>Org:</label>
    <div id="org-filters"></div>
    <div class="divider"></div>
    <label>Type:</label>
    <div id="type-filters"></div>
  </div>

  <div id="sections"></div>

  <footer>
    <p><strong>Source of truth:</strong> Store entities of type <code>accomplishment</code> with <code>financial_impact</code> populated, in the <code>{domain}</code> domain.</p>
    <p style="margin-top:6px"><strong>Regenerate:</strong> Call <code>dashboard_generate</code> tool with <code>kind=cost_savings</code>, or <code>python3 scripts/cost_savings_report.py</code></p>
    <p style="margin-top:6px"><strong>Add an entry:</strong> Update an accomplishment entity's <code>financial_impact</code> field. See <code>context/cost-savings.md</code> for schema.</p>
  </footer>
</div>

<script>
const RAW = {json_data};
const AWAITING = {awaiting_json};
const PENDING = {pending_json};

const accomplishments = RAW.accomplishments.map(a => ({{
  ...a,
  props: typeof a.properties === 'string' ? JSON.parse(a.properties) : (a.properties || {{}})
}}));

// Split accomplishments into:
//   - has financial_impact (with amount_recurring or amount_one_time set, including 0 negatives)
//   - awaiting (matched by name in AWAITING dict)
//   - other (no impact, no awaiting flag — not displayed)
const withImpact = accomplishments.filter(a => a.props.financial_impact);
const awaitingNames = new Set(Object.keys(AWAITING));
const awaiting = accomplishments.filter(a => !a.props.financial_impact && awaitingNames.has(a.name));

// Normalize impact values. Schema supports legacy {{amount, recurring}} too.
function normalizeImpact(fi) {{
  let recurring = fi.amount_recurring;
  let onetime = fi.amount_one_time;
  if (recurring === undefined && fi.recurring === true) recurring = fi.amount || 0;
  if (recurring === undefined) recurring = 0;
  if (onetime === undefined && fi.recurring === false) onetime = fi.amount || 0;
  if (onetime === undefined) onetime = 0;
  return {{
    type: fi.type || 'savings',
    recurring: Number(recurring) || 0,
    onetime: Number(onetime) || 0,
    status: fi.status || 'estimate',
    confidence: fi.confidence || 'low',
    fiscal_year: fi.fiscal_year || '',
    source_doc: fi.source_doc || '',
    notes: fi.notes || '',
  }};
}}

// Filters
const activeFilters = {{ statuses: new Set(), orgs: new Set(), types: new Set() }};
const allStatuses = ['realized', 'projected', 'estimate'];
const allOrgs = ['BSD', 'BVL', 'BPD', 'BIT'];
const allTypes = ['savings', 'avoidance', 'cost'];

function renderChips(containerId, items, filterSet) {{
  const el = document.getElementById(containerId);
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

function matchesFilters(a) {{
  const fi = normalizeImpact(a.props.financial_impact);
  const areas = a.props.areas || [];
  if (activeFilters.statuses.size > 0 && !activeFilters.statuses.has(fi.status)) return false;
  if (activeFilters.orgs.size > 0 && !areas.some(o => activeFilters.orgs.has(o))) return false;
  // Determine "type" label — savings if amount > 0, cost if onetime < 0, avoidance if type === avoidance
  const inferredType = fi.type === 'avoidance' ? 'avoidance' :
    (fi.recurring < 0 || fi.onetime < 0) && fi.recurring + fi.onetime < 0 ? 'cost' : 'savings';
  if (activeFilters.types.size > 0 && !activeFilters.types.has(inferredType)) return false;
  return true;
}}

function fmtMoney(n) {{
  if (n === 0) return '—';
  const abs = Math.abs(n);
  const sign = n < 0 ? '−' : '+';
  if (abs >= 1000) return sign + '$' + (abs / 1000).toFixed(abs >= 10000 ? 0 : 1) + 'K';
  return sign + '$' + abs.toLocaleString();
}}

function fmtMoneyExact(n) {{
  if (n === 0) return '—';
  const abs = Math.abs(n);
  const sign = n < 0 ? '−' : '+';
  return sign + '$' + abs.toLocaleString();
}}

function statusOrder(s) {{ return {{realized: 0, projected: 1, estimate: 2}}[s] ?? 99; }}

function render() {{
  renderChips('status-filters', allStatuses, activeFilters.statuses);
  renderChips('org-filters', allOrgs, activeFilters.orgs);
  renderChips('type-filters', allTypes, activeFilters.types);

  const filtered = withImpact.filter(matchesFilters);

  // Aggregate stats — by status
  const byStatus = {{realized: {{r: 0, o: 0, n: 0}}, projected: {{r: 0, o: 0, n: 0}}, estimate: {{r: 0, o: 0, n: 0}}}};
  filtered.forEach(a => {{
    const fi = normalizeImpact(a.props.financial_impact);
    if (!byStatus[fi.status]) byStatus[fi.status] = {{r: 0, o: 0, n: 0}};
    byStatus[fi.status].r += fi.recurring;
    byStatus[fi.status].o += fi.onetime;
    byStatus[fi.status].n += 1;
  }});

  // Top stats
  const statsRow = document.getElementById('stats-row');
  statsRow.innerHTML = `
    <div class="stat-card">
      <div class="label">Realized Savings</div>
      <div class="value-row">
        <div><span class="value recurring">${{fmtMoney(byStatus.realized.r)}}</span><span class="unit"> /yr</span></div>
        <div><span class="value onetime">${{fmtMoney(byStatus.realized.o)}}</span><span class="unit"> one-time</span></div>
      </div>
      <div class="sub">${{byStatus.realized.n}} ${{byStatus.realized.n === 1 ? 'entry' : 'entries'}} · already booked</div>
    </div>
    <div class="stat-card">
      <div class="label">Projected (Med+ Confidence)</div>
      <div class="value-row">
        <div><span class="value recurring">${{fmtMoney(byStatus.projected.r)}}</span><span class="unit"> /yr</span></div>
        <div><span class="value onetime">${{fmtMoney(byStatus.projected.o)}}</span><span class="unit"> one-time</span></div>
      </div>
      <div class="sub">${{byStatus.projected.n}} ${{byStatus.projected.n === 1 ? 'entry' : 'entries'}} · pending realization</div>
    </div>
    <div class="stat-card">
      <div class="label">Estimates (Low Confidence)</div>
      <div class="value-row">
        <div><span class="value ${{byStatus.estimate.r >= 0 ? 'recurring' : 'cost'}}">${{fmtMoney(byStatus.estimate.r)}}</span><span class="unit"> /yr</span></div>
        <div><span class="value ${{byStatus.estimate.o >= 0 ? 'onetime' : 'cost'}}">${{fmtMoney(byStatus.estimate.o)}}</span><span class="unit"> one-time</span></div>
      </div>
      <div class="sub">${{byStatus.estimate.n}} ${{byStatus.estimate.n === 1 ? 'entry' : 'entries'}} · needs validation</div>
    </div>
    <div class="stat-card">
      <div class="label">Awaiting Assessment</div>
      <div class="value-row">
        <div><span class="value muted">${{awaiting.length}}</span><span class="unit"> items</span></div>
      </div>
      <div class="sub">${{PENDING.length}} budget-line investigations pending</div>
    </div>
  `;

  // Build sections by status
  const sectionsEl = document.getElementById('sections');
  sectionsEl.innerHTML = '';

  const statusInfo = [
    {{key: 'realized', icon: '✅', label: 'Realized', desc: 'Savings or impact already booked. High confidence.'}},
    {{key: 'projected', icon: '🎯', label: 'Projected', desc: 'Decisions made, savings expected once execution completes. Medium-to-high confidence.'}},
    {{key: 'estimate', icon: '📐', label: 'Estimates', desc: 'Early estimates pending baseline data or vendor quotes. Low-to-medium confidence.'}},
  ];

  statusInfo.forEach(si => {{
    const items = filtered.filter(a => normalizeImpact(a.props.financial_impact).status === si.key);
    if (items.length === 0) return;

    const totalR = items.reduce((s, a) => s + normalizeImpact(a.props.financial_impact).recurring, 0);
    const totalO = items.reduce((s, a) => s + normalizeImpact(a.props.financial_impact).onetime, 0);

    const section = document.createElement('div');
    section.className = 'section';
    section.innerHTML = `
      <div class="section-header">
        <h2>${{si.icon}} ${{si.label}}</h2>
        <span class="section-count">${{items.length}}</span>
        <div class="section-totals">
          Subtotal:
          <span class="${{totalR >= 0 ? 'pos' : 'neg'}}">${{fmtMoneyExact(totalR)}}/yr</span>
          ·
          <span class="${{totalO >= 0 ? 'pos' : 'neg'}}">${{fmtMoneyExact(totalO)}} one-time</span>
        </div>
      </div>
      <div class="section-desc">${{si.desc}}</div>
      <div class="entries"></div>
    `;
    const entriesEl = section.querySelector('.entries');

    items.sort((x, y) => (y.props.date || '').localeCompare(x.props.date || ''));
    items.forEach(a => entriesEl.appendChild(renderEntry(a)));

    sectionsEl.appendChild(section);
  }});

  // Awaiting Assessment section
  if (awaiting.length > 0) {{
    const section = document.createElement('div');
    section.className = 'section';
    section.innerHTML = `
      <div class="section-header">
        <h2>📝 Awaiting Financial Assessment</h2>
        <span class="section-count">${{awaiting.length}}</span>
      </div>
      <div class="section-desc">Accomplishments where financial impact exists in principle but hasn't been quantified yet. These represent the next set of entries to populate as numbers firm up.</div>
      <div class="entries"></div>
    `;
    const entriesEl = section.querySelector('.entries');
    awaiting.sort((x, y) => (y.props.date || '').localeCompare(x.props.date || ''));
    awaiting.forEach(a => {{
      const entry = document.createElement('div');
      entry.className = 'entry';
      const areas = (a.props.areas || []).map(o => `<span class="tag ${{o}}">${{o}}</span>`).join('');
      entry.innerHTML = `
        <div class="entry-header">
          <div class="entry-title-block">
            <div class="entry-title">${{a.name}}</div>
            <div class="entry-date">${{a.props.date || ''}}</div>
          </div>
          <div class="entry-amounts">
            <div class="amount-block"><div class="amount-label">Status</div><div class="amount tbd">TODO</div></div>
          </div>
        </div>
        <div class="entry-tags">${{areas}}<span class="status todo">Awaiting</span></div>
        <div class="entry-detail"><strong>Why on this list:</strong> ${{AWAITING[a.name]}}</div>
      `;
      entriesEl.appendChild(entry);
    }});
    sectionsEl.appendChild(section);
  }}

  // Pending Investigations section
  const piSection = document.createElement('div');
  piSection.className = 'section';
  piSection.innerHTML = `
    <div class="section-header">
      <h2>🔍 Pending Investigations (Budget Analysis)</h2>
      <span class="section-count">${{PENDING.length}}</span>
    </div>
    <div class="section-desc">Items flagged in <code>context/budget-analysis-fy25-fy26.md</code> for review. Resolution may produce new ledger entries.</div>
    <table class="pending">
      <thead><tr><th>Item</th><th>Question</th><th>Potential Impact</th></tr></thead>
      <tbody>
        ${{PENDING.map(p => `<tr><td>${{p.item}}</td><td style="color:var(--text-muted)">${{p.question}}</td><td class="potential">${{p.potential}}</td></tr>`).join('')}}
      </tbody>
    </table>
  `;
  sectionsEl.appendChild(piSection);
}}

function renderEntry(a) {{
  const fi = normalizeImpact(a.props.financial_impact);
  const areas = (a.props.areas || []).map(o => `<span class="tag ${{o}}">${{o}}</span>`).join('');
  const recClass = fi.recurring > 0 ? (fi.type === 'avoidance' ? 'avoidance' : 'savings') : fi.recurring < 0 ? 'cost' : 'zero';
  const oneClass = fi.onetime > 0 ? (fi.type === 'avoidance' ? 'avoidance' : 'savings') : fi.onetime < 0 ? 'cost' : 'zero';
  const fyTag = fi.fiscal_year ? `<span class="tag">${{fi.fiscal_year}}</span>` : '';
  const typeTag = `<span class="tag">${{fi.type}}</span>`;
  const sourceLink = fi.source_doc ? `<div class="source-link">📄 ${{fi.source_doc}}</div>` : '';

  const entry = document.createElement('div');
  entry.className = 'entry';
  entry.innerHTML = `
    <div class="entry-header">
      <div class="entry-title-block">
        <div class="entry-title">${{a.name}}</div>
        <div class="entry-date">${{a.props.date || ''}}</div>
      </div>
      <div class="entry-amounts">
        <div class="amount-block">
          <div class="amount-label">Recurring</div>
          <div class="amount ${{recClass}}">${{fmtMoneyExact(fi.recurring)}}<span class="unit" style="font-size:11px">${{fi.recurring !== 0 ? '/yr' : ''}}</span></div>
        </div>
        <div class="amount-block">
          <div class="amount-label">One-Time</div>
          <div class="amount ${{oneClass}}">${{fmtMoneyExact(fi.onetime)}}</div>
        </div>
      </div>
    </div>
    <div class="entry-tags">
      ${{areas}}
      <span class="status ${{fi.status}}">${{fi.status}}</span>
      ${{typeTag}}
      ${{fyTag}}
      <span class="confidence ${{fi.confidence}}">${{fi.confidence}}</span>
    </div>
    <div class="entry-detail">${{fi.notes || '<em style="color:var(--text-muted)">No notes</em>'}}${{sourceLink}}</div>
  `;
  return entry;
}}

document.getElementById('gen-time').textContent = new Date(RAW.generated_at).toLocaleString();
document.getElementById('counts-summary').textContent =
  `${{withImpact.length}} entries · ${{awaiting.length}} awaiting · ${{PENDING.length}} pending`;
render();
</script>
</body>
</html>'''


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate cost & savings report")
    parser.add_argument("--domain", default="", help="Domain to generate report for (required)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    print(f"Fetching cost & savings data for domain: {args.domain}")
    data = await fetch_data(args.domain)
    print(f"  {len(data['accomplishments'])} total accomplishments")

    html = generate_html(data)
    output_path = Path(args.output) if args.output else OUTPUTS_DIR / "cost-savings-report.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    print(f"Report written to: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    asyncio.run(main())
