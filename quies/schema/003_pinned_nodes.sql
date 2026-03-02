-- Migration 003: Pinned nodes
-- Adds pinned column to nodes table (pinned nodes resist decay).
-- Pinned nodes are regular nodes that resist decay and represent
-- active projects, jobs, interests, etc.

ALTER TABLE nodes ADD COLUMN pinned INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_nodes_pinned ON nodes(pinned);
