"""
work_queue.py — Dream Queue infrastructure (Phase 1)

Async job queue for the Somnia dream cycle.
Provides: job lifecycle management, activity registry, and cycle runner.

Phase 1: This module is infrastructure only.
         The existing dream_scheduler() is untouched.
         Wire in by calling run_cycle() from dream_scheduler once
         you're ready to migrate an activity type (Phase 2+).

## Selection Model: Tier + D Score

Each activity has two scheduling attributes:

  tier (int, unique, required)
      Hard priority partition. Lower tier = higher precedence.
      The scheduler finds the lowest tier among all eligible activities
      and only considers activities in that tier for D competition.
      Tiers must be unique across all registered activities.

      Suggested assignments:
          0  — process_stm       (obligatory inbox drain, always preempts)
          10 — harvest_acquire   (timely fetch before backlog grows)
          20 — ruminate          (post-interaction reinforcement)
          20 — solo_work         (idle-time exploration) ← same tier, compete via D
          30 — harvest_process   (background backlog drain)
          40 — backfill_acquire  (one-shot seeding, lowest urgency)

      Note: ruminate and solo_work intentionally share tier 20 — they
      are natural competitors and should be selected by D score alone.

  d_fn (callable, optional)
      D ∈ [0.0, 1.0] — urgency score computed from live system state.
      Evaluated fresh each cycle. Activities with D < D_MIN are excluded
      even if they are the lowest-tier eligible activity.
      If omitted, D defaults to 0.5 (always eligible above threshold).

The net effect: tier gives you unambiguous preemption across activity
classes; D handles nuanced competition within a tier. Cooldowns
disappear as a concept — a satisfied activity has low D naturally.

## Usage (future dream_scheduler integration)

    from work_queue import run_cycle, enqueue, register_activity, TIER_*
    register_activity('process_stm', handler=..., tier=TIER_PROCESS_STM, d_fn=d_process_stm)
    run_cycle(budget_tokens=50_000, budget_seconds=1200)
"""

from __future__ import annotations

import logging
import time
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from db import execute

logger = logging.getLogger(__name__)

# ── Tier constants (unique, lower = higher precedence) ─────────────────────

TIER_PROCESS_STM     = 0
TIER_HARVEST_ACQUIRE = 10
TIER_RUMINATE        = 20   # ruminate and solo_work share tier — D decides
TIER_SOLO_WORK       = 20
TIER_HARVEST_PROCESS = 30
TIER_BACKFILL        = 40

# ── Scheduler tuning ───────────────────────────────────────────────────────

D_MIN = 0.05   # Activities with D below this are excluded regardless of tier


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class Budget:
    """Remaining cycle budget passed into each handler."""
    tokens_remaining: int
    seconds_remaining: float
    cycle_start: float = field(default_factory=time.time)

    def elapsed(self) -> float:
        return time.time() - self.cycle_start

    def is_exhausted(self) -> bool:
        return (
            self.tokens_remaining <= 0 or
            self.seconds_remaining - self.elapsed() <= 0
        )


@dataclass
class JobResult:
    """
    Returned by every activity handler.

    complete:    True if the job finished all work and should be marked complete.
    cursor:      Resumption state to persist if paused (type-specific dict).
    tokens_used: Tokens consumed in this unit of work.
    error:       Non-None if the job should be marked failed.
    """
    complete: bool = False
    cursor: Optional[dict] = None
    tokens_used: int = 0
    error: Optional[str] = None


# ── Activity registry ──────────────────────────────────────────────────────

@dataclass
class ActivityDefinition:
    type_name:   str
    handler:     Callable[[dict, Budget], JobResult]
    tier:        int                                    # unique hard-priority partition
    d_fn:        Optional[Callable[[dict], float]]     # urgency scorer; None → D=0.5
    resumable:   bool      # can a paused job be continued next cycle?
    deduplicate: bool      # skip enqueue if pending/paused job of this type exists?
    description: str = ""


_REGISTRY: dict[str, ActivityDefinition] = {}


def register_activity(
    type_name:   str,
    handler:     Callable[[dict, Budget], JobResult],
    tier:        int,
    d_fn:        Optional[Callable[[dict], float]] = None,
    resumable:   bool = True,
    deduplicate: bool = True,
    description: str = "",
) -> None:
    """
    Register an activity type. Call at module import time.

    tier must be unique across all registered activities, EXCEPT that
    activities intended to compete purely on D (e.g. ruminate / solo_work)
    may share a tier.
    """
    _REGISTRY[type_name] = ActivityDefinition(
        type_name=type_name,
        handler=handler,
        tier=tier,
        d_fn=d_fn,
        resumable=resumable,
        deduplicate=deduplicate,
        description=description,
    )
    logger.debug(
        f"WorkQueue: registered '{type_name}' "
        f"(tier={tier}, d_fn={'yes' if d_fn else 'default 0.5'})"
    )


