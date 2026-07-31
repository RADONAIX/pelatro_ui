"""The canonical rule model — ORM for the ``ra_rule`` schema.

Nothing in this package is read or written by the live rating path yet: R1 lands
the schema and the vocabulary, R2 adds the typed-value codec and the single
writer, R3 routes manual authoring through it, and R4 backfills, proves parity
and switches the legacy tables to read-only. Until then the legacy
``rules/models.py`` remains the source of truth and is untouched.

The one rule that governs this package from R3 onward: these classes are only
ever instantiated inside ``ingest/writer.py``. A CI test AST-walks the tree and
fails otherwise, because documentation has never once stopped a future connector
from calling ``db.add(Rule(...))``.
"""

from __future__ import annotations

# The canonical tables carry foreign keys into the `rating` schema — products,
# offers, tariff plans, source systems, rule snapshots. SQLAlchemy resolves those
# targets out of the shared metadata when the mappers configure, so the modules
# defining them must be imported. Doing it here rather than relying on `app.models`
# means this package can be mapped on its own: a test that needs only these tables
# should not have to know which unrelated module to import first.
from app.modules.catalog import models as _catalog_models  # noqa: F401
from app.modules.compiler import models as _compiler_models  # noqa: F401
from app.modules.connectors import models as _connector_models  # noqa: F401
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id
from app.modules.rules.canonical.graph import RuleDependency, RuleFallback
from app.modules.rules.canonical.lineage import (
    CanonicalRuleAudit,
    RuleImportProfile,
    RuleIngestionBatch,
    RuleSourceLineage,
    RuleValidationIssue,
)
from app.modules.rules.canonical.logic import (
    MONEY,
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.lookups import (
    RuleConflictGroupRow,
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.canonical.sets import CanonicalRuleSet, RuleSetMember

#: Every canonical table, in dependency order — used by the migration and by the
#: teardown helper in tests.
CANONICAL_TABLES: tuple[str, ...] = (
    "rule_stage",
    "rule_type",
    "rule_stacking_policy",
    "rule_conflict_group",
    "rule_import_profile",
    "rule",
    "rule_version",
    "rule_condition_group",
    "rule_condition",
    "rule_action",
    "rule_parameter",
    "rule_dependency",
    "rule_fallback",
    "rule_set",
    "rule_set_member",
    "rule_ingestion_batch",
    "rule_source_lineage",
    "rule_validation_issue",
    "rule_audit",
)

__all__ = [
    "CANONICAL_TABLES",
    "MONEY",
    "RULE_SCHEMA",
    "CanonicalRule",
    "CanonicalRuleAudit",
    "CanonicalRuleSet",
    "CanonicalRuleVersion",
    "RuleActionRow",
    "RuleConditionGroup",
    "RuleConditionRow",
    "RuleConflictGroupRow",
    "RuleDependency",
    "RuleFallback",
    "RuleImportProfile",
    "RuleIngestionBatch",
    "RuleParameter",
    "RuleSetMember",
    "RuleSourceLineage",
    "RuleStackingPolicyRow",
    "RuleStage",
    "RuleTypeRow",
    "RuleValidationIssue",
    "TenantMixin",
    "new_id",
]
