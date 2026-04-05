"""
work_queue.py — Dream Queue infrastructure (Phase 1)

Async job queue for the Somnia dream cycle.
Provides: job lifecycle management, activity registry, and cycle runner.

Phase 1: This module is infrastructure only.
         The existing dream_scheduler() is untouched.
         Wire in by calling run_cycle() from dream_scheduler once
         you're ready to migrate an activity type (Phase 2+).

Activity types register themselves via @register_activity.
Each handler receives a Job and a Budget, does one meaningful unit
of work, and returns a JobResult with a cursor (for resumption),
tokens consumed, and a completion flag.

Priority scale (lower = higher priority):
    PRIORITY_PROCESS_STM     = 10
    PRIORITY_HARVEST_ACQUIRE = 30
    PRIORITY_RUMINATE        = 50
    PRIORITY_SOLO_WORK       = 70
    PRIORITY_HARVEST_PROCESS = 80

Usage (future dream_scheduler integration):
    from work_queue import run_cycle, enqueue
    enqueue('process_stm', priority=PRIORITY_PROCESS_STM)
    run_cycle(budget_tokens=50000, budget_seconds=1200)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from db import execute

logger = logging.getLogger(__name__)

# ── Priority constants ─────────────────────────────────────────────────────

PRIORITY_PROCESS_STM     = 10
PRIORITY_HARVEST_ACQUIRE = 30
PRIORITY_RUMINATE        = 50
PRIORITY_SOLO_WORK       = 70
PRIORITY_HARVEST_PROCESS = 80


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
    cursor:      Resumption state to persist if paused (type-specific JSONB).
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
    type_name: str
    handler: Callable[[dict, Budget], JobResult]
    default_priority: int
    resumable: bool            # Can a paused job be continued next cycle?
    deduplicate: bool          # Skip enqueue if a pending/paused job of this type exists?
    description: str = ""


_REGISTRY: dict[str, ActivityDefinition] = {}


def register_activity(
    type_name: str,
    handler: Callable[[dict, Budget], JobResult],
    default_priority: int = 50,
    resumable: bool = True,
    deduplicate: bool = True,
    description: str = "",
) -> None:
    """Register an activity type. Call at module import time."""
    _REGISTRY[type_name] = ActivityDefinition(
        type_name=type_name,
        handler=handler,
        default_priority=default_priority,
        resumable=resumable,
        deduplicate=deduplicate,
        description=description,
    )
    logger.debug(f"WorkQueue: registered activity '{type_name}' (priority={default_priority})")


def registered_types() -> list[str]:
    return list(_REGISTRY.keys())


# ── Job lifecycle ──────────────────────────────────────────────────────────

def enqueue(
    type_name: str,
    priority: Optional[int] = None,
    progress: Optional[dict] = None,
    deduplicate: bool = True,
) -> Optional[str]:
    """
    Add a job to the queue. Returns the new job ID, or None if deduplicated.

    If deduplicate=True (and the activity definition also has deduplicate=True),
    skips enqueueing if a pending or paused job of this type already exists.
    """
    defn = _REGISTRY.get(type_name)
    effective_priority = priority if priority is not None else (
        defn.default_priority if defn else 50
    )
    should_dedup = deduplicate and (defn.deduplicate if defn else True)

    if should_dedup:
        existing = execute(
            "SELECT id FROM work_queue WHERE type = %s AND state IN ('pending','paused') LIMIT 1",
            (type_name,), fetch='one'
        )
        if existing:
            logger.debug(f"WorkQueue: enqueue '{type_name}' skipped — already queued ({existing['id'][:8]})")
            return None

    job_id = str(uuid.uuid4())
    execute(
        """
        INSERT INTO work_queue (id, type, priority, state, progress, created_at)
        VALUES (%s, %s, %s, 'pending', %s, NOW())
        """,
        (job_id, type_name, effective_priority,
         __import__('json').dumps(progress) if progress else None)
    )
    logger.info(f"WorkQueue: enqueued '{type_name}' job {job_id[:8]} (priority={effective_priority})")
    return job_id


def get_next_job() -> Optional[dict]:
    """
    Fetch the highest-priority runnable job (pending or paused).
    Marks it in_progress atomically.
    Returns None if queue is empty.
    """
    row = execute(
        """
        UPDATE work_queue
        SET    state = 'in_progress', last_run_at = NOW()
        WHERE  id = (
            SELECT id FROM work_queue
            WHERE  state IN ('pending', 'paused')
            ORDER  BY priority ASC, created_at ASC
            LIMIT  1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        fetch='one'
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
        (__import__('json').dumps(cursor) if cursor else None, tokens_used, job_id)
    )
    logger.debug(f"WorkQueue: job {job_id[:8]} paused (+{tokens_used} tokens)")