def registered_types() -> list[str]:
    return list(_REGISTRY.keys())


# ── D-score computation ────────────────────────────────────────────────────

def compute_d(type_name: str, system_state: dict) -> float:
    """
    Compute the urgency score D ∈ [0.0, 1.0] for an activity type.

    If the activity has no d_fn registered, returns 0.5 (always above
    D_MIN, never dominates activities with real D functions).
    """
    defn = _REGISTRY.get(type_name)
    if defn is None:
        return 0.0
    if defn.d_fn is None:
        return 0.5
    try:
        raw = defn.d_fn(system_state)
        return max(0.0, min(1.0, float(raw)))
    except Exception as e:
        logger.warning(f"WorkQueue: d_fn for '{type_name}' raised {e}, defaulting D=0.0")
        return 0.0


def select_next_activity(system_state: dict) -> Optional[str]:
    """
    Select the activity type to run next cycle.

    Algorithm:
      1. Compute D for all registered activity types.
      2. Exclude any with D < D_MIN.
      3. Find the lowest tier among remaining eligible activities.
      4. Among activities in that tier, return the one with highest D.
      5. Return None if nothing is eligible.

    This means a tier-0 activity with D=0.06 beats a tier-20 activity
    with D=0.95 — tier is a hard gate, not a soft preference.
    """
    scores: dict[str, float] = {}
    for type_name in _REGISTRY:
        d = compute_d(type_name, system_state)
        if d >= D_MIN:
            scores[type_name] = d

    if not scores:
        return None

    lowest_tier = min(_REGISTRY[t].tier for t in scores)
    tier_candidates = {t: d for t, d in scores.items()
                       if _REGISTRY[t].tier == lowest_tier}

    winner = max(tier_candidates, key=tier_candidates.get)
    logger.debug(
        f"WorkQueue: selected '{winner}' "
        f"(tier={lowest_tier}, D={tier_candidates[winner]:.3f}) "
        f"from {len(scores)} eligible activities"
    )
    return winner


# ── System state ───────────────────────────────────────────────────────────

def get_system_state() -> dict:
    """
    Collect all live inputs needed by D functions.

    Returns a dict with well-known keys. Add keys here as new D functions
    require new inputs — all D functions receive the full state dict and
    can ignore keys they don't need.
    """
    import os
    from pathlib import Path

    state: dict[str, Any] = {}

    # Inbox depth — counted from stm_nodes (the live STM table).
    # The legacy `inbox` table still exists in the schema but is no longer
    # written to: somnia_remember and the in-session harvester both target
    # `stm_nodes`, and the dream consolidation drains by deletion (no
    # `processed` flag exists on stm_nodes). Reading from `inbox` here would
    # always yield 0, which silently zeroes the D-score for `process_stm`
    # and prevents the work_queue from ever dispatching it.
    try:
        row = execute(
            "SELECT COUNT(*) AS n FROM stm_nodes",
            fetch='one'
        )
        state['inbox_depth'] = int(row['n']) if row else 0
    except Exception:
        state['inbox_depth'] = 0

    # Time since last user interaction (seconds)
    try:
        row = execute(
            "SELECT timestamp FROM activity ORDER BY timestamp DESC LIMIT 1",
            fetch='one'
        )
        if row:
            last = row['timestamp']
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            state['seconds_since_interaction'] = (
                datetime.now(timezone.utc) - last
            ).total_seconds()
        else:
            state['seconds_since_interaction'] = float('inf')
    except Exception:
        state['seconds_since_interaction'] = float('inf')

    # Time since last in-session harvest (seconds)
    # Claude writes harvest_state.json to workspaces/claude/findings/ at
    # the end of conversations via the in-session harvest pipeline. The
    # daemon no longer runs its own harvester; this metric exists only
    # for state-view purposes.
    try:
        hs_path = Path('/data/workspaces/claude/findings/harvest_state.json')
        if hs_path.exists():
            import json as _json
            hs = _json.loads(hs_path.read_text())
            last_h = hs.get('last_harvest_at')
            if last_h:
                last_dt = datetime.fromisoformat(last_h)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                state['seconds_since_harvest'] = (
                    datetime.now(timezone.utc) - last_dt
                ).total_seconds()
            else:
                state['seconds_since_harvest'] = float('inf')
        else:
            state['seconds_since_harvest'] = float('inf')
    except Exception:
        state['seconds_since_harvest'] = float('inf')

    return state


# ── Job lifecycle ──────────────────────────────────────────────────────────

