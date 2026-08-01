import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { ratingApi } from "@/lib/rating/api";
import type {
  ActionSpec,
  AuditEntry,
  CatalogEntity,
  CatalogSummaryRow,
  EntitySchema,
  ImportBatch,
  ImportCapabilities,
  ImportColumn,
  ImportPreview,
  CdrBatch,
  CdrProfile,
  ExceptionMeta,
  RatingExceptionDetail,
  RatingExceptionRow,
  AssuranceDashboard,
  LeakageRow,
  RatingResultDetail,
  RatingResultRow,
  RatingRun,
  RuleSetValidationReport,
  RunSummary,
  SnapshotDetail,
  SnapshotDiff,
  SnapshotSummary,
  ConnectorCatalog,
  ConnectorImport,
  PipelineRun,
  PipelineRunDetail,
  MirrorAssuranceResultPage,
  MirrorAssuranceRun,
  MirrorAssuranceSchedule,
  SimulationResult,
  SourceSystem,
  StageSpec,
  OperatorSpec,
  RatingEnums,
  RatingMe,
  RatingOverview,
  RuleAttribute,
  RuleDetail,
  CanonicalRuleListResponse,
  CanonicalRuleEstateStats,
  CanonicalRuleDetail,
  CanonicalVersionSummary,
  CanonicalAuditEntry,
  CanonicalWriteResponse,
  RuleListResponse,
  RuleSetRow,
  RuleSummary,
  RuleTemplate,
  ValidationReport,
  CanonicalRuleSet,
  CompileReport,
  ExecutableRule,
  SnapshotImpact,
  StageGroup,
  IngestPreview,
  IngestBatch,
  BulkSelector,
  BulkResponse,
  BulkJob,
} from "@/lib/rating/types";

const KEY = "rating";

// The rule vocabulary changes only on a backend deploy, so it is cached for the
// life of the tab. Every builder screen depends on all four; fetching them per
// mount would put four requests in front of every "Create Rule" click.
const VOCAB_OPTIONS = {
  staleTime: Infinity,
  gcTime: Infinity,
  retry: 1,
} satisfies Partial<UseQueryOptions>;

async function get<T>(
  url: string,
  params?: Record<string, unknown>,
): Promise<T> {
  const { data } = await ratingApi.get<T>(url, { params });
  return data;
}

// --- Vocabulary -------------------------------------------------------------

export function useRuleAttributes() {
  return useQuery({
    queryKey: [KEY, "attributes"],
    queryFn: () => get<RuleAttribute[]>("/meta/attributes"),
    ...VOCAB_OPTIONS,
  });
}

export function useOperators() {
  return useQuery({
    queryKey: [KEY, "operators"],
    queryFn: () => get<OperatorSpec[]>("/meta/operators"),
    ...VOCAB_OPTIONS,
  });
}

export function useActionSpecs() {
  return useQuery({
    queryKey: [KEY, "actions"],
    queryFn: () => get<ActionSpec[]>("/meta/actions"),
    ...VOCAB_OPTIONS,
  });
}

export function useRatingEnums() {
  return useQuery({
    queryKey: [KEY, "enums"],
    queryFn: () => get<RatingEnums>("/meta/enums"),
    ...VOCAB_OPTIONS,
  });
}

export function useRatingMe() {
  return useQuery({
    queryKey: [KEY, "me"],
    queryFn: () => get<RatingMe>("/me"),
    staleTime: 60_000,
    retry: false,
  });
}

/** Convenience: can the signed-in user edit rules in the rating service? */
export function useCanEditRules(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingRules?.edit;
}

export function useCanEditCatalog(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingCatalog?.edit;
}

// --- Catalogue --------------------------------------------------------------

export function useCatalogList(
  entity: string,
  params?: Record<string, unknown>,
  enabled = true,
) {
  return useQuery({
    queryKey: [KEY, "catalog", entity, params],
    queryFn: () =>
      get<CatalogEntity[]>(`/catalog/${entity}`, { limit: 500, ...params }),
    enabled,
    staleTime: 30_000,
  });
}

export function useCatalogSummary() {
  return useQuery({
    queryKey: [KEY, "catalog", "summary"],
    queryFn: () => get<CatalogSummaryRow[]>("/catalog/summary"),
    staleTime: 30_000,
  });
}

/**
 * Value options for a REFERENCE-typed condition or action parameter.
 * Defaults to `{value: code, label: "CODE — Name"}` because rules reference the
 * catalogue by *code*, not id — a rule then imports between environments
 * unchanged.
 *
 * Catalog foreign-key columns (`product_id`, `offer_id`, …) are the exception:
 * the API stores the row *id* there, so a code would violate the FK. Callers
 * whose field is an `*_id` column pass `valueKey: "id"`.
 */
export function useReferenceOptions(
  entity: string | null | undefined,
  valueKey: "code" | "id" = "code",
) {
  const { data, isLoading } = useCatalogList(
    entity ?? "",
    { status: "ACTIVE" },
    !!entity,
  );
  const options = (data ?? []).map((row) => ({
    value: valueKey === "id" ? row.id : row.code,
    label: row.code === row.name ? row.code : `${row.code} — ${row.name}`,
  }));
  return { options, isLoading };
}

// --- Rules ------------------------------------------------------------------

export interface RuleFilters {
  search?: string;
  status?: string;
  service_type?: string;
  rule_type?: string;
  limit?: number;
  offset?: number;
  sort?: string;
  order?: string;
}

