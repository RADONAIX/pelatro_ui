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


-- ---------------------------------------------------------------------------
-- Scheduling time-of-day, and the case-raising threshold.
--
-- `execution_time` pairs with `frequency`: the frequency says how often, this
-- says when. Stored as `time` rather than text so the database rejects "25:00"
-- and the scheduler can do arithmetic with it directly.
--
-- `breach_threshold` is how many breached rows a run must produce before a case
-- is raised. It only means anything when the rule's case routing asks for a
-- case; 1 (raise on any breach) preserves what rules did before it existed.
-- ---------------------------------------------------------------------------
ALTER TABLE application_schema.assurance_rule
    ADD COLUMN IF NOT EXISTS execution_time time NOT NULL DEFAULT '00:00';

ALTER TABLE application_schema.assurance_rule
    ADD COLUMN IF NOT EXISTS breach_threshold integer NOT NULL DEFAULT 1;

ALTER TABLE application_schema.assurance_rule
    DROP CONSTRAINT IF EXISTS assurance_rule_breach_threshold_positive;
ALTER TABLE application_schema.assurance_rule
    ADD CONSTRAINT assurance_rule_breach_threshold_positive
    CHECK (breach_threshold >= 1);

-- The compiled definition carries them too: the scheduler reads only this table
-- when deciding what is due, and the engine reads it when deciding whether a
-- finished run warrants a case.
ALTER TABLE application_schema.recon_definition
    ADD COLUMN IF NOT EXISTS execution_time time NOT NULL DEFAULT '00:00';

ALTER TABLE application_schema.recon_definition
    ADD COLUMN IF NOT EXISTS breach_threshold integer NOT NULL DEFAULT 1;

-- Case routing, copied from the rule at compile time so the engine does not
-- have to join back to assurance_rule on every finished run.
ALTER TABLE application_schema.recon_definition
    ADD COLUMN IF NOT EXISTS case_routing jsonb;


-- ---------------------------------------------------------------------------
-- Execution accounting and report storage.
--
-- rows_scanned vs rows_returned: a row-level rule reads a whole table and
-- returns the few rows that breach, and both figures belong on the summary —
-- "12 of 135,686" says something "12" alone does not.
-- ---------------------------------------------------------------------------
ALTER TABLE application_schema.recon_execution
    ADD COLUMN IF NOT EXISTS rows_scanned bigint DEFAULT 0;
ALTER TABLE application_schema.recon_execution
    ADD COLUMN IF NOT EXISTS rows_returned bigint DEFAULT 0;
ALTER TABLE application_schema.recon_execution
    ADD COLUMN IF NOT EXISTS case_raised boolean NOT NULL DEFAULT false;
ALTER TABLE application_schema.recon_execution
    ADD COLUMN IF NOT EXISTS case_reference text;

-- The requested `rule_execution` name, over the table that already holds this
-- data. A view rather than a second table: two tables recording the same runs
-- would drift, and every existing writer and index already points here.
CREATE OR REPLACE VIEW application_schema.rule_execution AS
    SELECT execution_id      AS id,
           rule_id,
           started_at        AS execution_start,
           ended_at          AS execution_end,
           duration_ms,
           rows_scanned,
           rows_returned,
           status,
           started_at        AS created_at,
           trigger_source,
           triggered_by,
           error,
           case_raised,
           case_reference
    FROM application_schema.recon_execution;


-- One row per finished execution: the report's metadata and, for reports small
-- enough to be worth it, the rows themselves.
--
-- report_json is deliberately NOT the storage for a large report. The rows live
-- in the rule's own generated table (assurance.recon_<rule_id>), which is what
-- lets a report of millions of rows exist at all and be paginated by index. A
-- JSONB copy is kept only under recon_report_inline_max_rows so small reports
-- can be served and downloaded without touching the source database.
CREATE TABLE IF NOT EXISTS application_schema.rule_report (
    id            bigserial PRIMARY KEY,
    execution_id  uuid NOT NULL
                  REFERENCES application_schema.recon_execution (execution_id) ON DELETE CASCADE,
    rule_id       text NOT NULL,
    report_json   jsonb,
    report_count  bigint NOT NULL DEFAULT 0,
    -- Where the full result set lives when it is too big to inline.
    output_table  text,
    columns       jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (execution_id)
);