def enqueue(
    type_name:   str,
    progress:    Optional[dict] = None,
    deduplicate: bool = True,
) -> Optional[str]:
    """
    Add a job to the queue. Returns the new job ID, or None if deduplicated.

    Priority stored in work_queue is derived from the activity's tier —
    lower tier = lower priority integer = sorts first. Within a tier,
    jobs sort by created_at (FIFO). D-score selection happens at scheduling
    time, not storage time.
    """
    defn = _REGISTRY.get(type_name)
    should_dedup = deduplicate and (defn.deduplicate if defn else True)

    if should_dedup:
        existing = execute(
            "SELECT id FROM work_queue "
            "WHERE type = %s AND state IN ('pending','paused') LIMIT 1",
            (type_name,), fetch='one'
        )
        if existing:
            logger.debug(
                f"WorkQueue: enqueue '{type_name}' skipped — "
                f"already queued ({existing['id'][:8]})"
            )
            return None

    job_id = str(uuid.uuid4())
    tier = defn.tier if defn else 50
    execute(
        """
        INSERT INTO work_queue (id, type, priority, state, progress, created_at)
        VALUES (%s, %s, %s, 'pending', %s, NOW())
        """,
        (job_id, type_name, tier,
         json.dumps(progress) if progress else None)
    )
    logger.info(
        f"WorkQueue: enqueued '{type_name}' job {job_id[:8]} (tier={tier})"
    )
    return job_id