export function useRules(filters: RuleFilters) {
  return useQuery({
    queryKey: [KEY, "rules", filters],
    queryFn: () =>
      get<RuleListResponse>("/rules", {
        ...filters,
        // Blank filter values must not become `?status=` — the backend would
        // read that as "status equal to empty string" and return nothing.
        search: filters.search || undefined,
        status: filters.status || undefined,
        service_type: filters.service_type || undefined,
        rule_type: filters.rule_type || undefined,
      }),
    staleTime: 10_000,
  });
}

/** Canonical rules written by the ingestion kernel (including file imports). */
export function useCanonicalRules(filters: RuleFilters) {
  return useQuery({
    queryKey: [KEY, "canonical-rules", filters],
    queryFn: () =>
      get<CanonicalRuleListResponse>("/canonical-rules", {
        ...filters,
        search: filters.search || undefined,
        status: filters.status || undefined,
        service_type: filters.service_type || undefined,
        rule_type: filters.rule_type || undefined,
      }),
    staleTime: 10_000,
  });
}

export function useCanonicalRuleStats() {
  return useQuery({
    queryKey: [KEY, "canonical-rules", "stats"],
    queryFn: () => get<CanonicalRuleEstateStats>("/canonical-rules/stats"),
    staleTime: 10_000,
  });
}

/** Create through the canonical kernel so the returned id is the id exposed by
 * the canonical catalogue and detail routes. */
export function useCreateCanonicalRule() {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async (body: unknown) => {
      const { data } = await ratingApi.post<CanonicalWriteResponse>(
        "/canonical-rules",
        body,
      );
      return data;
    },
    onSuccess: (response) => invalidate(response.rule.rule_id),
  });
}

export function useCanonicalRule(ruleId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "canonical-rule", ruleId],
    queryFn: () => get<CanonicalRuleDetail>(`/canonical-rules/${ruleId}`),
    enabled: !!ruleId,
  });
}

export function useCanonicalRuleVersions(ruleId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "canonical-rule", ruleId, "versions"],
    queryFn: () =>
      get<CanonicalVersionSummary[]>(`/canonical-rules/${ruleId}/versions`),
    enabled: !!ruleId,
  });
}

export function useCanonicalRuleAudit(ruleId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "canonical-rule", ruleId, "audit"],
    queryFn: () =>
      get<CanonicalAuditEntry[]>(`/canonical-rules/${ruleId}/audit`),
    enabled: !!ruleId,
  });
}

function useCanonicalRuleInvalidation() {
  const qc = useQueryClient();
  return (ruleId?: string) => {
    qc.invalidateQueries({ queryKey: [KEY, "canonical-rules"] });
    qc.invalidateQueries({ queryKey: [KEY, "overview"] });
    if (ruleId) {
      qc.invalidateQueries({ queryKey: [KEY, "canonical-rule", ruleId] });
    }
  };
}

export function useUpdateCanonicalRule(ruleId: string | undefined) {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async (body: unknown) => {
      const { data } = await ratingApi.patch<CanonicalWriteResponse>(
        `/canonical-rules/${ruleId}`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useValidateCanonicalRule(ruleId: string | undefined) {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async () => {
      const { data } = await ratingApi.post<ValidationReport>(
        `/canonical-rules/${ruleId}/validate`,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useChangeCanonicalRuleStatus(ruleId: string | undefined) {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async (body: { status: string; comment?: string }) => {
      const { data } = await ratingApi.post<CanonicalRuleDetail>(
        `/canonical-rules/${ruleId}/status`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useNewCanonicalRuleVersion(ruleId: string | undefined) {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async (body: unknown) => {
      const { data } = await ratingApi.post<CanonicalWriteResponse>(
        `/canonical-rules/${ruleId}/versions`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useCloneCanonicalRule(ruleId: string | undefined) {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async (body: { rule_name: string; rule_key?: string }) => {
      const { data } = await ratingApi.post<CanonicalWriteResponse>(
        `/canonical-rules/${ruleId}/clone`,
        body,
      );
      return data;
    },
    onSuccess: (response) => invalidate(response.rule.rule_id),
  });
}

export function useDeleteCanonicalRule() {
  const invalidate = useCanonicalRuleInvalidation();
  return useMutation({
    mutationFn: async (ruleId: string) => {
      await ratingApi.delete(`/canonical-rules/${ruleId}`);
    },
    onSuccess: () => invalidate(),
  });
}

export function useRule(ruleId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rule", ruleId],
    queryFn: () => get<RuleDetail>(`/rules/${ruleId}`),
    enabled: !!ruleId,
  });
}

export function useRuleVersions(ruleId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rule", ruleId, "versions"],
    queryFn: () => get<RuleSummary[]>(`/rules/${ruleId}/versions`),
    enabled: !!ruleId,
  });
}

export function useRuleAudit(ruleId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rule", ruleId, "audit"],
    queryFn: () => get<AuditEntry[]>(`/rules/${ruleId}/audit`),
    enabled: !!ruleId,
  });
}

export function useRuleSets() {
  return useQuery({
    queryKey: [KEY, "rule-sets"],
    queryFn: () => get<RuleSetRow[]>("/rule-sets"),
    staleTime: 30_000,
  });
}

export function useRuleTemplates() {
  return useQuery({
    queryKey: [KEY, "rule-templates"],
    queryFn: () => get<RuleTemplate[]>("/rule-templates"),
    staleTime: Infinity,
  });
}

export function useRatingOverview() {
  return useQuery({
    queryKey: [KEY, "overview"],
    queryFn: () => get<RatingOverview>("/dashboards/overview"),
    staleTime: 15_000,
  });
}

// --- Mutations --------------------------------------------------------------

/** Invalidate everything a rule write can affect: list, detail, KPIs, audit. */
function useRuleInvalidation() {
  const qc = useQueryClient();
  return (ruleId?: string) => {
    qc.invalidateQueries({ queryKey: [KEY, "rules"] });
    qc.invalidateQueries({ queryKey: [KEY, "overview"] });
    if (ruleId) qc.invalidateQueries({ queryKey: [KEY, "rule", ruleId] });
  };
}

export function useCreateRule() {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async (body: unknown) => {
      const { data } = await ratingApi.post<RuleDetail>("/rules", body);
      return data;
    },
    onSuccess: (rule) => invalidate(rule.id),
  });
}

export function useUpdateRule(ruleId: string | undefined) {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async (body: unknown) => {
      const { data } = await ratingApi.patch<RuleDetail>(
        `/rules/${ruleId}`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useValidateRule(ruleId: string | undefined) {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async () => {
      const { data } = await ratingApi.post<ValidationReport>(
        `/rules/${ruleId}/validate`,
      );
      return data;
    },
    // Validation caches its verdict on the rule row, so the list's health column
    // is stale until we refetch.
    onSuccess: () => invalidate(ruleId),
  });
}

export function useChangeRuleStatus(ruleId: string | undefined) {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async (body: { status: string; comment?: string }) => {
      const { data } = await ratingApi.post<RuleDetail>(
        `/rules/${ruleId}/status`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useNewRuleVersion(ruleId: string | undefined) {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async (body: { change_comment?: string; name?: string }) => {
      const { data } = await ratingApi.post<RuleDetail>(
        `/rules/${ruleId}/versions`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(ruleId),
  });
}

export function useCloneRule(ruleId: string | undefined) {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async (body: { name: string; rule_key?: string }) => {
      const { data } = await ratingApi.post<RuleDetail>(
        `/rules/${ruleId}/clone`,
        body,
      );
      return data;
    },
    onSuccess: () => invalidate(),
  });
}

export function useDeleteRule() {
  const invalidate = useRuleInvalidation();
  return useMutation({
    mutationFn: async (ruleId: string) => {
      await ratingApi.delete(`/rules/${ruleId}`);
    },
    onSuccess: () => invalidate(),
  });
}

export function useSaveCatalogEntity(entity: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id?: string; body: unknown }) => {
      const { data } = id
        ? await ratingApi.patch<CatalogEntity>(`/catalog/${entity}/${id}`, body)
        : await ratingApi.post<CatalogEntity>(`/catalog/${entity}`, body);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "catalog"] });
      qc.invalidateQueries({ queryKey: [KEY, "overview"] });
    },
  });
}

