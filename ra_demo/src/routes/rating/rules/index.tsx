import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  AlertOctagon,
  BookOpenCheck,
  ChevronLeft,
  ChevronRight,
  FileCog,
  Layers,
  Search,
  ShieldCheck,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import {
  useCanEditRules,
  useCanonicalRules,
  useCanonicalRuleStats,
  useRatingEnums,
} from "@/lib/rating/hooks";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import { RuleStatusBadge } from "@/components/rating/RuleStatusBadge";
import { BulkDeleteBar } from "@/components/rating/BulkDeleteBar";

export const Route = createFileRoute("/rating/rules/")({
  component: RuleCataloguePage,
});

const PAGE_SIZE = 25;

function RuleCataloguePage() {
  const t = useT();
  const canEdit = useCanEditRules();
  const { data: enums } = useRatingEnums();
  const { data: estate } = useCanonicalRuleStats();

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [serviceType, setServiceType] = useState("");
  const [ruleType, setRuleType] = useState("");
  const [page, setPage] = useState(0);
  // Ticked rules, by key rather than by id — the key is what the bulk endpoint
  // takes, and what makes the resulting audit entry readable.
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const toggle = (key: string) =>
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const filters = {
    search,
    status,
    service_type: serviceType,
    rule_type: ruleType,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const { data, isLoading, error, refetch, isFetching } =
    useCanonicalRules(filters);

  // Any filter change invalidates the current page number — otherwise a
  // narrowed result set lands the user on an empty page 4.
  const setFilter = (fn: () => void) => {
    fn();
    setPage(0);
  };

  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  return (
    <AppShell>
      <PageHeader
        title={t("Rule Catalogue")}
        description={t(
          "Every canonical tariff rule, one row per logical rule at its latest version. Selection is resolved by specificity first, then priority.",
        )}
        info={t(
          "A rule is editable only while it is a draft. After approval a change means a new version, so a historical charge can always be re-explained against the exact rule that produced it.",
        )}
        actions={
          canEdit && (
            <Link
              to="/rating/rules/new"
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <FileCog className="h-4 w-4" /> {t("Create Rule")}
            </Link>
          )
        }
      />

      {estate && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
          <StatTile
            icon={BookOpenCheck}
            label={t("Logical rules")}
            value={String(estate.logical_rules)}
          />
          <StatTile
            icon={Layers}
            label={t("Versions")}
            value={String(estate.total_versions)}
          />
          <StatTile
            icon={ShieldCheck}
            label={t("Awaiting approval")}
            value={String(estate.pending_approval)}
          />
          <StatTile
            icon={AlertOctagon}
            label={t("With errors")}
            value={String(estate.rules_with_errors)}
          />
        </div>
      )}

      {/* --- Filters --------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative flex-1 min-w-[220px] max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setFilter(() => setSearch(e.target.value))}
            placeholder={t("Search name, key or description")}
            className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
          />
        </div>
        <Select
          value={status}
          onChange={(v) => setFilter(() => setStatus(v))}
          options={[
            { value: "", label: t("All statuses") },
            ...(enums?.rule_status ?? []).map((v) => ({ value: v, label: v })),
          ]}
          minWidth={150}
          ariaLabel={t("Filter by status")}
        />
        <Select
          value={serviceType}
          onChange={(v) => setFilter(() => setServiceType(v))}
          options={[
            { value: "", label: t("All services") },
            ...(enums?.service_type ?? []).map((v) => ({ value: v, label: v })),
          ]}
          minWidth={140}
          ariaLabel={t("Filter by service type")}
        />
        <Select
          value={ruleType}
          onChange={(v) => setFilter(() => setRuleType(v))}
          options={[
            { value: "", label: t("All rule types") },
            ...(enums?.rule_type ?? []).map((v) => ({ value: v, label: v })),
          ]}
          minWidth={170}
          ariaLabel={t("Filter by rule type")}
        />
      </div>

      {isLoading && <RatingLoading label="Loading rules…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {data && data.items.length === 0 && (
        <RatingEmpty
          icon={BookOpenCheck}
          title={
            total === 0 && !search && !status
              ? "No rules yet"
              : "No rules match these filters"
          }
          description={
            total === 0 && !search && !status
              ? "Author the first tariff rule, or run `python -m app.seed` in ra_rating_backend to load the worked example."
              : "Try widening the search or clearing a filter."
          }
          action={
            canEdit && (
              <Link
                to="/rating/rules/new"
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
              >
                <FileCog className="h-4 w-4" /> {t("Create Rule")}
              </Link>
            )
          }
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40 text-left">
                    {canEdit && (
                      <th className="w-10 px-4 py-2.5">
                        <input
                          type="checkbox"
                          aria-label={t("Select every rule on this page")}
                          className="rounded border-border"
                          checked={
                            data.items.length > 0 &&
                            data.items.every((r) => picked.has(r.rule_key))
                          }
                          onChange={(e) =>
                            setPicked((current) => {
                              const next = new Set(current);
                              // Only this page: a header tick that silently
                              // selected 4,000 unseen rules would be the exact
                              // UI bug that makes bulk actions dangerous.
                              data.items.forEach((r) =>
                                e.target.checked
                                  ? next.add(r.rule_key)
                                  : next.delete(r.rule_key),
                              );
                              return next;
                            })
                          }
                        />
                      </th>
                    )}
                    <Th>{t("Rule")}</Th>
                    <Th>{t("Type")}</Th>
                    <Th>{t("Service")}</Th>
                    <Th>{t("Status")}</Th>
                    <Th className="text-right">{t("Priority")}</Th>
                    <Th className="text-right">{t("Specificity")}</Th>
                    <Th className="text-right">{t("Logic")}</Th>
                    <Th>{t("Effective")}</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.items.map((rule) => (
                    <tr
                      key={rule.rule_id}
                      className={`hover:bg-muted/30 transition-colors ${
                        picked.has(rule.rule_key) ? "bg-primary/5" : ""
                      }`}
                    >
                      {canEdit && (
                        <td className="px-4 py-3 align-top">
                          <input
                            type="checkbox"
                            aria-label={`${t("Select")} ${rule.rule_key}`}
                            className="rounded border-border"
                            checked={picked.has(rule.rule_key)}
                            onChange={() => toggle(rule.rule_key)}
                          />
                        </td>
                      )}
                      <td className="px-4 py-3 align-top">
                        <Link
                          to="/rating/rules/$ruleId"
                          params={{ ruleId: rule.rule_id }}
                          className="font-medium text-foreground hover:text-primary transition-colors"
                        >
                          {rule.rule_name}
                        </Link>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="font-mono text-[11px] text-muted-foreground">
                            {rule.rule_key}
                          </span>
                          <span className="text-[11px] text-muted-foreground">
                            v{rule.version_number ?? 1}
                          </span>
                          {rule.validation_state === "ERROR" && (
                            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded border bg-destructive/10 text-destructive border-destructive/20">
                              {t("errors")}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top">
                        <div className="text-foreground">
                          {rule.rule_type_code}
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {rule.stage_code}
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top text-muted-foreground">
                        {rule.service_type}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <RuleStatusBadge status={rule.status} />
                      </td>
                      <td className="px-4 py-3 align-top text-right tabular-nums text-muted-foreground">
                        {rule.priority ?? "—"}
                      </td>
                      <td className="px-4 py-3 align-top text-right tabular-nums text-muted-foreground">
                        {rule.specificity_score ?? "—"}
                      </td>
                      <td className="px-4 py-3 align-top text-right tabular-nums text-muted-foreground">
                        {rule.condition_count}c / {rule.action_count}a
                      </td>
                      <td className="px-4 py-3 align-top text-muted-foreground whitespace-nowrap">
                        {rule.effective_from}
                        {rule.effective_to ? ` → ${rule.effective_to}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {canEdit && (
            <BulkDeleteBar
              ruleKeys={[...picked]}
              onClear={() => setPicked(new Set())}
              onDone={() => void refetch()}
            />
          )}

          <div className="flex items-center justify-between gap-3 mt-4">
            <div className="text-xs text-muted-foreground">
              {t("Showing")} {page * PAGE_SIZE + 1}–
              {Math.min((page + 1) * PAGE_SIZE, total)} {t("of")} {total}
              {isFetching && ` · ${t("refreshing…")}`}
            </div>
            <div className="flex items-center gap-2">
              <PageButton
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                icon={ChevronLeft}
                label={t("Previous")}
              />
              <PageButton
                disabled={page >= lastPage}
                onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                icon={ChevronRight}
                label={t("Next")}
              />
            </div>
          </div>
        </>
      )}
    </AppShell>
  );
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground ${className}`}
    >
      {children}
    </th>
  );
}

function PageButton({
  disabled,
  onClick,
  icon: Icon,
  label,
}: {
  disabled: boolean;
  onClick: () => void;
  icon: typeof ChevronLeft;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="h-8 w-8 rounded-lg border border-border flex items-center justify-center text-muted-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition"
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}
