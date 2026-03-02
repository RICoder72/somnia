-- Migration 005: Dream notes on nodes
-- Allows the dream cycle to append observations about pinned nodes
-- without modifying their content or metadata.

ALTER TABLE nodes ADD COLUMN IF NOT EXISTS dream_notes JSONB DEFAULT '[]';
