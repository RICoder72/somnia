-- Add memory_layer column for three-tier memory: STM → LTM → SLTM
-- SLTM (super long-term) preserves everything but excludes from active recall
-- Nodes demote to SLTM when decay drops below threshold; promote back on access

ALTER TABLE nodes ADD COLUMN IF NOT EXISTS memory_layer TEXT NOT NULL DEFAULT 'ltm';

CREATE INDEX IF NOT EXISTS idx_nodes_memory_layer ON nodes(memory_layer);

-- Set any existing nodes that are very cold to SLTM
-- (we won't do this now — let the daemon handle it organically)
