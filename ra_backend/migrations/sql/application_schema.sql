-- Authored assurance rules.
--
-- Lives in rafms_rating on the RA Postgres host, in its own schema, so a rule a
-- user writes in the Rule Explorer survives an app-database reset and is
-- readable by anything else pointed at rafms_rating.
--
-- Idempotent: safe to re-run. Applied by hand (psql -f) rather than Alembic —
-- Alembic owns the app database (radonaix_app), not this one.

CREATE SCHEMA IF NOT EXISTS application_schema;

CREATE TABLE IF NOT EXISTS application_schema.assurance_rule (
    -- Human-facing control id, e.g. BA901. Unique across all assurances, and
    -- what the Controls table and Case Management both show.
    id              text PRIMARY KEY,

    -- The assurance this rule belongs to ("billing", "usage", "rating", …).
    -- Every read is filtered on it: Billing Assurance must only ever see
    -- billing rules. Indexed because that filter is on the hot path.
    assurance_id    text        NOT NULL,

    name            text        NOT NULL,
    description     text        NOT NULL DEFAULT '',
    category        text        NOT NULL,
    entity          text        NOT NULL,
    severity        text        NOT NULL DEFAULT 'medium',
    frequency       text        NOT NULL DEFAULT 'Daily',
    state           text        NOT NULL DEFAULT 'Draft',

    -- The category-specific parameter contract (CATEGORY_PARAMS in the UI).
    -- JSONB rather than columns: each category asks for a different set, and a
    -- new category must not need a migration.
    params          jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Present only when the author opted into raising a case.
    case_routing    jsonb,

    -- Present only for the two-table comparison categories (Reconciliation).
    comparison      jsonb,

    created_by      text        NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS assurance_rule_assurance_idx
    ON application_schema.assurance_rule (assurance_id);

-- Listing is newest-first within an assurance.
CREATE INDEX IF NOT EXISTS assurance_rule_assurance_created_idx
    ON application_schema.assurance_rule (assurance_id, created_at DESC);

-- updated_at maintained by the database so it cannot drift when a caller
-- forgets to set it.
CREATE OR REPLACE FUNCTION application_schema.touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS assurance_rule_touch_updated_at ON application_schema.assurance_rule;
CREATE TRIGGER assurance_rule_touch_updated_at
    BEFORE UPDATE ON application_schema.assurance_rule
    FOR EACH ROW EXECUTE FUNCTION application_schema.touch_updated_at();


-- ---------------------------------------------------------------------------
-- Compiled reconciliation definitions.
--
-- One row per Reconciliation rule: the metadata the compiler resolved, the SQL
-- it generated, where the output lives, and when it next runs. The scheduler
-- reads nothing but this table, so a recurring run needs no recompilation.
--
-- Separate from assurance_rule rather than more columns on it: only one rule
-- category compiles to an executable, and the compiled artefacts (DDL, DML,
-- output table, run state) are meaningless for the other fourteen.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS application_schema.recon_definition (
    rule_id         text PRIMARY KEY
                    REFERENCES application_schema.assurance_rule (id) ON DELETE CASCADE,
    assurance_id    text        NOT NULL,

    -- Both source tables must live in one database: the reconciliation runs as
    -- a single server-side INSERT ... SELECT, which cannot cross databases.
    source_database text        NOT NULL,
    left_schema     text        NOT NULL,
    left_table      text        NOT NULL,
    right_schema    text        NOT NULL,
    right_table     text        NOT NULL,

    -- [{"left": "...", "right": "..."}] — resolved and type-checked at compile
    -- time against information_schema, never trusted from the request.
    join_keys       jsonb       NOT NULL,
    metrics         jsonb       NOT NULL,

    -- Numeric tolerance (percent). NULL/0 = exact comparison.
    tolerance_pct   numeric,

    frequency       text        NOT NULL DEFAULT 'Daily',
    severity        text        NOT NULL DEFAULT 'medium',

    output_schema   text        NOT NULL,
    output_table    text        NOT NULL,
    generated_ddl   text        NOT NULL,
    generated_sql   text        NOT NULL,

    -- What the Reports menu shows for this rule.
    report_key      text        NOT NULL UNIQUE,
    report_title    text        NOT NULL,

    status          text        NOT NULL DEFAULT 'Pending',  -- Pending|Ready|Failed
    last_error      text,
    last_run_at     timestamptz,
    next_run_at     timestamptz,
    last_execution_id uuid,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS recon_definition_assurance_idx
    ON application_schema.recon_definition (assurance_id);

-- The scheduler's only query: due rules, cheapest possible. Partial so the
-- index holds just the rows it scans.
CREATE INDEX IF NOT EXISTS recon_definition_due_idx
    ON application_schema.recon_definition (next_run_at)
    WHERE status = 'Ready';

DROP TRIGGER IF EXISTS recon_definition_touch_updated_at ON application_schema.recon_definition;
CREATE TRIGGER recon_definition_touch_updated_at
    BEFORE UPDATE ON application_schema.recon_definition
    FOR EACH ROW EXECUTE FUNCTION application_schema.touch_updated_at();


-- ---------------------------------------------------------------------------
-- Execution audit. One row per run, whatever the outcome — a failed run must
-- leave a record with its error, not vanish.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS application_schema.recon_execution (
    execution_id    uuid PRIMARY KEY,
    rule_id         text        NOT NULL
                    REFERENCES application_schema.recon_definition (rule_id) ON DELETE CASCADE,
    trigger_source  text        NOT NULL,          -- create|manual|schedule
    status          text        NOT NULL,          -- Running|Succeeded|Failed
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    duration_ms     integer,

    rows_total              bigint DEFAULT 0,
    rows_match              bigint DEFAULT 0,
    rows_mismatch           bigint DEFAULT 0,
    rows_raw_missing        bigint DEFAULT 0,
    rows_processed_missing  bigint DEFAULT 0,

    error           text,
    triggered_by    text NOT NULL DEFAULT ''
);

-- History for one rule, newest first — the executions panel and the "latest
-- execution" lookup the report view does on every open.
CREATE INDEX IF NOT EXISTS recon_execution_rule_started_idx
    ON application_schema.recon_execution (rule_id, started_at DESC);


-- ---------------------------------------------------------------------------
-- Single-table rules (Sequence, Duplicate).
--
-- They share recon_definition rather than getting their own table: the
-- lifecycle is identical — compile once, store the SQL, run on a schedule,
-- publish a report — and only the shape of the generated SQL differs. `kind`
-- selects the generator; `options` carries whatever that generator needs
-- (which column carries the counter, which digit group inside it, what to
-- partition by).
--
-- For a single-table rule the right_* columns repeat the left table: there is
-- only one side, and repeating it keeps the NOT NULLs honest without a second
-- nullable pair of columns that only one kind would ever use.
-- ---------------------------------------------------------------------------
ALTER TABLE application_schema.recon_definition
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'reconciliation';

ALTER TABLE application_schema.recon_definition
    ADD COLUMN IF NOT EXISTS options jsonb NOT NULL DEFAULT '{}'::jsonb;
