import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Library, Pencil, Plus, Search } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useT } from "@/lib/i18n";
import {
  useCanEditCatalog,
  useCatalogList,
  useCatalogSchema,
  useCatalogSummary,
} from "@/lib/rating/hooks";
import { CatalogEntityDialog } from "@/components/rating/CatalogEntityDialog";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import type { CatalogEntity } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/catalog")({
  component: MetadataCataloguePage,
});

// Per-entity columns beyond the shared code/name/status. Kept as a spec table so
// a new catalogue is one row here rather than a new screen.
interface EntityView {
  slug: string;
  label: string;
  blurb: string;
  columns: { key: string; label: string; align?: "right" }[];
}

const VIEWS: EntityView[] = [
  {
    slug: "products",
    label: "Products",
    blurb:
      "The subscriber's rated product. Rules target a product, never a vendor identifier.",
    columns: [
      { key: "account_type", label: "Account type" },
      { key: "service_types", label: "Services" },
      { key: "currency_code", label: "Currency" },
      { key: "effective_from", label: "From" },
    ],
  },
  {
    slug: "offers",
    label: "Offers",
    blurb:
      "Commercial offers attached to a product. Exclusive offers cannot stack.",
    columns: [
      { key: "exclusive", label: "Exclusive" },
      { key: "effective_from", label: "From" },
    ],
  },
  {
    slug: "tariff-plans",
    label: "Tariff plans",
    blurb: "Per-service pricing plans a rule can select before base charging.",
    columns: [
      { key: "service_type", label: "Service" },
      { key: "currency_code", label: "Currency" },
      { key: "effective_from", label: "From" },
    ],
  },
  {
    slug: "destination-zones",
    label: "Destination zones",
    blurb:
      "On-net, off-net, national, international and premium destinations. Enrichment resolves a dialled number to one of these by longest-matching prefix.",
    columns: [
      { key: "zone_type", label: "Zone type" },
      { key: "country_code", label: "Country" },
    ],
  },
  {
    slug: "time-bands",
    label: "Time bands",
    blurb:
      "Peak / off-peak / weekend windows. Higher priority wins where bands overlap.",
    columns: [
      { key: "days", label: "Days" },
      { key: "start_time", label: "From" },
      { key: "end_time", label: "To" },
      { key: "timezone", label: "Timezone" },
      { key: "priority", label: "Priority", align: "right" },
    ],
  },
  {
    slug: "rating-groups",
    label: "Rating groups",
    blurb:
      "Coarse buckets used to narrow rule selection before per-rule matching.",
    columns: [{ key: "service_type", label: "Service" }],
  },
  {
    slug: "tax-rules",
    label: "Tax rules",
    blurb: "Applied after discounts, before rounding.",
    columns: [
      { key: "tax_type", label: "Type" },
      { key: "jurisdiction", label: "Jurisdiction" },
      { key: "rate_percent", label: "Rate %", align: "right" },
      { key: "inclusive", label: "Inclusive" },
    ],
  },
  {
    slug: "discounts",
    label: "Discounts",
    blurb: "Referenced by APPLY_DISCOUNT actions.",
    columns: [
      { key: "discount_type", label: "Type" },
      { key: "value", label: "Value", align: "right" },
      { key: "pre_tax", label: "Pre-tax" },
    ],
  },
  {
    slug: "bundles",
    label: "Bundles",
    blurb:
      "Free-usage allowances. Shared bundles force the stateful rating path, which partitions by account rather than subscriber.",
    columns: [
      { key: "service_type", label: "Service" },
      { key: "quota_value", label: "Quota", align: "right" },
      { key: "quota_unit", label: "Unit" },
      { key: "reset_period", label: "Resets" },
      { key: "shared", label: "Shared" },
    ],
  },
  {
    slug: "promotions",
    label: "Promotions",
    blurb:
      "Exclusive promotions cannot be applied together — the conflict validator enforces it.",
    columns: [
      { key: "promotion_type", label: "Type" },
      { key: "value", label: "Value", align: "right" },
      { key: "exclusive", label: "Exclusive" },
    ],
  },
  {
    slug: "currencies",
    label: "Currencies",
    blurb: "Minor-unit digits drive rounding and display.",
    columns: [
      { key: "symbol", label: "Symbol" },
      { key: "decimals", label: "Decimals", align: "right" },
    ],
  },
  {
    slug: "rounding-rules",
    label: "Rounding rules",
    blurb: "The final step of the charging sequence.",
    columns: [
      { key: "mode", label: "Mode" },
      { key: "decimals", label: "Decimals", align: "right" },
    ],
  },
  {
    slug: "services",
    label: "Services",
    blurb: "The unit usage is measured in before any pulse is applied.",
    columns: [
      { key: "service_type", label: "Service type" },
      { key: "usage_unit", label: "Usage unit" },
    ],
  },
];

function renderCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return String(value);
}