/** Form definitions for every catalogue, generated by the backend from its models. */
export function useCatalogSchema() {
  return useQuery({
    queryKey: [KEY, "catalog", "schema"],
    queryFn: () => get<EntitySchema[]>("/catalog/schema"),
    ...VOCAB_OPTIONS,
  });
}

// --- File-based rule import -------------------------------------------------

export function useImportColumns() {
  return useQuery({
    queryKey: [KEY, "import", "columns"],
    queryFn: () => get<ImportColumn[]>("/rule-imports/columns"),
    ...VOCAB_OPTIONS,
  });
}

export function useImportCapabilities() {
  return useQuery({
    queryKey: [KEY, "import", "capabilities"],
    queryFn: () => get<ImportCapabilities>("/rule-imports/capabilities"),
    ...VOCAB_OPTIONS,
  });
}

export function useImportHistory() {
  return useQuery({
    queryKey: [KEY, "import", "history"],
    queryFn: () => get<ImportBatch[]>("/rule-imports", { limit: 20 }),
    staleTime: 10_000,
  });
}

function importFormData(
  file: File,
  mapping?: Record<string, string | null>,
  extra?: Record<string, string>,
): FormData {
  const body = new FormData();
  body.append("file", file);
  if (mapping) body.append("mapping", JSON.stringify(mapping));
  Object.entries(extra ?? {}).forEach(([k, v]) => v && body.append(k, v));
  return body;
}

/** Dry run: the backend parses, maps and validates but writes nothing. */
export function usePreviewImport() {
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      mapping?: Record<string, string | null>;
    }) => {
      const { data } = await ratingApi.post<ImportPreview>(
        "/rule-imports/preview",
        importFormData(vars.file, vars.mapping),
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return data;
    },
  });
}

export function useCommitImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      mapping?: Record<string, string | null>;
      rule_set_id?: string;
      source_system?: string;
    }) => {
      const { data } = await ratingApi.post<ImportBatch>(
        "/rule-imports",
        importFormData(vars.file, vars.mapping, {
          rule_set_id: vars.rule_set_id ?? "",
          source_system: vars.source_system ?? "FILE_IMPORT",
        }),
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "rules"] });
      qc.invalidateQueries({ queryKey: [KEY, "canonical-rules"] });
      qc.invalidateQueries({ queryKey: [KEY, "overview"] });
      qc.invalidateQueries({ queryKey: [KEY, "import", "history"] });
    },
  });
}

/** Absolute URL for the rejected-rows CSV, so a plain link can download it. */
export function rejectedRowsUrl(batchId: string): string {
  return `${ratingApi.defaults.baseURL}/rule-imports/${batchId}/rejected.csv`;
}

// --- Phase 2: snapshots -----------------------------------------------------

export function useSnapshots() {
  return useQuery({
    queryKey: [KEY, "snapshots"],
    queryFn: () => get<SnapshotSummary[]>("/rule-snapshots", { limit: 50 }),
    staleTime: 10_000,
  });
}

