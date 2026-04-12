-- Migration 009: Foundational flag for nodes
-- Part of the decay reform (see workspaces/somnia/GRAPH_MEMORY_DESIGN.md)
--
-- The foundational flag is the explicit "this matters even if we don't talk
-- about it" lever. When set, the node receives a hard decay floor regardless
-- of type or connectivity. Sources of foundational=true:
--   - Set manually via portal or MCP tool
--   - Set by dream cycle when a node is identified as load-bearing
--   - Transferred automatically when a node is superseded (pg_010 and later)
--   - Always true for personality nodes (DREAM_QUEUE_DESIGN.md)
--
-- The actual decay floor for foundational nodes is configurable via
-- decay.foundational_floor in config.yaml (default 0.35).

ALTER TABLE nodes ADD COLUMN IF NOT EXISTS foundational BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index — the only interesting query is "find foundational nodes",
-- and the column is overwhelmingly false. Saves space vs a full index.
CREATE INDEX IF NOT EXISTS idx_nodes_foundational
    ON nodes(foundational)
    WHERE foundational = TRUE;
