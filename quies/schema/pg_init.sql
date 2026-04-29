-- Somnia PostgreSQL Schema
-- Graph-based memory system for Claude
-- All tables in 'public' schema of the 'somnia' database
-- FTS uses GENERATED ALWAYS tsvector columns (no triggers)

-- ============================================================================
-- CORE GRAPH
-- ============================================================================

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed TIMESTAMPTZ,
    reinforcement_count INTEGER DEFAULT 1,
    decay_state REAL DEFAULT 1.0,
    pinned BOOLEAN DEFAULT FALSE,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, '') || ' ' || coalesce(replace(id, '-', ' '), '') || ' ' || coalesce(type, ''))) STORED
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_reinforced TIMESTAMPTZ
);

-- ============================================================================
-- SHORT-TERM MEMORY (STM)
-- ============================================================================

CREATE TABLE IF NOT EXISTS stm_nodes (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    domain TEXT,
    source TEXT,
    captured_at TIMESTAMPTZ DEFAULT NOW(),
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, '') || ' ' || coalesce(replace(id, '-', ' '), ''))) STORED
);

CREATE TABLE IF NOT EXISTS stm_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES stm_nodes(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES stm_nodes(id) ON DELETE CASCADE,
    type TEXT NOT NULL DEFAULT 'related',
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- INBOX
-- ============================================================================

CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source_conversation TEXT,
    domain TEXT,
    captured_at TIMESTAMPTZ DEFAULT NOW(),
    processed BOOLEAN DEFAULT FALSE
);

-- ============================================================================
-- DREAM LOGS
-- ============================================================================

CREATE TABLE IF NOT EXISTS dream_log (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    interrupted BOOLEAN DEFAULT FALSE,
    mode TEXT DEFAULT 'process',
    checkpoint_state JSONB,
    summary TEXT,
    nodes_created JSONB DEFAULT '[]',
    edges_created JSONB DEFAULT '[]',
    edges_reinforced JSONB DEFAULT '[]',
    nodes_visited JSONB DEFAULT '[]',
    reflections TEXT
);

-- ============================================================================
-- DIAGNOSTICS
-- ============================================================================

CREATE TABLE IF NOT EXISTS diagnostics (
    id TEXT PRIMARY KEY,
    dream_id TEXT REFERENCES dream_log(id),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    total_cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms INTEGER,
    cli_output JSONB,
    exit_code INTEGER,
    node_count INTEGER,
    edge_count INTEGER,
    inbox_depth INTEGER,
    avg_decay_state REAL,
    notes TEXT
);

-- ============================================================================
-- CONTEXTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS contexts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    metadata JSONB DEFAULT '{}',
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_engaged TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS context_nodes (
    context_id TEXT NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relationship TEXT DEFAULT 'contains',
    PRIMARY KEY (context_id, node_id)
);

-- ============================================================================
-- ACTIVITY LOG
-- ============================================================================

CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB
);

-- ============================================================================
-- MIGRATIONS TRACKER
-- ============================================================================

CREATE TABLE IF NOT EXISTS _somnia_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Nodes
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_decay ON nodes(decay_state);
CREATE INDEX IF NOT EXISTS idx_nodes_last_accessed ON nodes(last_accessed);
CREATE INDEX IF NOT EXISTS idx_nodes_pinned ON nodes(pinned);
CREATE INDEX IF NOT EXISTS idx_nodes_search ON nodes USING GIN(search_vector);

-- Edges
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);

-- STM nodes
CREATE INDEX IF NOT EXISTS idx_stm_nodes_captured ON stm_nodes(captured_at);
CREATE INDEX IF NOT EXISTS idx_stm_nodes_domain ON stm_nodes(domain);
CREATE INDEX IF NOT EXISTS idx_stm_nodes_search ON stm_nodes USING GIN(search_vector);

-- STM edges
CREATE INDEX IF NOT EXISTS idx_stm_edges_source ON stm_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_stm_edges_target ON stm_edges(target_id);

-- Dream log
CREATE INDEX IF NOT EXISTS idx_dream_log_ended ON dream_log(ended_at);
CREATE INDEX IF NOT EXISTS idx_dream_log_mode ON dream_log(mode);

-- Diagnostics
CREATE INDEX IF NOT EXISTS idx_diagnostics_timestamp ON diagnostics(timestamp);
CREATE INDEX IF NOT EXISTS idx_diagnostics_dream ON diagnostics(dream_id);

-- Contexts
CREATE INDEX IF NOT EXISTS idx_contexts_type ON contexts(type);
CREATE INDEX IF NOT EXISTS idx_contexts_active ON contexts(active);

-- Activity
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity(type);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp);
