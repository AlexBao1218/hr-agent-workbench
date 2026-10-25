-- Explicit operator bootstrap. Run once with the schema-owning server role.
-- Connect from backend over TLS using direct/session pooling (not transaction pooling).
-- Keep hr_private out of the Supabase Data API exposed schemas. Never grant browser roles access.
BEGIN;
CREATE SCHEMA IF NOT EXISTS hr_private;
REVOKE ALL ON SCHEMA hr_private FROM PUBLIC;
CREATE TABLE IF NOT EXISTS hr_private.workspace (
    id integer PRIMARY KEY CHECK (id = 1),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0)
);
-- Application initializes a blank workspace only if this table has no id=1 row.
CREATE TABLE IF NOT EXISTS hr_private.diagnostics (
    reference text PRIMARY KEY,
    content bytea NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    run_id text NOT NULL
);
CREATE INDEX IF NOT EXISTS diagnostics_run_id ON hr_private.diagnostics(run_id);
CREATE TABLE IF NOT EXISTS hr_private.model_calls (
    id text PRIMARY KEY,
    role text NOT NULL,
    run_id text,
    status text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    cache_hit_tokens bigint CHECK (cache_hit_tokens >= 0),
    cache_miss_tokens bigint CHECK (cache_miss_tokens >= 0)
);
CREATE INDEX IF NOT EXISTS model_calls_run_id ON hr_private.model_calls(run_id);
CREATE TABLE IF NOT EXISTS hr_private.model_budget_guard (
    id integer PRIMARY KEY CHECK (id = 1)
);
INSERT INTO hr_private.model_budget_guard(id) VALUES (1) ON CONFLICT DO NOTHING;
REVOKE ALL ON ALL TABLES IN SCHEMA hr_private FROM PUBLIC;
-- Supabase built-in browser roles exist there, but may not exist on ordinary PostgreSQL.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON SCHEMA hr_private FROM anon;
        REVOKE ALL ON ALL TABLES IN SCHEMA hr_private FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON SCHEMA hr_private FROM authenticated;
        REVOKE ALL ON ALL TABLES IN SCHEMA hr_private FROM authenticated;
    END IF;
END $$;
COMMIT;
