"""The six pipeline stages.

Stage boundaries are the same ones the architecture calls out, and they are not
arbitrary: each is **independently restartable** and each has a single, countable
output. That is what makes the pipeline debuggable — when a run stalls, "which
stage, how many records in, how many out" is answerable without reading a log.

The same boundaries are the Airflow task boundaries (see ``deploy/airflow/``), so
running in-process and running under an orchestrator execute identical code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageKey(StrEnum):
    INGEST = "CDR_INGESTION"
    NORMALIZE = "CDR_NORMALIZATION"
    ENRICH = "DATA_ENRICHMENT"
    SELECT = "RULE_SELECTION"
    RATE = "RATING_CALCULATION"
    ASSURE = "RATING_ASSURANCE"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PipelineStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    #: Finished, but a stage reported records it could not process.
    COMPLETED_WITH_ISSUES = "COMPLETED_WITH_ISSUES"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    description: str
    #: What the counters mean for this stage, so the UI can label them honestly
    #: rather than showing a bare number.
    input_label: str
    output_label: str


STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        StageKey.INGEST,
        "CDR Ingestion",
        "Parse the source file and land every record verbatim. Duplicates are "
        "detected here by record hash, before anything is counted as revenue.",
        "records in file",
        "records landed",
    ),
    StageSpec(
        StageKey.NORMALIZE,
        "CDR Normalization",
        "Map each vendor layout onto the common usage model: timestamps, "
        "durations, numbers and currencies standardised.",
        "records landed",
        "records normalized",
    ),
    StageSpec(
        StageKey.ENRICH,
        "Data Enrichment",
        "Resolve the subscriber's product, destination zone, time band, "
        "on-net/off-net and roaming state, then build the rating context key.",
        "records normalized",
        "records enriched",
    ),
    StageSpec(
        StageKey.SELECT,
        "Rule Selection",
        "Collapse the batch to its distinct rating contexts and resolve the "
        "winning rule per charging stage — once per context, not per CDR.",
        "distinct contexts",
        "contexts resolved",
    ),
    StageSpec(
        StageKey.RATE,
        "Rating Calculation",
        "Apply the charging sequence — quantity, pulse, base charge, bundle, "
        "discount, tax, rounding — and record a full trace per CDR.",
        "records to rate",
        "results produced",
    ),
    StageSpec(
        StageKey.ASSURE,
        "Rating Assurance",
        "Compare expected against billed, classify the variance, infer a root "
        "cause and group the failures into investigations.",
        "results compared",
        "exception groups",
    ),
)

STAGE_BY_KEY: dict[str, StageSpec] = {s.key: s for s in STAGES}
STAGE_ORDER: tuple[str, ...] = tuple(s.key for s in STAGES)


def initial_stages() -> list[dict]:
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "status": StageStatus.PENDING.value,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "records_in": 0,
            "records_out": 0,
            "records_failed": 0,
            "detail": "",
            "error": None,
        }
        for spec in STAGES
    ]