export function useSnapshot(id: string | undefined) {
  return useQuery({
    queryKey: [KEY, "snapshot", id],
    queryFn: () => get<SnapshotDetail>(`/rule-snapshots/${id}`),
    enabled: !!id,
  });
}

export function useActiveSnapshot() {
  return useQuery({
    queryKey: [KEY, "snapshot", "active"],
    queryFn: () => get<SnapshotDetail | null>("/rule-snapshots/active"),
    staleTime: 10_000,
  });
}

/** Validation across the whole rule set — structural, conflict and coverage. */
export function useValidateRuleSet() {
  return useMutation({
    mutationFn: async (body: { rule_set_id?: string | null } = {}) => {
      const { data } = await ratingApi.post<RuleSetValidationReport>(
        "/rule-validation",
        body,
      );
      return data;
    },
  });
}

function useSnapshotInvalidation() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: [KEY, "snapshots"] });
    qc.invalidateQueries({ queryKey: [KEY, "snapshot"] });
    qc.invalidateQueries({ queryKey: [KEY, "rules"] });
    qc.invalidateQueries({ queryKey: [KEY, "canonical-rules"] });
    qc.invalidateQueries({ queryKey: [KEY, "overview"] });
  };
}

export function useCompileSnapshot() {
  const invalidate = useSnapshotInvalidation();
  return useMutation({
    mutationFn: async (body: {
      name: string;
      description?: string;
      force?: boolean;
    }) => {
      const { data } = await ratingApi.post<SnapshotDetail>(
        "/rule-snapshots",
        body,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useActivateSnapshot() {
  const invalidate = useSnapshotInvalidation();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await ratingApi.post<SnapshotDetail>(
        `/rule-snapshots/${id}/activate`,
        {},
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useRollbackSnapshot() {
  const invalidate = useSnapshotInvalidation();
  return useMutation({
    mutationFn: async () => {
      const { data } = await ratingApi.post<SnapshotDetail>(
        "/rule-snapshots/rollback",
        {},
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useSnapshotDiff(fromId?: string, toId?: string) {
  return useQuery({
    queryKey: [KEY, "snapshot", "diff", fromId, toId],
    queryFn: () =>
      get<SnapshotDiff>("/rule-snapshots/diff", {
        from_id: fromId,
        to_id: toId,
      }),
    enabled: !!fromId && !!toId && fromId !== toId,
  });
}

// --- Phase 3: CDR batches ---------------------------------------------------

export function useCdrBatches() {
  return useQuery({
    queryKey: [KEY, "cdr-batches"],
    queryFn: () => get<CdrBatch[]>("/cdr-batches", { limit: 50 }),
    staleTime: 10_000,
  });
}

export function useCdrProfiles() {
  return useQuery({
    queryKey: [KEY, "cdr-profiles"],
    queryFn: () => get<CdrProfile[]>("/cdr-profiles"),
    ...VOCAB_OPTIONS,
  });
}

export function useIngestCdrBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      cdr_type: string;
      source_system: string;
    }) => {
      const body = new FormData();
      body.append("file", vars.file);
      body.append("cdr_type", vars.cdr_type);
      body.append("source_system", vars.source_system);
      const { data } = await ratingApi.post<CdrBatch>("/cdr-batches", body, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY, "cdr-batches"] }),
  });
}

// --- Phase 4: rating runs, results, exceptions ------------------------------

export function useRatingRuns(batchId?: string) {
  return useQuery({
    queryKey: [KEY, "rating-runs", batchId],
    queryFn: () =>
      get<RatingRun[]>("/rating-runs", { limit: 50, batch_id: batchId }),
    staleTime: 5_000,
  });
}

export function useRunSummary(runId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rating-run", runId, "summary"],
    queryFn: () => get<RunSummary>(`/rating-runs/${runId}/summary`),
    enabled: !!runId,
  });
}

export function useStartRatingRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { batch_id: string; snapshot_id?: string }) => {
      // Rating a batch is long-running; the request holds until it completes.
      const { data } = await ratingApi.post<RatingRun>("/rating-runs", body, {
        timeout: 0,
      });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "rating-runs"] });
      qc.invalidateQueries({ queryKey: [KEY, "exceptions"] });
      qc.invalidateQueries({ queryKey: [KEY, "overview"] });
    },
  });
}

export interface ResultFilters {
  run_id?: string;
  status?: string;
  msisdn?: string;
  cdr_id?: string;
  product_code?: string;
  service_type?: string;
  root_cause?: string;
  date_from?: string;
  date_to?: string;
  min_variance?: number;
  limit?: number;
  offset?: number;
}

/** The reconciliation record explorer: one row per rated CDR, across runs. */
export function useRatingResults(filters: ResultFilters = {}) {
  return useQuery({
    queryKey: [KEY, "rating-results", filters],
    queryFn: () =>
      get<RatingResultRow[]>("/rating-results", {
        limit: filters.limit ?? 100,
        offset: filters.offset || undefined,
        run_id: filters.run_id || undefined,
        status: filters.status || undefined,
        msisdn: filters.msisdn || undefined,
        cdr_id: filters.cdr_id || undefined,
        product_code: filters.product_code || undefined,
        service_type: filters.service_type || undefined,
        root_cause: filters.root_cause || undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
        min_variance: filters.min_variance || undefined,
      }),
    staleTime: 5_000,
  });
}

export function useRatingRun(runId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rating-run", runId],
    queryFn: () => get<RatingRun>(`/rating-runs/${runId}`),
    enabled: !!runId,
  });
}

export function useRunContexts(runId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rating-run", runId, "contexts"],
    queryFn: () =>
      get<Record<string, unknown>[]>(`/rating-runs/${runId}/contexts`, {
        limit: 50,
      }),
    enabled: !!runId,
  });
}

