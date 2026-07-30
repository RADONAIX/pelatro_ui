"""Configuration for the Rating Assurance service (pydantic-settings, 12-factor).

Deliberately a SEPARATE settings object from ``ra_backend``: this service owns
its own process, its own Postgres schema (``rating``) and its own ClickHouse
database (``rating_assurance``). The only two values that must MATCH the
existing backend are ``jwt_secret`` and ``jwt_algorithm`` — this service
verifies the access tokens that ``ra_backend`` issues, and never mints one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_JWT_SECRETS = {
    "change-me-in-production",
    "dev-only-change-me-to-a-48char-random-secret-000000000000",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General -----------------------------------------------------------
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    project_name: str = "RADONaix Rating Assurance API"
    # Mounted under /api/rating so nginx can longest-prefix match it ahead of
    # the existing /api/ block without touching that block.
    api_prefix: str = "/api/rating"
    log_level: str = "INFO"
    log_json: bool = True

    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8080"

    # --- Auth (verify-only; ra_backend is the issuer) ----------------------
    jwt_secret: str = Field(default="change-me-in-production", min_length=8)
    jwt_algorithm: str = "HS256"

    # --- Rating database (Postgres schema `rating`) ------------------------
    # Same server as the existing app DB by default; a DIFFERENT schema, its own
    # Alembic history, and a connection pool this service alone owns.
    rating_db_host: str = "localhost"
    rating_db_port: int = 5432
    rating_db_name: str = "radonaix_app"
    rating_db_user: str = "radonaix"
    rating_db_password: str = "radonaix"
    rating_db_schema: str = "rating"
    rating_db_pool_size: int = 5
    rating_db_max_overflow: int = 5
    rating_db_echo: bool = False

    # --- Canonical model schema (R1+) --------------------------------------
    # The canonical rule model and the metadata catalogue that rules reference
    # live in ONE schema beside `rating`, which keeps the execution plane (CDRs,
    # runs, results, snapshots) on a different backup and grant profile from the
    # rule metadata that describes it. Same database, same connection: this is a
    # schema name, not a new datasource.
    #
    # The charging / prepaid / postpaid metadata deliberately shares that schema
    # rather than taking three of its own. Every one of those tables exists to be
    # referenced by a rule parameter, is edited on the same screens, is granted to
    # the same role and is restored in the same recovery as the rules themselves —
    # splitting them across ra_catalog / ra_bundle / ra_subscriber would put a
    # schema boundary through the middle of one lifecycle. The separate settings
    # remain so a deployment *can* split them later without a code change.
    rule_db_schema: str = "ra_rule"
    catalog_db_schema: str = "ra_rule"
    bundle_db_schema: str = "ra_rule"
    subscriber_db_schema: str = "ra_rule"

    #: Tenant every pre-multi-tenancy row is stamped with. Until the RLS policy
    #: is tightened (plan step M8) this is the only tenant that exists, so it is
    #: a fixed UUID rather than something a deployment invents — a backfilled row
    #: and a freshly written one must agree.
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # --- Identity bridge (READ-ONLY view of `administration`) --------------
    # Used only to resolve the bearer token's subject to a user + role. The
    # engine is opened with default_transaction_read_only=on, so a write to the
    # existing administration schema is rejected by Postgres itself — the
    # isolation guarantee is enforced by the database, not by convention.
    identity_db_host: str = "localhost"
    identity_db_port: int = 5432
    identity_db_name: str = "radonaix_app"
    identity_db_user: str = "radonaix"
    identity_db_password: str = "radonaix"
    identity_db_schema: str = "administration"
    identity_db_pool_size: int = 3
    identity_db_max_overflow: int = 2
    # Seconds to cache a resolved principal, so a burst of UI calls doesn't
    # hammer the identity DB. 0 disables caching.
    identity_cache_seconds: int = 30

    # --- ClickHouse (execution plane) --------------------------------------
    # Phase 1 does not read or write ClickHouse; the client is wired so Phase 2+
    # (executable rule snapshots) and Phase 3+ (CDRs) have it ready.
    clickhouse_enabled: bool = False
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    # A NEW database, never the existing `rafms`.
    clickhouse_database: str = "rating_assurance"
    clickhouse_max_execution_time: int = 600

    # --- Airflow (execution plane, Phase 3+) -------------------------------
    airflow_enabled: bool = False
    airflow_base_url: str = "http://localhost:8081/api/v1"
    airflow_username: str = "airflow"
    airflow_password: str = "airflow"

    # --- MSC CDR source (READ-ONLY view of the switch landing database) -----
    # The raw MSC records live in a database this service does not own, so the
    # engine is opened with default_transaction_read_only=on: Postgres itself
    # rejects a write, which means a coding mistake cannot corrupt the operator's
    # CDR archive. Disabled by default so nothing changes for a deployment that
    # has not configured a source.
    msc_source_enabled: bool = False
    msc_source_host: str = "localhost"
    msc_source_port: int = 5432
    msc_source_name: str = "rafms_rating"
    msc_source_user: str = "postgres"
    msc_source_password: str = "postgres"
    msc_source_schema: str = "msc_schema"
    #: Comma-separated, so an operator with sm_msc01..sm_msc08 lists them all
    #: without a code change.
    msc_source_tables: str = "sm_msc01"
    msc_source_pool_size: int = 3
    msc_source_max_overflow: int = 2
    #: The home country's dialling code. MSC address fields arrive in national,
    #: international and subscriber formats interchangeably; this is what they
    #: are normalised to, so longest-prefix matching sees one form (§26).
    msc_home_country_code: str = "855"
    #: The national trunk prefix stripped during that normalisation.
    msc_trunk_prefix: str = "0"
    #: The switch's own MCC+MNC as it writes it. A serving network other than
    #: this one means the subscriber was roaming.
    msc_home_mccmnc: str = "54F660"
    #: Rows pulled per round trip when streaming the source table.
    msc_source_fetch_size: int = 5_000

    # --- OCS / IN actual-charge source (READ-ONLY) -------------------------
    # MSC CDRs carry no billed amount, so the actual charge must be correlated
    # from the online charging system. Until a source is configured every result
    # is classified NO_ACTUAL_CHARGE rather than silently compared against zero.
    ocs_source_enabled: bool = False
    ocs_source_host: str = "localhost"
    ocs_source_port: int = 5432
    ocs_source_name: str = "rafms_rating"
    ocs_source_user: str = "postgres"
    ocs_source_password: str = "postgres"
    ocs_source_schema: str = "ocs_schema"
    ocs_source_table: str = "ocs_charges"
    ocs_source_pool_size: int = 3
    ocs_source_max_overflow: int = 2
    #: Column names on the OCS table, so a differently-named source is wired in
    #: .env rather than in code.
    ocs_column_msisdn: str = "msisdn"
    ocs_column_called_number: str = "called_number"
    ocs_column_event_time: str = "event_start_time"
    ocs_column_duration: str = "duration_seconds"
    ocs_column_call_reference: str = "call_reference"
    ocs_column_charge: str = "charged_amount"
    ocs_column_currency: str = "currency"

    # --- Rating assurance execution (§33) ----------------------------------
    # Every one of these is a tuning decision an operator makes per environment,
    # so none of them are constants inside a service.
    rating_batch_size: int = 5_000
    maximum_records_per_run: int = 1_000_000
    #: Below this absolute difference a variance is rounding noise, not leakage.
    absolute_variance_tolerance: float = 0.005
    #: Applied in addition to the absolute tolerance; either one clearing marks
    #: the record MATCHED, so a large charge is not flagged for a 0.006 rounding
    #: difference and a tiny charge is not flagged for a 1% one.
    percentage_variance_tolerance: float = 0.5
    default_currency_scale: int = 2
    default_currency: str = "GHS"
    #: How far apart an MSC event and an OCS charge may be and still correlate.
    actual_charge_time_tolerance_seconds: int = 5
    maximum_retry_count: int = 3

    # --- Rule engine limits ------------------------------------------------
    max_conditions_per_rule: int = 50
    max_actions_per_rule: int = 25
    # Guard rail for the requirement's "10,000+ rules" target: a single rule set
    # beyond this is a modelling smell, and compiles get pathological.
    max_rules_per_rule_set: int = 20_000

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rating_database_url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.rating_db_user,
                password=self.rating_db_password,
                host=self.rating_db_host,
                port=self.rating_db_port,
                path=self.rating_db_name,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rating_database_url_sync(self) -> str:
        """psycopg2 URL — Alembic runs synchronously."""
        return self.rating_database_url.replace("postgresql+asyncpg", "postgresql+psycopg2")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def identity_database_url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.identity_db_user,
                password=self.identity_db_password,
                host=self.identity_db_host,
                port=self.identity_db_port,
                path=self.identity_db_name,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def msc_source_url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.msc_source_user,
                password=self.msc_source_password,
                host=self.msc_source_host,
                port=self.msc_source_port,
                path=self.msc_source_name,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def msc_source_table_list(self) -> list[str]:
        return [t.strip() for t in self.msc_source_tables.split(",") if t.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ocs_source_url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.ocs_source_user,
                password=self.ocs_source_password,
                host=self.ocs_source_host,
                port=self.ocs_source_port,
                path=self.ocs_source_name,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_secret_is_insecure(self) -> bool:
        return self.jwt_secret in _INSECURE_JWT_SECRETS


settings = Settings()
