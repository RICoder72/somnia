-- System event log — persistent, queryable alternative to container logs.
-- Captures scheduler decisions, cycle outcomes, parse failures, recovery attempts.

CREATE TABLE IF NOT EXISTS system_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL CHECK (level IN ('error', 'warning', 'info')),
    source TEXT NOT NULL,        -- scheduler, dream, rumination, solo_work, recovery, backup, api
    message TEXT NOT NULL,
    metadata JSONB,              -- structured context: dream_id, mode, tokens, subtype, etc.
    dream_id UUID                -- optional correlation to dream_log
);

CREATE INDEX IF NOT EXISTS idx_system_log_timestamp ON system_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_log_level ON system_log (level, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_log_source ON system_log (source, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_log_dream ON system_log (dream_id) WHERE dream_id IS NOT NULL;

-- Auto-prune: keep 90 days of logs (run via scheduler or manual)
-- No auto-delete trigger — just a helper for maintenance.