export function useRatingResult(resultId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "rating-result", resultId],
    queryFn: () => get<RatingResultDetail>(`/rating-results/${resultId}`),
    enabled: !!resultId,
  });
}

export interface EnrichedCdr {
  id: string;
  cdr_id: string;
  subscriber_id: string | null;
  msisdn: string | null;
  service_type: string;
  event_timestamp: string;
  event_date: string;
  duration_seconds: number | null;
  usage_volume: number | null;
  calling_number: string | null;
  called_number: string | null;
  actual_charge: number | null;
  currency: string | null;
  product_code: string | null;
  account_type: string | null;
  destination_zone: string | null;
  on_net: boolean | null;
  time_band: string | null;
  roaming: boolean | null;
  context_key: string | null;
  context_hash: string | null;
  quality_status: string;
  quality_detail: string | null;
}

export function useEnrichedCdr(cdrEnrichedId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "cdr", cdrEnrichedId],
    queryFn: () => get<EnrichedCdr>(`/cdrs/${cdrEnrichedId}`),
    enabled: !!cdrEnrichedId,
  });
}

export function useExceptions(
  filters: {
    run_id?: string;
    status?: string;
    severity?: string;
    root_cause?: string;
  } = {},
) {
  return useQuery({
    queryKey: [KEY, "exceptions", filters],
    queryFn: () =>
      get<RatingExceptionRow[]>("/exceptions", {
        limit: 100,
        run_id: filters.run_id || undefined,
        status: filters.status || undefined,
        severity: filters.severity || undefined,
        root_cause: filters.root_cause || undefined,
      }),
    staleTime: 5_000,
  });
}

export function useException(id: string | undefined) {
  return useQuery({
    queryKey: [KEY, "exception", id],
    queryFn: () => get<RatingExceptionDetail>(`/exceptions/${id}`),
    enabled: !!id,
  });
}

export function useExceptionMeta() {
  return useQuery({
    queryKey: [KEY, "exception-meta"],
    queryFn: () => get<ExceptionMeta>("/exceptions/meta"),
    ...VOCAB_OPTIONS,
  });
}

export function useTransitionException(id: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      status: string;
      comment?: string;
      resolution?: string;
      assigned_to?: string;
      assigned_to_name?: string;
    }) => {
      const { data } = await ratingApi.post<RatingExceptionDetail>(
        `/exceptions/${id}/transition`,
        body,
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "exception", id] });
      qc.invalidateQueries({ queryKey: [KEY, "exceptions"] });
    },
  });
}

/** The CDRs behind an exception group, worst variance first. */
export function useExceptionResults(id: string | undefined) {
  return useQuery({
    queryKey: [KEY, "exception", id, "results"],
    queryFn: () =>
      get<RatingResultRow[]>(`/exceptions/${id}/results`, { limit: 50 }),
    enabled: !!id,
  });
}

export function useAddExceptionComment(id: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { body: string }) => {
      const { data } = await ratingApi.post<RatingExceptionDetail>(
        `/exceptions/${id}/comments`,
        body,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY, "exception", id] }),
  });
}

// --- Bundle balances --------------------------------------------------------

export interface BalanceBucketRow {
  id: string;
  owner_key: string;
  owner_type: string;
  subscriber_id: string | null;
  account_id: string | null;
  msisdn: string | null;
  bundle_code: string;
  service_type: string;
  quota_unit: string;
  shared: boolean;
  period_start: string;
  period_end: string;
  reset_period: string;
  allocated: number;
  consumed: number;
  overflow: number;
  remaining: number;
  consumption_count: number;
  last_consumed_at: string | null;
  source_system: string;
}

export interface BalanceLedgerRow {
  id: string;
  run_id: string;
  cdr_enriched_id: string;
  cdr_id: string;
  event_timestamp: string;
  requested: number;
  consumed: number;
  overflow: number;
  balance_before: number;
  balance_after: number;
  unit: string;
  rule_key: string | null;
}

export function useBalanceBuckets(
  filters: { owner?: string; bundle_code?: string; exhausted?: boolean } = {},
) {
  return useQuery({
    queryKey: [KEY, "balance-buckets", filters],
    queryFn: () =>
      get<BalanceBucketRow[]>("/balances/buckets", {
        limit: 100,
        owner: filters.owner || undefined,
        bundle_code: filters.bundle_code || undefined,
        exhausted: filters.exhausted,
      }),
    staleTime: 10_000,
  });
}

export function useBalanceStats() {
  return useQuery({
    queryKey: [KEY, "balance-stats"],
    queryFn: () =>
      get<{
        total_buckets: number;
        exhausted_buckets: number;
        ledger_entries: number;
      }>("/balances/buckets/stats"),
    staleTime: 10_000,
  });
}

export function useBucketLedger(bucketId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "balance-ledger", bucketId],
    queryFn: () =>
      get<BalanceLedgerRow[]>(`/balances/buckets/${bucketId}/ledger`, {
        limit: 200,
      }),
    enabled: !!bucketId,
  });
}

// --- Assurance dashboard ----------------------------------------------------

export function useAssuranceDashboard(
  params: { date_from?: string; date_to?: string; run_id?: string } = {},
) {
  return useQuery({
    queryKey: [KEY, "assurance-dashboard", params],
    queryFn: () =>
      get<AssuranceDashboard>("/dashboards/assurance", {
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
        run_id: params.run_id || undefined,
      }),
    staleTime: 15_000,
  });
}

