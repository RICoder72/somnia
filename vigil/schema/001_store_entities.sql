-- Vigil entity store: initial schema
-- Absorbed from Constellation Store
-- Tables: entities, relationships, entity_type_schemas
--
-- NOTE: These tables may already exist (created by Store).
-- All statements use IF NOT EXISTS / OR REPLACE for idempotency.

-- Updated-at trigger function
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Entities table
CREATE TABLE IF NOT EXISTS entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    name        TEXT NOT NULL,
    properties  JSONB NOT NULL DEFAULT '{}',
    archived    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities (domain);
CREATE INDEX IF NOT EXISTS idx_entities_domain_type ON entities (domain, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_properties ON entities USING GIN (properties);
CREATE INDEX IF NOT EXISTS idx_entities_archived ON entities (domain, archived);

-- Trigger (drop first to avoid duplicate errors if Store already created it)
DROP TRIGGER IF EXISTS trg_entities_updated_at ON entities;
CREATE TRIGGER trg_entities_updated_at
    BEFORE UPDATE ON entities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Relationships table
CREATE TABLE IF NOT EXISTS relationships (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain          TEXT NOT NULL,
    source_id       UUID NOT NULL REFERENCES entities(id),
    target_id       UUID NOT NULL REFERENCES entities(id),
    relationship_type TEXT NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (domain, source_id, target_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_relationships_domain ON relationships (domain);
CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships (domain, source_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships (domain, target_id);
CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships (domain, relationship_type);

DROP TRIGGER IF EXISTS trg_relationships_updated_at ON relationships;
CREATE TRIGGER trg_relationships_updated_at
    BEFORE UPDATE ON relationships
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Entity type schemas (per-domain type definitions)
CREATE TABLE IF NOT EXISTS entity_type_schemas (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    schema      JSONB NOT NULL DEFAULT '{}',
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (domain, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_type_schemas_domain ON entity_type_schemas (domain);

DROP TRIGGER IF EXISTS trg_type_schemas_updated_at ON entity_type_schemas;
CREATE TRIGGER trg_type_schemas_updated_at
    BEFORE UPDATE ON entity_type_schemas
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
