import { createFileRoute, Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  CalendarClock,
  CheckCircle2,
  FileCog,
  FileSpreadsheet,
  Library,
  Plug,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { useT } from "@/lib/i18n";
import { useRatingOverview } from "@/lib/rating/hooks";
import { RatingError, RatingLoading } from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/rules/overview")({
  component: RuleOperationsPage,
});

const CATALOG_ROWS: { key: keyof CatalogCounts; label: string }[] = [
  { key: "products", label: "Products" },
  { key: "offers", label: "Offers" },
  { key: "tariff_plans", label: "Tariff plans" },
  { key: "destination_zones", label: "Destination zones" },
  { key: "destination_prefixes", label: "Destination prefixes" },
  { key: "time_bands", label: "Time bands" },
  { key: "tax_rules", label: "Tax rules" },
];

type CatalogCounts = {
  products: number;
  offers: number;
  tariff_plans: number;
  destination_zones: number;
  destination_prefixes: number;
  time_bands: number;
  tax_rules: number;
};

function RuleOperationsPage() {
  const t = useT();
  const { data, isLoading, error, refetch } = useRatingOverview();

  return (
    <AppShell>
      <PageHeader
        title={t("Rule Operations")}
        description={t(
          "The rule workflow end to end: bring rules in from a vendor system, a file, or by hand — then validate, approve, compile and activate them as one immutable snapshot.",
        )}
        info={t(
          "Every CDR gets its own rating result, but rule resolution runs in bulk over indexed rating contexts rather than testing every CDR against every rule.",
        )}
        actions={
          <Link
            to="/rating/rules/new"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
          >
            <FileCog className="h-4 w-4" /> {t("Create Rule")}
          </Link>
        }
      />

      {isLoading && <RatingLoading label="Loading the rating overview…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {/* --- Three ways in --------------------------------------------------
          Every rule enters through exactly one of these doors, and they all
          converge on the same lifecycle: draft -> validate -> review ->
          approve -> compile -> activate. The doors differ; the ladder never
          does. */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <IngestCard
          to="/rating/connectors"
          icon={Plug}
          title={t("Connect a source system")}
          description={t(
            "Pull tariffs straight from Ericsson Charging, Oracle BRM or Huawei CBS. Re-imports detect changes by fingerprint, so a nightly sync only versions what actually changed.",
          )}
          cta={t("Open Rule Sources")}
        />
        <IngestCard
          to="/rating/rules/import"
          icon={FileSpreadsheet}
          title={t("Upload a rules file")}
          description={t(
            "CSV, Excel, JSON or XML — columns are auto-mapped, every row validated, and nothing touches a live rule until the batch is committed as drafts.",
          )}
          cta={t("Import Rules")}
        />
        <IngestCard
          to="/rating/rules/new"
          icon={FileCog}
          title={t("Create a rule manually")}
          description={t(
            "The full builder: conditions over 25 CDR attributes, every action type from rates and pulses to bundles, tiers and tax — with validation and simulation before it can go anywhere near approval.",
          )}
          cta={t("Create Rule")}
        />
      </section>

      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <StatTile
              icon={BookOpenCheck}
              label={t("Tariff rules")}
              value={String(data.rule_estate.logical_rules)}
            />
            <StatTile
              icon={FileCog}
              label={t("Drafts")}
              value={String(data.rule_estate.draft_count)}
            />
            <StatTile
              icon={ShieldCheck}
              label={t("Awaiting approval")}
              value={String(data.rule_estate.pending_approval)}
            />
            <StatTile
              icon={CheckCircle2}
              label={t("Active")}
              value={String(data.rule_estate.active_count)}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* --- Rule health ------------------------------------------------ */}
            <section className="bg-card border border-border rounded-xl p-5">
              <h2 className="text-sm font-semibold text-foreground mb-4">
                {t("Rule health")}
              </h2>
              <div className="space-y-3">
                <HealthRow
                  icon={XCircle}
                  tone={data.rule_estate.rules_with_errors > 0 ? "bad" : "good"}
                  label={t("Rules with validation errors")}
                  value={data.rule_estate.rules_with_errors}
                />
                <HealthRow
                  icon={CalendarClock}
                  tone={
                    data.rule_estate.expiring_within_30_days > 0
                      ? "warn"
                      : "good"
                  }
                  label={t("Expiring within 30 days")}
                  value={data.rule_estate.expiring_within_30_days}
                />
              </div>

              {Object.keys(data.rule_estate.by_status).length > 0 && (
                <>
                  <div className="mt-5 mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {t("By status")}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(data.rule_estate.by_status).map(
                      ([status, count]) => (
                        <span
                          key={status}
                          className="text-[11px] rounded-md border border-border bg-muted/40 px-2 py-1 text-muted-foreground"
                        >
                          {status}
                          <span className="ml-1.5 font-semibold text-foreground tabular-nums">
                            {count}
                          </span>
                        </span>
                      ),
                    )}
                  </div>
                </>
              )}
            </section>

            {/* --- Catalogue readiness ---------------------------------------- */}
            <section className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center justify-between gap-3 mb-4">
                <h2 className="text-sm font-semibold text-foreground">
                  {t("Canonical metadata readiness")}
                </h2>
                <Link
                  to="/rating/catalog"
                  className="text-xs font-medium text-primary hover:underline inline-flex items-center gap-1"
                >
                  <Library className="h-3.5 w-3.5" /> {t("Manage")}
                </Link>
              </div>

              <dl className="grid grid-cols-2 gap-x-6 gap-y-2">
                {CATALOG_ROWS.map((row) => (
                  <div
                    key={row.key}
                    className="flex items-baseline justify-between gap-2"
                  >
                    <dt className="text-sm text-muted-foreground truncate">
                      {t(row.label)}
                    </dt>
                    <dd className="text-sm font-semibold text-foreground tabular-nums">
                      {data.catalog[row.key]}
                    </dd>
                  </div>
                ))}
              </dl>

              {data.catalog.warnings.length > 0 && (
                <ul className="mt-4 space-y-2">
                  {data.catalog.warnings.map((w) => (
                    <li
                      key={w}
                      className="flex items-start gap-2 text-[12px] text-muted-foreground"
                    >
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-warning-foreground" />
                      <span className="leading-relaxed">{w}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          {/* --- Recent activity ---------------------------------------------- */}
          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Recent rule activity")}
              </h2>
            </div>
            {data.recent_activity.length === 0 ? (
              <p className="px-5 py-6 text-sm text-muted-foreground">
                {t(
                  "Nothing yet — the audit trail fills as rules are created and changed.",
                )}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {data.recent_activity.map((a, i) => (
                  <li
                    key={`${a.rule_key}-${a.created_at}-${i}`}
                    className="px-5 py-3 flex flex-wrap items-baseline gap-x-3 gap-y-1"
                  >
                    <span className="font-mono text-[12px] text-foreground">
                      {a.rule_key}
                    </span>
                    {a.version !== null && (
                      <span className="text-[11px] text-muted-foreground">
                        v{a.version}
                      </span>
                    )}
                    <span className="text-sm text-muted-foreground">
                      {a.action}
                    </span>
                    <span className="ml-auto text-[11px] text-muted-foreground">
                      {a.actor_name ?? "—"} ·{" "}
                      {new Date(a.created_at).toLocaleString()}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}

function HealthRow({
  icon: Icon,
  tone,
  label,
  value,
}: {
  icon: typeof XCircle;
  tone: "good" | "warn" | "bad";
  label: string;
  value: number;
}) {
  const cls =
    tone === "bad"
      ? "bg-destructive/10 text-destructive"
      : tone === "warn"
        ? "bg-warning/15 text-warning-foreground"
        : "bg-success/10 text-success";
  return (
    <div className="flex items-center gap-3">
      <span
        className={`h-8 w-8 shrink-0 rounded-lg flex items-center justify-center ${cls}`}
      >
        {tone === "good" ? (
          <CheckCircle2 className="h-4 w-4" />
        ) : (
          <Icon className="h-4 w-4" />
        )}
      </span>
      <span className="text-sm text-muted-foreground flex-1 min-w-0 truncate">
        {label}
      </span>
      <span className="text-sm font-semibold text-foreground tabular-nums">
        {value}
      </span>
    </div>
  );
}

function IngestCard({
  to,
  icon: Icon,
  title,
  description,
  cta,
}: {
  to: string;
  icon: typeof FileCog;
  title: string;
  description: string;
  cta: string;
}) {
  return (
    <Link
      to={to}
      className="group bg-card border border-border rounded-xl p-5 flex flex-col hover:border-primary/40 hover:shadow-sm transition"
    >
      <span className="h-10 w-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center mb-3">
        <Icon className="h-5 w-5" />
      </span>
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="text-[12px] text-muted-foreground leading-relaxed mt-1 flex-1">
        {description}
      </p>
      <span className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-primary">
        {cta}
        <ArrowRight className="h-3.5 w-3.5 transition group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}
