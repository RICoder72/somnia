-- Migration 006: Structural edge types
-- Adds is_structural flag to edges table, enabling behavioral semantics
-- beyond simple semantic labeling.
--
-- Structural edge types and their meanings:
--   superseded_by  — source was renamed/replaced by target; source becomes retired
--   part_of        — source is a component/subproject of target; warmth cascades upward
--   merged_into    — source was absorbed into target; source becomes a redirect shell
--
-- The is_structural flag lets graph queries cleanly separate structural
-- relationships from semantic ones.

ALTER TABLE edges ADD COLUMN IF NOT EXISTS is_structural BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_edges_is_structural ON edges(is_structural);
CREATE INDEX IF NOT EXISTS idx_edges_structural_type ON edges(type, is_structural);
