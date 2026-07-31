"""Canonical metadata API — one uniform CRUD surface per entity.

``_register`` builds the five endpoints for an entity from a spec, so adding a
new catalogue (say, "APN") is a model + two schemas + one ENTITIES row.

    GET    /catalog/{entity}          list  (search, status, paging)
    POST   /catalog/{entity}          create
    GET    /catalog/{entity}/{id}     read
    PATCH  /catalog/{entity}/{id}     update
    DELETE /catalog/{entity}/{id}     retire (soft)
"""

# NOTE: deliberately no `from __future__ import annotations` here. The endpoints
# are generated from `EntitySpec`, so their annotations (`payload: spec.create`)
# must evaluate to real classes at definition time — PEP 563 would turn them
# into unresolvable strings and FastAPI could not build the request models.

import inspect
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from sqlalchemy import func, select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.rbac import RatingPermKey
from app.modules.catalog import models as m
from app.modules.catalog import schemas as s
from app.modules.catalog import service as svc
from app.modules.charging import models as cm
from app.modules.charging import schemas as cs
from app.modules.mirror import hooks as mirror_hooks

router = APIRouter(prefix="/catalog", tags=["catalog"])

_view = require(RatingPermKey.CATALOG, "view")
_edit = require(RatingPermKey.CATALOG, "edit")
#: Enforces catalog:edit AND hands the handler the principal (for created_by).
CatalogEditor = principal_with(RatingPermKey.CATALOG, "edit")


@dataclass(frozen=True)
class EntitySpec:
    slug: str
    label: str
    model: type[Any]
    read: type[BaseModel]
    create: type[BaseModel]
    update: type[BaseModel]
    #: Query params exposed as exact-match filters, e.g. service_type.
    filters: tuple[str, ...] = ()
    #: field name -> catalogue slug it points at. Declared rather than inferred
    #: from the name: `currency_code` and `product_id` follow different
    #: conventions, and a guess that silently mis-resolves would give the UI a
    #: dropdown of the wrong entities.
    references: tuple[tuple[str, str], ...] = ()
    #: Section of the Metadata Catalogue screen. Declared here so the grouping
    #: lives beside the entity rather than being re-derived in the UI from a
    #: hard-coded slug list that nobody updates when a catalogue is added.
    group: str = "Shared"


#: The catalogue the platform started with: commercial and usage-rating
#: metadata. Charging, prepaid and postpaid entities are appended below from
#: `charging_entities()` — same spec shape, same generated endpoints.
_CORE_ENTITIES: tuple[EntitySpec, ...] = (
    EntitySpec(
        "currencies", "Currency", m.Currency,
        s.CurrencyRead, s.CurrencyCreate, s.CurrencyUpdate,
    ),
    EntitySpec(
        "rounding-rules", "Rounding rule", m.RoundingRule,
        s.RoundingRuleRead, s.RoundingRuleCreate, s.RoundingRuleUpdate,
    ),
    EntitySpec(
        "tax-rules", "Tax rule", m.TaxRule,
        s.TaxRuleRead, s.TaxRuleCreate, s.TaxRuleUpdate,
        ("tax_type", "jurisdiction"),
    ),
    EntitySpec(
        "services", "Service", m.ServiceDefinition,
        s.ServiceRead, s.ServiceCreate, s.ServiceUpdate,
        ("service_type",),
        group="Charging",
    ),
    EntitySpec(
        "products", "Product", m.Product,
        s.ProductRead, s.ProductCreate, s.ProductUpdate,
        ("account_type",),
        (("currency_code", "currencies"),),
        group="Commercial",
    ),
    EntitySpec(
        "offers", "Offer", m.Offer,
        s.OfferRead, s.OfferCreate, s.OfferUpdate,
        ("product_id",),
        (("product_id", "products"),),
        group="Commercial",
    ),
    EntitySpec(
        "tariff-plans", "Tariff plan", m.TariffPlan,
        s.TariffPlanRead, s.TariffPlanCreate, s.TariffPlanUpdate,
        ("service_type", "product_id"),
        (("product_id", "products"), ("offer_id", "offers"),
         ("currency_code", "currencies")),
        group="Commercial",
    ),
    EntitySpec(
        "time-bands", "Time band", m.TimeBand,
        s.TimeBandRead, s.TimeBandCreate, s.TimeBandUpdate,
    ),
    EntitySpec(
        "destination-zones", "Destination zone", m.DestinationZone,
        s.DestinationZoneRead, s.DestinationZoneCreate, s.DestinationZoneUpdate,
        ("zone_type", "country_code"),
    ),
    EntitySpec(
        "rating-groups", "Rating group", m.RatingGroup,
        s.RatingGroupRead, s.RatingGroupCreate, s.RatingGroupUpdate,
        ("service_type",),
        group="Charging",
    ),
    EntitySpec(
        "discounts", "Discount", m.DiscountDefinition,
        s.DiscountRead, s.DiscountCreate, s.DiscountUpdate,
        ("discount_type",),
        (("currency_code", "currencies"),),
    ),
    EntitySpec(
        "bundles", "Bundle", m.BundleDefinition,
        s.BundleRead, s.BundleCreate, s.BundleUpdate,
        ("service_type",),
    ),
    EntitySpec(
        "promotions", "Promotion", m.Promotion,
        s.PromotionRead, s.PromotionCreate, s.PromotionUpdate,
        (),
        (("currency_code", "currencies"),),
    ),
)


