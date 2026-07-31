// Wire types for the Rating Assurance service. Mirrors the FastAPI schemas in
// ra_rating_backend/app/modules/*/schemas.py.

export type RuleStatus =
  | "DRAFT"
  | "VALIDATED"
  | "REVIEWED"
  | "APPROVED"
  | "COMPILED"
  | "PUBLISHED"
  | "ACTIVE"
  | "SUPERSEDED"
  | "RETIRED";

export type DataType =
  | "STRING"
  | "NUMBER"
  | "BOOLEAN"
  | "ENUM"
  | "REFERENCE"
  | "DATETIME";

export interface RuleAttribute {
  key: string;
  label: string;
  data_type: DataType;
  group: string;
  reference: string | null;
  values: string[];
  specificity: number;
  description: string;
  operators: string[];
}

export interface OperatorSpec {
  key: string;
  label: string;
  min_values: number;
  max_values: number | null;
}

export interface ActionParamSpec {
  key: string;
  label: string;
  data_type: DataType;
  required: boolean;
  reference: string | null;
  values: string[];
  description: string;
}

export interface ActionSpec {
  type: string;
  label: string;
  stage: string;
  description: string;
  params: ActionParamSpec[];
}

export interface RatingEnums {
  service_type: string[];
  usage_unit: string[];
  account_type: string[];
  zone_type: string[];
  tax_type: string[];
  rounding_mode: string[];
  discount_type: string[];
  reset_period: string[];
  catalog_status: string[];
  rule_status: RuleStatus[];
  rule_type: string[];
  stacking_policy: string[];
  condition_logic: string[];
  execution_stage: string[];
  rule_type_stage: Record<string, string>;
  allowed_transitions: Record<string, string[]>;
}

export interface Condition {
  id?: string;
  sequence?: number;
  attribute: string;
  operator: string;
  values: unknown[];
  group_index: number;
  negate: boolean;
}

export interface Action {
  id?: string;
  sequence?: number;
  action_type: string;
  params: Record<string, unknown>;
}

export interface RuleSummary {
  id: string;
  rule_key: string;
  version: number;
  name: string;
  description: string;
  rule_type: string;
  execution_stage: string;
  category: string;
  service_type: string;
  status: RuleStatus;
  priority: number;
  specificity: number;
  stacking_policy: string;
  conflict_group: string | null;
  product_id: string | null;
  offer_id: string | null;
  tariff_plan_id: string | null;
  rule_set_id: string | null;
  effective_from: string;
  effective_to: string | null;
  currency_code: string | null;
  product_code?: string | null;
  offer_code?: string | null;
  tariff_plan_code?: string | null;
  source_system: string;
  owner: string | null;
  created_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  condition_count: number;
  action_count: number;
  has_errors: boolean;
  created_at: string;
  updated_at: string;
}

export interface RuleDetail extends RuleSummary {
  supersedes_id: string | null;
  condition_logic: string;
  change_comment: string;
  attributes: Record<string, unknown>;
  last_validation: { valid?: boolean; checked_at?: string } | null;
  conditions: Condition[];
  actions: Action[];
}

