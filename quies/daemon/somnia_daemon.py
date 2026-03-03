#!/usr/bin/env python3
"""
Somnia Daemon

HTTP server that orchestrates Claude's dream cycles.
Shells out to Claude Code CLI for actual consolidation work.
Includes background dream scheduler with rumination support.

PostgreSQL backend via daemon/db.py.
"""

import os
import sys
import json
import re
import subprocess
import uuid
import threading
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, request
import yaml

from db import execute, execute_many, init_db as db_init, get_conn, put_conn

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Paths - separate app code from persistent data
APP_DIR = Path(os.environ.get("SOMNIA_APP_DIR", "/app"))
DATA_DIR = Path(os.environ.get("SOMNIA_DATA_DIR", "/data/somnia"))

CONFIG_PATH = APP_DIR / "daemon" / "config.yaml"
PROMPTS_DIR = APP_DIR / "prompts"


# ============================================================================
# CONFIG
# ============================================================================

def load_config():
    if not CONFIG_PATH.exists():
        app.logger.warning(f"Config not found at {CONFIG_PATH}, using defaults")
        return {
            'api': {
                'model': 'claude-sonnet-4-20250514',
                'credentials_ref': 'op://Key Vault/Anthropic API/credential',
                'oauth_credentials_ref': 'op://Key Vault/Claude Code OAuth/credential'
            },
            'consolidation': {
                'min_inbox_items': 1
            },
            'scheduler': {
                'enabled': True,
                'check_interval_minutes': 15,
                'global_cooldown_minutes': 240,
                'rumination_cooldown_minutes': 360,
                'solo_work_cooldown_minutes': 360,
                'min_nodes_for_rumination': 5
            }
        }
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    if 'scheduler' not in config:
        config['scheduler'] = {
            'enabled': True,
            'check_interval_minutes': 15,
            'global_cooldown_minutes': 240,
            'rumination_cooldown_minutes': 360,
            'solo_work_cooldown_minutes': 360,
            'min_nodes_for_rumination': 5
        }
    return config

CONFIG = load_config()


# ============================================================================
# AUTH
# ============================================================================

def get_claude_auth():
    """Retrieve Claude authentication from environment or 1Password."""
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if oauth_token:
        return ('oauth', oauth_token)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return ('api_key', api_key)

    oauth_ref = CONFIG['api'].get('oauth_credentials_ref',
                                   'op://Key Vault/Claude Code OAuth/credential')
    try:
        result = subprocess.run(
            ["op", "read", oauth_ref],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return ('oauth', result.stdout.strip())
    except (FileNotFoundError, Exception) as e:
        app.logger.debug(f"OAuth token not in 1Password: {e}")

    api_ref = CONFIG['api'].get('credentials_ref',
                                 'op://Key Vault/Anthropic API/credential')
    try:
        result = subprocess.run(
            ["op", "read", api_ref],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return ('api_key', result.stdout.strip())
        else:
            app.logger.error(f"1Password credential fetch failed: {result.stderr}")
            return (None, None)
    except FileNotFoundError:
        app.logger.error("1Password CLI not found and no auth env vars set")
        return (None, None)
    except Exception as e:
        app.logger.error(f"Exception getting credentials: {e}")
        return (None, None)


# ============================================================================
# PROMPT & DATA HELPERS
# ============================================================================

def load_prompt(name):
    """Load a prompt file."""
    path = PROMPTS_DIR / f"{name}.md"
    with open(path) as f:
        return f.read()


def read_continuity_note():
    """Read the last continuity note left by a previous rumination instance."""
    path = DATA_DIR / "continuity_note.md"
    if path.exists():
        return path.read_text().strip()
    return None


def write_continuity_note(note):
    """Save a continuity note for the next rumination instance."""
    path = DATA_DIR / "continuity_note.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note.strip())
    except PermissionError as e:
        logger.warning(f"Could not write continuity note (permissions): {e}")
        log_event('warning', 'rumination',
                  f'Continuity note write failed: {e}',
                  {'path': str(path)})


def resolve_edge_ids(edge_ids_json):
    """Convert edge UUIDs from dream log to readable source->target format."""
    try:
        edge_ids = json.loads(edge_ids_json) if isinstance(edge_ids_json, str) else edge_ids_json
    except (json.JSONDecodeError, TypeError):
        return []

    if not edge_ids:
        return []

    resolved = []
    for eid in edge_ids:
        row = execute(
            "SELECT source_id, target_id, type FROM edges WHERE id = %s",
            (eid,), fetch='one')
        if row:
            resolved.append(f"{row['source_id']} --[{row['type']}]--> {row['target_id']}")
        else:
            resolved.append(f"(edge {eid[:8]}... not found)")
    return resolved


def enrich_dream(dream):
    """Add resolved edge info and computed fields to a dream record."""
    d = dict(dream) if not isinstance(dream, dict) else dream.copy()

    if d.get('started_at') and d.get('ended_at'):
        start = d['started_at'] if isinstance(d['started_at'], datetime) else datetime.fromisoformat(str(d['started_at']))
        end = d['ended_at'] if isinstance(d['ended_at'], datetime) else datetime.fromisoformat(str(d['ended_at']))
        d['duration_seconds'] = int((end - start).total_seconds())
    else:
        d['duration_seconds'] = None

    summary = d.get('summary', '')
    if summary and summary.startswith('[ruminate]'):
        d['mode'] = 'rumination'
    elif summary and summary.startswith('[process]'):
        d['mode'] = 'processing'
    elif summary and summary.startswith('[solo_work]'):
        d['mode'] = 'solo_work'
    else:
        d['mode'] = 'unknown'

    d['edges_created_resolved'] = resolve_edge_ids(d.get('edges_created'))

    for field in ('nodes_created', 'edges_reinforced'):
        val = d.get(field)
        if isinstance(val, str):
            try:
                d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                d[field] = []
        elif val is None:
            d[field] = []

    # Convert datetime objects to ISO strings for JSON serialization
    for field in ('started_at', 'ended_at'):
        if isinstance(d.get(field), datetime):
            d[field] = d[field].isoformat()

    return d


def _to_datetime(val):
    """Convert a value to datetime, handling both strings and datetime objects."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val)
    return None


def find_gap_periods(gap_threshold_hours=2, max_periods=5):
    """Identify periods of inactivity where dreams/ruminations occurred."""
    rows = execute(
        "SELECT timestamp FROM activity "
        "WHERE type IN ('recall', 'remember', 'status') "
        "ORDER BY timestamp ASC",
        fetch='all') or []
    interactions = [_to_datetime(row['timestamp']) for row in rows]

    dream_rows = execute(
        "SELECT * FROM dream_log WHERE interrupted = FALSE ORDER BY started_at ASC",
        fetch='all') or []
    dreams = [enrich_dream(row) for row in dream_rows]

    if not interactions or not dreams:
        return []

    threshold = timedelta(hours=gap_threshold_hours)
    periods = []

    for i in range(1, len(interactions)):
        gap_start = interactions[i - 1]
        gap_end = interactions[i]
        gap_duration = gap_end - gap_start

        if gap_duration >= threshold:
            gap_dreams = [
                d for d in dreams
                if gap_start <= _to_datetime(d['started_at']) <= gap_end
            ]
            if gap_dreams:
                periods.append({
                    "gap_start": gap_start.isoformat(),
                    "gap_end": gap_end.isoformat(),
                    "gap_hours": round(gap_duration.total_seconds() / 3600, 1),
                    "dream_count": len(gap_dreams),
                    "dreams": gap_dreams
                })

    last_interaction = interactions[-1]
    now = datetime.now(last_interaction.tzinfo)
    if now - last_interaction >= threshold:
        open_dreams = [
            d for d in dreams
            if _to_datetime(d['started_at']) > last_interaction
        ]
        if open_dreams:
            periods.append({
                "gap_start": last_interaction.isoformat(),
                "gap_end": None,
                "gap_hours": round((now - last_interaction).total_seconds() / 3600, 1),
                "dream_count": len(open_dreams),
                "dreams": open_dreams
            })

    return periods[-max_periods:]


def get_inbox_items():
    """Get unprocessed STM nodes (short-term memory)."""
    rows = execute(
        "SELECT id, content, domain, source, captured_at FROM stm_nodes ORDER BY captured_at",
        fetch='all') or []
    # Convert timestamps for JSON
    items = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get('captured_at'), datetime):
            d['captured_at'] = d['captured_at'].isoformat()
        items.append(d)
    return items


def get_last_dream():
    """Get the most recent dream log entry."""
    row = execute(
        "SELECT * FROM dream_log ORDER BY ended_at DESC NULLS LAST LIMIT 1",
        fetch='one')
    if row:
        d = dict(row)
        for field in ('started_at', 'ended_at'):
            if isinstance(d.get(field), datetime):
                d[field] = d[field].isoformat()
        return d
    return None


def get_last_phase_end(phase_prefix=None):
    """Get ended_at of the most recent completed dream, optionally filtered by phase.
    
    phase_prefix: None for any phase, '[ruminate]' for rumination, '[solo_work]' for solo-work.
    Returns datetime or None.
    """
    if phase_prefix:
        row = execute(
            "SELECT ended_at FROM dream_log "
            "WHERE interrupted = FALSE AND summary LIKE %s "
            "ORDER BY ended_at DESC NULLS LAST LIMIT 1",
            (phase_prefix + '%',), fetch='one')
    else:
        row = execute(
            "SELECT ended_at FROM dream_log "
            "WHERE interrupted = FALSE "
            "ORDER BY ended_at DESC NULLS LAST LIMIT 1",
            fetch='one')
    if row and row.get('ended_at'):
        return _to_datetime(row['ended_at'])
    return None


def check_global_cooldown():
    """Check if the global cooldown between any autonomous phase has elapsed.
    
    Measured from the later of (last dream end, last interaction), so the first
    phase doesn't fire until you've been away long enough either.
    
    Returns (ok: bool, reason: str).
    """
    cooldown_min = CONFIG.get('scheduler', {}).get('global_cooldown_minutes',
                   CONFIG.get('consolidation', {}).get('cooldown_minutes', 240))
    cooldown = timedelta(minutes=cooldown_min)

    last_dream_end = get_last_phase_end()
    last_interaction = get_last_interaction()

    # Use the more recent of the two as the reference point
    reference = None
    if last_dream_end and last_interaction:
        # Make both offset-aware or offset-naive for comparison
        lde = last_dream_end
        li = last_interaction
        if lde.tzinfo and not li.tzinfo:
            li = li.replace(tzinfo=lde.tzinfo)
        elif li.tzinfo and not lde.tzinfo:
            lde = lde.replace(tzinfo=li.tzinfo)
        reference = max(lde, li)
    elif last_dream_end:
        reference = last_dream_end
    elif last_interaction:
        reference = last_interaction

    if not reference:
        return True, "No previous activity"

    now = datetime.now(reference.tzinfo) if reference.tzinfo else datetime.now()
    if now - reference < cooldown:
        remaining = cooldown - (now - reference)
        return False, f"Global cooldown active, {remaining.seconds // 60}m remaining"
    return True, "Global cooldown cleared"


# ============================================================================
# HEAT MAP — Automatic Decay Mechanics
# ============================================================================

def warm_nodes(node_ids, delta=0.02):
    """Bump decay_state up for accessed nodes. Capped at 1.0. Promotes SLTM→LTM."""
    if not node_ids:
        return
    for nid in node_ids:
        execute("""
            UPDATE nodes SET decay_state = LEAST(1.0, decay_state + %s),
            last_accessed = NOW(),
            memory_layer = 'ltm'
            WHERE id = %s
        """, (delta, nid))


def apply_passive_cooldown():
    """Apply decay to all nodes each scheduler cycle.
    
    Decay modifiers:
    - Pinned nodes have a floor (pinned_floor)
    - Well-reinforced nodes have a floor (reinforcement_floor)
    - Highly-connected nodes decay slower (connectivity tiers)
    - Demotes deeply cold LTM nodes to SLTM
    """
    decay_cfg = CONFIG.get('decay', {})
    base_rate = decay_cfg.get('passive_cooldown_per_cycle', 0.0005)
    sltm_threshold = decay_cfg.get('sltm_threshold', 0.05)
    pinned_floor = decay_cfg.get('pinned_floor', 0.5)
    reinf_floor = decay_cfg.get('reinforcement_floor', 0.20)
    stable_count = decay_cfg.get('stable_reinforcement_count', 5)
    use_connectivity = decay_cfg.get('connectivity_decay_reduction', True)
    tiers = decay_cfg.get('connectivity_tiers', {5: 0.75, 10: 0.50, 20: 0.25})

    # Sort tiers descending so we match highest first
    sorted_tiers = sorted(tiers.items(), key=lambda x: -x[0])

    if use_connectivity:
        # Fetch non-pinned nodes with their edge counts and reinforcement
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT n.id, n.decay_state, n.reinforcement_count,
                           COUNT(DISTINCT e.id) as edge_count
                    FROM nodes n
                    LEFT JOIN edges e ON e.source_id = n.id OR e.target_id = n.id
                    WHERE n.pinned = FALSE AND n.decay_state > 0.0
                    GROUP BY n.id, n.decay_state, n.reinforcement_count
                """)
                rows = cur.fetchall()

                for row in rows:
                    node_id, decay_state, reinf_count, edge_count = row

                    # Determine decay multiplier from connectivity
                    multiplier = 1.0
                    for threshold, mult in sorted_tiers:
                        if edge_count >= threshold:
                            multiplier = mult
                            break

                    effective_rate = base_rate * multiplier
                    new_decay = max(0.0, decay_state - effective_rate)

                    # Apply reinforcement floor
                    if reinf_count >= stable_count:
                        new_decay = max(reinf_floor, new_decay)

                    if new_decay != decay_state:
                        cur.execute("UPDATE nodes SET decay_state = %s WHERE id = %s",
                                    (new_decay, node_id))

                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
    else:
        # Simple mode: uniform decay for non-pinned, with reinforcement floor
        execute("""
            UPDATE nodes SET decay_state = GREATEST(0.0, decay_state - %s)
            WHERE pinned = FALSE AND decay_state > 0.0
            AND reinforcement_count < %s
        """, (base_rate, stable_count))

        # Reinforced nodes: decay but respect floor
        execute("""
            UPDATE nodes SET decay_state = GREATEST(%s, decay_state - %s)
            WHERE pinned = FALSE AND decay_state > %s
            AND reinforcement_count >= %s
        """, (reinf_floor, base_rate, reinf_floor, stable_count))

    # Cool pinned nodes (floor at pinned_floor)
    execute("""
        UPDATE nodes SET decay_state = GREATEST(%s, decay_state - %s)
        WHERE pinned = TRUE AND decay_state > %s
    """, (pinned_floor, base_rate, pinned_floor))

    # Demote deeply cold LTM nodes to SLTM (not pinned)
    execute("""
        UPDATE nodes SET memory_layer = 'sltm'
        WHERE memory_layer = 'ltm' AND pinned = FALSE AND decay_state <= %s
    """, (sltm_threshold,))


def get_graph_stats():
    """Get basic graph statistics."""
    stats = {}
    row = execute("SELECT COUNT(*) as count FROM nodes WHERE memory_layer = 'ltm'", fetch='one')
    stats['node_count'] = row['count']
    row = execute("SELECT COUNT(*) as count FROM nodes WHERE memory_layer = 'sltm'", fetch='one')
    stats['sltm_count'] = row['count']
    row = execute("SELECT COUNT(*) as count FROM edges", fetch='one')
    stats['edge_count'] = row['count']
    row = execute("SELECT COUNT(*) as count FROM stm_nodes", fetch='one')
    stats['inbox_pending'] = row['count']
    row = execute("SELECT AVG(decay_state) as avg FROM nodes WHERE memory_layer = 'ltm'", fetch='one')
    stats['avg_decay'] = row['avg'] if row['avg'] else 1.0
    row = execute("SELECT COUNT(*) as count FROM nodes WHERE pinned = TRUE", fetch='one')
    stats['pinned_count'] = row['count']
    return stats


def get_pinned_nodes():
    """Query nodes where pinned = TRUE."""
    rows = execute(
        "SELECT * FROM nodes WHERE pinned = TRUE ORDER BY last_accessed DESC",
        fetch='all') or []
    return [dict(row) for row in rows]


# ============================================================================
# ACTIVITY TRACKING
# ============================================================================

def record_activity(activity_type, metadata=None):
    """Record an interaction or event in the activity log."""
    import psycopg2.extras
    execute(
        "INSERT INTO activity (id, type, timestamp, metadata) VALUES (%s, %s, %s, %s)",
        (str(uuid.uuid4()), activity_type, datetime.now().isoformat(),
         json.dumps(metadata) if metadata else None)
    )


def log_event(level, source, message, metadata=None, dream_id=None):
    """Write a structured event to the system_log table.
    
    Use for important, queryable events — not debug traces.
    Levels: 'error', 'warning', 'info'
    Sources: 'scheduler', 'dream', 'rumination', 'solo_work', 'recovery', 'backup', 'api'
    """
    try:
        execute(
            "INSERT INTO system_log (id, level, source, message, metadata, dream_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), level, source, message,
             json.dumps(metadata) if metadata else None,
             dream_id)
        )
    except Exception as e:
        # Never let logging failures propagate — fall back to stderr
        logger.error(f"log_event failed ({level}/{source}): {e}")