def get_or_create_job(type_name: str) -> Optional[dict]:
    """
    Get an existing pending/paused job of this type, or create one.
    Returns the job dict with state set to in_progress.
    """
    # Try to claim an existing job atomically
    row = execute(
        """
        UPDATE work_queue
        SET    state = 'in_progress', last_run_at = NOW()
        WHERE  id = (
            SELECT id FROM work_queue
            WHERE  type = %s AND state IN ('pending', 'paused')
            ORDER  BY created_at ASC
            LIMIT  1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (type_name,), fetch='one'
    )
    if row:
        return dict(row)

    # No existing job — create and immediately claim one
    defn = _REGISTRY.get(type_name)
    tier = defn.tier if defn else 50
    job_id = str(uuid.uuid4())
    row = execute(
        """
        INSERT INTO work_queue (id, type, priority, state, created_at, last_run_at)
        VALUES (%s, %s, %s, 'in_progress', NOW(), NOW())
        RETURNING *
        """,
        (job_id, type_name, tier), fetch='one'
    )
    return dict(row) if row else None


def mark_complete(job_id: str, tokens_used: int = 0) -> None:
    execute(
        """
        UPDATE work_queue
        SET state = 'complete', completed_at = NOW(),
            tokens_used = tokens_used + %s
        WHERE id = %s
        """,
        (tokens_used, job_id)
    )
    logger.info(f"WorkQueue: job {job_id[:8]} complete (+{tokens_used} tokens)")


def mark_failed(job_id: str, error: str, tokens_used: int = 0) -> None:
    execute(
        """
        UPDATE work_queue
        SET state = 'failed', error = %s, completed_at = NOW(),
            tokens_used = tokens_used + %s
        WHERE id = %s
        """,
        (error, tokens_used, job_id)
    )
    logger.warning(f"WorkQueue: job {job_id[:8]} failed — {error}")


def pause_job(job_id: str, cursor: Optional[dict], tokens_used: int = 0) -> None:
    execute(
        """
        UPDATE work_queue
        SET state = 'paused', progress = %s,
            tokens_used = tokens_used + %s
        WHERE id = %s
        """,
        (json.dumps(cursor) if cursor else None, tokens_used, job_id)
    )
    logger.debug(f"WorkQueue: job {job_id[:8]} paused (+{tokens_used} tokens)")


# ── Cycle runner ───────────────────────────────────────────────────────────

def run_cycle(
    budget_tokens:  int   = 50_000,
    budget_seconds: float = 1200.0,
    dry_run:        bool  = False,
) -> dict[str, Any]:
    """
    Run the dream queue for one cycle.

    Each iteration:
      1. Refresh system state (inbox depth, backlog, time, etc.)
      2. Select the highest-urgency eligible activity (tier + D)
      3. Dispatch to its handler
      4. Pause or complete based on result
      5. Repeat until budget exhausted or queue empty

    dry_run=True: evaluates and logs selections without executing handlers.
    Useful for one-night parity checks before cutting over from old scheduler.

    Returns a summary dict for logging and sticky notes.
    """
    summary: dict[str, Any] = {
        "jobs_attempted": 0,
        "jobs_completed": 0,
        "jobs_paused":    0,
        "jobs_failed":    0,
        "total_tokens":   0,
        "dry_run":        dry_run,
        "activity_log":   [],
    }

    budget = Budget(
        tokens_remaining=budget_tokens,
        seconds_remaining=budget_seconds,
    )

    logger.info(
        f"WorkQueue: cycle start {'(DRY RUN) ' if dry_run else ''}"
        f"— budget {budget_tokens:,} tokens / {budget_seconds:.0f}s"
    )

    while not budget.is_exhausted():
        # Refresh system state each iteration — inbox depth changes as jobs run
        system_state = get_system_state()

        atype = select_next_activity(system_state)
        if atype is None:
            logger.info("WorkQueue: no eligible activities, cycle done")
            break

        d_score = compute_d(atype, system_state)
        tier    = _REGISTRY[atype].tier

        if dry_run:
            logger.info(
                f"WorkQueue [DRY RUN]: would run '{atype}' "
                f"(tier={tier}, D={d_score:.3f})"
            )
            summary["activity_log"].append({
                "type": atype, "tier": tier, "d": d_score, "outcome": "dry_run"
            })
            # In dry run, break after one selection — enough to validate logic
            break

        summary["jobs_attempted"] += 1
        defn = _REGISTRY[atype]

        job = get_or_create_job(atype)
        if not job:
            logger.warning(f"WorkQueue: could not get/create job for '{atype}'")
            break

        job_id = job["id"]

        # Update remaining budget before passing to handler
        budget.tokens_remaining  = budget_tokens  - summary["total_tokens"]
        budget.seconds_remaining = budget_seconds - budget.elapsed()

        logger.info(
            f"WorkQueue: running '{atype}' {job_id[:8]} "
            f"(tier={tier}, D={d_score:.3f})"
        )

        try:
            result: JobResult = defn.handler(job, budget)
        except Exception as exc:
            err = f"Unhandled exception in handler: {exc}"
            mark_failed(job_id, err)
            summary["jobs_failed"] += 1
            summary["activity_log"].append({
                "type": atype, "tier": tier, "d": d_score,
                "outcome": "failed", "error": err
            })
            logger.exception(f"WorkQueue: handler for '{atype}' raised")
            continue

        summary["total_tokens"] += result.tokens_used

        if result.error:
            mark_failed(job_id, result.error, result.tokens_used)
            summary["jobs_failed"] += 1
            summary["activity_log"].append({
                "type": atype, "tier": tier, "d": d_score,
                "outcome": "failed", "error": result.error,
                "tokens": result.tokens_used
            })
        elif result.complete:
            mark_complete(job_id, result.tokens_used)
            summary["jobs_completed"] += 1
            summary["activity_log"].append({
                "type": atype, "tier": tier, "d": d_score,
                "outcome": "complete", "tokens": result.tokens_used
            })
        else:
            if not defn.resumable:
                mark_failed(
                    job_id,
                    "Handler returned incomplete but activity is not resumable"
                )
                summary["jobs_failed"] += 1
            else:
                pause_job(job_id, result.cursor, result.tokens_used)
                summary["jobs_paused"] += 1
                summary["activity_log"].append({
                    "type": atype, "tier": tier, "d": d_score,
                    "outcome": "paused", "tokens": result.tokens_used,
                    "cursor": result.cursor
                })

    elapsed = budget.elapsed()
    summary["elapsed_seconds"] = round(elapsed, 1)
    logger.info(
        f"WorkQueue: cycle done — "
        f"{summary['jobs_completed']} complete, "
        f"{summary['jobs_paused']} paused, "
        f"{summary['jobs_failed']} failed, "
        f"{summary['total_tokens']:,} tokens, {elapsed:.1f}s"
    )
    return summary


# ── Queue inspection ───────────────────────────────────────────────────────

def queue_depth() -> dict[str, int]:
    """Return counts by state — useful for status endpoints and sticky notes."""
    rows = execute(
        "SELECT state, COUNT(*) AS n FROM work_queue GROUP BY state",
        fetch='all'
    )
    return {row['state']: row['n'] for row in (rows or [])}


def queue_summary(limit: int = 20) -> list[dict]:
    """Recent jobs for dashboard/logging."""
    rows = execute(
        """
        SELECT id, type, priority, state, tokens_used, error,
               created_at, last_run_at, completed_at
        FROM   work_queue
        ORDER  BY created_at DESC
        LIMIT  %s
        """,
        (limit,), fetch='all'
    )
    return [dict(r) for r in (rows or [])]


def current_d_scores(system_state: Optional[dict] = None) -> dict[str, dict]:
    """
    Return D scores and tier for all registered activity types.
    Useful for status endpoints and portal dashboard display.
    """
    if system_state is None:
        system_state = get_system_state()
    return {
        t: {"tier": _REGISTRY[t].tier, "d": compute_d(t, system_state)}
        for t in _REGISTRY
    }