export function useLeakage(
  dimension: string,
  params: { date_from?: string; date_to?: string; run_id?: string } = {},
) {
  return useQuery({
    queryKey: [KEY, "leakage", dimension, params],
    queryFn: () =>
      get<LeakageRow[]>("/dashboards/leakage", {
        dimension,
        limit: 25,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
        run_id: params.run_id || undefined,
      }),
    staleTime: 15_000,
  });
}

export function useCanEditRuns(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingRuns?.edit;
}

export function useCanEditSnapshots(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingSnapshots?.edit;
}

export function useCanEditExceptions(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingExceptions?.edit;
}

// --- Pipeline ---------------------------------------------------------------

export function usePipelineStages() {
  return useQuery({
    queryKey: [KEY, "pipeline", "stages"],
    queryFn: () => get<StageSpec[]>("/pipeline/stages"),
    ...VOCAB_OPTIONS,
  });
}

export function usePipelineRuns() {
  return useQuery({
    queryKey: [KEY, "pipeline", "runs"],
    queryFn: () => get<PipelineRun[]>("/pipeline/runs", { limit: 30 }),
    // A run in flight needs polling; a settled list does not.
    refetchInterval: (query) => {
      const rows = query.state.data as PipelineRun[] | undefined;
      return rows?.some((r) => r.status === "RUNNING" || r.status === "QUEUED")
        ? 2000
        : false;
    },
    staleTime: 1000,
  });
}

export function usePipelineRun(runId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "pipeline", "run", runId],
    queryFn: () => get<PipelineRunDetail>(`/pipeline/runs/${runId}`),
    enabled: !!runId,
    // Poll only while the run is moving — once it settles, stop.
    refetchInterval: (query) => {
      const run = query.state.data as PipelineRunDetail | undefined;
      return run && (run.status === "RUNNING" || run.status === "QUEUED")
        ? 1500
        : false;
    },
  });
}

export function useStartPipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      cdr_type: string;
      source_system: string;
    }) => {
      const body = new FormData();
      body.append("file", vars.file);
      body.append("cdr_type", vars.cdr_type);
      body.append("source_system", vars.source_system);
      const { data } = await ratingApi.post<PipelineRun>(
        "/pipeline/runs",
        body,
        {
          headers: { "Content-Type": "multipart/form-data" },
        },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "pipeline"] });
      qc.invalidateQueries({ queryKey: [KEY, "cdr-batches"] });
    },
  });
}

export function useRetryStage(runId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (stage: string) => {
      const { data } = await ratingApi.post<PipelineRun>(
        `/pipeline/runs/${runId}/retry`,
        {},
        { params: { stage } },
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [KEY, "pipeline"] }),
  });
}

// --- Mirror rating assurance ------------------------------------------------

export function useMirrorAssuranceSchedule() {
  return useQuery({
    queryKey: [KEY, "mirror-assurance", "schedule"],
    queryFn: () => get<MirrorAssuranceSchedule>("/mirror-assurance/schedule"),
  });
}

export function useSaveMirrorAssuranceSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      enabled: boolean;
      interval_minutes: number;
      window_hours: number;
      tolerance: number;
    }) => {
      const { data } = await ratingApi.put<MirrorAssuranceSchedule>(
        "/mirror-assurance/schedule",
        payload,
      );
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: [KEY, "mirror-assurance", "schedule"],
      }),
  });
}

export function useMirrorAssuranceRuns() {
  return useQuery({
    queryKey: [KEY, "mirror-assurance", "runs"],
    queryFn: () =>
      get<MirrorAssuranceRun[]>("/mirror-assurance/runs", { limit: 20 }),
    refetchInterval: (query) => {
      const rows = query.state.data as MirrorAssuranceRun[] | undefined;
      return rows?.some((row) => ["QUEUED", "RUNNING"].includes(row.status))
        ? 2000
        : false;
    },
    staleTime: 1000,
  });
}

