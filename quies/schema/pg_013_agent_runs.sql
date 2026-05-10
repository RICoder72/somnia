-- pg_013: Agent run log
-- Full lifecycle record for every agent dispatch.
-- The daemon INSERTs at dispatch time and UPDATEs on completion.

CREATE TABLE IF NOT EXISTS agent_runs (
    id              TEXT PRIMARY KEY,
    dream_id        TEXT REFERENCES dream_log(id),
    mode            TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    exit_code       INTEGER,
    duration_seconds INTEGER,
    cost_usd        REAL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cli_json        JSONB,              -- full JSON from 'claude -p --output-format json'
    result_text     TEXT,               -- agent's summary / result field
    stderr          TEXT,               -- agent's stderr (MCP logs, tool traces)
    status          TEXT NOT NULL DEFAULT 'dispatched',  -- dispatched | success | failed | timeout
    error           TEXT,               -- error message if failed
    dispatch_params JSONB               -- what we sent the agent as input
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_dream    ON agent_runs(dream_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_mode     ON agent_runs(mode);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status   ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started  ON agent_runs(started_at);

-- Also fix: dream_log.mode was never being populated by run_consolidation.
-- Backfill from summary prefix where possible.
UPDATE dream_log SET mode = 'process'      WHERE mode IS NULL AND summary LIKE '[process]%';
UPDATE dream_log SET mode = 'ruminate'     WHERE mode IS NULL AND summary LIKE '[ruminate]%';
UPDATE dream_log SET mode = 'solo_work'    WHERE mode IS NULL AND summary LIKE '[solo_work]%';
UPDATE dream_log SET mode = 'archaeologize' WHERE mode IS NULL AND summary LIKE '[archaeologize]%';