# ── Cycle runner ───────────────────────────────────────────────────────────

def run_cycle(
    budget_tokens: int = 50_000,
    budget_seconds: float = 1200.0,
) -> dict[str, Any]:
    """
    Run the dream queue for one cycle.

    Picks jobs from the queue in priority order, dispatches each to its
    registered handler, and pauses or completes based on the result.
    Stops when the budget (tokens or time) is exhausted, or the queue
    is empty.

    Returns a summary dict for logging/sticky-notes.
    """
    summary: dict[str, Any] = {
        "jobs_attempted": 0,
        "jobs_completed": 0,
        "jobs_paused": 0,
        "jobs_failed": 0,
        "total_tokens": 0,
        "activity_log": [],
    }

    budget = Budget(
        tokens_remaining=budget_tokens,
        seconds_remaining=budget_seconds,
    )

    logger.info(
        f"WorkQueue: cycle start — budget {budget_tokens:,} tokens / "
        f"{budget_seconds:.0f}s"
    )

    while not budget.is_exhausted():
        job = get_next_job()
        if job is None:
            logger.info("WorkQueue: queue empty, cycle done")
            break

        job_id   = job["id"]
        job_type = job["type"]
        summary["jobs_attempted"] += 1

        defn = _REGISTRY.get(job_type)
        if defn is None:
            err = f"No handler registered for activity type '{job_type}'"
            mark_failed(job_id, err)
            summary["jobs_failed"] += 1
            summary["activity_log"].append({"type": job_type, "outcome": "failed", "error": err})
            logger.warning(f"WorkQueue: {err}")
            continue

        # Remaining budget for this handler
        budget.tokens_remaining = budget_tokens - summary["total_tokens"]
        budget.seconds_remaining = budget_seconds - budget.elapsed()

        logger.info(f"WorkQueue: running '{job_type}' {job_id[:8]}")
        try:
            result: JobResult = defn.handler(job, budget)
        except Exception as exc:
            err = f"Unhandled exception in handler: {exc}"
            mark_failed(job_id, err)
            summary["jobs_failed"] += 1
            summary["activity_log"].append({"type": job_type, "outcome": "failed", "error": err})
            logger.exception(f"WorkQueue: handler for '{job_type}' raised")
            continue

        summary["total_tokens"] += result.tokens_used

        if result.error:
            mark_failed(job_id, result.error, result.tokens_used)
            summary["jobs_failed"] += 1
            summary["activity_log"].append({
                "type": job_type, "outcome": "failed",
                "error": result.error, "tokens": result.tokens_used
            })
        elif result.complete:
            mark_complete(job_id, result.tokens_used)
            summary["jobs_completed"] += 1
            summary["activity_log"].append({
                "type": job_type, "outcome": "complete",
                "tokens": result.tokens_used
            })
        else:
            if not defn.resumable:
                # Non-resumable job that returned incomplete — treat as failed
                mark_failed(job_id, "Handler returned incomplete but activity is not resumable")
                summary["jobs_failed"] += 1
            else:
                pause_job(job_id, result.cursor, result.tokens_used)
                summary["jobs_paused"] += 1
                summary["activity_log"].append({
                    "type": job_type, "outcome": "paused",
                    "tokens": result.tokens_used,
                    "cursor": result.cursor
                })

    elapsed = budget.elapsed()
    summary["elapsed_seconds"] = round(elapsed, 1)
    logger.info(
        f"WorkQueue: cycle done — {summary['jobs_completed']} complete, "
        f"{summary['jobs_paused']} paused, {summary['jobs_failed']} failed, "
        f"{summary['total_tokens']:,} tokens, {elapsed:.1f}s"
    )
    return summary


# ── Queue inspection ───────────────────────────────────────────────────────

def queue_depth() -> dict[str, int]:
    """Return counts by state — useful for status endpoints and sticky notes."""
    rows = execute(
        """
        SELECT state, COUNT(*) AS n
        FROM work_queue
        GROUP BY state
        """,
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
