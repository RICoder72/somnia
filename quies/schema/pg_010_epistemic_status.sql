-- Migration 010: Epistemic status for nodes
-- Tracks the confidence level of claims in the graph, preventing
-- speculative nodes from being treated as established facts by
-- subsequent dream cycles.
--
-- Values:
--   established  — verified, sourced, or agreed upon in conversation
--   observed     — seen/noticed, not yet verified (default for existing nodes)
--   hypothesis   — plausible but unproven; requires evidence to promote
--   speculation  — explicitly uncertain; should not serve as foundation
--                  for new insight nodes
--
-- The default for existing nodes is 'observed' (conservative) —
-- they came from real conversations but haven't been explicitly assessed.
-- New nodes created by dream cycles must set this field explicitly.
-- The default for new nodes is 'hypothesis' (skeptical).

ALTER TABLE nodes ADD COLUMN IF NOT EXISTS epistemic_status TEXT
    NOT NULL DEFAULT 'hypothesis'
    CHECK (epistemic_status IN ('established', 'observed', 'hypothesis', 'speculation'));

-- Back-fill existing nodes to 'observed' — they came from real conversations
-- and are more credible than a fresh hypothesis from a dream cycle.
UPDATE nodes SET epistemic_status = 'observed'
    WHERE epistemic_status = 'hypothesis'
    AND created_at < NOW();

-- Partial index for the two "weak" statuses — the common query is
-- "find nodes I shouldn't build on top of", and these are the minority.
CREATE INDEX IF NOT EXISTS idx_nodes_epistemic_weak
    ON nodes(epistemic_status)
    WHERE epistemic_status IN ('hypothesis', 'speculation');

-- Partial index for pinned nodes (always want fast access to established anchors)
CREATE INDEX IF NOT EXISTS idx_nodes_epistemic_established
    ON nodes(epistemic_status)
    WHERE epistemic_status = 'established';