function MetadataCataloguePage() {
  const t = useT();
  const [activeSlug, setActiveSlug] = useState(VIEWS[0].slug);
  const [search, setSearch] = useState("");

  const canEdit = useCanEditCatalog();
  const view = VIEWS.find((v) => v.slug === activeSlug) ?? VIEWS[0];
  const { data: summary = [] } = useCatalogSummary();
  const { data: schemas = [] } = useCatalogSchema();
  const { data, isLoading, error, refetch } = useCatalogList(view.slug);

  // `null` = the create dialog; a row = edit that row; undefined = closed.
  const [editing, setEditing] = useState<CatalogEntity | null | undefined>(
    undefined,
  );
  const schema = schemas.find((s) => s.slug === view.slug);

  const counts = useMemo(
    () => new Map(summary.map((s) => [s.entity, s.total])),
    [summary],
  );

  const rows: CatalogEntity[] = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return data ?? [];
    return (data ?? []).filter(
      (r) =>
        r.code.toLowerCase().includes(needle) ||
        r.name.toLowerCase().includes(needle) ||
        r.description.toLowerCase().includes(needle),
    );
  }, [data, search]);

  return (
    <AppShell>
      <PageHeader
        title={t("Metadata Catalogue")}
        description={t(
          "The canonical model every tariff rule references. Vendor configurations are normalised into these entities, so a rule imported from one charging system means the same thing as one authored by hand.",
        )}
        info={t(
          "Rules reference these by code, not by id — which is what lets a rule set move between environments unchanged.",
        )}
        actions={
          canEdit &&
          schema && (
            <button
              onClick={() => setEditing(null)}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <Plus className="h-4 w-4" /> {t("New")}{" "}
              {view.label.toLowerCase().replace(/s$/, "")}
            </button>
          )
        }
      />

      {/* --- Entity tabs ----------------------------------------------------- */}
      <div className="flex flex-wrap gap-2 mb-5">
        {VIEWS.map((v) => {
          const count = counts.get(v.slug);
          const active = v.slug === activeSlug;
          return (
            <button
              key={v.slug}
              onClick={() => {
                setActiveSlug(v.slug);
                setSearch("");
              }}
              className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium transition ${
                active
                  ? "border-primary bg-primary/5 text-foreground"
                  : "border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {t(v.label)}
              {count !== undefined && (
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded-md tabular-nums ${
                    active
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <p className="text-sm text-muted-foreground max-w-2xl leading-relaxed">
          {t(view.blurb)}
        </p>
        <div className="relative w-full sm:w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("Filter this catalogue")}
            className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
          />
        </div>
      </div>

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {data && rows.length === 0 && (
        <RatingEmpty
          icon={Library}
          title={
            search
              ? "Nothing matches that filter"
              : `No ${view.label.toLowerCase()} yet`
          }
          description={
            search
              ? "Try a shorter search term."
              : "Add the first one, load the reference data with `python -m app.seed`, or import it from a source system in Phase 5."
          }
          action={
            canEdit &&
            schema && (
              <button
                onClick={() => setEditing(null)}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
              >
                <Plus className="h-4 w-4" /> {t("New")}{" "}
                {view.label.toLowerCase().replace(/s$/, "")}
              </button>
            )
          }
        />
      )}

      {data && rows.length > 0 && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-left">
                  <Th>{t("Code")}</Th>
                  <Th>{t("Name")}</Th>
                  {view.columns.map((c) => (
                    <Th
                      key={c.key}
                      className={c.align === "right" ? "text-right" : ""}
                    >
                      {t(c.label)}
                    </Th>
                  ))}
                  <Th>{t("Status")}</Th>
                  <Th>{t("Source")}</Th>
                  {canEdit && <Th className="w-10">{""}</Th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    className="hover:bg-muted/30 transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-[12px] text-foreground whitespace-nowrap">
                      {row.code}
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-foreground">{row.name}</div>
                      {row.description && (
                        <div className="text-[11px] text-muted-foreground line-clamp-1">
                          {row.description}
                        </div>
                      )}
                    </td>
                    {view.columns.map((c) => (
                      <td
                        key={c.key}
                        className={`px-4 py-3 text-muted-foreground whitespace-nowrap ${
                          c.align === "right" ? "text-right tabular-nums" : ""
                        }`}
                      >
                        {renderCell(row[c.key])}
                      </td>
                    ))}
                    <td className="px-4 py-3 text-muted-foreground">
                      {row.status}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {row.source_system}
                    </td>
                    {canEdit && (
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => setEditing(row)}
                          aria-label={`${t("Edit")} ${row.code}`}
                          className="h-8 w-8 rounded-lg inline-flex items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground transition"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {editing !== undefined && schema && (
        <CatalogEntityDialog
          schema={schema}
          entity={editing ?? undefined}
          onClose={() => setEditing(undefined)}
        />
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
      className={`px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground whitespace-nowrap ${className}`}
    >
      {children}
    </th>
  );
}
