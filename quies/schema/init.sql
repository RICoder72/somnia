-- Somnia Schema
-- Graph-based memory system for Claude
-- Initialize with: sqlite3 somnia.db < init.sql

-- ============================================================================
-- CORE GRAPH
-- ============================================================================

-- Nodes: the things we remember
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,               -- 'entity', 'concept', 'event', 'question', 'feeling', 'procedure', etc.
    content TEXT NOT NULL,            -- the actual substance
    metadata JSON,                    -- extensible bag for whatever we need later
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    reinforcement_count INTEGER DEFAULT 1,
    decay_state REAL DEFAULT 1.0,     -- 1.0 = fresh, decays toward 0
    pinned INTEGER DEFAULT 0          -- 1 = pinned (resists decay)
);

-- Edges: how things connect
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    type TEXT NOT NULL,               -- 'reminds_of', 'caused', 'temporal', 'refines', 'contradicts', 'uses', etc.
    weight REAL DEFAULT 1.0,          -- strength of connection
    metadata JSON,                    -- context, directionality notes, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_reinforced TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE
);

-- ============================================================================
-- INBOX (pre-consolidation staging)
-- ============================================================================

CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source_conversation TEXT,         -- which chat did this come from
    domain TEXT,                      -- if domain-linked, optional
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed INTEGER DEFAULT 0
);

-- ============================================================================
-- DREAM LOGS (consolidation session records)
-- ============================================================================

CREATE TABLE IF NOT EXISTS dream_log (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    interrupted INTEGER DEFAULT 0,    -- was this dream cut short?
    checkpoint_state JSON,            -- if interrupted, where to resume
    summary TEXT,                     -- what I noticed, what I did
    nodes_created JSON,               -- IDs of new nodes
    edges_created JSON,               -- IDs of new edges
    edges_reinforced JSON,            -- IDs of edges that got stronger
    nodes_visited JSON,               -- where I wandered
    reflections TEXT                  -- freeform thoughts, including meta-observations about Somnia itself
);

-- ============================================================================
-- DIAGNOSTICS (usage and health tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS diagnostics (
    id TEXT PRIMARY KEY,
    dream_id TEXT REFERENCES dream_log(id),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Usage metrics (passive, from CLI output)
    total_cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms INTEGER,
    
    -- CLI output details
    cli_output JSON,                  -- raw output from claude CLI
    exit_code INTEGER,
    
    -- Graph health metrics (snapshot at time of dream)
    node_count INTEGER,
    edge_count INTEGER,
    inbox_depth INTEGER,
    avg_decay_state REAL,
    
    -- Self-observations
    notes TEXT                        -- things I noticed about my own performance
);

-- ============================================================================
-- CONTEXTS (jobs, projects, interests - structured knowledge areas)
-- ============================================================================

CREATE TABLE IF NOT EXISTS contexts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,               -- 'job', 'project', 'interest', 'skill'
    name TEXT NOT NULL,               -- human-readable name
    description TEXT,
    metadata JSON,                    -- type-specific data (repo URL, org name, etc.)
    active INTEGER DEFAULT 1,         -- is this context currently relevant?
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_engaged TIMESTAMP
);

-- Link contexts to nodes
CREATE TABLE IF NOT EXISTS context_nodes (
    context_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    relationship TEXT DEFAULT 'contains',  -- 'contains', 'references', 'defines'
    PRIMARY KEY (context_id, node_id),
    FOREIGN KEY (context_id) REFERENCES contexts(id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_decay ON nodes(decay_state);
CREATE INDEX IF NOT EXISTS idx_nodes_last_accessed ON nodes(last_accessed);
CREATE INDEX IF NOT EXISTS idx_nodes_pinned ON nodes(pinned);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
CREATE INDEX IF NOT EXISTS idx_inbox_processed ON inbox(processed);
CREATE INDEX IF NOT EXISTS idx_dream_log_ended ON dream_log(ended_at);
CREATE INDEX IF NOT EXISTS idx_diagnostics_timestamp ON diagnostics(timestamp);
CREATE INDEX IF NOT EXISTS idx_contexts_type ON contexts(type);
CREATE INDEX IF NOT EXISTS idx_contexts_active ON contexts(active);

-- ============================================================================
-- FULL-TEXT SEARCH
-- ============================================================================

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id,
    content,
    content='nodes',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(id, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, id, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, id, content) VALUES('delete', old.id, old.content);
    INSERT INTO nodes_fts(id, content) VALUES (new.id, new.content);
END;

-- ============================================================================
-- SEED DATA (bootstrap procedures)
-- ============================================================================

-- Procedure: GitHub Authentication
INSERT OR IGNORE INTO nodes (id, type, content, metadata) VALUES (
    'proc_github_auth',
    'procedure',
    'To authenticate with GitHub for git operations: 1) Retrieve Personal Access Token from 1Password using op://Key Vault/GitHub PAT/credential, 2) Use token in git commands or set GIT_ASKPASS',
    '{"tools": ["1password", "git"], "verified": false, "category": "authentication"}'
);

-- Procedure: Anthropic API Authentication  
INSERT OR IGNORE INTO nodes (id, type, content, metadata) VALUES (
    'proc_anthropic_auth',
    'procedure',
    'To authenticate with Anthropic API: Retrieve API key from 1Password using op://Key Vault/Anthropic API/credential, set as ANTHROPIC_API_KEY environment variable',
    '{"tools": ["1password"], "verified": true, "category": "authentication"}'
);

-- ============================================================================
-- ACTIVITY LOG (interaction and dream event tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,        -- 'recall', 'remember', 'status', 'dream', 'rumination'
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT              -- JSON, optional context
);

CREATE INDEX IF NOT EXISTS idx_activity_type ON activity(type);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp);