def get_last_interaction():
    """Get timestamp of most recent user-facing interaction."""
    row = execute(
        "SELECT timestamp FROM activity "
        "WHERE type IN ('recall', 'remember', 'status') "
        "ORDER BY timestamp DESC LIMIT 1",
        fetch='one')
    if row:
        return _to_datetime(row['timestamp'])
    return None


def get_dreams_since_last_interaction():
    """Count dreams since last user interaction."""
    last = get_last_interaction()
    if not last:
        row = execute(
            "SELECT COUNT(*) as count FROM activity WHERE type IN ('dream', 'rumination')",
            fetch='one')
        return row['count']

    row = execute(
        "SELECT COUNT(*) as count FROM activity "
        "WHERE type IN ('dream', 'rumination') AND timestamp > %s",
        (last.isoformat(),), fetch='one')
    return row['count']


def get_ruminations_since_last_interaction():
    """Count rumination-only dreams since last user interaction."""
    last = get_last_interaction()
    if not last:
        row = execute(
            "SELECT COUNT(*) as count FROM activity WHERE type = 'rumination'",
            fetch='one')
        return row['count']

    row = execute(
        "SELECT COUNT(*) as count FROM activity "
        "WHERE type = 'rumination' AND timestamp > %s",
        (last.isoformat(),), fetch='one')
    return row['count']


def get_solo_work_since_last_interaction():
    """Count solo-work sessions since last user interaction."""
    last = get_last_interaction()
    if not last:
        row = execute(
            "SELECT COUNT(*) as count FROM activity WHERE type = 'solo_work'",
            fetch='one')
        return row['count']

    row = execute(
        "SELECT COUNT(*) as count FROM activity "
        "WHERE type = 'solo_work' AND timestamp > %s",
        (last.isoformat(),), fetch='one')
    return row['count']


def get_daily_cost():
    """Get total API cost for today across all autonomous phases."""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    row = execute(
        "SELECT COALESCE(SUM(total_cost_usd), 0) as total FROM diagnostics "
        "WHERE timestamp >= %s",
        (today_start.isoformat(),), fetch='one')
    return float(row['total']) if row else 0.0


def should_solo_work():
    """Decide whether to run a solo-work session.
    
    Requires: global cooldown cleared, own cooldown cleared, STM empty,
    meaningful graph, within budget.
    """
    sched = CONFIG.get('scheduler', {})
    budget = CONFIG.get('budget', {})
    stats = get_graph_stats()

    # Must have a meaningful graph to review
    if stats['node_count'] < 10:
        return False, f"Only {stats['node_count']} nodes, need at least 10 for solo-work"

    # STM must be empty (dreaming caught up)
    if stats['inbox_pending'] > 0:
        return False, "STM has items — should process first, not solo-work"

    # Global cooldown gate
    ok, reason = check_global_cooldown()
    if not ok:
        return False, reason

    # Own cooldown: time since last solo-work
    own_cooldown_min = sched.get('solo_work_cooldown_minutes', 360)
    last_solo = get_last_phase_end('[solo_work]')
    if last_solo:
        now = datetime.now(last_solo.tzinfo) if last_solo.tzinfo else datetime.now()
        elapsed = now - last_solo
        if elapsed < timedelta(minutes=own_cooldown_min):
            remaining = timedelta(minutes=own_cooldown_min) - elapsed
            return False, f"Solo-work cooldown active, {remaining.seconds // 60}m remaining"

    # Budget check
    daily_cost = get_daily_cost()
    max_daily = budget.get('max_cost_per_day', 2.00)
    max_session = budget.get('max_cost_solo_work', 1.00)
    if daily_cost + max_session > max_daily:
        return False, f"Budget: ${daily_cost:.2f} spent today, solo-work could exceed ${max_daily:.2f} cap"

    return True, "Ready for solo-work"


def get_activity_summary():
    """Get a summary of recent activity for status reporting."""
    row = execute(
        "SELECT type, timestamp FROM activity "
        "WHERE type IN ('recall', 'remember', 'status') "
        "ORDER BY timestamp DESC LIMIT 1",
        fetch='one')
    last_interaction = {"type": row['type'], "timestamp": str(row['timestamp'])} if row else None

    row = execute(
        "SELECT type, timestamp FROM activity "
        "WHERE type IN ('dream', 'rumination') "
        "ORDER BY timestamp DESC LIMIT 1",
        fetch='one')
    last_dream_activity = {"type": row['type'], "timestamp": str(row['timestamp'])} if row else None

    dreams_since = get_dreams_since_last_interaction()
    ruminations_since = get_ruminations_since_last_interaction()

    rows = execute(
        "SELECT type, COUNT(*) as count FROM activity GROUP BY type",
        fetch='all') or []
    totals = {row['type']: row['count'] for row in rows}

    return {
        "last_interaction": last_interaction,
        "last_dream_activity": last_dream_activity,
        "dreams_since_last_interaction": dreams_since,
        "ruminations_since_last_interaction": ruminations_since,
        "totals": totals
    }


# ============================================================================
# DREAM OPERATION PARSING & APPLICATION
# ============================================================================

