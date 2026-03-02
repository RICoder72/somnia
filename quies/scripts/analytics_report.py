#!/usr/bin/env python3
"""
Somnia Analytics Report Generator

Generates a markdown analytics report for a configurable time window.
Can be called as a script or imported and used by the daemon endpoint.

Usage:
    python3 analytics_report.py              # default 14 days
    python3 analytics_report.py --days 7     # last 7 days
    python3 analytics_report.py --days 30    # last 30 days
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow importing db.py from daemon/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daemon"))

DATABASE_URL = os.environ.get("SOMNIA_DATABASE_URL")

def get_conn():
    """Get a direct connection for the report."""
    import psycopg2, psycopg2.extras
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def q(cur, sql, params=None):
    """Query helper."""
    cur.execute(sql, params or ())
    return cur.fetchall()


def q1(cur, sql, params=None):
    """Query single row."""
    cur.execute(sql, params or ())
    return cur.fetchone()


def generate_report(days=14):
    """Generate the full analytics report as markdown."""
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    period_label = f"{days} days"

    lines = []
    def w(s=""): lines.append(s)

    # ── Header ──
    w(f"# Somnia Analytics — Last {period_label}")
    w(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    w()

    # ── Graph Snapshot ──
    w("## Graph Snapshot")
    w()
    total = q1(cur, "SELECT COUNT(*) as c FROM nodes")['c']
    ltm = q1(cur, "SELECT COUNT(*) as c FROM nodes WHERE memory_layer = 'ltm'")['c']
    sltm = q1(cur, "SELECT COUNT(*) as c FROM nodes WHERE memory_layer = 'sltm'")['c']
    pinned = q1(cur, "SELECT COUNT(*) as c FROM nodes WHERE pinned = TRUE")['c']
    edges = q1(cur, "SELECT COUNT(*) as c FROM edges")['c']
    stm = q1(cur, "SELECT COUNT(*) as c FROM stm_nodes")['c']
    avg_decay = q1(cur, "SELECT COALESCE(AVG(decay_state), 0) as a FROM nodes WHERE memory_layer = 'ltm'")['a']

    w(f"| Metric | Value |")
    w(f"|--------|-------|")
    w(f"| Total nodes | {total} |")
    w(f"| LTM (active) | {ltm} |")
    w(f"| SLTM (faded) | {sltm} |")
    w(f"| Pinned | {pinned} |")
    w(f"| Edges | {edges} |")
    w(f"| STM inbox | {stm} |")
    w(f"| Avg LTM decay | {float(avg_decay):.2f} |")
    w()

    # ── Heat Distribution ──
    w("## Heat Map Distribution (LTM)")
    w()
    heat = q1(cur, """
        SELECT 
            SUM(CASE WHEN decay_state < 0.3 THEN 1 ELSE 0 END) as cold,
            SUM(CASE WHEN decay_state >= 0.3 AND decay_state < 0.6 THEN 1 ELSE 0 END) as cool,
            SUM(CASE WHEN decay_state >= 0.6 AND decay_state < 0.85 THEN 1 ELSE 0 END) as warm,
            SUM(CASE WHEN decay_state >= 0.85 THEN 1 ELSE 0 END) as hot
        FROM nodes WHERE memory_layer = 'ltm'
    """)
    total_ltm = max(ltm, 1)
    for label, emoji, val in [
        ("Hot (≥0.85)", "☀️", heat['hot']),
        ("Warm (0.6–0.85)", "🔥", heat['warm']),
        ("Cool (0.3–0.6)", "🌤️", heat['cool']),
        ("Cold (<0.3)", "🥶", heat['cold']),
    ]:
        bar_len = int((int(val or 0) / total_ltm) * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        pct = (int(val or 0) / total_ltm) * 100
        w(f"{emoji} {label:20s} {bar} {int(val or 0):3d} ({pct:.0f}%)")
    w()

    # ── Growth ──
    w(f"## Growth (last {period_label})")
    w()
    nodes_created = q1(cur, "SELECT COUNT(*) as c FROM nodes WHERE created_at >= %s", (since,))['c']
    edges_created = q1(cur, "SELECT COUNT(*) as c FROM edges WHERE created_at >= %s", (since,))['c']
    w(f"- **Nodes created**: {nodes_created}")
    w(f"- **Edges created**: {edges_created}")
    w(f"- **Growth rate**: ~{nodes_created / max(days, 1):.1f} nodes/day, ~{edges_created / max(days, 1):.1f} edges/day")
    w()

    # ── Dream Activity ──
    w("## Dream Activity")
    w()
    dreams = q(cur, """
        SELECT 
            CASE 
                WHEN summary LIKE '[process]%%' THEN 'processing'
                WHEN summary LIKE '[ruminate]%%' THEN 'rumination'
                WHEN summary LIKE '[solo_work]%%' THEN 'solo-work'
                ELSE mode
            END as phase,
            COUNT(*) as sessions,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (ended_at - started_at))
            ), 0) as total_seconds,
            COALESCE(AVG(
                EXTRACT(EPOCH FROM (ended_at - started_at))
            ), 0) as avg_seconds
        FROM dream_log 
        WHERE ended_at >= %s AND interrupted = FALSE
        GROUP BY phase ORDER BY sessions DESC
    """, (since,))
    
    w("| Phase | Sessions | Total Time | Avg Duration |")
    w("|-------|----------|------------|--------------|")
    total_sessions = 0
    total_time = 0
    for r in dreams:
        sess = r['sessions']
        tot = float(r['total_seconds'])
        avg = float(r['avg_seconds'])
        total_sessions += sess
        total_time += tot
        w(f"| {r['phase']} | {sess} | {tot:.0f}s ({tot/60:.1f}m) | {avg:.0f}s |")
    w(f"| **Total** | **{total_sessions}** | **{total_time:.0f}s ({total_time/60:.1f}m)** | |")
    w()

    # Operation totals from dreams
    dream_details = q(cur, """
        SELECT nodes_created, edges_created, edges_reinforced
        FROM dream_log WHERE ended_at >= %s AND interrupted = FALSE
    """, (since,))
    
    total_nc = total_ec = total_er = 0
    for d in dream_details:
        for field, acc in [('nodes_created', 'nc'), ('edges_created', 'ec'), ('edges_reinforced', 'er')]:
            val = d[field]
            if isinstance(val, str):
                try: val = json.loads(val)
                except: val = []
            elif val is None:
                val = []
            if acc == 'nc': total_nc += len(val)
            elif acc == 'ec': total_ec += len(val)
            else: total_er += len(val)
    
    w("**Dream operations:**")
    w(f"- Nodes created by dreams: {total_nc}")
    w(f"- Edges created by dreams: {total_ec}")
    w(f"- Edges reinforced: {total_er}")
    w()

    # ── User Activity ──
    w("## User Interaction")
    w()
    activity = q(cur, """
        SELECT type, COUNT(*) as count
        FROM activity WHERE timestamp >= %s
        GROUP BY type ORDER BY count DESC
    """, (since,))
    
    user_types = {'recall', 'remember', 'status', 'pin', 'session', 'journal'}
    auto_types = {'dream', 'rumination', 'solo_work'}
    
    user_total = sum(r['count'] for r in activity if r['type'] in user_types)
    auto_total = sum(r['count'] for r in activity if r['type'] in auto_types)
    
    w(f"| Activity | Count |")
    w(f"|----------|-------|")
    for r in activity:
        kind = "👤" if r['type'] in user_types else "🤖"
        w(f"| {kind} {r['type']} | {r['count']} |")
    w(f"| | |")
    w(f"| **👤 User-driven** | **{user_total}** |")
    w(f"| **🤖 Autonomous** | **{auto_total}** |")
    w()

    # ── Daily Timeline ──
    w("## Daily Timeline")
    w()
    daily = q(cur, """
        SELECT DATE(timestamp) as day, type, COUNT(*) as count
        FROM activity WHERE timestamp >= %s
        GROUP BY DATE(timestamp), type ORDER BY day
    """, (since,))
    
    day_map = {}
    for r in daily:
        d = str(r['day'])
        if d not in day_map: day_map[d] = {}
        day_map[d][r['type']] = r['count']
    
    w("| Date | Recalls | Remembers | Dreams | Ruminations | Other |")
    w("|------|---------|-----------|--------|-------------|-------|")
    for d in sorted(day_map.keys()):
        dm = day_map[d]
        recalls = dm.get('recall', 0)
        remembers = dm.get('remember', 0)
        dreams = dm.get('dream', 0)
        rums = dm.get('rumination', 0)
        other = sum(v for k, v in dm.items() if k not in ('recall', 'remember', 'dream', 'rumination'))
        # Activity bar
        activity_level = recalls + remembers
        bar = "▓" * min(activity_level // 2, 15) if activity_level > 0 else "·"
        w(f"| {d} {bar} | {recalls} | {remembers} | {dreams} | {rums} | {other} |")
    w()

    # ── Idle Days ──
    all_days = set(day_map.keys())
    date_range = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
    idle_days = [d for d in date_range if d not in all_days or 
                 sum(v for k, v in day_map.get(d, {}).items() if k in user_types) == 0]
    if idle_days:
        w(f"**Idle days** (no user interaction): {', '.join(sorted(idle_days)[:10])}")
        w()

    # ── Cost ──
    w("## Cost & Token Usage")
    w()
    cost = q1(cur, """
        SELECT COUNT(*) as sessions,
               COALESCE(SUM(total_cost_usd), 0) as cost,
               COALESCE(SUM(input_tokens), 0) as input_tok,
               COALESCE(SUM(output_tokens), 0) as output_tok
        FROM diagnostics WHERE timestamp >= %s
    """, (since,))
    
    total_tokens = int(cost['input_tok']) + int(cost['output_tok'])
    w(f"| Metric | Value |")
    w(f"|--------|-------|")
    w(f"| Sessions tracked | {cost['sessions']} |")
    w(f"| Reported cost | ${float(cost['cost']):.4f} |")
    w(f"| Input tokens | {int(cost['input_tok']):,} |")
    w(f"| Output tokens | {int(cost['output_tok']):,} |")
    w(f"| Total tokens | {total_tokens:,} |")
    if total_sessions > 0:
        w(f"| Avg tokens/session | {total_tokens // max(int(cost['sessions']), 1):,} |")
    w()
    if float(cost['cost']) == 0 and int(cost['output_tok']) > 0:
        w("⚠️ *Cost reporting shows $0.00 — the CLI output likely doesn't include `cost_usd`. Token counts are accurate.*")
        w()

    # ── Pinned Node Health ──
    w("## Pinned Nodes")
    w()
    pinned_nodes = q(cur, """
        SELECT id, content, decay_state, last_accessed, metadata,
               (SELECT COUNT(*) FROM edges WHERE source_id = n.id OR target_id = n.id) as edge_count
        FROM nodes n WHERE pinned = TRUE 
        ORDER BY last_accessed DESC NULLS LAST
    """)
    
    w("| Node | Decay | Edges | Last Active | Status |")
    w("|------|-------|-------|-------------|--------|")
    for n in pinned_nodes:
        meta = n.get('metadata') or {}
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except: meta = {}
        status = meta.get('status', '—')
        la = str(n['last_accessed'])[:10] if n['last_accessed'] else 'never'
        staleness = ''
        if n['last_accessed']:
            days_stale = (now - n['last_accessed']).days
            if days_stale > 7:
                staleness = f" ⚠️ {days_stale}d ago"
        w(f"| {n['id']} | {n['decay_state']:.2f} | {n['edge_count']} | {la}{staleness} | {status} |")
    w()

    # ── Top Nodes by Connectivity ──
    w("## Most Connected Nodes (Top 10)")
    w()
    connected = q(cur, """
        SELECT n.id, n.type, n.content, n.decay_state, n.pinned, n.memory_layer,
               COUNT(DISTINCT e.id) as edge_count
        FROM nodes n
        LEFT JOIN edges e ON e.source_id = n.id OR e.target_id = n.id
        GROUP BY n.id, n.type, n.content, n.decay_state, n.pinned, n.memory_layer
        ORDER BY edge_count DESC LIMIT 10
    """)
    
    w("| Node | Type | Edges | Decay | Layer |")
    w("|------|------|-------|-------|-------|")
    for n in connected:
        pin = "📌 " if n['pinned'] else ""
        w(f"| {pin}{n['id'][:35]} | {n['type']} | {n['edge_count']} | {n['decay_state']:.2f} | {n['memory_layer']} |")
    w()

    # ── Coldest LTM (at risk) ──
    w("## Coldest LTM Nodes (at risk of SLTM demotion)")
    w()
    coldest = q(cur, """
        SELECT id, type, content, decay_state, last_accessed
        FROM nodes WHERE memory_layer = 'ltm' AND pinned = FALSE
        ORDER BY decay_state ASC LIMIT 10
    """)
    
    for n in coldest:
        la = str(n['last_accessed'])[:10] if n['last_accessed'] else 'never'
        w(f"- 🥶 **{n['id']}** (decay={n['decay_state']:.3f}, last={la}): {n['content'][:80]}")
    w()

    # ── SLTM Contents ──
    if sltm > 0:
        w(f"## SLTM Archive ({sltm} faded memories)")
        w()
        sltm_sample = q(cur, """
            SELECT id, type, content, decay_state, last_accessed
            FROM nodes WHERE memory_layer = 'sltm'
            ORDER BY decay_state ASC LIMIT 15
        """)
        for n in sltm_sample:
            la = str(n['last_accessed'])[:10] if n['last_accessed'] else 'never'
            w(f"- 🌫️ {n['id']} ({n['type']}, last={la}): {n['content'][:80]}")
        if sltm > 15:
            w(f"- ... and {sltm - 15} more")
        w()

    # ── Footer ──
    w("---")
    w(f"*Report covers {since[:10]} to {now.strftime('%Y-%m-%d')}. Generated by `scripts/analytics_report.py`.*")

    conn.close()
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Somnia Analytics Report")
    parser.add_argument("--days", type=int, default=14, help="Number of days to cover")
    parser.add_argument("--output", type=str, help="Write to file instead of stdout")
    args = parser.parse_args()

    report = generate_report(days=args.days)
    
    if args.output:
        Path(args.output).write_text(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
