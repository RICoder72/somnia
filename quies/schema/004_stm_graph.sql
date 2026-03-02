-- Somnia Schema Migration: Short-Term Memory Graph
-- Replaces flat inbox with structured STM nodes + edges
-- STM is searchable by recall, processed by dream cycle

-- STM nodes: raw observations, pre-consolidation
CREATE TABLE IF NOT EXISTS stm_nodes (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    domain TEXT,
    source TEXT,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stm_nodes_captured ON stm_nodes(captured_at);
CREATE INDEX IF NOT EXISTS idx_stm_nodes_domain ON stm_nodes(domain);

-- STM edges: connections between STM nodes (for future compound observations)
CREATE TABLE IF NOT EXISTS stm_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'related',
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES stm_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES stm_nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stm_edges_source ON stm_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_stm_edges_target ON stm_edges(target_id);

-- Full-text search for STM nodes (searched by somnia_recall)
CREATE VIRTUAL TABLE IF NOT EXISTS stm_nodes_fts USING fts5(
    id,
    content,
    content='stm_nodes',
    content_rowid='rowid'
);

-- Triggers to keep STM FTS in sync
CREATE TRIGGER IF NOT EXISTS stm_nodes_ai AFTER INSERT ON stm_nodes BEGIN
    INSERT INTO stm_nodes_fts(id, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS stm_nodes_ad AFTER DELETE ON stm_nodes BEGIN
    INSERT INTO stm_nodes_fts(stm_nodes_fts, id, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS stm_nodes_au AFTER UPDATE ON stm_nodes BEGIN
    INSERT INTO stm_nodes_fts(stm_nodes_fts, id, content) VALUES('delete', old.id, old.content);
    INSERT INTO stm_nodes_fts(id, content) VALUES (new.id, new.content);
END;
