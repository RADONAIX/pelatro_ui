import { createFileRoute } from "@tanstack/react-router";
import { Fragment, useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { auditService, type AuditModuleOption } from "@/services";
import { useT } from "@/lib/i18n";
import { Search, SlidersHorizontal, X } from "lucide-react";
import { DateRangePicker } from "@/components/ui-kit/DateRangePicker";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";
import { TablePagination } from "@/components/reports/ReportFilters";

export const Route = createFileRoute("/audit-logs")({ component: AuditPage });

// Actions whose styling flags them as security-relevant.
function actionTone(action: string): string {
  const a = action.toLowerCase();
  if (a.includes("failed") || a.includes("locked") || a.includes("deleted")) {
    return "bg-red-100 text-red-700";
  }
  if (a.includes("created") || a.includes("exported")) return "bg-emerald-100 text-emerald-700";
  if (a.includes("updated") || a.includes("changed") || a.includes("saved")) {
    return "bg-amber-100 text-amber-700";
  }
  return "bg-muted text-muted-foreground";
}

function AuditPage() {
  const t = useT();
  const [logs, setLogs] = useState<any[]>([]);
  const [modules, setModules] = useState<AuditModuleOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  // filters
  const [query, setQuery] = useState("");
  const [module, setModule] = useState("");
  const defaultRange = () => {
    const start = new Date();
    start.setHours(0, 0, 0, 0); // midnight today (local) → now
    return { start, end: new Date() };
  };

  const [range, setRange] = useState<{ start: Date; end: Date }>(defaultRange);
  const [rangeTouched, setRangeTouched] = useState(false);
  const { sortKey, sortDir, onSort } = useSort();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // A single global search filters the loaded rows live (client-side) across
  // every visible field. The date range drives the server fetch (picker Apply).
  const q = query.trim().toLowerCase();
  const filteredLogs = logs.filter((l) => {
    if (!q) return true;
    const target = l.target_label ?? (l.target && l.target !== l.actor ? l.target : "");
    const haystack = [
      l.actor, l.action, l.module_label, target, l.target, l.ip_address,
      new Date(l.at).toLocaleString(),
      l.meta ? JSON.stringify(l.meta) : "",
    ];
    return haystack.some((v) => String(v ?? "").toLowerCase().includes(q));
  });
  const sortedLogs = sortRows(filteredLogs, sortKey, sortDir, (l, key) => {
    switch (key) {
      case "when": return new Date(l.at).getTime();
      case "actor": return l.actor;
      case "module": return l.module_label ?? "";
      case "action": return l.action;
      case "target": return l.target_label ?? (l.target && l.target !== l.actor ? l.target : "");
      case "ip": return l.ip_address ?? "";
      default: return "";
    }
  });

  // Client-side pagination over the filtered + sorted rows.
  const pageCount = Math.max(1, Math.ceil(sortedLogs.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageItems = sortedLogs.slice((safePage - 1) * pageSize, safePage * pageSize);
  // Back to page 1 whenever the search, page size, or freshly-loaded data change.
  useEffect(() => { setPage(1); }, [query, pageSize, logs]);

  // Only the date range hits the server; text filters stay client-side.
  function load(r: { start: Date; end: Date } = range, m: string = module) {
    setLoading(true);
    auditService
      .list({
        date_from: r.start.toISOString(),
        date_to: r.end.toISOString(),
        module: m || undefined,
        limit: 200,
      })
      .then(setLogs)
      .finally(() => setLoading(false));
  }

  // initial load
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { auditService.modules().then(setModules); }, []);

  function clearFilters() {
    const r = defaultRange();
    setQuery(""); setModule(""); setRange(r);
    if (rangeTouched || module) { setRangeTouched(false); setTimeout(() => load(r, ""), 0); }
  }

  const active = query.trim() !== "" || module !== "" || rangeTouched;

  // Match the Reports module's filter styling for a consistent look across pages.
  const inputCls =
    "h-9 shrink-0 rounded-lg border border-border bg-card text-xs px-2.5 focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition";
  const hdrCls = "flex items-center gap-1 mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground";

  return (
    <AppShell>
      <PageHeader
        title={t("Audit Logs")}
        description={t("Immutable trail of operator and system actions.")}
        info={t("An immutable record of every operator and system action — who did what, when, and from where.")}
      />

      <div className="bg-card border border-border rounded-xl overflow-hidden shadow-sm">
        {/* Filters — mirrors the Reports module's filter bar for a uniform look. */}
        <div className="px-5 py-3 border-b border-border bg-muted/20 flex flex-nowrap items-end gap-3 overflow-x-auto">
          <div className="inline-flex items-center gap-1.5 self-center shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mr-1">
            <SlidersHorizontal className="h-3.5 w-3.5" /> {t("Filters")}
            <InfoHint text={t("Search filters as you type; the date range reloads when you click Apply in the picker.")} />
          </div>

          {/* Global search */}
          <div className="shrink-0">
            <div className={hdrCls}>
              {t("Search")}
              <InfoHint text={t("Filter events across every column — user, action, target, source IP, time and details.")} />
            </div>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search all columns…")}
                maxLength={100}
                className={`${inputCls} pl-8 ${query ? "pr-8" : ""} w-72`}
              />
              {query && (
                <button type="button" onClick={() => setQuery("")} aria-label={t("Clear")} title={t("Clear")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 inline-flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition">
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>

          {/* Module filter — server-side; reloads on change. */}
          {/* <div className="shrink-0">
            <div className={hdrCls}>
              {t("Module")}
              <InfoHint text={t("Show only events raised by the selected module.")} />
            </div>
            <select
              value={module}
              onChange={(e) => { const m = e.target.value; setModule(m); load(range, m); }}
              className={`${inputCls} w-44`}
            >
              <option value="">{t("All modules")}</option>
              {modules.map((m) => (
                <option key={m.key} value={m.key}>{t(m.label)}</option>
              ))}
            </select>
          </div> */}

          {/* Date range */}
          <div className="shrink-0">
            <div className={hdrCls}>
              {t("Date range")}
              <InfoHint text={t("Show only events that occurred within the selected date range.")} />
            </div>
            <DateRangePicker
              start={range.start}
              end={range.end}
              max={new Date()}
              showApply
              onChange={(r) => { setRange(r); setRangeTouched(true); load(r); }}
              className="h-9 px-3 inline-flex items-center gap-2 rounded-lg border border-border bg-card text-sm hover:bg-muted transition"
            />
          </div>

          {/* Clear */}
          <button onClick={clearFilters} disabled={!active}
            className="ml-auto shrink-0 inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-border bg-card text-xs font-medium hover:bg-muted transition disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap">
            <X className="h-3.5 w-3.5" /> {t("Clear filters")}
          </button>
        </div>

        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              {[
                { key: "when", label: "When" },
                { key: "actor", label: "User" },
                { key: "module", label: "Module" },
                { key: "action", label: "Action" },
                { key: "target", label: "Target" },
                { key: "ip", label: "Source IP" },
              ].map((c) => (
                <SortHeader
                  key={c.key}
                  label={t(c.label)}
                  colKey={c.key}
                  activeKey={sortKey}
                  dir={sortDir}
                  onSort={onSort}
                  thClassName="text-left font-medium px-4 py-3"
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {pageItems.map((l) => {
              const isOpen = expanded === l.id;
              const target = l.target_label ?? (l.target && l.target !== l.actor ? l.target : "—");
              return (
                <Fragment key={l.id}>
                  <tr className="border-t border-border hover:bg-muted/30 cursor-pointer"
                    onClick={() => setExpanded(isOpen ? null : l.id)}>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                      {new Date(l.at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-foreground/80">{l.actor}</td>
                    <td className="px-4 py-3 text-foreground/80 whitespace-nowrap">
                      {l.module_label ?? "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${actionTone(l.action)}`}>
                        {l.action}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-foreground/80">{target}</td>
                    <td className="px-4 py-3 text-muted-foreground font-mono text-xs">{l.ip_address ?? "—"}</td>
                  </tr>
                  {isOpen && (
                    <tr className="border-t border-border bg-muted/20">
                      <td colSpan={6} className="px-4 py-3">
                        <dl className="grid grid-cols-1 gap-x-8 gap-y-1 text-xs sm:grid-cols-2">
                          <div className="flex gap-2"><dt className="text-muted-foreground">{t("Event ID")}:</dt>
                            <dd className="font-mono text-foreground/80">{l.id}</dd></div>
                          {l.target && (
                            <div className="flex gap-2"><dt className="text-muted-foreground">{t("Target ID")}:</dt>
                              <dd className="font-mono text-foreground/80">{l.target}</dd></div>
                          )}
                          {l.meta && Object.keys(l.meta).length > 0 && (
                            <div className="flex gap-2 sm:col-span-2"><dt className="text-muted-foreground">{t("Details")}:</dt>
                              <dd className="font-mono text-foreground/80">{JSON.stringify(l.meta)}</dd></div>
                          )}
                        </dl>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {!loading && sortedLogs.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-12 text-center text-muted-foreground text-sm">
                {logs.length === 0 ? t("No data found") : t("No events match the current filters.")}
              </td></tr>
            )}
            {loading && (
              <tr><td colSpan={6} className="px-4 py-12 text-center text-muted-foreground text-sm">{t("Loading…")}</td></tr>
            )}
          </tbody>
        </table>

        <TablePagination
          page={safePage}
          pageSize={pageSize}
          total={filteredLogs.length}
          onPage={setPage}
          onPageSize={setPageSize}
        />
      </div>
    </AppShell>
  );
}
