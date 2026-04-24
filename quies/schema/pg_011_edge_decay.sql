-- pg_011_edge_decay.sql
-- Add edge decay support: flagged_for_review column, backfill last_reinforced

-- Column for edge decay review flagging
ALTER TABLE edges ADD COLUMN IF NOT EXISTS flagged_for_review BOOLEAN DEFAULT FALSE;

-- Backfill last_reinforced from created_at for edges that have never been reinforced.
-- This gives a sane baseline — edges start their decay window from creation date.
UPDATE edges SET last_reinforced = created_at WHERE last_reinforced IS NULL;

-- Record migration
INSERT INTO _somnia_migrations (filename, applied_at)
VALUES ('pg_011_edge_decay.sql', NOW());
