"""Model aggregate.

Importing this module registers every table on ``Base.metadata`` — which is
what Alembic autogenerate and the test bootstrap rely on. Add new modules here
or their tables will be silently missing from migrations.
"""

from __future__ import annotations

from app.core.database import Base
from app.modules.access.models import RatingRolePermission  # noqa: F401
from app.modules.balances.models import (  # noqa: F401
    BalanceBucket,
    BalanceLedgerEntry,
    UsageCounter,
)
from app.modules.catalog.models import (  # noqa: F401
    BundleDefinition,
    Currency,
    DestinationPrefix,
    DestinationZone,
    DiscountDefinition,
    Offer,
    Product,
    Promotion,
    RatingGroup,
    RoundingRule,
    ServiceDefinition,
    TariffPlan,
    TaxRule,
    TimeBand,
)
from app.modules.cdr.models import (  # noqa: F401
    CdrBatch,
    CdrEnriched,
    CdrLanding,
    CdrSequence,
    HolidayCalendar,
    NetworkPrefix,
    SubscriberGroupMembership,
    SubscriberProduct,
)
from app.modules.compiler.models import (  # noqa: F401
    ExecutableRule,
    RuleSnapshot,
)
from app.modules.connectors.models import (  # noqa: F401
    ConnectorImport,
    SourceSystem,
)
from app.modules.imports.models import (  # noqa: F401
    RuleImportBatch,
    RuleImportRow,
    RuleImportTemplate,
)
from app.modules.mirror_assurance.models import (  # noqa: F401
    MirrorAssuranceResult,
    MirrorAssuranceRun,
    MirrorAssuranceSchedule,
)
from app.modules.msc.models import MscIngestCursor  # noqa: F401
from app.modules.pipeline.models import (  # noqa: F401
    PipelineEvent,
    PipelineRun,
)
from app.modules.rating.audit_models import (  # noqa: F401
    CalculationComponent,
    RatingResultFinal,
    RuleEvaluationAudit,
    UsageException,
)
from app.modules.rating.models import (  # noqa: F401
    ContextRuleMap,
    ExceptionComment,
    RatingException,
    RatingResult,
    RatingRun,
)
from app.modules.replay.models import ReplayRun  # noqa: F401
from app.modules.reports.models import GeneratedReport  # noqa: F401
from app.modules.rules.canonical import (  # noqa: F401
    CanonicalRule,
    CanonicalRuleAudit,
    CanonicalRuleSet,
    CanonicalRuleVersion,
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleConflictGroupRow,
    RuleDependency,
    RuleFallback,
    RuleImportProfile,
    RuleIngestionBatch,
    RuleParameter,
    RuleSetMember,
    RuleSourceLineage,
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
    RuleValidationIssue,
)
from app.modules.rules.lifecycle.models import RuleBulkRun  # noqa: F401
from app.modules.rules.models import (  # noqa: F401
    Rule,
    RuleAction,
    RuleAuditEntry,
    RuleCondition,
    RuleSet,
    RuleTemplate,
)

__all__ = ["Base"]