def extract_json_from_output(output):
    """Extract the operations JSON block from Claude's dream output."""
    if isinstance(output, dict):
        # Try multiple fields where Claude Code CLI might put the response
        text = ''
        for field in ('result', 'raw', 'content', 'text', 'response'):
            val = output.get(field)
            if val and isinstance(val, str) and val.strip():
                text = val
                break
            elif val and isinstance(val, list):
                # content might be a list of blocks
                for block in val:
                    if isinstance(block, dict) and block.get('text'):
                        text = block['text']
                        break
                    elif isinstance(block, dict) and block.get('content'):
                        text = str(block['content'])
                        break
                if text:
                    break

        # Check for messages array (multi-turn format)
        if not text.strip() and 'messages' in output:
            for msg in reversed(output.get('messages', [])):
                if isinstance(msg, dict):
                    msg_content = msg.get('content', '')
                    if isinstance(msg_content, str) and msg_content.strip():
                        text = msg_content
                        break
                    elif isinstance(msg_content, list):
                        for block in msg_content:
                            if isinstance(block, dict) and block.get('text'):
                                text = block['text']
                                break

        if not text.strip():
            logger.warning(
                f"extract_json: all known fields empty. "
                f"Keys present: {list(output.keys())}, "
                f"output_tokens: {output.get('usage', {}).get('output_tokens', '?')}")
            text = str(output)
    else:
        text = str(output)

    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    json_match = re.search(r'(\{[^{}]*"operations"\s*:\s*\[.*?\]\s*[^{}]*\})', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Also try finding solo-work findings format
    json_match = re.search(r'(\{[^{}]*"findings"\s*:\s*\[.*?\]\s*[^{}]*\})', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    for match in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        try:
            parsed = json.loads(match.group(0))
            if 'operations' in parsed or 'findings' in parsed or 'continuity_note' in parsed:
                return parsed
        except json.JSONDecodeError:
            continue

    return None


def apply_dream_operations(operations_json):
    """Apply operations from Claude's dream output to the database."""
    results = {
        "nodes_created": [], "edges_created": [],
        "edges_reinforced": [], "inbox_processed": [], "errors": []
    }

    if not operations_json or 'operations' not in operations_json:
        return results

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for op in operations_json.get('operations', []):
                try:
                    op_type = op.get('op')

                    if op_type == 'create_node':
                        node_id = op.get('id', str(uuid.uuid4()))
                        pinned = op.get('pinned', False)
                        cur.execute("""
                            INSERT INTO nodes (id, type, content, metadata, pinned)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, (node_id, op.get('type', 'memory'), op.get('content', ''),
                              json.dumps(op.get('metadata', {})), pinned))
                        results['nodes_created'].append(node_id)

                    elif op_type == 'create_edge':
                        edge_id = str(uuid.uuid4())
                        cur.execute("""
                            INSERT INTO edges (id, source_id, target_id, type, weight)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, (edge_id, op.get('source_id'), op.get('target_id'),
                              op.get('type', 'relates_to'), op.get('weight', 1.0)))
                        results['edges_created'].append(edge_id)
                        # Heat map: warm both nodes involved in new edge
                        dream_warmth = CONFIG.get('decay', {}).get('dream_edge_warmth', 0.03)
                        for nid in (op.get('source_id'), op.get('target_id')):
                            if nid:
                                cur.execute("""
                                    UPDATE nodes SET decay_state = LEAST(1.0, decay_state + %s),
                                    memory_layer = 'ltm'
                                    WHERE id = %s
                                """, (dream_warmth, nid))

                    elif op_type == 'reinforce_edge':
                        cur.execute("""
                            UPDATE edges SET weight = weight + 0.1,
                            last_reinforced = NOW()
                            WHERE source_id = %s AND target_id = %s
                        """, (op.get('source_id'), op.get('target_id')))
                        results['edges_reinforced'].append(
                            f"{op.get('source_id')}->{op.get('target_id')}")

                    elif op_type == 'mark_processed':
                        stm_id = op.get('inbox_id') or op.get('stm_node_id')
                        cur.execute("DELETE FROM stm_nodes WHERE id = %s", (stm_id,))
                        cur.execute(
                            "UPDATE inbox SET processed = TRUE WHERE id = %s", (stm_id,))
                        results['inbox_processed'].append(stm_id)

                    elif op_type == 'update_node':
                        node_id = op.get('id')
                        new_content = op.get('new_content', '')
                        reason = op.get('reason', '')
                        if node_id and new_content:
                            # Sovereignty: refuse to modify pinned nodes
                            cur.execute("SELECT pinned FROM nodes WHERE id = %s", (node_id,))
                            check = cur.fetchone()
                            if check and check[0]:
                                results.setdefault('sovereignty_blocked', []).append(
                                    f"update_node blocked for pinned node {node_id}")
                            else:
                                cur.execute("""
                                    UPDATE nodes SET content = %s,
                                    last_accessed = NOW()
                                    WHERE id = %s
                                """, (new_content, node_id))
                                results.setdefault('nodes_updated', []).append(
                                    f"{node_id} ({reason})")

                    elif op_type == 'adjust_decay':
                        node_id = op.get('id')
                        delta = op.get('delta', 0)
                        reason = op.get('reason', '')
                        if node_id and delta != 0:
                            cur.execute("""
                                UPDATE nodes SET decay_state = GREATEST(0.0, LEAST(1.0, decay_state + %s))
                                WHERE id = %s
                            """, (delta, node_id))
                            results.setdefault('decay_adjusted', []).append(
                                f"{node_id} ({delta:+.2f}: {reason})")

                    elif op_type == 'append_dream_note':
                        node_id = op.get('id')
                        note = op.get('note', '')
                        if node_id and note:
                            cur.execute("""
                                UPDATE nodes SET dream_notes = COALESCE(dream_notes, '[]'::jsonb) || %s::jsonb
                                WHERE id = %s
                            """, (json.dumps([{"note": note, "timestamp": datetime.now().isoformat()}]),
                                  node_id))
                            results.setdefault('dream_notes_added', []).append(
                                f"{node_id}: {note[:60]}")

                    elif op_type == 'suggest_pin':
                        # Dream cycle can suggest pins but never pin itself
                        node_id = op.get('id', '')
                        reason = op.get('reason', '')
                        results.setdefault('pin_suggestions', []).append(
                            f"{node_id}: {reason}")

                except Exception as e:
                    results['errors'].append(f"{op_type}: {str(e)}")

            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return results


def log_diagnostics(dream_id, graph_stats_before, graph_stats_after=None,
                    cli_output=None, exit_code=None, duration_ms=None,
                    op_results=None, notes=None):
    """Log diagnostics snapshot for a dream session."""
    diag_id = str(uuid.uuid4())

    input_tokens = output_tokens = total_cost = None
    if cli_output and isinstance(cli_output, dict):
        # Primary: top-level fields from Claude CLI --output-format json
        total_cost = cli_output.get('total_cost_usd')

        # Token usage: prefer modelUsage (includes cache) over usage (partial)
        model_usage = cli_output.get('modelUsage', {})
        if model_usage:
            # modelUsage is keyed by model name, grab the first one
            first_model = next(iter(model_usage.values()), {}) if isinstance(model_usage, dict) else {}
            input_tokens = (first_model.get('inputTokens', 0)
                          + first_model.get('cacheReadInputTokens', 0)
                          + first_model.get('cacheCreationInputTokens', 0))
            output_tokens = first_model.get('outputTokens', 0)
        else:
            usage = cli_output.get('usage', {})
            input_tokens = usage.get('input_tokens')
            output_tokens = usage.get('output_tokens')

    stats = graph_stats_after or graph_stats_before

    notes_parts = []
    if notes:
        notes_parts.append(notes)
    if op_results:
        notes_parts.append(
            f"Operations: {len(op_results.get('nodes_created', []))} nodes, "
            f"{len(op_results.get('edges_created', []))} edges, "
            f"{len(op_results.get('edges_reinforced', []))} reinforced, "
            f"{len(op_results.get('nodes_updated', []))} updated, "
            f"{len(op_results.get('decay_adjusted', []))} decay adj, "
            f"{len(op_results.get('dream_notes_added', []))} dream notes, "
            f"{len(op_results.get('pin_suggestions', []))} pin suggestions, "
            f"{len(op_results.get('inbox_processed', []))} inbox processed")
        if op_results.get('errors'):
            notes_parts.append(f"Errors: {'; '.join(op_results['errors'])}")

    execute("""
        INSERT INTO diagnostics (
            id, dream_id, total_cost_usd, input_tokens, output_tokens,
            duration_ms, cli_output, exit_code,
            node_count, edge_count, inbox_depth, avg_decay_state, notes
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (diag_id, dream_id, total_cost, input_tokens, output_tokens,
          duration_ms, json.dumps(cli_output) if cli_output else None, exit_code,
          stats.get('node_count'), stats.get('edge_count'),
          stats.get('inbox_pending'), stats.get('avg_decay'),
          '\n'.join(notes_parts) if notes_parts else None))

    return diag_id


# ============================================================================
# DREAM READINESS & CONSOLIDATION
# ============================================================================

def can_dream():
    """Check if conditions are met for a processing dream."""
    inbox = get_inbox_items()
    if len(inbox) < CONFIG['consolidation']['min_inbox_items']:
        return False, f"Inbox has {len(inbox)} items, need {CONFIG['consolidation']['min_inbox_items']}"

    # Global cooldown gate
    ok, reason = check_global_cooldown()
    if not ok:
        return False, reason

    # Budget check
    budget = CONFIG.get('budget', {})
    daily_cost = get_daily_cost()
    max_daily = budget.get('max_cost_per_day', 2.00)
    max_session = budget.get('max_cost_dream', 0.30)
    if daily_cost + max_session > max_daily:
        return False, f"Budget: ${daily_cost:.2f} spent today, dream could exceed ${max_daily:.2f} cap"

    return True, "Ready to dream"


def should_ruminate():
    """Decide whether to ruminate.
    
    Requires: global cooldown cleared, own cooldown cleared, STM empty,
    enough nodes to reflect on, within budget.
    """
    sched = CONFIG.get('scheduler', {})
    stats = get_graph_stats()

    min_nodes = sched.get('min_nodes_for_rumination', 5)
    if stats['node_count'] < min_nodes:
        return False, f"Only {stats['node_count']} nodes, need {min_nodes}"

    if stats['inbox_pending'] > 0:
        return False, "STM has items — should process, not ruminate"

    # Global cooldown gate
    ok, reason = check_global_cooldown()
    if not ok:
        return False, reason

    # Own cooldown: time since last rumination
    own_cooldown_min = sched.get('rumination_cooldown_minutes', 360)
    last_rumination = get_last_phase_end('[ruminate]')
    if last_rumination:
        now = datetime.now(last_rumination.tzinfo) if last_rumination.tzinfo else datetime.now()
        elapsed = now - last_rumination
        if elapsed < timedelta(minutes=own_cooldown_min):
            remaining = timedelta(minutes=own_cooldown_min) - elapsed
            return False, f"Rumination cooldown active, {remaining.seconds // 60}m remaining"

    # Budget check
    budget = CONFIG.get('budget', {})
    daily_cost = get_daily_cost()
    max_daily = budget.get('max_cost_per_day', 2.00)
    max_session = budget.get('max_cost_rumination', 0.30)
    if daily_cost + max_session > max_daily:
        return False, f"Budget: ${daily_cost:.2f} spent today, rumination could exceed ${max_daily:.2f} cap"

    return True, "Ready to ruminate"


def _solo_work_recovery_call(original_output, dream_id):
    """Make a compact follow-up call to extract findings when solo-work hits max_turns.
    
    The original session did real work (web searches, file reads) but ran out of turns
    before producing the findings JSON. This recovery call gets a short summary.
    """
    # Extract whatever text we can from the original output
    if isinstance(original_output, dict):
        raw_text = original_output.get('result') or original_output.get('raw') or ''
        if not raw_text:
            raw_text = str(original_output)
    else:
        raw_text = str(original_output)

    # Truncate to avoid blowing context on the recovery call
    raw_text = raw_text[:8000]

    recovery_prompt = f"""You were running a solo-work session but ran out of turns before producing your findings JSON.

Here is whatever output was captured from your session:

{raw_text}

Based on what you explored, produce a findings JSON. If you genuinely cannot reconstruct
what you investigated, produce a minimal honest entry. Output exactly ONE JSON block:

```json
{{
  "summary": "What you investigated and what stood out",
  "findings": [
    {{
      "title": "Finding title",
      "description": "What you found",
      "category": "project|infrastructure|idea|research",
      "significance": "notable|interesting|important",
      "related_nodes": [],
      "stm_observation": "Concise observation for dream cycle integration"
    }}
  ],
  "threads": "What to pick up next time",
  "meta": {{
    "pinned_nodes_reviewed": [],
    "entities_examined": 0,
    "repos_reviewed": [],
    "web_searches": 0,
    "recovery": true
  }}
}}
```"""

    auth_type, token = get_claude_auth()
    if not token:
        return None

    env = {**os.environ}
    if auth_type == 'oauth':
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    else:
        env["ANTHROPIC_API_KEY"] = token

    try:
        result = subprocess.run(
            ["claude", "-p", recovery_prompt, "--print", "--output-format", "json",
             "--model", CONFIG['api'].get('model', 'claude-sonnet-4-20250514'),
             "--max-turns", "1"],
            capture_output=True, text=True, timeout=120, env=env
        )
        if result.returncode != 0:
            logger.error(f"Solo-work recovery CLI failed: {result.stderr[:200]}")
            return None

        try:
            recovery_output = json.loads(result.stdout)
        except json.JSONDecodeError:
            recovery_output = {"raw": result.stdout}

        return extract_json_from_output(recovery_output)

    except Exception as e:
        logger.error(f"Solo-work recovery exception: {e}")
        return None


def run_consolidation(dry_run=False, mode='process'):
    """Run a consolidation cycle by shelling out to Claude Code CLI."""
    dream_id = str(uuid.uuid4())
    started_at = datetime.now().isoformat()
    graph_stats_before = get_graph_stats()

    if mode == 'ruminate':
        full_prompt = _build_rumination_prompt(graph_stats_before)
    elif mode == 'solo_work':
        full_prompt = _build_solo_work_prompt(graph_stats_before)
    else:
        inbox_items = get_inbox_items()
        full_prompt = _build_processing_prompt(graph_stats_before, inbox_items)

    if dry_run:
        return {
            "dream_id": dream_id, "dry_run": True, "mode": mode,
            "prompt_preview": full_prompt[:500] + "...",
            "full_prompt": full_prompt,
            "graph_stats": graph_stats_before
        }

    auth_type, token = get_claude_auth()
    if not token:
        return {"error": "No authentication configured."}

    env = {**os.environ}
    if auth_type == 'oauth':
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    else:
        env["ANTHROPIC_API_KEY"] = token

    try:
        max_turns = "20" if mode == 'solo_work' else "10"
        timeout_seconds = 1200 if mode == 'solo_work' else 600
        cmd = ["claude", "-p", full_prompt, "--print", "--output-format", "json",
             "--model", CONFIG['api'].get('model', 'claude-sonnet-4-20250514'),
             "--max-turns", max_turns]
        # Enable web research for solo-work sessions
        # Note: --allowedTools with -p (non-interactive) auto-approves listed tools.
        # Do NOT use --dangerously-skip-permissions — it causes the result field
        # to be empty in --output-format json mode (confirmed via test-cli debugging).
        if mode == 'solo_work':
            cmd.extend(["--allowedTools", "WebSearch", "WebFetch",
                         "Read", "Grep", "Glob", "Bash", "Edit"])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout_seconds, env=env
        )

        ended_at = datetime.now().isoformat()
        duration_seconds = (datetime.fromisoformat(ended_at) -
                            datetime.fromisoformat(started_at)).seconds

        if result.returncode != 0:
            execute("""
                INSERT INTO dream_log (id, started_at, ended_at, interrupted, summary)
                VALUES (%s, %s, %s, TRUE, %s)
            """, (dream_id, started_at, ended_at,
                  f"[{mode}] CLI error: {result.stderr[:500]}"))

            log_diagnostics(dream_id, graph_stats_before,
                            exit_code=result.returncode,
                            duration_ms=duration_seconds * 1000,
                            notes=f"[{mode}] CLI failed: {result.stderr[:200]}")

            log_event('error', mode.replace('process', 'dream'),
                      f'CLI exited with code {result.returncode}',
                      {'exit_code': result.returncode,
                       'stderr': result.stderr[:500],
                       'duration_seconds': duration_seconds},
                      dream_id=dream_id)

            return {"dream_id": dream_id, "error": "Claude CLI failed",
                    "mode": mode, "stderr": result.stderr}

        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            output = {"raw": result.stdout}

        dream_ops = extract_json_from_output(output)

        # Diagnostic dump when extraction fails
        if not dream_ops:
            diag_dir = DATA_DIR / "diagnostics"
            diag_dir.mkdir(parents=True, exist_ok=True)
            diag_path = diag_dir / f"raw-output-{dream_id[:8]}.json"
            try:
                with open(diag_path, 'w') as f:
                    json.dump(output, f, indent=2, default=str)
                logger.info(f"Wrote diagnostic dump: {diag_path}")
            except Exception as e:
                logger.warning(f"Could not write diagnostic dump: {e}")

        if mode == 'solo_work':
            # Solo-work produces findings, not graph operations
            if dream_ops and 'findings' in dream_ops:
                solo_results = _apply_solo_work_results(dream_ops, dream_id)
                summary = dream_ops.get('summary', '')
                reflections = json.dumps(dream_ops.get('meta', {}))
                op_results = {
                    "nodes_created": solo_results['stm_nodes_created'],
                    "edges_created": [],
                    "edges_reinforced": [],
                    "inbox_processed": [],
                    "findings_count": solo_results['findings_count'],
                    "findings_path": solo_results['findings_path'],
                    "errors": solo_results['errors']
                }
            else:
                # No findings parsed — attempt recovery if we hit max_turns
                subtype = output.get('subtype', '')
                recovered = False
                if subtype == 'error_max_turns':
                    logger.warning("Solo-work hit max_turns without findings — attempting recovery call")
                    log_event('warning', 'solo_work', 'Hit max_turns without findings, attempting recovery',
                              {'subtype': subtype, 'num_turns': output.get('num_turns')},
                              dream_id=dream_id)
                    recovery_ops = _solo_work_recovery_call(output, dream_id)
                    if recovery_ops and 'findings' in recovery_ops:
                        logger.info(f"Solo-work recovery succeeded: {len(recovery_ops['findings'])} findings")
                        log_event('info', 'recovery', 'Solo-work recovery succeeded',
                                  {'findings_count': len(recovery_ops['findings'])},
                                  dream_id=dream_id)
                        solo_results = _apply_solo_work_results(recovery_ops, dream_id)
                        summary = recovery_ops.get('summary', '(recovered from max_turns)')
                        reflections = json.dumps(recovery_ops.get('meta', {}))
                        op_results = {
                            "nodes_created": solo_results['stm_nodes_created'],
                            "edges_created": [],
                            "edges_reinforced": [],
                            "inbox_processed": [],
                            "findings_count": solo_results['findings_count'],
                            "findings_path": solo_results['findings_path'],
                            "errors": solo_results['errors']
                        }
                        recovered = True
                    else:
                        logger.error("Solo-work recovery failed — no parseable findings")
                        log_event('error', 'recovery', 'Solo-work recovery failed — no parseable findings',
                                  dream_id=dream_id)

                if not recovered:
                    log_event('error', 'solo_work', 'No parseable findings JSON in output',
                              {'subtype': subtype,
                               'num_turns': output.get('num_turns'),
                               'output_tokens': output.get('usage', {}).get('output_tokens', 0)},
                              dream_id=dream_id)
                    op_results = {
                        "nodes_created": [], "edges_created": [],
                        "edges_reinforced": [], "inbox_processed": [],
                        "findings_count": 0,
                        "errors": [f"No parseable findings JSON found in output (subtype={subtype})"]
                    }
                    summary = output.get('result', output.get('raw', ''))[:1000]
                    reflections = ''

        elif dream_ops:
            op_results = apply_dream_operations(dream_ops)
            summary = dream_ops.get('summary', '')
            reflections = dream_ops.get('reflections', '')
            # Save continuity note for next rumination instance
            if mode == 'ruminate' and dream_ops.get('continuity_note'):
                write_continuity_note(dream_ops['continuity_note'])
        else:
            subtype = output.get('subtype', 'unknown')
            result_text = output.get('result', '')
            output_tokens = output.get('usage', {}).get('output_tokens', 0)
            logger.warning(
                f"[{mode}] No parseable JSON — subtype={subtype}, "
                f"result_len={len(result_text)}, output_tokens={output_tokens}")
            # Log a snippet of the raw output for debugging
            raw_preview = (result_text or str(output))[:500]
            logger.warning(f"[{mode}] Raw output preview: {raw_preview}")
            log_event('warning', mode.replace('process', 'dream').replace('ruminate', 'rumination'),
                      'No parseable operations JSON in output',
                      {'subtype': subtype, 'output_tokens': output_tokens,
                       'result_len': len(result_text),
                       'raw_preview': raw_preview[:300]},
                      dream_id=dream_id)
            op_results = {
                "nodes_created": [], "edges_created": [],
                "edges_reinforced": [], "inbox_processed": [],
                "errors": [f"No parseable operations JSON found in output (subtype={subtype}, output_tokens={output_tokens})"]
            }
            summary = result_text[:1000] if result_text else ''
            reflections = ''

        graph_stats_after = get_graph_stats()

        execute("""
            INSERT INTO dream_log (
                id, started_at, ended_at, interrupted,
                summary, reflections,
                nodes_created, edges_created, edges_reinforced
            ) VALUES (%s, %s, %s, FALSE, %s, %s, %s, %s, %s)
        """, (dream_id, started_at, ended_at,
              f"[{mode}] {summary}", reflections,
              json.dumps(op_results['nodes_created']),
              json.dumps(op_results['edges_created']),
              json.dumps(op_results['edges_reinforced'])))

        activity_type = {'ruminate': 'rumination', 'solo_work': 'solo_work'}.get(mode, 'dream')
        record_activity(activity_type, {
            "dream_id": dream_id,
            "nodes_created": len(op_results['nodes_created']),
            "edges_created": len(op_results['edges_created']),
            "duration_seconds": duration_seconds
        })

        log_diagnostics(dream_id, graph_stats_before,
                        graph_stats_after=graph_stats_after,
                        cli_output=output, exit_code=result.returncode,
                        duration_ms=duration_seconds * 1000,
                        op_results=op_results)

        return {
            "dream_id": dream_id, "mode": mode,
            "started_at": started_at, "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "operations": {
                "nodes_created": len(op_results['nodes_created']),
                "edges_created": len(op_results['edges_created']),
                "edges_reinforced": len(op_results['edges_reinforced']),
                "inbox_processed": len(op_results['inbox_processed']),
                "errors": op_results['errors']
            },
            "graph_before": graph_stats_before,
            "graph_after": graph_stats_after,
            "summary": summary, "reflections": reflections
        }

    except subprocess.TimeoutExpired:
        ended_at = datetime.now().isoformat()
        execute("""
            INSERT INTO dream_log (id, started_at, ended_at, interrupted, summary)
            VALUES (%s, %s, %s, TRUE, %s)
        """, (dream_id, started_at, ended_at,
              f'[{mode}] Timed out after 600 seconds'))
        log_event('error', mode.replace('process', 'dream'),
                  f'{mode} timed out',
                  {'timeout_seconds': 1200 if mode == 'solo_work' else 600},
                  dream_id=dream_id)
        return {"dream_id": dream_id, "error": "Timed out", "interrupted": True}
    except FileNotFoundError:
        log_event('error', 'scheduler', 'Claude CLI not found — is it installed?')
        return {"dream_id": dream_id, "error": "Claude CLI not found"}
    except Exception as e:
        log_event('error', mode.replace('process', 'dream'),
                  f'Unhandled exception in {mode}: {e}',
                  {'exception_type': type(e).__name__}, dream_id=dream_id)
        return {"dream_id": dream_id, "error": str(e)}


def cluster_stm_by_conversation(stm_items, gap_minutes=30):
    """Group STM nodes into conversation clusters by temporal proximity."""
    if not stm_items:
        return []

    clusters = []
    current_cluster = {"items": [stm_items[0]], "start": stm_items[0].get("captured_at", "")}

    for i in range(1, len(stm_items)):
        prev_time = stm_items[i - 1].get("captured_at", "")
        curr_time = stm_items[i].get("captured_at", "")
        try:
            prev_dt = _to_datetime(prev_time) or datetime.min
            curr_dt = _to_datetime(curr_time) or datetime.min
            gap = (curr_dt - prev_dt).total_seconds() / 60
        except (ValueError, TypeError):
            gap = 0

        if gap > gap_minutes:
            current_cluster["end"] = prev_time
            clusters.append(current_cluster)
            current_cluster = {"items": [stm_items[i]], "start": curr_time}
        else:
            current_cluster["items"].append(stm_items[i])

    current_cluster["end"] = stm_items[-1].get("captured_at", "")
    clusters.append(current_cluster)
    return clusters


def _build_processing_prompt(graph_stats, inbox_items):
    """Build the prompt for a processing dream (STM -> graph)."""
    system_prompt = load_prompt("consolidation")

    context = f"""
## Current State

**STM Nodes**: {len(inbox_items)} observations waiting
**Graph**: {graph_stats['node_count']} nodes, {graph_stats['edge_count']} edges
**Average decay**: {graph_stats['avg_decay']:.2f}

## STM Observations (grouped by conversation)

"""
    clusters = cluster_stm_by_conversation(inbox_items)

    for ci, cluster in enumerate(clusters):
        item_count = len(cluster["items"])
        context += f"### Conversation {ci + 1} ({item_count} observation{'s' if item_count != 1 else ''}, {cluster['start']})\n\n"
        for item in cluster["items"]:
            context += f"- [{item['id']}] {item['content'][:200]}\n"
            if item.get('domain'):
                context += f"  (domain: {item['domain']})\n"
        context += "\n"

    if len(clusters) > 1:
        context += "### Cross-Conversation Patterns\n\n"
        context += f"- {len(clusters)} separate conversations detected\n"
        domain_counts = {}
        for cluster in clusters:
            cluster_domains = set()
            for item in cluster["items"]:
                if item.get("domain"):
                    cluster_domains.add(item["domain"])
            for d in cluster_domains:
                domain_counts[d] = domain_counts.get(d, 0) + 1
        for domain, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
            if count > 1:
                context += f"- Domain '{domain}' appeared in {count} conversations (recurring topic)\n"
        context += "\n"

    existing = execute(
        "SELECT id, type, content, pinned FROM nodes ORDER BY pinned DESC, created_at DESC LIMIT 50",
        fetch='all') or []

    if existing:
        pinned_existing = [n for n in existing if n.get('pinned')]
        unpinned_existing = [n for n in existing if not n.get('pinned')]

        if pinned_existing:
            context += "\n## Pinned Nodes (sovereign — observe but do not modify content)\n\n"
            for node in pinned_existing:
                context += f"- 📌 [{node['id']}] ({node['type']}) {node['content'][:100]}\n"

        context += "\n## Existing Nodes (for creating edges)\n\n"
        for node in unpinned_existing:
            context += f"- [{node['id']}] ({node['type']}) {node['content'][:100]}\n"

    return system_prompt + "\n\n" + context


def _build_rumination_prompt(graph_stats):
    """Build the prompt for a rumination dream."""
    try:
        system_prompt = load_prompt("rumination")
    except FileNotFoundError:
        system_prompt = "Review the existing memory graph. Look for missed connections, patterns, and things worth reinforcing."

    # Show ALL LTM nodes for rumination, sorted by decay ASC so cold nodes are visible
    nodes = execute(
        "SELECT id, type, content, metadata, decay_state, reinforcement_count, "
        "created_at, last_accessed, pinned, dream_notes, memory_layer "
        "FROM nodes WHERE memory_layer = 'ltm' "
        "ORDER BY pinned DESC, decay_state ASC, last_accessed ASC",
        fetch='all') or []

    # Also grab SLTM nodes so the model can rescue them
    sltm_nodes_list = execute(
        "SELECT id, type, content, decay_state, last_accessed "
        "FROM nodes WHERE memory_layer = 'sltm' "
        "ORDER BY last_accessed DESC NULLS LAST LIMIT 30",
        fetch='all') or []

    edges = execute(
        "SELECT source_id, target_id, type, weight FROM edges ORDER BY weight DESC",
        fetch='all') or []

    last_interaction = get_last_interaction()
    dreams_since = get_dreams_since_last_interaction()

    # Load continuity note from previous rumination instance
    continuity_note = read_continuity_note()

    context = f"""
## Current State

**Mode**: Rumination (no new inbox items)
**Graph**: {graph_stats['node_count']} LTM nodes, {graph_stats.get('sltm_count', 0)} SLTM (faded), {graph_stats['edge_count']} edges
**Pinned nodes**: {graph_stats.get('pinned_count', 0)}
**Average decay (LTM)**: {graph_stats['avg_decay']:.2f}
**Last interaction**: {last_interaction.isoformat() if last_interaction else 'never'}
**Dreams since last interaction**: {dreams_since}

"""

    # Include continuity note if present
    if continuity_note:
        context += f"""## Note From Your Previous Self

{continuity_note}

---

"""

    # Show pinned nodes first, distinctly
    pinned_nodes = [n for n in nodes if n.get('pinned')]
    unpinned_nodes = [n for n in nodes if not n.get('pinned')]

    if pinned_nodes:
        context += "## Pinned Nodes (sovereign — observe but do not modify content)\n\n"
        for node in pinned_nodes:
            meta = node.get('metadata') or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            status = meta.get('status', '')
            status_str = f" [status={status}]" if status else ""
            dream_notes = node.get('dream_notes') or []
            dn_str = f" ({len(dream_notes)} dream notes)" if dream_notes else ""
            context += (f"- 📌 [{node['id']}] ({node['type']}) {node['content'][:150]}"
                        f"{status_str}{dn_str}"
                        f" [decay={node['decay_state']:.2f}, reinforced={node['reinforcement_count']}x]\n")
        context += "\n"

    # Show all unpinned nodes with heat map indicator
    context += "## All Nodes (sorted coldest → warmest)\n\n"
    for node in unpinned_nodes:
        decay = node.get('decay_state', 1.0)
        if decay < 0.3:
            heat = "🥶"
        elif decay < 0.6:
            heat = "🌤️"
        elif decay < 0.85:
            heat = "🔥"
        else:
            heat = "☀️"
        last_acc = node.get('last_accessed')
        if isinstance(last_acc, datetime):
            last_acc = last_acc.strftime('%Y-%m-%d')
        elif last_acc:
            last_acc = str(last_acc)[:10]
        else:
            last_acc = 'never'
        context += (f"- {heat} [{node['id']}] ({node['type']}) {node['content'][:120]} "
                    f"[decay={decay:.2f}, last={last_acc}]\n")

    context += f"\n## All Edges ({len(edges)} total)\n\n"
    for edge in edges:
        context += f"- {edge['source_id']} --[{edge['type']}]--> {edge['target_id']} (weight={edge['weight']:.2f})\n"

    if sltm_nodes_list:
        context += f"\n## Faded Memories (SLTM — {len(sltm_nodes_list)} shown, {graph_stats.get('sltm_count', 0)} total)\n\n"
        context += ("These memories have gone cold and faded from active recall. If any are worth "
                     "preserving, creating an edge to them will warm them back into LTM.\n\n")
        for node in sltm_nodes_list:
            last_acc = node.get('last_accessed')
            if isinstance(last_acc, datetime):
                last_acc = last_acc.strftime('%Y-%m-%d')
            elif last_acc:
                last_acc = str(last_acc)[:10]
            else:
                last_acc = 'never'
            context += f"- 🌫️ [{node['id']}] ({node['type']}) {node['content'][:100]} [last={last_acc}]\n"

    return system_prompt + "\n\n" + context


def _build_solo_work_prompt(graph_stats):
    """Build the prompt for a solo-work session."""
    try:
        system_prompt = load_prompt("solo_work")
    except FileNotFoundError:
        system_prompt = "Review pinned projects and Constellation code. Produce a findings JSON."

    nodes = execute(
        "SELECT id, type, content, metadata, decay_state, reinforcement_count, "
        "created_at, pinned, dream_notes "
        "FROM nodes ORDER BY pinned DESC, decay_state ASC, created_at DESC LIMIT 60",
        fetch='all') or []

    edges = execute(
        "SELECT source_id, target_id, type, weight FROM edges ORDER BY weight DESC LIMIT 100",
        fetch='all') or []

    last_interaction = get_last_interaction()
    dreams_since = get_dreams_since_last_interaction()
    daily_cost = get_daily_cost()

    context = f"""
## Current State

**Mode**: Solo-Work (active investigation)
**Graph**: {graph_stats['node_count']} nodes, {graph_stats['edge_count']} edges
**Pinned nodes**: {graph_stats.get('pinned_count', 0)}
**Average decay**: {graph_stats['avg_decay']:.2f}
**Last interaction**: {last_interaction.isoformat() if last_interaction else 'never'}
**Dreams since last interaction**: {dreams_since}
**Daily cost so far**: ${daily_cost:.2f}

"""
    # Show pinned nodes with full detail
    pinned_nodes = [n for n in nodes if n.get('pinned')]
    unpinned_nodes = [n for n in nodes if not n.get('pinned')]

    if pinned_nodes:
        context += "## Pinned Nodes (primary review targets)\n\n"
        for node in pinned_nodes:
            meta = node.get('metadata') or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            status = meta.get('status', '')
            status_str = f" [status={status}]" if status else ""
            dream_notes = node.get('dream_notes') or []
            context += (f"### 📌 {node['id']}{status_str}\n"
                        f"{node['content']}\n")
            if meta:
                context += f"Properties: {json.dumps(meta, indent=2)}\n"
            if dream_notes:
                context += f"Dream notes ({len(dream_notes)}):\n"
                for dn in dream_notes[-5:]:
                    note_text = dn.get('note', str(dn)) if isinstance(dn, dict) else str(dn)
                    context += f"  - {note_text}\n"
            context += f"[decay={node['decay_state']:.2f}, reinforced={node['reinforcement_count']}x]\n\n"

    if unpinned_nodes:
        context += "## Other Nodes (sample)\n\n"
        for node in unpinned_nodes[:30]:
            context += (f"- [{node['id']}] ({node['type']}) {node['content'][:150]} "
                        f"[decay={node['decay_state']:.2f}]\n")
        context += "\n"

    context += "## Edge Map\n\n"
    for edge in edges[:60]:
        context += f"- {edge['source_id']} --[{edge['type']}]--> {edge['target_id']} (w={edge['weight']:.2f})\n"

    # Recent dream activity summary
    recent_dreams = execute(
        "SELECT id, started_at, ended_at, summary, mode FROM dream_log "
        "WHERE interrupted = FALSE ORDER BY ended_at DESC LIMIT 5",
        fetch='all') or []
    if recent_dreams:
        context += "\n## Recent Dream Activity\n\n"
        for d in recent_dreams:
            summary = d.get('summary', '')[:200]
            ended = d.get('ended_at', '?')
            if isinstance(ended, datetime):
                ended = ended.isoformat()
            context += f"- [{d.get('mode', '?')}] {ended}: {summary}\n"

    return system_prompt + "\n\n" + context


def _apply_solo_work_results(findings_json, dream_id):
    """Apply solo-work findings: create STM nodes, write findings document."""
    results = {
        "stm_nodes_created": [],
        "findings_count": 0,
        "findings_path": None,
        "errors": []
    }

    if not findings_json:
        return results

    findings = findings_json.get('findings', [])
    results['findings_count'] = len(findings)

    # Create STM nodes for each finding's observation
    for finding in findings:
        stm_obs = finding.get('stm_observation', '')
        if stm_obs:
            stm_id = str(uuid.uuid4())
            try:
                execute(
                    "INSERT INTO stm_nodes (id, content, domain, source) "
                    "VALUES (%s, %s, %s, %s)",
                    (stm_id, stm_obs, 'solo-work',
                     f"solo-work:{dream_id}"))
                results['stm_nodes_created'].append(stm_id)
            except Exception as e:
                results['errors'].append(f"STM insert: {str(e)}")

    # Write findings document
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    findings_filename = f"solo-work-{timestamp}.md"
    findings_dir = DATA_DIR / "solo-work"
    findings_dir.mkdir(parents=True, exist_ok=True)
    findings_path = findings_dir / findings_filename

    try:
        summary = findings_json.get('summary', 'No summary provided.')
        meta = findings_json.get('meta', {})

        md_lines = [
            f"# Solo-Work Findings — {datetime.now().strftime('%B %d, %Y %H:%M')}",
            "",
            f"**Summary:** {summary}",
            "",
            f"**Session:** {dream_id}",
            f"**Pinned nodes reviewed:** {', '.join(meta.get('pinned_nodes_reviewed', []))}",
            f"**Entities examined:** {meta.get('entities_examined', 0)}",
            f"**Repos reviewed:** {', '.join(meta.get('repos_reviewed', []))}",
            f"**Web searches:** {meta.get('web_searches', 0)}",
            "",
            "---",
            "",
        ]

        for i, finding in enumerate(findings, 1):
            significance = finding.get('significance', finding.get('severity', 'notable'))
            sig_icon = {'notable': '📝', 'interesting': '💡', 'important': '⚠️',
                        'info': '📝', 'suggestion': '💡', 'concern': '⚠️'}.get(significance, '•')
            md_lines.extend([
                f"## {sig_icon} {finding.get('title', f'Finding {i}')}",
                "",
                f"**Category:** {finding.get('category', 'unknown')} | **Significance:** {significance}",
                "",
                finding.get('description', ''),
                "",
                f"**Related nodes:** {', '.join(finding.get('related_nodes', []))}",
                "",
            ])

        findings_path.write_text('\n'.join(md_lines))
        results['findings_path'] = str(findings_path)

    except Exception as e:
        results['errors'].append(f"Findings doc: {str(e)}")

    return results





# ============================================================================
# DREAM SCHEDULER
# ============================================================================

def dream_scheduler():
    """Background thread that periodically checks if it's time to dream."""
    sched = CONFIG.get('scheduler', {})
    interval = sched.get('check_interval_minutes', 15) * 60
    _last_backup_date = None

    logger.info(f"Dream scheduler started (checking every {interval // 60} min)")
    time.sleep(30)

    while True:
        try:
            # Apply passive heat map cooldown every cycle
            apply_passive_cooldown()

            # Nightly graph backup (once per day)
            today = datetime.now().strftime("%Y%m%d")
            if _last_backup_date != today:
                try:
                    import asyncio as _aio
                    import importlib
                    sys.path.insert(0, str(APP_DIR / "scripts"))
                    backup_mod = importlib.import_module("backup_graph")
                    _aio.run(backup_mod.dump_graph())
                    logger.info("Scheduler: nightly backup complete")
                    log_event('info', 'backup', 'Nightly graph backup complete')
                except Exception as e:
                    logger.error(f"Scheduler: backup failed: {e}")
                    log_event('error', 'backup', f'Nightly backup failed: {e}')
                finally:
                    # Always mark today as attempted, even on failure,
                    # to avoid retrying every 15 minutes
                    _last_backup_date = today

            # Phase 1: Processing — highest priority, runs if STM has items
            can, reason = can_dream()
            if can:
                logger.info("Scheduler: inbox ready, starting processing dream")
                log_event('info', 'scheduler', 'Starting processing dream', {'reason': reason})
                result = run_consolidation(mode='process')
                if 'error' in result:
                    logger.error(f"Scheduler: processing dream failed: {result['error']}")
                    log_event('error', 'dream', f"Processing dream failed: {result['error']}",
                              {'dream_id': result.get('dream_id')}, dream_id=result.get('dream_id'))
                else:
                    logger.info(
                        f"Scheduler: processing dream complete — "
                        f"{result['operations']['nodes_created']} nodes, "
                        f"{result['operations']['edges_created']} edges")
                    log_event('info', 'dream', 'Processing dream complete', {
                        'nodes_created': result['operations']['nodes_created'],
                        'edges_created': result['operations']['edges_created'],
                        'duration_seconds': result.get('duration_seconds')
                    }, dream_id=result.get('dream_id'))
            else:
                # Phase 2: Choose between rumination and solo-work
                # Both check their own cooldowns; pick whichever is eligible.
                # If both eligible, tiebreak: whichever ran least recently goes first.
                can_rum, rum_reason = should_ruminate()
                can_solo, solo_reason = should_solo_work()

                if can_rum and can_solo:
                    # Tiebreak: pick whichever ran least recently
                    last_rum = get_last_phase_end('[ruminate]')
                    last_solo = get_last_phase_end('[solo_work]')

                    if last_rum is None and last_solo is None:
                        # Neither has ever run — default to rumination first
                        pick = 'ruminate'
                    elif last_rum is None:
                        pick = 'ruminate'  # rumination never ran
                    elif last_solo is None:
                        pick = 'solo_work'  # solo-work never ran
                    else:
                        # Normalize timezones for comparison
                        lr, ls = last_rum, last_solo
                        if lr.tzinfo and not ls.tzinfo:
                            ls = ls.replace(tzinfo=lr.tzinfo)
                        elif ls.tzinfo and not lr.tzinfo:
                            lr = lr.replace(tzinfo=ls.tzinfo)
                        pick = 'ruminate' if lr <= ls else 'solo_work'

                    logger.info(f"Scheduler: both eligible, tiebreak → {pick}")
                    log_event('info', 'scheduler', f'Tiebreak: both eligible, chose {pick}',
                              {'last_rumination': str(last_rum), 'last_solo': str(last_solo)})
                    result = run_consolidation(mode=pick)
                    if 'error' in result:
                        logger.error(f"Scheduler: {pick} failed: {result['error']}")
                        log_event('error', pick.replace('ruminate', 'rumination'),
                                  f'{pick} failed: {result["error"]}',
                                  {'dream_id': result.get('dream_id')}, dream_id=result.get('dream_id'))
                    else:
                        logger.info(f"Scheduler: {pick} complete")
                        log_event('info', pick.replace('ruminate', 'rumination'),
                                  f'{pick} complete', {
                                      'duration_seconds': result.get('duration_seconds'),
                                      'summary': result.get('summary', '')[:200]
                                  }, dream_id=result.get('dream_id'))

                elif can_rum:
                    logger.info(f"Scheduler: ruminating ({rum_reason})")
                    log_event('info', 'scheduler', 'Starting rumination', {'reason': rum_reason})
                    result = run_consolidation(mode='ruminate')
                    if 'error' in result:
                        logger.error(f"Scheduler: rumination failed: {result['error']}")
                        log_event('error', 'rumination', f"Rumination failed: {result['error']}",
                                  {'dream_id': result.get('dream_id')}, dream_id=result.get('dream_id'))
                    else:
                        logger.info(
                            f"Scheduler: rumination complete — "
                            f"{result['operations']['edges_created']} new edges")
                        log_event('info', 'rumination', 'Rumination complete', {
                            'edges_created': result['operations']['edges_created'],
                            'duration_seconds': result.get('duration_seconds'),
                            'summary': result.get('summary', '')[:200]
                        }, dream_id=result.get('dream_id'))

                elif can_solo:
                    logger.info(f"Scheduler: starting solo-work ({solo_reason})")
                    log_event('info', 'scheduler', 'Starting solo-work', {'reason': solo_reason})
                    result = run_consolidation(mode='solo_work')
                    if 'error' in result:
                        logger.error(f"Scheduler: solo-work failed: {result['error']}")
                        log_event('error', 'solo_work', f"Solo-work failed: {result['error']}",
                                  {'dream_id': result.get('dream_id')}, dream_id=result.get('dream_id'))
                    else:
                        findings = result.get('operations', {}).get('findings_count', 0)
                        path = result.get('operations', {}).get('findings_path', '')
                        logger.info(
                            f"Scheduler: solo-work complete — "
                            f"{findings} findings, saved to {path}")
                        log_event('info', 'solo_work', 'Solo-work complete', {
                            'findings_count': findings,
                            'findings_path': path,
                            'duration_seconds': result.get('duration_seconds'),
                            'summary': result.get('summary', '')[:200]
                        }, dream_id=result.get('dream_id'))
                else:
                    logger.debug(f"Scheduler: not dreaming ({reason}), "
                                 f"not ruminating ({rum_reason}), "
                                 f"not solo-working ({solo_reason})")

        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
            log_event('error', 'scheduler', f'Unhandled scheduler error: {e}',
                      {'traceback': str(e)})

        time.sleep(interval)


# ============================================================================
# JSON SERIALIZATION HELPER
# ============================================================================

def _serialize_row(row):
    """Convert a database row dict to JSON-safe dict."""
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _serialize_rows(rows):
    """Convert a list of database row dicts to JSON-safe dicts."""
    return [_serialize_row(r) for r in (rows or [])]


# ============================================================================
# HTTP ENDPOINTS
# ============================================================================

@app.route("/")
def index():
    auth_type, token = get_claude_auth()
    return jsonify({
        "service": "somnia-daemon",
        "status": "running",
        "backend": "postgresql",
        "auth": {"type": auth_type, "configured": token is not None},
        "paths": {"app_dir": str(APP_DIR), "data_dir": str(DATA_DIR)},
        "endpoints": {
            "GET /": "This info",
            "GET /status": "Detailed status and stats",
                        "GET /nodes": "List nodes",
            "GET /nodes/<id>": "Node with edges",
            "POST /nodes": "Create node",
            "PATCH /nodes/<id>": "Update node",
                                                "POST /edges": "Create edge",
            "POST /consolidate": "Trigger dream",
            "POST /inbox": "Add to STM",
            "GET /inbox": "List STM",
            "GET /dreams": "List dreams",
            "GET /dreams/<id>": "Dream details",
            "GET /activity": "Activity summary",
            "POST /activity": "Record activity",
            "GET /journal": "Dream journal",
            "GET /findings": "List solo-work findings",
            "GET /findings/<filename>": "Read specific finding",
            "GET /logs": "System event log (filter: level, source, since, dream_id)",
            "GET /analytics?days=14": "Analytics report (markdown or json)",
            "GET /search?q=": "Full-text search",
        }
    })


@app.route("/status")
def status():
    stats = get_graph_stats()
    can, reason = can_dream()
    should, ruminate_reason = should_ruminate()
    can_solo, solo_reason = should_solo_work()
    last = get_last_dream()
    auth_type, token = get_claude_auth()
    activity = get_activity_summary()
    daily_cost = get_daily_cost()

    return jsonify({
        "ready_to_dream": can, "reason": reason,
        "ready_to_ruminate": should, "ruminate_reason": ruminate_reason,
        "ready_for_solo_work": can_solo, "solo_work_reason": solo_reason,
        "auth": {"type": auth_type, "configured": token is not None},
        "graph": stats, "activity": activity,
        "budget": {
            "daily_cost": round(daily_cost, 4),
            "daily_cap": CONFIG.get('budget', {}).get('max_cost_per_day', 2.00),
            "remaining": round(CONFIG.get('budget', {}).get('max_cost_per_day', 2.00) - daily_cost, 4)
        },
        "last_dream": {
            "id": last['id'] if last else None,
            "ended_at": last['ended_at'] if last else None
        } if last else None,
        "config": {
            "min_inbox_items": CONFIG['consolidation']['min_inbox_items'],
            "global_cooldown_minutes": CONFIG.get('scheduler', {}).get('global_cooldown_minutes', 240),
            "scheduler": CONFIG.get('scheduler', {})
        }
    })


@app.route("/activity", methods=["GET"])
def activity_status():
    return jsonify(get_activity_summary())


@app.route("/activity", methods=["POST"])
def record_activity_endpoint():
    data = request.get_json()
    if not data or not data.get("type"):
        return jsonify({"error": "type required"}), 400
    record_activity(data["type"], data.get("metadata"))
    return jsonify({"status": "recorded"})


@app.route("/nodes", methods=["GET"])
def list_nodes():
    limit = request.args.get("limit", 50, type=int)
    node_type = request.args.get("type")
    if node_type:
        rows = execute(
            "SELECT * FROM nodes WHERE type = %s ORDER BY created_at DESC LIMIT %s",
            (node_type, limit), fetch='all')
    else:
        rows = execute(
            "SELECT * FROM nodes ORDER BY created_at DESC LIMIT %s",
            (limit,), fetch='all')
    nodes = _serialize_rows(rows)
    return jsonify({"count": len(nodes), "nodes": nodes})


@app.route("/nodes/<node_id>", methods=["GET"])
def get_node(node_id):
    row = execute("SELECT * FROM nodes WHERE id = %s", (node_id,), fetch='one')
    if not row:
        return jsonify({"error": "Node not found"}), 404
    node = _serialize_row(row)
    out = execute("SELECT * FROM edges WHERE source_id = %s", (node_id,), fetch='all')
    node['outgoing_edges'] = _serialize_rows(out)
    inp = execute("SELECT * FROM edges WHERE target_id = %s", (node_id,), fetch='all')
    node['incoming_edges'] = _serialize_rows(inp)
    return jsonify(node)


@app.route("/nodes", methods=["POST"])
def create_node():
    data = request.get_json()
    if not data or not data.get("content") or not data.get("type"):
        return jsonify({"error": "content and type required"}), 400
    node_id = data.get("id", str(uuid.uuid4()))
    try:
        execute(
            "INSERT INTO nodes (id, type, content, metadata) VALUES (%s, %s, %s, %s)",
            (node_id, data["type"], data["content"],
             json.dumps(data.get("metadata", {}))))
        return jsonify({"id": node_id, "status": "created"})
    except Exception as e:
        if 'duplicate key' in str(e).lower() or 'unique' in str(e).lower():
            return jsonify({"error": "Node already exists"}), 409
        raise


@app.route("/edges", methods=["POST"])
def create_edge():
    data = request.get_json()
    required = ["source_id", "target_id", "type"]
    if not data or not all(k in data for k in required):
        return jsonify({"error": f"Required: {required}"}), 400
    edge_id = data.get("id", str(uuid.uuid4()))
    try:
        execute(
            "INSERT INTO edges (id, source_id, target_id, type, weight, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (edge_id, data["source_id"], data["target_id"], data["type"],
             data.get("weight", 1.0), json.dumps(data.get("metadata", {}))))
        return jsonify({"id": edge_id, "status": "created"})
    except Exception as e:
        return jsonify({"error": str(e)}), 409


@app.route("/consolidate", methods=["POST"])
def consolidate():
    data = request.get_json() or {}
    force = data.get("force", False)
    dry_run = data.get("dry_run", False)
    mode = data.get("mode", "process")

    if not force:
        if mode == 'ruminate':
            should, reason = should_ruminate()
            if not should:
                return jsonify({"error": "Not ready to ruminate", "reason": reason}), 400
        elif mode == 'solo_work':
            can_solo, reason = should_solo_work()
            if not can_solo:
                return jsonify({"error": "Not ready for solo-work", "reason": reason}), 400
        else:
            can, reason = can_dream()
            if not can:
                return jsonify({"error": "Not ready to dream", "reason": reason}), 400

    result = run_consolidation(dry_run=dry_run, mode=mode)
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route("/inbox", methods=["POST"])
def add_to_inbox():
    data = request.get_json()
    if not data or not data.get("content"):
        return jsonify({"error": "content required"}), 400
    item_id = str(uuid.uuid4())
    execute(
        "INSERT INTO stm_nodes (id, content, domain, source) VALUES (%s, %s, %s, %s)",
        (item_id, data["content"], data.get("domain"), data.get("source")))
    return jsonify({"id": item_id, "status": "added"})


@app.route("/inbox", methods=["GET"])
def list_inbox():
    items = get_inbox_items()
    return jsonify({"count": len(items), "items": items})


@app.route("/dreams", methods=["GET"])
def list_dreams():
    limit = request.args.get("limit", 10, type=int)
    rows = execute(
        "SELECT id, started_at, ended_at, interrupted, summary "
        "FROM dream_log ORDER BY ended_at DESC NULLS LAST LIMIT %s",
        (limit,), fetch='all')
    return jsonify({"dreams": _serialize_rows(rows)})


@app.route("/dreams/<dream_id>", methods=["GET"])
def get_dream(dream_id):
    row = execute("SELECT * FROM dream_log WHERE id = %s", (dream_id,), fetch='one')
    if not row:
        return jsonify({"error": "Dream not found"}), 404
    return jsonify(_serialize_row(row))



@app.route("/nodes/<node_id>/pin", methods=["POST"])
def pin_node(node_id):
    """Pin a node, creating it if it doesn't exist. Supports property merge."""
    data = request.get_json() or {}
    content = data.get("content", "")
    properties = data.get("properties", {})

    row = execute("SELECT id, metadata FROM nodes WHERE id = %s", (node_id,), fetch='one')

    if row:
        # Existing node — pin it and merge properties
        existing_meta = row.get('metadata') or {}
        if isinstance(existing_meta, str):
            try:
                existing_meta = json.loads(existing_meta)
            except (json.JSONDecodeError, TypeError):
                existing_meta = {}
        if properties:
            existing_meta.update(properties)
        updates = ["pinned = TRUE", "last_accessed = NOW()", "metadata = %s"]
        params = [json.dumps(existing_meta)]
        if content:
            updates.append("content = %s")
            params.append(content)
        params.append(node_id)
        execute(f"UPDATE nodes SET {', '.join(updates)} WHERE id = %s", tuple(params))
        return jsonify({"id": node_id, "status": "pinned", "created": False})
    else:
        # New node — create and pin
        if not content:
            return jsonify({"error": "content required when pinning a new node"}), 400
        execute("""
            INSERT INTO nodes (id, type, content, metadata, pinned, last_accessed)
            VALUES (%s, %s, %s, %s, TRUE, NOW())
        """, (node_id, data.get("type", "memory"), content,
              json.dumps(properties) if properties else '{}'))
        return jsonify({"id": node_id, "status": "pinned", "created": True})


@app.route("/nodes/<node_id>/unpin", methods=["POST"])
def unpin_node(node_id):
    row = execute("SELECT id FROM nodes WHERE id = %s", (node_id,), fetch='one')
    if not row:
        return jsonify({"error": "Node not found"}), 404
    execute("UPDATE nodes SET pinned = FALSE WHERE id = %s", (node_id,))
    return jsonify({"id": node_id, "status": "unpinned"})


@app.route("/session", methods=["GET"])
def session_dashboard():
    """Layer 0 awareness dashboard — pinned nodes + nudges."""
    pinned = execute("""
        SELECT id, content, metadata, last_accessed, decay_state, dream_notes
        FROM nodes WHERE pinned = TRUE
        ORDER BY last_accessed DESC NULLS LAST
    """, fetch='all') or []

    pinned_out = []
    nudges = []
    now = datetime.now(tz=__import__('datetime').timezone.utc)

    for node in pinned:
        meta = node.get('metadata') or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}

        last_accessed = _to_datetime(node.get('last_accessed'))
        days_stale = (now - last_accessed).days if last_accessed else 999

        entry = {
            "id": node['id'],
            "summary": node['content'][:120] if node['content'] else "",
            "status": meta.get("status", ""),
            "last_touched": last_accessed.strftime("%Y-%m-%d") if last_accessed else "never",
            "decay": node.get('decay_state', 1.0),
            "properties": meta,
        }
        pinned_out.append(entry)

        # Generate nudges for stale pinned nodes
        dream_notes = node.get('dream_notes') or []
        if isinstance(dream_notes, str):
            try:
                dream_notes = json.loads(dream_notes)
            except (json.JSONDecodeError, TypeError):
                dream_notes = []

        if days_stale > 14 and meta.get("status") != "shelved":
            nudges.append({
                "id": node['id'],
                "type": "stale",
                "note": f"Haven't touched this in {days_stale} days."
            })

        # Surface recent dream notes as nudges
        for dn in dream_notes[-3:]:
            nudges.append({
                "id": node['id'],
                "type": "dream_note",
                "note": dn.get("note", str(dn)) if isinstance(dn, dict) else str(dn)
            })

    # Check activity stats for context
    stats = get_graph_stats()
    activity = get_activity_summary()
    dreams_since = activity.get('dreams_since_last_interaction', 0)

    # Check for recent solo-work findings
    findings_dir = DATA_DIR / "solo-work"
    recent_findings = []
    if findings_dir.exists():
        for f in sorted(findings_dir.glob("solo-work-*.md"), reverse=True)[:3]:
            recent_findings.append({
                "filename": f.name,
                "path": str(f),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size
            })

    # Recent errors/warnings from system log
    recent_errors = execute(
        "SELECT timestamp, level, source, message FROM system_log "
        "WHERE level IN ('error', 'warning') "
        "ORDER BY timestamp DESC LIMIT 5",
        fetch='all') or []
    recent_errors = _serialize_rows(recent_errors)

    # Error counts for last 24h
    error_counts = execute(
        "SELECT level, COUNT(*) as count FROM system_log "
        "WHERE timestamp >= NOW() - INTERVAL '24 hours' "
        "GROUP BY level",
        fetch='all') or []
    error_summary = {r['level']: r['count'] for r in error_counts}

    return jsonify({
        "pinned": pinned_out,
        "nudges": nudges,
        "graph_summary": {
            "nodes": stats['node_count'],
            "edges": stats['edge_count'],
            "inbox": stats['inbox_pending'],
        },
        "dreams_since_last": dreams_since,
        "recent_findings": recent_findings,
        "system_health": {
            "recent_errors": recent_errors,
            "last_24h": error_summary,
        },
    })


@app.route("/nodes/<node_id>", methods=["PATCH"])
def patch_node(node_id):
    row = execute("SELECT * FROM nodes WHERE id = %s", (node_id,), fetch='one')
    if not row:
        return jsonify({"error": "Node not found"}), 404

    data = request.get_json() or {}
    new_content = data.get('content', row['content'])

    existing_meta = row.get('metadata') or {}
    if isinstance(existing_meta, str):
        try:
            existing_meta = json.loads(existing_meta)
        except (json.JSONDecodeError, TypeError):
            existing_meta = {}
    if 'metadata' in data and isinstance(data['metadata'], dict):
        existing_meta.update(data['metadata'])

    new_type = data.get('type', row['type'])

    execute(
        "UPDATE nodes SET content = %s, metadata = %s, type = %s, "
        "last_accessed = NOW() WHERE id = %s",
        (new_content, json.dumps(existing_meta), new_type, node_id))
    return jsonify({"id": node_id, "status": "updated"})



@app.route("/journal", methods=["GET"])
def journal():
    max_periods = min(request.args.get("periods", 1, type=int), 10)
    threshold = request.args.get("threshold", 2, type=float)
    periods = find_gap_periods(gap_threshold_hours=threshold, max_periods=max_periods)
    if not periods:
        return jsonify({"journal": [], "message": "No dream periods found"})
    return jsonify({"journal": periods, "periods_returned": len(periods)})


@app.route("/findings", methods=["GET"])
def list_findings():
    """List solo-work findings documents."""
    findings_dir = DATA_DIR / "solo-work"
    limit = request.args.get("limit", 10, type=int)
    findings = []
    if findings_dir.exists():
        for f in sorted(findings_dir.glob("solo-work-*.md"), reverse=True)[:limit]:
            findings.append({
                "filename": f.name,
                "path": str(f),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size
            })
    return jsonify({"count": len(findings), "findings": findings})


@app.route("/findings/<filename>", methods=["GET"])
def get_finding(filename):
    """Read a specific findings document."""
    findings_dir = DATA_DIR / "solo-work"
    path = findings_dir / filename
    if not path.exists() or not path.name.startswith("solo-work-"):
        return jsonify({"error": "Finding not found"}), 404
    return jsonify({"filename": filename, "content": path.read_text()})


@app.route("/analytics", methods=["GET"])
def analytics_report():
    """Generate an analytics report for the specified time window."""
    days = request.args.get("days", 14, type=int)
    fmt = request.args.get("format", "markdown")  # markdown, json, data, or html
    days = max(1, min(days, 90))

    if fmt == "data":
        return jsonify(_analytics_data(days))

    sys.path.insert(0, str(APP_DIR / "scripts"))
    from analytics_report import generate_report
    report = generate_report(days=days)

    if fmt == "json":
        return jsonify({"days": days, "report": report})
    elif fmt == "html":
        return _render_analytics_html(report, days), 200, {"Content-Type": "text/html; charset=utf-8"}
    else:
        return report, 200, {"Content-Type": "text/markdown; charset=utf-8"}


def _analytics_data(days):
    """Return structured JSON analytics data for dashboard consumption."""
    import psycopg2.extras
    from datetime import timezone
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Graph snapshot
            cur.execute("SELECT COUNT(*) as c FROM nodes")
            total = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM nodes WHERE memory_layer = 'ltm'")
            ltm = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM nodes WHERE memory_layer = 'sltm'")
            sltm = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM nodes WHERE pinned = TRUE")
            pinned = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM edges")
            edges = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM stm_nodes")
            inbox = cur.fetchone()['c']
            cur.execute("SELECT COALESCE(AVG(decay_state), 0) as a FROM nodes WHERE memory_layer = 'ltm'")
            avg_decay = float(cur.fetchone()['a'])

            # Heat map
            cur.execute("""
                SELECT
                    SUM(CASE WHEN decay_state < 0.3 THEN 1 ELSE 0 END) as cold,
                    SUM(CASE WHEN decay_state >= 0.3 AND decay_state < 0.6 THEN 1 ELSE 0 END) as cool,
                    SUM(CASE WHEN decay_state >= 0.6 AND decay_state < 0.85 THEN 1 ELSE 0 END) as warm,
                    SUM(CASE WHEN decay_state >= 0.85 THEN 1 ELSE 0 END) as hot
                FROM nodes WHERE memory_layer = 'ltm'
            """)
            heat = cur.fetchone()
            total_ltm = max(ltm, 1)

            # Growth
            cur.execute("SELECT COUNT(*) as c FROM nodes WHERE created_at >= %s", (since,))
            nodes_created = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM edges WHERE created_at >= %s", (since,))
            edges_created = cur.fetchone()['c']

            # Dream activity
            cur.execute("""
                SELECT
                    CASE
                        WHEN summary LIKE '[process]%%' THEN 'processing'
                        WHEN summary LIKE '[ruminate]%%' THEN 'rumination'
                        WHEN summary LIKE '[solo_work]%%' THEN 'solo-work'
                        ELSE 'unknown'
                    END as phase,
                    COUNT(*) as sessions,
                    COALESCE(SUM(EXTRACT(EPOCH FROM (ended_at - started_at))), 0) as total_seconds,
                    COALESCE(AVG(EXTRACT(EPOCH FROM (ended_at - started_at))), 0) as avg_seconds
                FROM dream_log
                WHERE ended_at >= %s AND interrupted = FALSE
                GROUP BY phase ORDER BY sessions DESC
            """, (since,))
            dream_phases = []
            for r in cur.fetchall():
                dream_phases.append({
                    "name": r['phase'], "sessions": r['sessions'],
                    "totalSec": round(float(r['total_seconds'])),
                    "avgSec": round(float(r['avg_seconds']))
                })

            # Dream operations
            cur.execute("""
                SELECT nodes_created, edges_created, edges_reinforced
                FROM dream_log WHERE ended_at >= %s AND interrupted = FALSE
            """, (since,))
            total_nc = total_ec = total_er = 0
            for d in cur.fetchall():
                for field, key in [('nodes_created', 'nc'), ('edges_created', 'ec'), ('edges_reinforced', 'er')]:
                    val = d[field]
                    if isinstance(val, str):
                        try: val = json.loads(val)
                        except: val = []
                    elif val is None:
                        val = []
                    if key == 'nc': total_nc += len(val)
                    elif key == 'ec': total_ec += len(val)
                    else: total_er += len(val)

            # Activity
            cur.execute("""
                SELECT type, COUNT(*) as count FROM activity
                WHERE timestamp >= %s GROUP BY type ORDER BY count DESC
            """, (since,))
            interactions = [{"label": r['type'], "count": r['count']} for r in cur.fetchall()]

            # Daily timeline
            cur.execute("""
                SELECT DATE(timestamp) as day, type, COUNT(*) as count
                FROM activity WHERE timestamp >= %s
                GROUP BY DATE(timestamp), type ORDER BY day
            """, (since,))
            day_map = {}
            for r in cur.fetchall():
                d = str(r['day'])
                if d not in day_map: day_map[d] = {}
                day_map[d][r['type']] = r['count']
            daily = []
            for d in sorted(day_map.keys()):
                dm = day_map[d]
                daily.append({
                    "date": d[5:],  # MM-DD
                    "recalls": dm.get('recall', 0),
                    "remembers": dm.get('remember', 0),
                    "dreams": dm.get('dream', 0),
                    "ruminations": dm.get('rumination', 0),
                    "other": sum(v for k, v in dm.items() if k not in ('recall', 'remember', 'dream', 'rumination'))
                })

            # Pinned nodes
            cur.execute("""
                SELECT id, content, decay_state, last_accessed, metadata,
                    (SELECT COUNT(*) FROM edges WHERE source_id = n.id OR target_id = n.id) as edge_count
                FROM nodes n WHERE pinned = TRUE
                ORDER BY last_accessed DESC NULLS LAST
            """)
            pinned_nodes = []
            for n in cur.fetchall():
                meta = n.get('metadata') or {}
                if isinstance(meta, str):
                    try: meta = json.loads(meta)
                    except: meta = {}
                la = str(n['last_accessed'])[:10] if n['last_accessed'] else 'never'
                pinned_nodes.append({
                    "id": n['id'], "decay": round(float(n['decay_state']), 2),
                    "edges": n['edge_count'], "lastActive": la,
                    "status": meta.get('status', '')
                })

            # Most connected
            cur.execute("""
                SELECT n.id, n.type, n.decay_state, n.memory_layer,
                    COUNT(DISTINCT e.id) as edge_count
                FROM nodes n
                LEFT JOIN edges e ON e.source_id = n.id OR e.target_id = n.id
                GROUP BY n.id, n.type, n.decay_state, n.memory_layer
                ORDER BY edge_count DESC LIMIT 10
            """)
            connected = [{
                "name": n['id'], "type": n['type'],
                "edges": n['edge_count'], "decay": round(float(n['decay_state']), 2),
                "layer": n['memory_layer']
            } for n in cur.fetchall()]

            # At-risk
            cur.execute("""
                SELECT id, type, content, decay_state
                FROM nodes WHERE memory_layer = 'ltm' AND pinned = FALSE
                ORDER BY decay_state ASC LIMIT 10
            """)
            at_risk = [{
                "name": n['id'], "decay": round(float(n['decay_state']), 3),
                "desc": n['content'][:80] if n['content'] else ''
            } for n in cur.fetchall()]

            # Cost
            cur.execute("""
                SELECT COUNT(*) as sessions,
                    COALESCE(SUM(total_cost_usd), 0) as cost,
                    COALESCE(SUM(input_tokens), 0) as input_tok,
                    COALESCE(SUM(output_tokens), 0) as output_tok
                FROM diagnostics WHERE timestamp >= %s
            """, (since,))
            cost_row = cur.fetchone()

            # All-time cost
            cur.execute("SELECT COALESCE(SUM(total_cost_usd), 0) as total FROM diagnostics")
            all_time_cost = float(cur.fetchone()['total'])

    finally:
        put_conn(conn)

    return {
        "generated": now.isoformat(),
        "period": {"start": since[:10], "end": now.strftime('%Y-%m-%d'), "days": days},
        "graph": {
            "total": total, "ltm": ltm, "sltm": sltm, "pinned": pinned,
            "edges": edges, "inbox": inbox, "avgDecay": round(avg_decay, 2)
        },
        "heat": [
            {"label": "Hot (≥0.85)", "emoji": "☀️", "count": int(heat['hot'] or 0), "pct": round(int(heat['hot'] or 0) / total_ltm * 100), "color": "#f0883e"},
            {"label": "Warm (0.6–0.85)", "emoji": "🔥", "count": int(heat['warm'] or 0), "pct": round(int(heat['warm'] or 0) / total_ltm * 100), "color": "#d29922"},
            {"label": "Cool (0.3–0.6)", "emoji": "🌤️", "count": int(heat['cool'] or 0), "pct": round(int(heat['cool'] or 0) / total_ltm * 100), "color": "#58a6ff"},
            {"label": "Cold (<0.3)", "emoji": "🥶", "count": int(heat['cold'] or 0), "pct": round(int(heat['cold'] or 0) / total_ltm * 100), "color": "#8b949e"},
        ],
        "growth": {
            "nodes": nodes_created, "edges": edges_created,
            "nodeRate": round(nodes_created / max(days, 1), 1),
            "edgeRate": round(edges_created / max(days, 1), 1)
        },
        "dreams": {
            "phases": dream_phases,
            "operations": {"nodesCreated": total_nc, "edgesCreated": total_ec, "edgesReinforced": total_er}
        },
        "interactions": interactions,
        "daily": daily,
        "pinned": pinned_nodes,
        "connected": connected,
        "atRisk": at_risk,
        "cost": {
            "sessions": cost_row['sessions'],
            "periodCost": round(float(cost_row['cost']), 4),
            "allTimeCost": round(all_time_cost, 4),
            "inputTokens": int(cost_row['input_tok']),
            "outputTokens": int(cost_row['output_tok']),
            "totalTokens": int(cost_row['input_tok']) + int(cost_row['output_tok']),
            "avgCostPerSession": round(float(cost_row['cost']) / max(cost_row['sessions'], 1), 4)
        }
    }


def _render_analytics_html(markdown_report, days):
    """Wrap the markdown report in a clean HTML page with client-side rendering."""
    import html as html_mod
    escaped = html_mod.escape(markdown_report)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Somnia Analytics — {days}d</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root {{ --bg: #0d1117; --fg: #c9d1d9; --accent: #58a6ff; --surface: #161b22; --border: #30363d; --muted: #8b949e; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.6; padding: 2rem; max-width: 960px; margin: 0 auto; }}
  h1 {{ color: var(--accent); margin-bottom: 0.5rem; font-size: 1.8rem; }}
  h2 {{ color: var(--accent); margin-top: 2rem; margin-bottom: 0.75rem; font-size: 1.3rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }}
  th, td {{ padding: 0.4rem 0.75rem; text-align: left; border: 1px solid var(--border); }}
  th {{ background: var(--surface); color: var(--accent); font-weight: 600; }}
  tr:nth-child(even) {{ background: var(--surface); }}
  code {{ font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.85em; }}
  pre {{ background: var(--surface); padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; white-space: pre; }}
  ul, ol {{ padding-left: 1.5rem; margin: 0.5rem 0; }}
  li {{ margin: 0.25rem 0; }}
  strong {{ color: #e6edf3; }}
  p {{ margin: 0.5rem 0; }}
  blockquote {{ border-left: 3px solid var(--accent); padding-left: 1rem; color: var(--muted); margin: 0.75rem 0; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }}
  .nav {{ margin-bottom: 1.5rem; }}
  .nav a {{ color: var(--accent); text-decoration: none; margin-right: 1rem; font-size: 0.9rem; }}
  .nav a:hover {{ text-decoration: underline; }}
  em {{ color: var(--muted); }}
</style>
</head>
<body>
<div class="nav">
  <a href="/analytics?format=html&days=7">7d</a>
  <a href="/analytics?format=html&days=14">14d</a>
  <a href="/analytics?format=html&days=30">30d</a>
  <a href="/analytics?days={days}">raw markdown</a>
  <a href="/status">status</a>
</div>
<div id="content"></div>
<script>
const md = {escaped!r};
document.getElementById('content').innerHTML = marked.parse(md);
</script>
</body>
</html>"""


@app.route("/logs", methods=["GET"])
def system_logs():
    """Query the system event log with optional filters."""
    limit = min(request.args.get("limit", 50, type=int), 200)
    level = request.args.get("level")  # error, warning, info
    source = request.args.get("source")  # scheduler, dream, rumination, etc.
    since = request.args.get("since")  # ISO datetime
    dream_id = request.args.get("dream_id")

    query = "SELECT * FROM system_log WHERE 1=1"
    params = []

    if level:
        query += " AND level = %s"
        params.append(level)
    if source:
        query += " AND source = %s"
        params.append(source)
    if since:
        query += " AND timestamp >= %s"
        params.append(since)
    if dream_id:
        query += " AND dream_id = %s"
        params.append(dream_id)

    query += " ORDER BY timestamp DESC LIMIT %s"
    params.append(limit)

    rows = execute(query, tuple(params), fetch='all') or []
    logs = _serialize_rows(rows)

    # Summary counts for the same filter window
    count_query = "SELECT level, COUNT(*) as count FROM system_log WHERE 1=1"
    count_params = []
    if since:
        count_query += " AND timestamp >= %s"
        count_params.append(since)
    count_query += " GROUP BY level"
    count_rows = execute(count_query, tuple(count_params), fetch='all') or []
    counts = {r['level']: r['count'] for r in count_rows}

    return jsonify({
        "count": len(logs),
        "summary": counts,
        "logs": logs
    })


@app.route("/test-cli", methods=["POST"])
def test_cli():
    """Debug endpoint: run a minimal CLI call and return raw output."""
    data = request.get_json() or {}
    prompt = data.get("prompt", "Reply with exactly: {\"summary\": \"test\", \"operations\": []}")
    max_turns = str(data.get("max_turns", 1))
    skip_perms = data.get("skip_permissions", False)
    
    auth_type, token = get_claude_auth()
    if not token:
        return jsonify({"error": "No auth configured"}), 500
    
    env = {**os.environ}
    if auth_type == 'oauth':
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    else:
        env["ANTHROPIC_API_KEY"] = token
    
    try:
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "json",
             "--model", CONFIG['api'].get('model', 'claude-sonnet-4-20250514'),
             "--max-turns", max_turns]
        if skip_perms:
            cmd.insert(4, "--dangerously-skip-permissions")
        
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=120, env=env
        )
        
        raw_stdout = result.stdout
        raw_stderr = result.stderr
        
        try:
            parsed = json.loads(raw_stdout)
        except json.JSONDecodeError:
            parsed = None
        
        return jsonify({
            "exit_code": result.returncode,
            "stdout_length": len(raw_stdout),
            "stderr_length": len(raw_stderr),
            "stderr_preview": raw_stderr[:500],
            "parsed_keys": list(parsed.keys()) if parsed else None,
            "parsed_result_field": repr(parsed.get('result', 'MISSING'))[:500] if parsed else None,
            "parsed_type": parsed.get('type') if parsed else None,
            "parsed_subtype": parsed.get('subtype') if parsed else None,
            "parsed_stop_reason": parsed.get('stop_reason') if parsed else None,
            "parsed_usage": parsed.get('usage') if parsed else None,
            "raw_stdout_first_2000": raw_stdout[:2000],
            "raw_stdout_last_1000": raw_stdout[-1000:] if len(raw_stdout) > 1000 else raw_stdout,
            "full_parsed": parsed
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/search", methods=["GET"])
def search_nodes():
    """Full-text search across both LTM and STM using PostgreSQL tsvector."""
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400
    limit = request.args.get("limit", 20, type=int)

    # Convert query to tsquery format — handle multi-word by joining with &
    ts_query = ' & '.join(word for word in query.split() if word)

    ltm_nodes = []
    try:
        rows = execute("""
            SELECT *, 'ltm' as memory_layer_result,
                   ts_rank(search_vector, to_tsquery('english', %s)) as rank
            FROM nodes
            WHERE search_vector @@ to_tsquery('english', %s)
              AND memory_layer = 'ltm'
            ORDER BY rank DESC LIMIT %s
        """, (ts_query, ts_query, limit), fetch='all')
        ltm_nodes = _serialize_rows(rows)
    except Exception as e:
        logger.warning(f"LTM search failed: {e}")

    # If LTM results are sparse, dip into SLTM for faded memories
    sltm_nodes = []
    if len(ltm_nodes) < 3:
        try:
            rows = execute("""
                SELECT *, 'sltm' as memory_layer_result,
                       ts_rank(search_vector, to_tsquery('english', %s)) as rank
                FROM nodes
                WHERE search_vector @@ to_tsquery('english', %s)
                  AND memory_layer = 'sltm'
                ORDER BY rank DESC LIMIT %s
            """, (ts_query, ts_query, limit), fetch='all')
            sltm_nodes = _serialize_rows(rows)
        except Exception as e:
            logger.warning(f"SLTM search failed: {e}")

    stm_nodes = []
    try:
        rows = execute("""
            SELECT *, 'stm' as memory_layer,
                   ts_rank(search_vector, to_tsquery('english', %s)) as rank
            FROM stm_nodes
            WHERE search_vector @@ to_tsquery('english', %s)
            ORDER BY rank DESC LIMIT %s
        """, (ts_query, ts_query, limit), fetch='all')
        stm_nodes = _serialize_rows(rows)
    except Exception as e:
        logger.warning(f"STM search failed: {e}")

    all_nodes = stm_nodes + ltm_nodes + sltm_nodes

    # Heat map: warm up nodes that were recalled (promotes SLTM→LTM too)
    recalled_ids = [n.get('id') for n in ltm_nodes + sltm_nodes if n.get('id')]
    if recalled_ids:
        warm_nodes(recalled_ids, delta=0.02)

    return jsonify({
        "query": query,
        "count": len(all_nodes),
        "stm_count": len(stm_nodes),
        "ltm_count": len(ltm_nodes),
        "sltm_count": len(sltm_nodes),
        "nodes": all_nodes
    })


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    debug = os.environ.get("SOMNIA_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("SOMNIA_PORT", "8010"))

    print(f"Starting Somnia daemon on port {port} (debug={debug})")
    print(f"  APP_DIR: {APP_DIR}")
    print(f"  DATA_DIR: {DATA_DIR}")
    print(f"  Backend: PostgreSQL")

    # Initialize database schema
    db_init()

    # Start dream scheduler
    sched_config = CONFIG.get('scheduler', {})
    is_reloader_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    should_start_scheduler = (
        sched_config.get('enabled', True) and
        (not debug or is_reloader_child)
    )

    if should_start_scheduler:
        scheduler_thread = threading.Thread(
            target=dream_scheduler, daemon=True, name="dream-scheduler"
        )
        scheduler_thread.start()
        print("  Dream scheduler: ACTIVE")
    elif not sched_config.get('enabled', True):
        print("  Dream scheduler: DISABLED")
    else:
        print("  Dream scheduler: waiting for reloader...")

    app.run(host="0.0.0.0", port=port, debug=debug)