#: Charging, prepaid and postpaid metadata. Every one of these is referenced by
#: a rule action parameter or a condition attribute; the slugs here MUST match
#: the `reference=` values in `rules.vocabulary`, and a test asserts they do —
#: a mismatch would leave the wizard offering a dropdown with no source.
_CHARGING_ENTITIES: tuple[EntitySpec, ...] = (
    # --- Charging -----------------------------------------------------------
    EntitySpec(
        "charging-units", "Charging unit", cm.ChargingUnit,
        cs.ChargingUnitRead, cs.ChargingUnitCreate, cs.ChargingUnitUpdate,
        ("dimension",),
        group="Charging",
    ),
    EntitySpec(
        "pulse-profiles", "Pulse profile", cm.PulseProfile,
        cs.PulseProfileRead, cs.PulseProfileCreate, cs.PulseProfileUpdate,
        ("service_type",),
        (("unit_code", "charging-units"),),
        group="Charging",
    ),
    EntitySpec(
        "charge-limit-profiles", "Min/max profile", cm.ChargeLimitProfile,
        cs.ChargeLimitProfileRead, cs.ChargeLimitProfileCreate,
        cs.ChargeLimitProfileUpdate,
        ("service_type",),
        (("currency_code", "currencies"), ("unit_code", "charging-units")),
        group="Charging",
    ),
    # --- Prepaid ------------------------------------------------------------
    EntitySpec(
        "balance-types", "Balance type", cm.BalanceType,
        cs.BalanceTypeRead, cs.BalanceTypeCreate, cs.BalanceTypeUpdate,
        ("category",),
        (("unit_code", "charging-units"),),
        group="Prepaid",
    ),
    EntitySpec(
        "balance-buckets", "Balance bucket", cm.BalanceBucketDefinition,
        cs.BalanceBucketDefinitionRead, cs.BalanceBucketDefinitionCreate,
        cs.BalanceBucketDefinitionUpdate,
        ("balance_type_id",),
        (("balance_type_id", "balance-types"), ("currency_code", "currencies"),
         ("unit_code", "charging-units")),
        group="Prepaid",
    ),
    EntitySpec(
        "ocs-profiles", "OCS profile", cm.OcsProfile,
        cs.OcsProfileRead, cs.OcsProfileCreate, cs.OcsProfileUpdate,
        ("vendor",),
        group="Prepaid",
    ),
    EntitySpec(
        "reservation-policies", "Reservation policy", cm.ReservationPolicy,
        cs.ReservationPolicyRead, cs.ReservationPolicyCreate,
        cs.ReservationPolicyUpdate,
        (),
        (("ocs_profile_id", "ocs-profiles"), ("unit_code", "charging-units")),
        group="Prepaid",
    ),
    EntitySpec(
        "charging-profiles", "Charging profile", cm.ChargingProfile,
        cs.ChargingProfileRead, cs.ChargingProfileCreate, cs.ChargingProfileUpdate,
        ("charging_mode",),
        (("ocs_profile_id", "ocs-profiles"),
         ("reservation_policy_id", "reservation-policies"),
         ("credit_limit_profile_id", "credit-limit-profiles"),
         ("rounding_rule_id", "rounding-rules"),
         ("default_currency_code", "currencies")),
        group="Prepaid",
    ),
    EntitySpec(
        "balance-priorities", "Balance priority", cm.BalancePriority,
        cs.BalancePriorityRead, cs.BalancePriorityCreate, cs.BalancePriorityUpdate,
        ("charging_profile_id", "balance_type_id", "service_type"),
        (("charging_profile_id", "charging-profiles"),
         ("balance_type_id", "balance-types")),
        group="Prepaid",
    ),
    # --- Postpaid -----------------------------------------------------------
    EntitySpec(
        "proration-profiles", "Proration profile", cm.ProrationProfile,
        cs.ProrationProfileRead, cs.ProrationProfileCreate, cs.ProrationProfileUpdate,
        ("method",),
        group="Postpaid",
    ),
    EntitySpec(
        "billing-cycles", "Billing cycle", cm.BillingCycle,
        cs.BillingCycleRead, cs.BillingCycleCreate, cs.BillingCycleUpdate,
        ("frequency",),
        (("proration_profile_id", "proration-profiles"),),
        group="Postpaid",
    ),
    EntitySpec(
        "invoice-components", "Invoice component", cm.InvoiceComponent,
        cs.InvoiceComponentRead, cs.InvoiceComponentCreate, cs.InvoiceComponentUpdate,
        ("component_type",),
        (("tax_rule_id", "tax-rules"),),
        group="Postpaid",
    ),
    EntitySpec(
        "recurring-charges", "Recurring charge", cm.RecurringCharge,
        cs.RecurringChargeRead, cs.RecurringChargeCreate, cs.RecurringChargeUpdate,
        ("product_id", "billing_cycle_id"),
        (("product_id", "products"), ("offer_id", "offers"),
         ("currency_code", "currencies"), ("billing_cycle_id", "billing-cycles"),
         ("invoice_component_id", "invoice-components"),
         ("proration_profile_id", "proration-profiles")),
        group="Postpaid",
    ),
    EntitySpec(
        "one-time-charges", "One-time charge", cm.OneTimeCharge,
        cs.OneTimeChargeRead, cs.OneTimeChargeCreate, cs.OneTimeChargeUpdate,
        ("trigger_event",),
        (("currency_code", "currencies"),
         ("invoice_component_id", "invoice-components")),
        group="Postpaid",
    ),
    EntitySpec(
        "credit-limit-profiles", "Credit limit profile", cm.CreditLimitProfile,
        cs.CreditLimitProfileRead, cs.CreditLimitProfileCreate,
        cs.CreditLimitProfileUpdate,
        ("breach_action",),
        (("currency_code", "currencies"),),
        group="Postpaid",
    ),
    EntitySpec(
        "usage-aggregation-profiles", "Usage aggregation profile",
        cm.UsageAggregationProfile,
        cs.UsageAggregationProfileRead, cs.UsageAggregationProfileCreate,
        cs.UsageAggregationProfileUpdate,
        ("dimension", "service_type"),
        (("invoice_component_id", "invoice-components"),),
        group="Postpaid",
    ),
    EntitySpec(
        "late-fee-profiles", "Late fee profile", cm.LateFeeProfile,
        cs.LateFeeProfileRead, cs.LateFeeProfileCreate, cs.LateFeeProfileUpdate,
        (),
        (("currency_code", "currencies"),
         ("invoice_component_id", "invoice-components")),
        group="Postpaid",
    ),
)

