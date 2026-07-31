"""Core `Table` definitions for the ten `canonical_rating` targets.

Deliberately SQLAlchemy Core on a **standalone `MetaData`**, not ORM models on
``app.core.database.Base``. Two reasons, both load-bearing:

* Anything registered on ``Base.metadata`` is picked up by Alembic autogenerate
  and by the test bootstrap's ``create_all``. Mirror tables landing there would
  make the next migration try to create `canonical_rating` inside the primary
  database — a behavioural change, which this feature is not allowed to have.
* The mirror only ever inserts and upserts. It never navigates a relationship,
  never lazy-loads, never needs identity-map semantics. Core says exactly that.

Column types and lengths mirror the supplied DDL exactly. Where the DDL has a
CHECK constraint the values are enforced in ``valuemaps.py`` before a row is
built, so a violation surfaces as a skipped-and-logged row rather than as a
failed statement.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

from app.core.config import settings

#: Its own MetaData — never `Base.metadata`.
metadata = MetaData(schema=settings.mirror_db_schema)


# --- Vocabulary --------------------------------------------------------------

rule_attribute = Table(
    "rule_attribute", metadata,
    Column("attribute_name", String(100), primary_key=True),
    Column("attribute_description", String(500), nullable=False),
    Column("data_type", String(30), nullable=False),
    Column("source_entity", String(100)),
    Column("is_runtime_attribute", Boolean, nullable=False, default=True),
    Column("status", String(20), nullable=False, default="ACTIVE"),
)

rule_operator = Table(
    "rule_operator", metadata,
    Column("operator_code", String(30), primary_key=True),
    Column("operator_name", String(100), nullable=False),
    Column("description", String(500)),
    Column("supported_data_types", String(200)),
    Column("status", String(20), nullable=False, default="ACTIVE"),
)

rule_action_type = Table(
    "rule_action_type", metadata,
    Column("action_type", String(50), primary_key=True),
    Column("action_name", String(150), nullable=False),
    Column("rule_stage", String(30), nullable=False),
    Column("handler_name", String(150)),
    Column("description", String(500)),
    Column("status", String(20), nullable=False, default="ACTIVE"),
)


# --- Rules -------------------------------------------------------------------

rating_rule = Table(
    "rating_rule", metadata,
    Column("rule_id", String(50), primary_key=True),
    Column("rule_name", String(250), nullable=False),
    Column("rule_description", String(1000)),
    Column("rule_stage", String(30), nullable=False),
    Column("rule_type", String(50), nullable=False),
    Column("priority", Integer, nullable=False, default=100),
    Column("match_strategy", String(30), nullable=False, default="FIRST_MATCH"),
    Column("currency_code", CHAR(3)),
    Column("account_scope", String(20), nullable=False, default="BOTH"),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("version_no", Integer, nullable=False, default=1),
    Column("status", String(20), nullable=False, default="DRAFT"),
    Column("source_system_code", String(50)),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("approved_by", String(100)),
    Column("approved_at", DateTime(timezone=True)),
)

rating_rule_condition = Table(
    "rating_rule_condition", metadata,
    Column("condition_id", String(50), primary_key=True),
    Column("rule_id", String(50), nullable=False),
    Column("condition_group", Integer, nullable=False, default=1),
    Column("sequence_no", Integer, nullable=False),
    Column("attribute_name", String(100), nullable=False),
    Column("operator_code", String(30), nullable=False),
    Column("value_type", String(30), nullable=False),
    Column("comparison_value", Text),
    Column("comparison_value_to", Text),
    Column("case_sensitive", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True)),
)

rating_rule_action = Table(
    "rating_rule_action", metadata,
    Column("action_id", String(50), primary_key=True),
    Column("rule_id", String(50), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("action_type", String(50), nullable=False),
    Column("parameter_name", String(100), nullable=False),
    Column("parameter_value", Text, nullable=False),
    Column("value_type", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True)),
)


# --- Metadata catalogue ------------------------------------------------------

destination_prefix = Table(
    "destination_prefix", metadata,
    # GENERATED ALWAYS AS IDENTITY — never supplied by the mirror.
    Column("destination_prefix_id", BigInteger, primary_key=True),
    Column("prefix", String(40), nullable=False),
    Column("zone_code", String(50), nullable=False),
    Column("destination_type", String(50), nullable=False),
    Column("country_code", String(10)),
    Column("operator_code", String(50)),
    Column("priority", Integer, nullable=False, default=100),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("status", String(20), nullable=False, default="ACTIVE"),
)

time_band = Table(
    "time_band", metadata,
    Column("time_band_id", BigInteger, primary_key=True),
    Column("time_band_code", String(50), nullable=False),
    Column("time_band_name", String(150), nullable=False),
    Column("day_type", String(30), nullable=False),
    Column("start_second", Integer, nullable=False),
    Column("end_second", Integer, nullable=False),
    Column("timezone_name", String(100), nullable=False),
    Column("priority", Integer, nullable=False, default=100),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("status", String(20), nullable=False, default="ACTIVE"),
)


# --- Subscriber plane (gated behind `mirror_subscriber_enabled`) -------------

subscriber_offer = Table(
    "subscriber_offer", metadata,
    Column("subscriber_offer_id", BigInteger, primary_key=True),
    Column("subscriber_id", String(50), nullable=False),
    Column("offer_id", String(50), nullable=False),
    Column("status", String(20), nullable=False, default="ACTIVE"),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("priority", Integer, nullable=False, default=100),
)

subscriber_bundle_balance = Table(
    "subscriber_bundle_balance", metadata,
    Column("balance_id", BigInteger, primary_key=True),
    Column("subscriber_id", String(50), nullable=False),
    Column("bundle_code", String(50), nullable=False),
    Column("balance_type", String(50), nullable=False),
    Column("initial_balance", Numeric(20, 6), nullable=False),
    Column("remaining_balance", Numeric(20, 6), nullable=False),
    Column("unit_of_measure", String(30), nullable=False),
    Column("priority", Integer, nullable=False, default=100),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_to", DateTime(timezone=True)),
    Column("status", String(20), nullable=False, default="ACTIVE"),
    Column("last_updated_at", DateTime(timezone=True)),
)
