-- Somnia Schema Migration: Activity Tracking
-- Adds activity log for interaction tracking and dream scheduling

CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,        -- 'recall', 'remember', 'status', 'dream', 'rumination'
    timestamp TEXT NOT NULL,
    metadata TEXT              -- JSON, optional context
);

CREATE INDEX IF NOT EXISTS idx_activity_type ON activity(type);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp);