export interface RuleListResponse {
  items: RuleSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** One row returned by the canonical `/canonical-rules` catalogue. */
export interface CanonicalRuleSummary {
  rule_id: string;
  rule_key: string;
  rule_name: string;
  charging_mode: string;
  rule_type_code: string;
  rule_type_name: string;
  stage_code: string;
  service_type: string;
  status: RuleStatus;
  owner: string | null;
  source: string;
  version_number: number | null;
  priority: number | null;
  specificity_score: number | null;
  effective_from: string | null;
  effective_to: string | null;
  currency_code: string | null;
  validation_state: string;
  condition_count: number;
  action_count: number;
  updated_at: string | null;
}

export interface CanonicalRuleListResponse {
  items: CanonicalRuleSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface CanonicalRuleEstateStats {
  logical_rules: number;
  total_versions: number;
  pending_approval: number;
  rules_with_errors: number;
}

export interface CanonicalCondition {
  attribute: string;
  operator: string;
  values: unknown[];
  value_type: string;
  negated: boolean;
  unit_code: string | null;
  currency_code: string | null;
  sequence: number;
}

export interface CanonicalConditionGroup {
  logic: string;
  negated: boolean;
  label: string;
  sequence: number;
  conditions: CanonicalCondition[];
  children: CanonicalConditionGroup[];
}

export interface CanonicalActionParameter {
  name: string;
  value: unknown;
  value_type: string;
  numeric: number | string | null;
  currency_code: string | null;
  unit_code: string | null;
  sequence: number;
}

export interface CanonicalAction {
  action_type: string;
  label: string;
  stage_code: string;
  target_attribute: string | null;
  parameters: CanonicalActionParameter[];
  sequence: number;
}

export interface CanonicalVersionSummary {
  rule_version_id: string;
  version_number: number;
  status: RuleStatus;
  validation_state: string;
  effective_from: string;
  effective_to: string | null;
  priority: number;
  specificity_score: number;
  execution_mode: string;
  change_reason: string;
  created_at: string;
}

export interface CanonicalAuditEntry {
  action: string;
  version_number: number | null;
  from_status: string | null;
  to_status: string | null;
  channel: string;
  actor_name: string | null;
  comment: string;
  created_at: string;
}

export interface CanonicalRuleDetail extends CanonicalRuleSummary {
  description: string;
  execution_mode: string;
  stacking_policy: string;
  conflict_group: string | null;
  fallback_policy: string;
  stop_processing: boolean;
  condition_logic: string;
  behaviour_hash: string;
  change_reason: string;
  rule_version_id: string | null;
  conditions: CanonicalConditionGroup | null;
  actions: CanonicalAction[];
  issues: ValidationIssue[];
  versions: CanonicalVersionSummary[];
}

export interface CanonicalWriteResponse {
  rule: CanonicalRuleDetail;
  decision: string;
  issues: ValidationIssue[];
  validation_state: string;
}

export interface ValidationIssue {
  severity: "ERROR" | "WARNING" | "INFO";
  code: string;
  message: string;
  path: string;
  hint: string;
}

export interface ValidationReport {
  valid: boolean;
  checked_at: string;
  error_count: number;
  warning_count: number;
  issues: ValidationIssue[];
}

export interface CatalogEntity {
  id: string;
  code: string;
  name: string;
  description: string;
  status: string;
  source_system: string;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  // Entity-specific columns arrive alongside these; the catalogue screen reads
  // them positionally from its column spec rather than typing all 14 shapes.
  [key: string]: unknown;
}

export interface CatalogSummaryRow {
  entity: string;
  label: string;
  total: number;
  active: number;
}

export interface RuleSetRow {
  id: string;
  code: string;
  name: string;
  description: string;
  status: string;
  owner: string | null;
  source_system: string;
  rule_count: number;
}

export interface RuleTemplate {
  id: string;
  code: string;
  name: string;
  description: string;
  service_type: string;
  rule_type: string;
  payload: { conditions?: Condition[]; actions?: Action[] };
  is_system: boolean;
}

export interface AuditEntry {
  id: string;
  rule_key: string;
  rule_id: string | null;
  version: number | null;
  action: string;
  from_status: string | null;
  to_status: string | null;
  actor_name: string | null;
  comment: string;
  diff: Record<string, unknown>;
  created_at: string;
}

export interface RatingOverview {
  rule_estate: {
    total_rules: number;
    logical_rules: number;
    draft_count: number;
    pending_approval: number;
    active_count: number;
    rules_with_errors: number;
    expiring_within_30_days: number;
    by_status: Record<string, number>;
    by_service_type: Record<string, number>;
  };
  catalog: {
    products: number;
    offers: number;
    tariff_plans: number;
    destination_zones: number;
    destination_prefixes: number;
    time_bands: number;
    tax_rules: number;
    warnings: string[];
  };
  assurance: {
    total_cdrs: number | null;
    expected_revenue: number | null;
    billed_revenue: number | null;
    revenue_leakage: number | null;
    customer_overcharge: number | null;
    match_rate: number | null;
    open_exceptions: number | null;
    available: boolean;
    message: string;
  };
  recent_activity: {
    rule_key: string;
    version: number | null;
    action: string;
    actor_name: string | null;
    comment: string;
    created_at: string;
  }[];
}

export interface RatingPermissions {
  [key: string]: { view: boolean; edit: boolean };
}

export interface RatingMe {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions: RatingPermissions;
}

export interface FieldSchema {
  key: string;
  label: string;
  data_type:
    | "STRING"
    | "NUMBER"
    | "BOOLEAN"
    | "ENUM"
    | "REFERENCE"
    | "DATE"
    | "TIME";
  required: boolean;
  values: string[];
  reference: string | null;
  multiple: boolean;
  default: unknown;
  help: string;
}

export interface EntitySchema {
  slug: string;
  label: string;
  fields: FieldSchema[];
}

// --- File-based rule import -------------------------------------------------

export interface ImportColumn {
  key: string;
  label: string;
  kind: "HEADER" | "CONDITION" | "ACTION";
  required: boolean;
  description: string;
  aliases: string[];
}

export interface ImportRowIssue {
  row_number: number;
  column: string;
  message: string;
}

export interface PreviewRow {
  row_number: number;
  raw: Record<string, string>;
  canonical: Record<string, unknown> | null;
  status: string;
  errors: ImportRowIssue[];
}

export interface ImportPreview {
  filename: string;
  headings: string[];
  mapping: Record<string, string | null>;
  unmapped_headings: string[];
  missing_required: string[];
  total_rows: number;
  valid_rows: number;
  rejected_rows: number;
  rows: PreviewRow[];
  truncated: boolean;
}

export interface ImportBatch {
  id: string;
  filename: string;
  source_system: string;
  status: string;
  total_rows: number;
  valid_rows: number;
  imported_rows: number;
  rejected_rows: number;
  created_by_name: string | null;
  created_at: string;
  completed_at: string | null;
  summary?: { reasons?: { message: string; count: number }[] };
}

export interface ImportCapabilities {
  formats: string[];
  excel_available: boolean;
  max_upload_mb: number;
  max_rows: number;
}

// --- Phase 2: snapshots -----------------------------------------------------

export interface SnapshotSummary {
  id: string;
  version: number;
  name: string;
  description: string;
  status: "PUBLISHED" | "ACTIVE" | "SUPERSEDED" | "FAILED";
  rule_set_id: string | null;
  effective_from: string;
  effective_to: string | null;
  rule_count: number;
  product_count: number;
  checksum: string;
  compiled_by_name: string | null;
  activated_at: string | null;
  superseded_at: string | null;
  published_to_clickhouse: boolean;
  created_at: string;
}

export interface SnapshotDetail extends SnapshotSummary {
  stats: Record<string, unknown>;
  issues: ValidationIssue[];
}

export interface RuleSetValidationReport {
  checked_at: string;
  rule_count: number;
  error_count: number;
  warning_count: number;
  can_compile: boolean;
  structural: ValidationIssue[];
  conflicts: ValidationIssue[];
  coverage: ValidationIssue[];
}

export interface SnapshotDiffEntry {
  rule_key: string;
  change: "ADDED" | "REMOVED" | "CHANGED" | "UNCHANGED";
  from_version: number | null;
  to_version: number | null;
  details: string[];
}

export interface SnapshotDiff {
  from_snapshot: number;
  to_snapshot: number;
  added: number;
  removed: number;
  changed: number;
  unchanged: number;
  identical: boolean;
  entries: SnapshotDiffEntry[];
}

// --- Phase 3: CDR batches ---------------------------------------------------

export interface CdrBatch {
  id: string;
  filename: string;
  source_system: string;
  cdr_type: string;
  event_date: string | null;
  status: string;
  total_records: number;
  loaded_records: number;
  duplicate_records: number;
  rejected_records: number;
  enriched_records: number;
  control_total: number | null;
  summary: {
    quality?: Record<string, number>;
    distinct_contexts?: number;
    cdrs_per_context?: number;
    ingest_ms?: number;
  };
  created_at: string;
}

export interface CdrProfile {
  code: string;
  label: string;
  service_type: string;
  required: string[];
  identity: string[];
  notes: string;
  fields: Record<string, string[]>;
}

// --- Phase 4: rating runs, results, exceptions ------------------------------

export interface RatingRun {
  id: string;
  batch_id: string;
  snapshot_id: string;
  snapshot_version: number;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  run_type: string;
  total_cdrs: number;
  rated_cdrs: number;
  distinct_contexts: number;
  matched_count: number;
  exception_count: number;
  expected_revenue: number;
  billed_revenue: number;
  undercharge_total: number;
  overcharge_total: number;
  stats: {
    duration_ms?: number;
    cdrs_per_context?: number;
    engine_version?: string;
  };
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  triggered_by_name: string | null;
  created_at: string;
}

export interface RunSummary {
  run_id: string;
  status: string;
  snapshot_version: number;
  total_cdrs: number;
  rated_cdrs: number;
  distinct_contexts: number;
  match_rate: number;
  expected_revenue: number;
  billed_revenue: number;
  revenue_leakage: number;
  customer_overcharge: number;
  exception_count: number;
  by_status: { status: string; count: number; variance: number }[];
  stats: Record<string, unknown>;
}

export interface TraceStep {
  step: number;
  stage: string;
  label: string;
  detail: string;
  value: string | null;
  rule_key: string | null;
}

export interface RuleCandidate {
  rule_id: string;
  rule_key: string;
  rule_version: number;
  rule_name: string;
  stage: string;
  specificity: number;
  priority: number;
  signature: string;
  selected: boolean;
  reason: string;
}

export interface RatingResultRow {
  id: string;
  run_id: string;
  cdr_enriched_id: string;
  cdr_id: string;
  context_hash: string | null;
  subscriber_id: string | null;
  msisdn: string | null;
  service_type: string;
  product_code: string | null;
  destination_zone: string | null;
  time_band: string | null;
  event_date: string;
  selected_rule_ids: Record<string, string>;
  billable_quantity: number | null;
  billable_unit: string | null;
  expected_base_charge: number;
  expected_discount: number;
  expected_tax: number;
  expected_final_charge: number;
  actual_charge: number | null;
  variance: number;
  currency: string | null;
  bundle_code: string | null;
  bundle_consumed: number;
  bundle_overflow: number;
  unpriced_quantity: number;
  status: string;
  root_cause: string | null;
  engine_version: string;
}

// --- Assurance dashboard ----------------------------------------------------

export interface AssuranceKpiSet {
  total_cdrs: number;
  matched_cdrs: number;
  match_rate: number | null;
  expected_revenue: number;
  billed_revenue: number;
  revenue_leakage: number;
  customer_overcharge: number;
  unrated_cdrs: number;
  no_matching_rule: number;
  open_exceptions: number;
  by_status: Record<string, number>;
}

export interface TrendPoint {
  date: string;
  cdrs: number;
  matched: number;
  expected: number;
  billed: number;
  undercharge: number;
  overcharge: number;
}

export interface LeakageRow {
  key: string;
  cdrs: number;
  matched: number;
  match_rate: number | null;
  expected: number;
  billed: number;
  undercharge: number;
  overcharge: number;
  net_variance: number;
}

export interface LatestRunRow {
  id: string;
  status: string;
  total_cdrs: number;
  rated_cdrs: number;
  match_rate: number | null;
  expected_revenue: number;
  billed_revenue: number;
  undercharge_total: number;
  overcharge_total: number;
  exception_count: number;
  finished_at: string | null;
}

export interface AssuranceDashboard {
  kpis: AssuranceKpiSet;
  trend: TrendPoint[];
  by_product: LeakageRow[];
  by_root_cause: LeakageRow[];
  latest_runs: LatestRunRow[];
}

export interface RatingResultDetail extends RatingResultRow {
  trace: TraceStep[];
  candidates: RuleCandidate[];
  context_key: string | null;
}

export interface RatingExceptionRow {
  id: string;
  run_id: string;
  group_key: string;
  title: string;
  assurance_status: string;
  root_cause: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  status: string;
  service_type: string | null;
  product_code: string | null;
  destination_zone: string | null;
  rule_key: string | null;
  cdr_count: number;
  subscriber_count: number;
  revenue_impact: number;
  expected_total: number;
  actual_total: number;
  first_event_date: string | null;
  last_event_date: string | null;
  sample_result_id: string | null;
  probable_cause: string;
  recommended_action: string;
  assigned_to_name: string | null;
  resolution: string;
  recovered_amount: number;
  created_at: string;
}

export interface ExceptionComment {
  id: string;
  kind: string;
  body: string;
  author_name: string | null;
  created_at: string;
}

export interface RatingExceptionDetail extends RatingExceptionRow {
  details: Record<string, unknown>;
  comments: ExceptionComment[];
}

export interface ExceptionMeta {
  assurance_statuses: string[];
  root_causes: string[];
  transitions: Record<string, string[]>;
}

// --- Pipeline ---------------------------------------------------------------

export interface StageSpec {
  key: string;
  label: string;
  description: string;
  input_label: string;
  output_label: string;
}

export interface StageState {
  key: string;
  label: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  records_in: number;
  records_out: number;
  records_failed: number;
  detail: string;
  error: string | null;
}

export interface PipelineRun {
  id: string;
  filename: string;
  source_system: string;
  cdr_type: string;
  status:
    | "QUEUED"
    | "RUNNING"
    | "COMPLETED"
    | "COMPLETED_WITH_ISSUES"
    | "FAILED";
  current_stage: string | null;
  stages: StageState[];
  batch_id: string | null;
  rating_run_id: string | null;
  snapshot_version: number | null;
  total_records: number;
  exception_count: number;
  error: string | null;
  summary: {
    match_rate?: number;
    expected_revenue?: number;
    billed_revenue?: number;
    revenue_leakage?: number;
    customer_overcharge?: number;
    exception_groups?: number;
  };
  started_at: string | null;
  finished_at: string | null;
  triggered_by_name: string | null;
  connector_id: string | null;
  created_at: string;
}

export interface PipelineEvent {
  id: string;
  stage: string | null;
  level: string;
  message: string;
  created_at: string;
}

export interface PipelineRunDetail extends PipelineRun {
  events: PipelineEvent[];
}

export interface MirrorAssuranceStage {
  key: string;
  label: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";
  detail: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface MirrorAssuranceRun {
  id: string;
  trigger: "MANUAL" | "SCHEDULED";
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  window_start: string;
  window_end: string;
  tolerance: number | string;
  stages: MirrorAssuranceStage[];
  row_count: number;
  matched_count: number;
  undercharged_count: number;
  overcharged_count: number;
  exception_count: number;
  total_variance: number | string;
  summary: { by_status?: Record<string, number> };
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  triggered_by_name: string | null;
  created_at: string;
}

export interface MirrorAssuranceSchedule {
  id: string;
  enabled: boolean;
  interval_minutes: number;
  window_hours: number;
  tolerance: number | string;
  next_run_at: string | null;
  last_run_at: string | null;
  updated_by_name: string | null;
}

export interface MirrorAssuranceResult {
  ordinal: number;
  event_id: string;
  service_type: string | null;
  reconciliation_status: string;
  event_time: string | null;
  payload: Record<string, unknown>;
}

export interface MirrorAssuranceResultPage {
  total: number;
  limit: number;
  offset: number;
  rows: MirrorAssuranceResult[];
}

// --- Connectors -------------------------------------------------------------

export interface VendorSpec {
  code: string;
  vendor: string;
  label: string;
  description: string;
  record_path: string;
  expects: string[];
  notes: string;
  adapter_available: boolean;
}

export interface ConnectorCatalog {
  vendors: VendorSpec[];
  source_types: string[];
  import_modes: string[];
  categories: string[];
}

export interface SourceSystem {
  id: string;
  code: string;
  name: string;
  description: string;
  vendor: string;
  category: string;
  source_type: string;
  status: string;
  connection: Record<string, unknown>;
  credentials: Record<string, unknown>;
  schedule: string | null;
  import_mode: string;
  field_mapping: Record<string, unknown>;
  health_status: "UNKNOWN" | "HEALTHY" | "DEGRADED" | "UNREACHABLE";
  health_detail: string;
  last_tested_at: string | null;
  last_import_at: string | null;
  last_import_status: string | null;
  total_imports: number;
  failed_imports: number;
  total_records_imported: number;
  enabled: boolean;
  created_at: string;
}

export interface ConnectorImport {
  id: string;
  source_id: string;
  trigger: string;
  import_mode: string;
  status: string;
  records_read: number;
  records_mapped: number;
  rules_created: number;
  rules_updated: number;
  rules_unchanged: number;
  rules_deleted: number;
  records_rejected: number;
  summary: {
    adapter_label?: string;
    reasons?: { message: string; count: number }[];
  };
  error: string | null;
  duration_ms: number | null;
  triggered_by_name: string | null;
  created_at: string;
  errors?: { index: number; field: string; message: string }[];
}

// --- Simulation -------------------------------------------------------------

export interface SimulationResult {
  snapshot_id: string;
  snapshot_version: number;
  context_key: string;
  enrichment: Record<string, unknown>;
  selected_rules: {
    stage: string;
    rule_key: string;
    rule_version: number;
    rule_name: string;
    specificity: number;
    priority: number;
    signature: string;
    actions: unknown[];
  }[];
  candidates: RuleCandidate[];
  candidate_count: number;
  ambiguous_stages: string[];
  calculation: {
    billable_quantity: number;
    billable_unit: string | null;
    base_charge: number;
    discount: number;
    tax: number;
    expected_charge: number;
    currency: string | null;
    zero_rated: boolean;
    missing_stages: string[];
    error: string | null;
  };
  trace: TraceStep[];
  comparison: {
    actual_charge: number;
    variance: number;
    status: string;
    root_cause: string | null;
    explanation: string;
  } | null;
}

// --- Canonical ingestion (`/rule-ingest`) -----------------------------------
//
// Distinct from the legacy `Import*` types above, and deliberately not a
// replacement for them at the type level. The two endpoints answer different
// questions: `/rule-imports` reports rows in and rows out, while `/rule-ingest`
// reports what the kernel *decided* about each row — new, changed, unchanged,
// withdrawn or quarantined. Collapsing them into one shape would mean throwing
// away the half of the answer an operator acts on.

export interface CanonicalRuleSet {
  rule_set_id: string;
  code: string;
  name: string;
  description: string;
  set_type: string;
  status: string;
  rule_count: number;
  created_at: string | null;
}

export interface IngestParseRejection {
  source_offset: number;
  reason: string;
  raw: Record<string, unknown>;
}

/** A catalogue entry the file references and the catalogue lacks. */
export interface IngestMissingMetadata {
  entity: string;
  code: string;
  count: number;
  /** False when a placeholder would change what gets priced, not merely name it. */
  creatable: boolean;
  reason: string;
  create_endpoint: string;
}

export interface IngestPreviewRow {
  source_offset: number;
  rule_key: string | null;
  rule_name: string | null;
  decision: string;
  reason: string;
  issues: { severity?: string; message: string; path?: string }[];
  raw: Record<string, unknown>;
}

export interface IngestPreview {
  filename: string;
  headings: string[];
  mapping: Record<string, string>;
  unmapped_headings: string[];
  row_count: number;
  content_hash: string;
  counts: Record<string, number>;
  reasons: { message: string; count: number }[];
  /** False when the batch would change or withdraw something already live. */
  safe_to_auto_commit: boolean;
  notes: string[];
  parse_rejections: IngestParseRejection[];
  missing_metadata: IngestMissingMetadata[];
  sample: IngestPreviewRow[];
}

export interface IngestBatch {
  batch_id: string;
  channel: string;
  source_system_id: string | null;
  filename: string | null;
  import_mode: string;
  status: string;
  dry_run: boolean;
  counts: Record<string, number>;
  duration_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
  triggered_by_name: string | null;
  touched_live_pricing: boolean;
  /** The set this import's rules joined — what makes the batch addressable. */
  rule_set_id: string | null;
  rule_set_code: string | null;
  reasons?: { message: string; count: number }[];
  error?: string | null;
  record_decisions?: Record<string, number>;
  parse_rejections?: IngestParseRejection[];
  missing_metadata?: IngestMissingMetadata[];
  created_references?: Record<string, string[]>;
}

// --- Bulk lifecycle (`/rule-lifecycle`) -------------------------------------

/** Exactly one selector. No arbitrary id list — the backend refuses them. */
export interface BulkSelector {
  batch_id?: string;
  rule_set_id?: string;
  filters?: Record<string, unknown>;
  /**
   * Explicitly ticked rules, by key. Keys rather than ids because the request
   * body is the audit record — a delete naming rule keys can be reviewed later
   * by a human, and one naming five UUIDs cannot.
   */
  rule_keys?: string[];
}

export interface BulkRuleOutcome {
  rule_id: string;
  rule_key: string;
  rule_name: string;
  outcome: string;
  from_status: string;
  to_status: string;
  reason: string;
  code: string;
}

/** Blockers grouped by cause. Thirty-two rules failing one check is one fix. */
export interface BulkBlocker {
  outcome: string;
  code: string;
  reason: string;
  count: number;
  examples: string[];
}

export interface BulkResponse {
  operation: string;
  selector: Record<string, unknown>;
  dry_run: boolean;
  total: number;
  eligible: number;
  applied: number;
  counts: Record<string, number>;
  blocked: BulkBlocker[];
  touched_live_pricing: boolean;
  requires_approver_role: boolean;
  caller_can_approve: boolean;
  rules?: BulkRuleOutcome[];
  snapshot?: BulkSnapshotRef | null;
}

export interface BulkSnapshotRef {
  snapshot_id: string;
  version: number;
  status: string;
  rule_count: number;
  checksum: string;
  forced: boolean;
}

export type BulkJobStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "REJECTED"
  | "ERROR";

export interface BulkJob {
  bulk_run_id: string;
  operation: string;
  status: BulkJobStatus;
  selector: Record<string, unknown>;
  dry_run: boolean;
  atomic: boolean;
  force: boolean;
  comment: string;
  total: number;
  processed: number;
  applied: number;
  counts: Record<string, number>;
  blocked: BulkBlocker[];
  snapshot: BulkSnapshotRef | null;
  touched_live_pricing: boolean;
  error: string;
  error_details: Record<string, unknown> | null;
  actor_name: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** A RUNNING row whose process died. Derived by the backend, not stored. */
  stale: boolean;
  percent: number | null;
}

// --- Snapshot detail --------------------------------------------------------
//
// A snapshot is the answer to "what was rating on the 14th", so its detail page
// is where an operator goes when a charge is disputed. Every shape here exists
// to answer one question they arrive with: what is in it, what changed, is it
// safe, who does it touch, and in what order does it run.

/** A rule as the engine will actually walk it — compiled, not authored. */
export interface ExecutableRule {
  id: string;
  rule_id: string;
  rule_key: string;
  rule_version: number;
  rule_name: string;
  rule_type: string;
  execution_stage: string;
  stage_order: number;
  priority: number;
  specificity: number;
  stacking_policy: string;
  conflict_group: string | null;
  effective_from: string;
  effective_to: string | null;
  currency_code: string | null;
  signature: string;
  dimension_sets: Record<string, unknown>;
  predicates: unknown[];
  actions: unknown[];
}

export interface CompileReport {
  snapshot_id: string;
  version: number;
  status: string;
  checksum: string;
  rule_count: number;
  compiled_by: string | null;
  compiled_at: string | null;
  /** True when it was compiled past its own blocking issues. */
  forced: boolean;
  error_count: number;
  warning_count: number;
  safe_to_activate: boolean;
  grouped_issues: {
    code: string;
    severity: string;
    count: number;
    message: string;
    hint?: string;
    examples?: string[];
  }[];
  issues: unknown[];
  stats: Record<string, unknown>;
}

export interface StageGroup {
  stage: string;
  stage_order: number;
  rule_count: number;
  rules: Record<string, unknown>[];
}

/** Which dimension values the snapshot reaches. Wildcards counted separately —
 *  a rule matching every zone is not the same as one naming forty. */
export interface Reach {
  dimension: string;
  label: string;
  values: string[];
  wildcard_rules: number;
}

/** Measured against rated traffic, not estimated. `has_traffic` false means
 *  there was nothing in the window to measure, which is not the same as zero. */
export interface TrafficImpact {
  window_days: number;
  from_date: string;
  to_date: string;
  has_traffic: boolean;
  rated_events: number;
  affected_events: number;
  distinct_subscribers: number;
  affected_charge: string;
  currency: string;
  top_rules: Record<string, unknown>[];
}

export interface SnapshotImpact {
  snapshot_id: string;
  version: number;
  rule_count: number;
  reach: Reach[];
  traffic: TrafficImpact | null;
  changed_rule_keys: string[];
  compared_with_version: number | null;
  note: string;
}
