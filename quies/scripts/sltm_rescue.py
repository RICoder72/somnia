#!/usr/bin/env python3
"""
SLTM Rescue Pass — sltm_rescue.py

One-time script to retroactively apply pg_009 decay floors to nodes that
were already in SLTM before the reform landed. pg_009 protects nodes going
forward; this script repairs the historical damage.

Logic mirrors apply_passive_cooldown() exactly:

    effective_floor = max(
        pinned_floor       (if pinned),
        foundational_floor (if foundational),
        type_profile.floor (per node type),
        connectivity_floor (edge_count * per_edge, capped at max),
        reinforcement_floor (if reinforced >= stable_count)
    )

    If effective_floor > current decay_state:
        SET decay_state = effective_floor, memory_layer = 'ltm'

Usage:
    python sltm_rescue.py              # dry-run (default, safe)
    python sltm_rescue.py --apply      # commit the rescue
    python sltm_rescue.py --verbose    # dry-run with per-node detail
    python sltm_rescue.py --apply --verbose

Requires: SOMNIA_DATABASE_URL env var (same as daemon).
"""

import argparse
import os
import sys
import yaml
import psycopg2
import psycopg2.extras
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).resolve().parent.parent / "daemon" / "config.yaml"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    db_url = os.environ.get("SOMNIA_DATABASE_URL")
    if not db_url:
        print("ERROR: SOMNIA_DATABASE_URL not set.", file=sys.stderr)
        sys.exit(1)
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


# ── Floor computation (mirrors apply_passive_cooldown) ────────────────────────

def compute_effective_floor(node, decay_cfg, type_profiles):
    """Return the effective decay floor for a node given its attributes."""
    pinned_floor       = decay_cfg.get('pinned_floor', 0.5)
    foundational_floor = decay_cfg.get('foundational_floor', 0.35)
    reinf_floor        = decay_cfg.get('reinforcement_floor', 0.20)
    stable_count       = decay_cfg.get('stable_reinforcement_count', 5)
    conn_floor_per_edge = decay_cfg.get('connectivity_floor_per_edge', 0.01)
    conn_floor_max      = decay_cfg.get('connectivity_floor_max', 0.30)

    ntype        = node['type']
    is_pinned    = node['pinned']
    is_foundational = node['foundational']
    reinf_count  = node['reinforcement_count'] or 0
    edge_count   = node['edge_count']

    profile = type_profiles.get(ntype, {})

    floors = [0.0]
    if is_pinned:
        floors.append(pinned_floor)
    if is_foundational:
        floors.append(foundational_floor)
    floors.append(profile.get('floor', 0.0))
    floors.append(min(conn_floor_max, edge_count * conn_floor_per_edge))
    if reinf_count >= stable_count:
        floors.append(reinf_floor)

    return max(floors)


# ── Main ─────────────────────────────────────────────────────────────────────

def run(apply: bool, verbose: bool):
    config      = load_config()
    decay_cfg   = config.get('decay', {})
    type_profiles = decay_cfg.get('type_profiles', {})

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Fetch all SLTM nodes with edge counts
            cur.execute("""
                SELECT n.id, n.content, n.type, n.decay_state,
                       n.reinforcement_count, n.pinned, n.foundational,
                       COUNT(DISTINCT e.id) AS edge_count
                FROM nodes n
                LEFT JOIN edges e ON e.source_id = n.id OR e.target_id = n.id
                WHERE n.memory_layer = 'sltm'
                GROUP BY n.id, n.content, n.type, n.decay_state,
                         n.reinforcement_count, n.pinned, n.foundational
                ORDER BY n.type, n.content
            """)
            sltm_nodes = cur.fetchall()

        print(f"SLTM nodes found: {len(sltm_nodes)}")
        print()

        rescuable = []
        zero_floor = []

        for node in sltm_nodes:
            floor = compute_effective_floor(node, decay_cfg, type_profiles)
            if floor > (node['decay_state'] or 0.0):
                rescuable.append((node, floor))
            else:
                zero_floor.append(node)

        # Summary
        print(f"Rescuable (floor > current decay): {len(rescuable)}")
        print(f"Staying in SLTM (no floor applies):  {len(zero_floor)}")
        print()

        if not rescuable:
            print("Nothing to rescue.")
            return

        # Group rescuable by what's driving the floor
        by_driver = {}
        for node, floor in rescuable:
            drivers = _floor_drivers(node, decay_cfg, type_profiles)
            key = ", ".join(sorted(drivers))
            by_driver.setdefault(key, []).append((node, floor))

        print("── Rescue breakdown by floor driver ─────────────────────────")
        for driver, items in sorted(by_driver.items(), key=lambda x: -len(x[1])):
            print(f"  {driver}: {len(items)} nodes")
        print()

        if verbose:
            print("── Per-node detail ───────────────────────────────────────────")
            for node, floor in rescuable:
                drivers = _floor_drivers(node, decay_cfg, type_profiles)
                print(
                    f"  [{node['type']:20s}] {node["content"][:50]:<50}  "
                    f"decay={node['decay_state']:.4f} → floor={floor:.4f}  "
                    f"({', '.join(sorted(drivers))})"
                )
            print()

        if not apply:
            print("DRY RUN — no changes made. Re-run with --apply to commit.")
            return

        # Apply
        print("Applying rescue pass...")
        updates = [(floor, node['id']) for node, floor in rescuable]

        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE nodes SET decay_state = %s, memory_layer = 'ltm' WHERE id = %s",
                updates
            )
        conn.commit()

        print(f"Rescued {len(rescuable)} nodes → promoted to LTM with updated decay floors.")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


def _floor_drivers(node, decay_cfg, type_profiles):
    """Return a list of floor-source labels that contribute to the effective floor."""
    pinned_floor        = decay_cfg.get('pinned_floor', 0.5)
    foundational_floor  = decay_cfg.get('foundational_floor', 0.35)
    reinf_floor         = decay_cfg.get('reinforcement_floor', 0.20)
    stable_count        = decay_cfg.get('stable_reinforcement_count', 5)
    conn_floor_per_edge = decay_cfg.get('connectivity_floor_per_edge', 0.01)
    conn_floor_max      = decay_cfg.get('connectivity_floor_max', 0.30)

    profile      = type_profiles.get(node['type'], {})
    type_floor   = profile.get('floor', 0.0)
    conn_floor   = min(conn_floor_max, node['edge_count'] * conn_floor_per_edge)
    reinf_count  = node['reinforcement_count'] or 0

    # Compute the winner
    candidates = {
        'pinned':        (pinned_floor       if node['pinned']        else 0.0),
        'foundational':  (foundational_floor if node['foundational']  else 0.0),
        'type':          type_floor,
        'connectivity':  conn_floor,
        'reinforcement': (reinf_floor if reinf_count >= stable_count else 0.0),
    }
    best = max(candidates.values())
    if best == 0.0:
        return ['none']
    return [k for k, v in candidates.items() if v == best]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="SLTM rescue pass — apply pg_009 floors retroactively")
    parser.add_argument('--apply',   action='store_true', help='Commit changes (default is dry-run)')
    parser.add_argument('--verbose', action='store_true', help='Print per-node detail')
    args = parser.parse_args()

    run(apply=args.apply, verbose=args.verbose)
