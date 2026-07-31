-- Create the role the rating service must connect as for tenant isolation to work.
--
-- WHY THIS FILE EXISTS
--
-- Migration 0014 creates correct row-level security policies on every
-- tenant-scoped table, enables RLS, and adds FORCE ROW LEVEL SECURITY. All of
-- that is inert if the service connects as a superuser: Postgres exempts
-- superusers from RLS unconditionally, and FORCE only closes the table-*owner*
-- exemption.
--
-- The failure mode is the dangerous kind. Every table reports
-- relrowsecurity = true, every policy is present and correct, a schema review
-- passes — and one tenant can read another tenant's tariffs. Nothing errors.
--
-- Run this once per environment, as a superuser, then point RATING_DB_USER at
-- the role it creates and restart the service. The startup log will say
-- `tenant_isolation_enforced`; if it says `tenant_isolation_not_enforced`, the
-- service is still connecting as the wrong role.
--
--     psql -v app_database=radonaix_app \
--          -v app_password='a-secret-from-your-secret-store' \
--          -f deploy/postgres/tenant-isolation.sql
--
-- Safe to re-run.

\if :{?app_role}
\else
\set app_role 'radonaix_rating'
\endif
\if :{?app_password}
\else
\warn 'app_password is required; pass -v app_password=...'
\quit
\endif
\if :{?app_database}
\else
\set app_database 'radonaix_app'
\endif

BEGIN;

-- 1. The role. `\gexec` is intentional: psql does not substitute variables
--    inside a dollar-quoted DO body. Generate CREATE only when it is absent,
--    then make every security-sensitive attribute explicit on every re-run.
SELECT format(
           'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS '
           'NOCREATEDB NOCREATEROLE INHERIT',
           :'app_role', :'app_password'
       )
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec

ALTER ROLE :"app_role"
    WITH LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT;

-- 2. Connect and schema usage.
GRANT CONNECT ON DATABASE :"app_database" TO :"app_role";
GRANT USAGE ON SCHEMA rating, ra_rule TO :"app_role";

-- 3. Data rights on both schemas. The service reads and writes the rating
--    execution plane and the canonical rule model; it owns neither, which is
--    what keeps the owner exemption irrelevant.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA rating, ra_rule TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA rating, ra_rule TO :"app_role";

-- 4. And on whatever a future migration adds, so this file does not have to be
--    re-run after every deploy.
ALTER DEFAULT PRIVILEGES IN SCHEMA rating, ra_rule
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA rating, ra_rule
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";

-- 5. The read-only identity bridge. The service resolves a bearer token's
--    subject here and must never write: the engine is opened with
--    default_transaction_read_only, and this grant makes the database agree.
GRANT USAGE ON SCHEMA administration TO :"app_role";
GRANT SELECT ON administration.users, administration.user_sessions TO :"app_role";

COMMIT;

-- 6. Verify. Both must be false, or isolation is not in force.
SELECT rolname,
       rolsuper     AS is_superuser,
       rolbypassrls AS bypasses_rls
  FROM pg_roles
 WHERE rolname = :'app_role';

-- 7. And every tenant-scoped table must be forced, not merely enabled.
SELECT count(*) FILTER (WHERE relrowsecurity)      AS enabled,
       count(*) FILTER (WHERE relforcerowsecurity) AS forced,
       count(*)                                    AS tenant_scoped_tables
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'ra_rule'
   AND c.relkind = 'r'
   AND EXISTS (
       SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND a.attnum > 0
   );