export function useMirrorAssuranceRun(runId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "mirror-assurance", "run", runId],
    queryFn: () => get<MirrorAssuranceRun>(`/mirror-assurance/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) => {
      const run = query.state.data as MirrorAssuranceRun | undefined;
      return run && ["QUEUED", "RUNNING"].includes(run.status) ? 1500 : false;
    },
  });
}

export function useStartMirrorAssurance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      window_start: string;
      window_end: string;
      tolerance: number;
    }) => {
      const { data } = await ratingApi.post<MirrorAssuranceRun>(
        "/mirror-assurance/runs",
        payload,
      );
      return data;
    },
    onSuccess: (run) => {
      qc.setQueryData([KEY, "mirror-assurance", "run", run.id], run);
      qc.invalidateQueries({ queryKey: [KEY, "mirror-assurance", "runs"] });
    },
  });
}

export function useMirrorAssuranceResults(
  runId: string | undefined,
  offset: number,
  status: string,
  active: boolean,
) {
  return useQuery({
    queryKey: [KEY, "mirror-assurance", "results", runId, offset, status],
    queryFn: () =>
      get<MirrorAssuranceResultPage>(
        `/mirror-assurance/runs/${runId}/results`,
        { limit: 50, offset, status: status || undefined },
      ),
    enabled: !!runId,
    refetchInterval: active ? 2000 : false,
  });
}

// --- Connectors -------------------------------------------------------------

export function useConnectorCatalog() {
  return useQuery({
    queryKey: [KEY, "connector-catalog"],
    queryFn: () => get<ConnectorCatalog>("/connector-catalog"),
    ...VOCAB_OPTIONS,
  });
}

export function useSourceSystems() {
  return useQuery({
    queryKey: [KEY, "source-systems"],
    queryFn: () => get<SourceSystem[]>("/source-systems", { limit: 100 }),
    staleTime: 10_000,
  });
}

export function useConnectorImports(sourceId: string | undefined) {
  return useQuery({
    queryKey: [KEY, "source-systems", sourceId, "imports"],
    queryFn: () =>
      get<ConnectorImport[]>(`/source-systems/${sourceId}/imports`, {
        limit: 20,
      }),
    enabled: !!sourceId,
  });
}

function useConnectorInvalidation() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: [KEY, "source-systems"] });
    qc.invalidateQueries({ queryKey: [KEY, "rules"] });
    qc.invalidateQueries({ queryKey: [KEY, "overview"] });
  };
}

export function useCreateSourceSystem() {
  const invalidate = useConnectorInvalidation();
  return useMutation({
    mutationFn: async (body: unknown) => {
      const { data } = await ratingApi.post<SourceSystem>(
        "/source-systems",
        body,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useTestConnection() {
  const invalidate = useConnectorInvalidation();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await ratingApi.post<SourceSystem>(
        `/source-systems/${id}/test`,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useRunConnectorImport() {
  const invalidate = useConnectorInvalidation();
  return useMutation({
    mutationFn: async (vars: {
      id: string;
      file?: File;
      dry_run?: boolean;
    }) => {
      const body = new FormData();
      if (vars.file) body.append("file", vars.file);
      body.append("dry_run", String(!!vars.dry_run));
      const { data } = await ratingApi.post<ConnectorImport>(
        `/source-systems/${vars.id}/import`,
        body,
        { headers: { "Content-Type": "multipart/form-data" }, timeout: 0 },
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteSourceSystem() {
  const invalidate = useConnectorInvalidation();
  return useMutation({
    mutationFn: async (id: string) => {
      await ratingApi.delete(`/source-systems/${id}`);
    },
    onSuccess: invalidate,
  });
}

// --- Simulation -------------------------------------------------------------

export function useSimulate() {
  return useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const { data } = await ratingApi.post<SimulationResult>(
        "/rating-simulation",
        body,
      );
      return data;
    },
  });
}

export function useCanSimulate(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingSimulation?.view;
}

// --- Canonical ingestion + bulk lifecycle (plan B6) -------------------------
//
// These sit alongside the legacy `/rule-imports` hooks rather than replacing
// them. `/rule-ingest` runs the file through the ingestion kernel, which is what
// makes `charging_mode`, the full rule-type vocabulary and nested XML survive
// the trip — the legacy importer silently discards all three. It also returns a
// **rule set**, and the rule set is what makes an import addressable afterwards:
// validate it, approve it, activate it, roll it back, as one unit.

/** Can the signed-in user approve rules? Bulk actions on live pricing need it. */
export function useCanApproveRules(): boolean {
  const { data } = useRatingMe();
  return !!data?.permissions?.ratingApprovals?.edit;
}

export function useIngestColumns() {
  return useQuery({
    queryKey: [KEY, "ingest", "columns"],
    queryFn: () => get<ImportColumn[]>("/rule-ingest/columns"),
    ...VOCAB_OPTIONS,
  });
}

/**
 * Rule sets `/rule-ingest` will actually accept.
 *
 * Not `useRuleSets`, which lists the *legacy* `rating.rule_sets`. The two are
 * different tables in different schemas, and handing a legacy id to the ingest
 * endpoint produces a 404 that reads like the set was deleted.
 */
export function useCanonicalRuleSets() {
  return useQuery({
    queryKey: [KEY, "ingest", "rule-sets"],
    queryFn: () => get<CanonicalRuleSet[]>("/rule-ingest/rule-sets"),
    staleTime: 30_000,
  });
}

export function useIngestBatches(limit = 20) {
  return useQuery({
    queryKey: [KEY, "ingest", "batches", limit],
    queryFn: () =>
      get<{ items: IngestBatch[]; total: number }>("/rule-ingest/batches", {
        limit,
      }),
    staleTime: 10_000,
  });
}

function ingestForm(
  file: File,
  vars: Record<string, unknown> = {},
  mapping?: Record<string, string | null>,
): FormData {
  const body = new FormData();
  body.append("file", file);
  if (mapping) body.append("mapping", JSON.stringify(mapping));
  Object.entries(vars).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    body.append(k, typeof v === "boolean" ? String(v) : String(v));
  });
  return body;
}

/** Dry run through the kernel: it parses, resolves, validates and rolls back. */
export function useIngestPreview() {
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      mapping?: Record<string, string | null>;
      source_system_code?: string;
      import_mode?: string;
      default_charging_mode?: string;
      default_currency?: string;
      effective_from?: string;
    }) => {
      const { file, mapping, ...rest } = vars;
      const { data } = await ratingApi.post<IngestPreview>(
        "/rule-ingest/preview",
        ingestForm(file, rest, mapping),
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return data;
    },
  });
}

export function useIngestCommit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      mapping?: Record<string, string | null>;
      source_system_code?: string;
      import_mode?: string;
      default_charging_mode?: string;
      default_currency?: string;
      effective_from?: string;
      rule_set_id?: string;
      create_rule_set?: boolean;
      create_missing_references?: boolean;
    }) => {
      const { file, mapping, ...rest } = vars;
      const { data } = await ratingApi.post<IngestBatch>(
        "/rule-ingest/batches",
        ingestForm(file, rest, mapping),
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "rules"] });
      qc.invalidateQueries({ queryKey: [KEY, "canonical-rules"] });
      qc.invalidateQueries({ queryKey: [KEY, "overview"] });
      qc.invalidateQueries({ queryKey: [KEY, "ingest", "batches"] });
    },
  });
}

/** Absolute URL for the quarantined-rows CSV, so a plain link downloads it. */
export function ingestRejectsUrl(batchId: string): string {
  return `${ratingApi.defaults.baseURL}/rule-ingest/batches/${batchId}/rejects.csv`;
}

// --- Bulk lifecycle ---------------------------------------------------------

/**
 * What a bulk operation *would* do. The same call as the real thing minus the
 * write, so the preview cannot drift from the action it previews.
 */
export function useBulkPreview() {
  return useMutation({
    mutationFn: async (
      vars: BulkSelector & { operation?: "validate" | "approve" | "revert" },
    ) => {
      const { data } = await ratingApi.post<BulkResponse>(
        "/rule-lifecycle/preview",
        { operation: "approve", ...vars },
      );
      return data;
    },
  });
}

/** Run a bulk operation synchronously. Refused above 2,000 rules — use a job. */
export function useBulkAction(
  operation: "validate" | "approve" | "revert" | "activate" | "delete",
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      vars: BulkSelector & {
        comment?: string;
        atomic?: boolean;
        force?: boolean;
        dry_run?: boolean;
      },
    ) => {
      const { data } = await ratingApi.post<BulkResponse>(
        `/rule-lifecycle/${operation}`,
        vars,
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "rules"] });
      qc.invalidateQueries({ queryKey: [KEY, "canonical-rules"] });
      qc.invalidateQueries({ queryKey: [KEY, "overview"] });
      qc.invalidateQueries({ queryKey: [KEY, "snapshots"] });
      qc.invalidateQueries({ queryKey: [KEY, "ingest", "batches"] });
    },
  });
}

/** Queue a bulk operation. Returns immediately with a run to poll. */
export function useSubmitBulkJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      vars: BulkSelector & {
        operation: "validate" | "approve" | "revert" | "activate" | "delete";
        comment?: string;
        atomic?: boolean;
        force?: boolean;
        dry_run?: boolean;
      },
    ) => {
      const { data } = await ratingApi.post<BulkJob>(
        "/rule-lifecycle/jobs",
        vars,
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [KEY, "lifecycle", "jobs"] });
    },
  });
}

/**
 * Poll one run until it reaches a terminal state.
 *
 * Polling stops on its own rather than running forever: a screen left open on a
 * finished job should not keep a request every second going all afternoon.
 */
export function useBulkJob(runId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: [KEY, "lifecycle", "jobs", runId],
    enabled: !!runId,
    queryFn: async () => {
      const job = await get<BulkJob>(`/rule-lifecycle/jobs/${runId}`);
      if (job.status === "SUCCEEDED" || job.status === "REJECTED") {
        // The estate moved. Anything showing rule status is now stale.
        qc.invalidateQueries({ queryKey: [KEY, "rules"] });
        qc.invalidateQueries({ queryKey: [KEY, "snapshots"] });
      }
      return job;
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "PENDING" || status === "RUNNING" ? 1_000 : false;
    },
  });
}

export function useBulkJobHistory(limit = 20) {
  return useQuery({
    queryKey: [KEY, "lifecycle", "jobs", "history", limit],
    queryFn: () =>
      get<{ items: BulkJob[]; total: number }>("/rule-lifecycle/jobs", {
        limit,
      }),
    staleTime: 5_000,
  });
}

// --- Snapshot detail --------------------------------------------------------
//
// Each section of the detail page fetches independently. A snapshot's impact
// analysis reads rated traffic and is by far the slowest of these; putting it in
// one combined request would make the rule list — the thing the operator came
// for — wait for it.

export function useSnapshotRules(
  id: string | null,
  params: {
    execution_stage?: string;
    service_type?: string;
    limit?: number;
  } = {},
) {
  return useQuery({
    queryKey: [KEY, "snapshot", id, "rules", params],
    enabled: !!id,
    queryFn: () =>
      get<ExecutableRule[]>(`/rule-snapshots/${id}/rules`, {
        limit: params.limit ?? 200,
        execution_stage: params.execution_stage || undefined,
        service_type: params.service_type || undefined,
      }),
    staleTime: 30_000,
  });
}

export function useSnapshotReport(id: string | null) {
  return useQuery({
    queryKey: [KEY, "snapshot", id, "report"],
    enabled: !!id,
    queryFn: () => get<CompileReport>(`/rule-snapshots/${id}/report`),
    staleTime: 30_000,
  });
}

export function useSnapshotExecutionOrder(id: string | null) {
  return useQuery({
    queryKey: [KEY, "snapshot", id, "execution-order"],
    enabled: !!id,
    queryFn: () => get<StageGroup[]>(`/rule-snapshots/${id}/execution-order`),
    staleTime: 30_000,
  });
}

/** Slowest of the sections — it measures against rated traffic. */
export function useSnapshotImpact(id: string | null) {
  return useQuery({
    queryKey: [KEY, "snapshot", id, "impact"],
    enabled: !!id,
    queryFn: () => get<SnapshotImpact>(`/rule-snapshots/${id}/impact`),
    staleTime: 60_000,
  });
}

/** Diff against the snapshot that came before this one. */
export function useSnapshotDiffWithPrevious(id: string | null) {
  return useQuery({
    queryKey: [KEY, "snapshot", id, "diff-previous"],
    enabled: !!id,
    queryFn: () => get<SnapshotDiff>(`/rule-snapshots/${id}/diff`),
    staleTime: 30_000,
  });
}

/** Absolute URL, so a plain link downloads it with the browser's own progress. */
export function snapshotExportUrl(id: string, format: "csv" | "json" | "xml") {
  return `${ratingApi.defaults.baseURL}/rule-snapshots/${id}/export?format=${format}`;
}
