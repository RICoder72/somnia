-- Migration 008: Work Queue
-- Implements the async job queue for the dream cycle refactor.
-- See: domains/somnia/DREAM_QUEUE_DESIGN.md
--
-- Job lifecycle: pending → in_progress → paused → complete
--                                      ↘ failed
--
-- Priority scale: lower number = higher priority
--   10  process_stm       (time-sensitive, always wins)
--   30  harvest_acquire   (burst fetch, fast)
--   50  ruminate          (normal background)
--   70  solo_work         (background, lower urgency)
--   80  harvest_process   (backlog drain, lowest)

CREATE TABLE IF NOT EXISTS work_queue (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    type            TEXT        NOT NULL,
    priority        INTEGER     NOT NULL DEFAULT 50,
    state           TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending','in_progress','paused','complete','failed')),
    progress        JSONB,                          -- resumption cursor, type-specific
    tokens_used     INTEGER     NOT NULL DEFAULT 0, -- cumulative across all runs of this job
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_at     TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

-- Fetch the next runnable job: pending or paused, ordered by priority then age
CREATE INDEX IF NOT EXISTS idx_wq_runnable
    ON work_queue (priority ASC, created_at ASC)
    WHERE state IN ('pending', 'paused');

-- Fast lookup by type + state (for deduplication checks)
CREATE INDEX IF NOT EXISTS idx_wq_type_state
    ON work_queue (type, state);

-- History queries
CREATE INDEX IF NOT EXISTS idx_wq_completed
    ON work_queue (completed_at DESC)
    WHERE completed_at IS NOT NULL;