ENTITIES: tuple[EntitySpec, ...] = (*_CORE_ENTITIES, *_CHARGING_ENTITIES)

ENTITY_BY_SLUG = {e.slug: e for e in ENTITIES}


class ListEnvelope(BaseModel):
    items: list[Any]
    meta: s.PageMeta


def _register(spec: EntitySpec) -> None:
    model, label = spec.model, spec.label

    @router.get(
        f"/{spec.slug}",
        response_model=list[spec.read],  # type: ignore[valid-type]
        summary=f"List {spec.label.lower()}s",
        dependencies=[Depends(_view)],
    )
    async def _list(
        db: DbSession,
        page: PageParams,
        search: str | None = Query(None, description="Matches code or name"),
        status: str | None = Query(None),
        filters: dict[str, str | None] = Depends(_filter_dep(spec)),
    ):
        stmt = svc.apply_filters(
            select(model), model, search=search, status=status, extra_equals=filters
        )
        stmt = stmt.order_by(model.code).limit(page.limit).offset(page.offset)
        return list((await db.execute(stmt)).scalars().all())

    @router.get(
        f"/{spec.slug}/{{entity_id}}",
        response_model=spec.read,  # type: ignore[valid-type]
        summary=f"Get a {spec.label.lower()}",
        dependencies=[Depends(_view)],
    )
    async def _get(db: DbSession, entity_id: str):
        return await svc.get_by_id(db, model, entity_id, label)

    @router.post(
        f"/{spec.slug}",
        response_model=spec.read,  # type: ignore[valid-type]
        status_code=201,
        summary=f"Create a {spec.label.lower()}",
    )
    async def _create(
        db: DbSession,
        principal: CatalogEditor,
        payload: spec.create = Body(...),  # type: ignore[valid-type]
    ):
        data = payload.model_dump()
        _stringify_enums(data)
        return await svc.create(db, model, data, label=label, actor_id=principal.id)

    @router.patch(
        f"/{spec.slug}/{{entity_id}}",
        response_model=spec.read,  # type: ignore[valid-type]
        summary=f"Update a {spec.label.lower()}",
        dependencies=[Depends(_edit)],
    )
    async def _update(
        db: DbSession,
        entity_id: str,
        payload: spec.update = Body(...),  # type: ignore[valid-type]
    ):
        data = payload.model_dump(exclude_unset=True)
        _stringify_enums(data)
        return await svc.update(db, model, entity_id, data, label=label)

    @router.delete(
        f"/{spec.slug}/{{entity_id}}",
        response_model=spec.read,  # type: ignore[valid-type]
        summary=f"Retire a {spec.label.lower()} (soft delete)",
        dependencies=[Depends(_edit)],
    )
    async def _retire(db: DbSession, entity_id: str):
        return await svc.retire(db, model, entity_id, label=label)


