"""``ra_rule.rule_ingestion_record`` — every incoming record, kept verbatim.

Additive. One new table, hash-partitioned on ``batch_id`` into eight partitions.

**Why partitioned, and why now.** This is by a wide margin the largest table in
the schema: a nightly 40,000-row vendor dump, retained for forensics, is 40,000
rows a night against a few thousand rules. Repartitioning a populated table means
a full rewrite with an exclusive lock, so the partitioning is established before
anything writes rather than retrofitted once it hurts. Eight partitions is enough
that no single one dominates and few enough that a planner scan across all of
them stays cheap; the count is fixed at creation because changing it later means
redistributing every row.

**Why the primary key is composite.** Postgres requires the partition key to
appear in every unique constraint on a partitioned table, so the key is
``(record_id, batch_id)`` rather than the bare ``record_id``.

**Why there are no foreign keys to `rule`.** A record must outlive the rule it
created. A rule deleted as a mistaken draft should leave its evidence behind,
which is the opposite of what a cascade does.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None

RULE = "ra_rule"
TABLE = "rule_ingestion_record"
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

#: Fixed at creation. Changing it later redistributes every row, so it is a
#: decision taken once rather than a tunable.
PARTITIONS = 8


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("record_id", sa.String(36), nullable=False),
        # In the primary key because Postgres requires the partition key there.
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False,
                  server_default=DEFAULT_TENANT),
        # Position in the source file, so a reject report can say "row 4,182"
        # and an operator can find it in the file they sent.
        sa.Column("source_offset", sa.Integer, nullable=False),
        sa.Column("external_ref", sa.String(255), nullable=True),
        # The record verbatim. Written before anything is interpreted, so a parse
        # that dies at row 12,000 still leaves 12,000 payloads to look at.
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("canonical_payload", JSONB, nullable=True),
        # NEW | CHANGED | UNCHANGED | WITHDRAWN | REJECTED | QUARANTINED
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=True),
        sa.Column("rule_version_id", sa.String(36), nullable=True),
        sa.Column("issues", JSONB, server_default="[]", nullable=False),
        sa.Column("reason", sa.Text, server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("record_id", "batch_id", name=f"pk_{TABLE}"),
        sa.UniqueConstraint("batch_id", "source_offset",
                            name="uq_ingestion_record_offset"),
        schema=RULE,
        postgresql_partition_by="HASH (batch_id)",
    )

    for n in range(PARTITIONS):
        op.execute(
            f"CREATE TABLE {RULE}.{TABLE}_p{n} PARTITION OF {RULE}.{TABLE} "
            f"FOR VALUES WITH (MODULUS {PARTITIONS}, REMAINDER {n})"
        )

    # Indexes on the parent propagate to every partition, existing and future.
    op.create_index(
        f"ix_{TABLE}_batch", TABLE, ["batch_id", "decision"], schema=RULE
    )
    op.create_index(
        f"ix_{TABLE}_extref", TABLE, ["tenant_id", "external_ref"], schema=RULE
    )
    op.create_index(f"ix_{TABLE}_rule", TABLE, ["rule_id"], schema=RULE)
    op.create_index(f"ix_{TABLE}_tenant_id", TABLE, ["tenant_id"], schema=RULE)

    # Same posture as migrations 0010 and 0011: enabled, permissive, tightened in
    # M8. RLS on a partitioned parent applies to every partition.
    op.execute(f"ALTER TABLE {RULE}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {RULE}.{TABLE} "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    # Dropping the parent takes its partitions with it.
    op.drop_table(TABLE, schema=RULE)