CREATE INDEX IF NOT EXISTS rule_report_rule_created_idx
    ON application_schema.rule_report (rule_id, created_at DESC);


-- Cases raised by a run. `case` is reserved in SQL, so the table is rule_case
-- and the API calls it a case.
CREATE TABLE IF NOT EXISTS application_schema.rule_case (
    id                bigserial PRIMARY KEY,
    rule_execution_id uuid NOT NULL
                      REFERENCES application_schema.recon_execution (execution_id) ON DELETE CASCADE,
    rule_id           text NOT NULL,
    report_id         bigint REFERENCES application_schema.rule_report (id) ON DELETE SET NULL,
    severity          text NOT NULL DEFAULT 'medium',
    status            text NOT NULL DEFAULT 'Open',
    reference         text,
    breached_rows     bigint NOT NULL DEFAULT 0,
    breach_threshold  integer NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rule_case_rule_created_idx
    ON application_schema.rule_case (rule_id, created_at DESC);


-- ---------------------------------------------------------------------------
-- Status rename: RAW_MISSING / PROCESSED_MISSING -> TABLE2_MISSING / TABLE1_MISSING.
--
-- The old names assumed Table 1 is always "processed" and Table 2 always "raw",
-- which is only true of an AIR processed-vs-raw rule. The new ones name the
-- side that has no record, which is true of every rule.
--
-- NOTE the crossover: RAW_MISSING meant "not found in Table 2", so it becomes
-- TABLE2_MISSING — not TABLE1_MISSING. Renaming these the other way round would
-- silently invert every historical count.
-- ---------------------------------------------------------------------------
DO $rename$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'application_schema'
          AND table_name = 'recon_execution'
          AND column_name = 'rows_raw_missing'
    ) THEN
        DROP VIEW IF EXISTS application_schema.rule_execution;
        ALTER TABLE application_schema.recon_execution
            RENAME COLUMN rows_raw_missing TO rows_table2_missing;
        ALTER TABLE application_schema.recon_execution
            RENAME COLUMN rows_processed_missing TO rows_table1_missing;
    END IF;
END
$rename$;

CREATE OR REPLACE VIEW application_schema.rule_execution AS
    SELECT execution_id      AS id,
           rule_id,
           started_at        AS execution_start,
           ended_at          AS execution_end,
           duration_ms,
           rows_scanned,
           rows_returned,
           status,
           started_at        AS created_at,
           trigger_source,
           triggered_by,
           error,
           case_raised,
           case_reference
    FROM application_schema.recon_execution;

-- Rows already written carry the old status strings. They are derived data and
-- a re-run would rewrite them, but a report should not read wrong until then.
DO $restatus$
DECLARE t record;
BEGIN
    FOR t IN
        SELECT c.table_schema, c.table_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'assurance'
          AND c.table_name LIKE 'recon\_%'
          AND c.column_name = 'status'
    LOOP
        EXECUTE format(
            'UPDATE %I.%I SET status = CASE status
                 WHEN ''RAW_MISSING'' THEN ''TABLE2_MISSING''
                 WHEN ''PROCESSED_MISSING'' THEN ''TABLE1_MISSING''
                 ELSE status END
             WHERE status IN (''RAW_MISSING'', ''PROCESSED_MISSING'')',
            t.table_schema, t.table_name);
    END LOOP;
END
$restatus$;

-- The inlined JSONB copies of small reports hold the old strings too.
UPDATE application_schema.rule_report
SET report_json = replace(
        replace(report_json::text, '"RAW_MISSING"', '"TABLE2_MISSING"'),
        '"PROCESSED_MISSING"', '"TABLE1_MISSING"'
    )::jsonb
WHERE report_json::text LIKE '%_MISSING%';


-- Extra source columns an author wants carried into a rule's report, beyond
-- the ones the rule itself uses. JSONB array of column names ("1:col"/"2:col"
-- for a two-table reconciliation).
ALTER TABLE application_schema.assurance_rule
    ADD COLUMN IF NOT EXISTS report_columns jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE application_schema.recon_definition
    ADD COLUMN IF NOT EXISTS report_columns jsonb NOT NULL DEFAULT '[]'::jsonb;