def _filter_dep(spec: EntitySpec):
    """Build a dependency exposing this entity's exact-match filters as query params."""

    names = spec.filters

    async def _dep(**kwargs: str | None) -> dict[str, str | None]:
        return kwargs

    # FastAPI reads the signature, so synthesise one with the right parameter names.
    params = [
        inspect.Parameter(
            n,
            inspect.Parameter.KEYWORD_ONLY,
            default=Query(None),
            annotation=str | None,
        )
        for n in names
    ]
    _dep.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    return _dep


# --- Form-schema introspection ----------------------------------------------

#: Never offered in the generated form. `attributes` is vendor pass-through for
#: connector imports, not something an author fills in by hand.
_HIDDEN_FIELDS = frozenset({"attributes"})

_HELP: dict[str, str] = {
    "code": "Stable key. Rules reference this rather than the id, so a rule set\n"
            "moves between environments unchanged.",
    "status": "RETIRED entities stop matching new rules but stay resolvable for\n"
              "historical results.",
    "source_system": "Which upstream system this came from. MANUAL when hand-created.",
    "effective_to": "Leave blank for an open-ended record.",
    "priority": "Higher wins where two time bands overlap.",
    "decimals": "Minor-unit digits — drives rounding and display.",
    "shared": "Shared bundles are consumed across an account, which forces the\n"
              "stateful rating path.",
    "inclusive": "Tick when the billed amount already includes this tax.",
    "pre_tax": "Applied before tax. A few jurisdictions require the reverse.",
}


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Strip `| None`, reporting whether it was there."""
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _field_schema(key: str, info: Any, reference: str | None) -> s.FieldSchema:
    annotation, _ = _unwrap_optional(info.annotation)

    multiple = False
    if get_origin(annotation) is list:
        multiple = True
        inner = get_args(annotation)
        annotation = inner[0] if inner else str
        annotation, _ = _unwrap_optional(annotation)

    values: list[str] = []
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        data_type = "ENUM"
        values = [e.value for e in annotation]
    elif reference:
        data_type = "REFERENCE"
    elif annotation is bool:
        data_type = "BOOLEAN"
    elif annotation in (int, float, Decimal):
        data_type = "NUMBER"
    elif annotation is date:
        data_type = "DATE"
    elif annotation is time:
        data_type = "TIME"
    else:
        data_type = "STRING"

    # A REFERENCE field wins over its enum-ness only if declared; conversely a
    # list of plain strings (time band `days`) stays STRING + multiple.
    default = info.default
    if default is PydanticUndefined:
        default = None
    elif isinstance(default, Enum):
        default = default.value

    return s.FieldSchema(
        key=key,
        label=key.replace("_", " ").capitalize(),
        data_type=data_type,
        required=info.is_required(),
        values=values,
        reference=reference,
        multiple=multiple,
        default=default,
        help=_HELP.get(key, ""),
    )


#: Declaration order follows Python's MRO, which puts the mixin's dates first
#: and buries `code` in the middle. Pin the shared fields to the positions a
#: form should read in; anything entity-specific keeps its declared order in
#: the gap between them.
_FIELD_ORDER_HEAD = ("code", "name", "description")
_FIELD_ORDER_TAIL = ("effective_from", "effective_to", "status", "source_system")


def _entity_schema(spec: EntitySpec) -> s.EntitySchema:
    references = dict(spec.references)
    by_key = {
        key: _field_schema(key, info, references.get(key))
        for key, info in spec.create.model_fields.items()
        if key not in _HIDDEN_FIELDS
    }
    middle = [
        f for k, f in by_key.items()
        if k not in _FIELD_ORDER_HEAD and k not in _FIELD_ORDER_TAIL
    ]
    ordered = (
        [by_key[k] for k in _FIELD_ORDER_HEAD if k in by_key]
        + middle
        + [by_key[k] for k in _FIELD_ORDER_TAIL if k in by_key]
    )
    return s.EntitySchema(
        slug=spec.slug, label=spec.label, fields=ordered, group=spec.group
    )


def _stringify_enums(data: dict[str, Any]) -> None:
    """Flatten enum members to their plain string value before they hit asyncpg.

    Scalars are mostly harmless (StrEnum subclasses str), but a *list* of them —
    ``product.service_types`` — is JSON-encoded for a JSONB column and the enum
    objects fail to serialise. Normalising both keeps the two paths identical.
    """
    for key, value in data.items():
        if isinstance(value, Enum):
            data[key] = value.value
        elif isinstance(value, list):
            data[key] = [v.value if isinstance(v, Enum) else v for v in value]


for _spec in ENTITIES:
    _register(_spec)


# --- Cross-entity endpoints -------------------------------------------------


@router.get(
    "/schema",
    response_model=list[s.EntitySchema],
    summary="Form definition for every catalogue",
    dependencies=[Depends(_view)],
)
async def catalog_schema() -> list[s.EntitySchema]:
    """Describe each catalogue's editable fields, derived from its Create model.

    The UI generates its create/edit forms from this rather than hard-coding 13
    of them — the same principle as the rule builder. Adding a column to a
    catalogue model makes the field appear in the form with no UI change, and a
    field can never be offered that the API would then reject.
    """
    return [_entity_schema(spec) for spec in ENTITIES]


@router.get(
    "/summary",
    response_model=list[s.CatalogSummary],
    summary="Row counts per catalogue (tab badges)",
    dependencies=[Depends(_view)],
)
async def catalog_summary(db: DbSession) -> list[s.CatalogSummary]:
    out: list[s.CatalogSummary] = []
    for spec in ENTITIES:
        total = int(
            (await db.execute(select(func.count()).select_from(spec.model))).scalar_one()
        )
        active = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(spec.model)
                    .where(spec.model.status == "ACTIVE")
                )
            ).scalar_one()
        )
        out.append(
            s.CatalogSummary(
                entity=spec.slug, label=spec.label, total=total, active=active,
                group=spec.group,
            )
        )
    return out


@router.get(
    "/destination-zones/{zone_id}/prefixes",
    response_model=list[s.DestinationPrefixRead],
    summary="Prefixes mapped to a destination zone",
    dependencies=[Depends(_view)],
)
async def list_prefixes(db: DbSession, zone_id: str) -> list[m.DestinationPrefix]:
    await svc.get_by_id(db, m.DestinationZone, zone_id, "Destination zone")
    stmt = (
        select(m.DestinationPrefix)
        .where(m.DestinationPrefix.zone_id == zone_id)
        .order_by(m.DestinationPrefix.prefix)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/destination-prefixes",
    response_model=s.DestinationPrefixRead,
    status_code=201,
    summary="Map a dialled-number prefix to a zone",
    dependencies=[Depends(_edit)],
)
async def create_prefix(
    db: DbSession, payload: s.DestinationPrefixCreate
) -> m.DestinationPrefix:
    await svc.get_by_id(db, m.DestinationZone, payload.zone_id, "Destination zone")
    obj = m.DestinationPrefix(**payload.model_dump())
    db.add(obj)
    await db.flush()
    await db.refresh(obj)
    mirror_hooks.record_prefix(db, obj.id)
    return obj


@router.delete(
    "/destination-prefixes/{prefix_id}",
    status_code=204,
    summary="Remove a prefix mapping",
    dependencies=[Depends(_edit)],
)
async def delete_prefix(db: DbSession, prefix_id: str) -> None:
    obj = await svc.get_by_id(db, m.DestinationPrefix, prefix_id, "Prefix")
    # Captured before the delete: this is a hard delete, so after the commit
    # there is no row left to read the zone code from. Guarded on the flag so a
    # deployment with the mirror off issues exactly the queries it always did.
    if mirror_hooks.is_enabled():
        zone = await db.get(m.DestinationZone, obj.zone_id)
        if zone is not None:
            mirror_hooks.record_prefix_delete(db, obj.prefix, zone.code)
    await db.delete(obj)
