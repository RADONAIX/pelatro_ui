import { createFileRoute, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { CheckCheck, CheckCircle2, Clock, Download, FolderOpen, MoreVertical, Plus, Search, Sparkles, UserCheck, UserPlus, UserX, X } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import { StatTile } from "@/components/ui-kit/StatTile";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";
import { TablePagination } from "@/components/reports/ReportFilters";
import { CaseInvestigation } from "@/components/cases/CaseInvestigation";
import { downloadBlob } from "@/services";
import {
  FINDING_TYPES, SEVERITIES, STREAMS, CURRENT_ANALYST,
  findingLabel, fmtDate, loadCases, newId, relative, saveCases,
  type AssuranceCase,
} from "@/lib/casesDemo";

export const Route = createFileRoute("/cases")({ component: CasesPage });

// DEMO-ONLY. Client-side only, persisted to localStorage; no backend calls (the
// /cases router is commented out server-side). See lib/casesDemo for the data
// shape and how it lines up with the real Case model.

// An empty owner is the marker for an unassigned case.
const isUnassigned = (c: AssuranceCase) => !c.owner.trim();
const ownerLabel = (c: AssuranceCase) => (isUnassigned(c) ? "Unassigned" : c.owner);

type CountKey = "unassigned" | "assigned" | "open" | "inProgress" | "resolved" | "closed";

interface Tile {
  key: CountKey;
  label: string;
  icon: typeof UserX;
  match: (c: AssuranceCase) => boolean;
  empty: string;
}

// Cases tab: the list is a mix of owned and unowned, so assignment is the useful
// axis. These mix two dimensions (assignment + status) and intentionally overlap
// — they do not sum to the total.
const ALL_TILES: Tile[] = [
  { key: "unassigned", label: "Unassigned", icon: UserX, match: isUnassigned, empty: "No unassigned cases." },
  { key: "assigned", label: "Assigned", icon: UserCheck, match: (c) => !isUnassigned(c), empty: "No assigned cases." },
  { key: "inProgress", label: "In Progress", icon: Clock, match: (c) => c.status === "In Progress", empty: "No cases in progress." },
  { key: "closed", label: "Closed", icon: CheckCircle2, match: (c) => c.status === "Closed", empty: "No closed cases." },
];

// Self Assigned tab: everything here is already yours, so assignment is constant
// (Unassigned would always read 0, Assigned would always equal the total). Show
// the status lifecycle of your own queue instead — that's what actually varies.
const SELF_TILES: Tile[] = [
  { key: "open", label: "Open", icon: FolderOpen, match: (c) => c.status === "Open", empty: "No open cases." },
  { key: "inProgress", label: "In Progress", icon: Clock, match: (c) => c.status === "In Progress", empty: "No cases in progress." },
  { key: "resolved", label: "Resolved", icon: CheckCircle2, match: (c) => c.status === "Resolved", empty: "No resolved cases." },
  { key: "closed", label: "Closed", icon: CheckCheck, match: (c) => c.status === "Closed", empty: "No closed cases." },
];

type ColKey = "date" | "reference" | "title" | "origin" | "status" | "action" | "severity" | "owner" | "findingType";

const COLUMNS: { key: ColKey; label: string }[] = [
  { key: "date", label: "Date" },
  { key: "reference", label: "Case" },
  // The screenshots' fraud tickets carried their meaning in the description;
  // for an RA finding the title is what tells you what the case actually is.
  { key: "title", label: "Title" },
  { key: "origin", label: "Origin" },
  { key: "status", label: "Status" },
  { key: "action", label: "Action" },
  { key: "severity", label: "Priority" },
  { key: "owner", label: "Assigned To" },
  { key: "findingType", label: "Finding Type" },
];

const ALL_COLS = new Set<ColKey>(COLUMNS.map((c) => c.key));

// Values the sorter reads per column. `date` deliberately returns the raw
// timestamp, not the "2h ago" label, so ordering is chronological. `severity`
// returns its index so low→critical sorts by rank rather than alphabetically.
const accessor = (c: AssuranceCase, key: string): unknown => {
  switch (key as ColKey) {
    case "date": return new Date(c.createdAt).getTime();
    case "reference": return c.reference;
    case "title": return c.title;
    case "origin": return c.origin;
    case "status": return c.status;
    case "action": return c.action;
    case "severity": return SEVERITIES.indexOf(c.severity as (typeof SEVERITIES)[number]);
    // Sort/export the label, not the raw blank — otherwise unassigned rows sort
    // to the top as empty strings and export as an empty CSV field.
    case "owner": return ownerLabel(c);
    case "findingType": return findingLabel(c.findingType);
    default: return "";
  }
};

const cellValue = (c: AssuranceCase, key: ColKey): string => {
  switch (key) {
    case "date": return fmtDate(c.createdAt);
    case "reference": return c.reference;
    case "title": return c.title;
    case "origin": return c.origin;
    case "status": return c.status;
    case "action": return c.action;
    case "severity": return c.severity;
    case "owner": return ownerLabel(c);
    case "findingType": return findingLabel(c.findingType);
  }
};

function toCsv(rows: string[][]): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return rows.map((r) => r.map(esc).join(",")).join("\r\n");
}

// findingType/severity/stream are widened to string: they're bound to <select>
// elements whose onChange yields a plain string, not the `as const` literal.
interface CaseForm {
  title: string;
  description: string;
  findingType: string;
  severity: string;
  stream: string;
  owner: string;
}

const EMPTY_FORM: CaseForm = {
  title: "", description: "", findingType: FINDING_TYPES[0].key,
  severity: "medium", stream: STREAMS[0], owner: CURRENT_ANALYST,
};

function CasesPage() {
  const navigate = useNavigate();
  const tabParam = useRouterState({ select: (s) => (s.location.search as { tab?: string } | undefined)?.tab });
  const tab: "all" | "self" = tabParam === "self" ? "self" : "all";

  const [cases, setCases] = useState<AssuranceCase[]>([]);
  const [q, setQ] = useState("");
  const [visibleCols, setVisibleCols] = useState<Set<ColKey>>(ALL_COLS);
  // 1-based: TablePagination renders `(page - 1) * pageSize + 1` and calls
  // onPage(1) for the first page.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [countFilter, setCountFilter] = useState<CountKey | null>(null);
  const { sortKey, sortDir, onSort } = useSort("date", "desc");

  useEffect(() => { setCases(loadCases()); }, []);

  // Persist ONLY from explicit user actions — never a reactive effect, which
  // would race the mount load and clobber storage with the empty initial state.
  const persist = (next: AssuranceCase[]) => {
    setCases(next);
    saveCases(next);
  };

  const setTab = (next: "all" | "self") => {
    setPage(1);
    setCountFilter(null);
    navigate({ to: "/cases", search: next === "self" ? { tab: "self" } : {} });
  };

  const scoped = useMemo(() => (tab === "self" ? cases.filter((c) => c.owner === CURRENT_ANALYST) : cases), [cases, tab]);

  // Assignment tiles on the Cases tab; status-lifecycle tiles on Self Assigned.
  const tiles = tab === "self" ? SELF_TILES : ALL_TILES;

  // Counts describe the active tab's scope, so they always match what's below.
  const counts = useMemo(
    () => Object.fromEntries(tiles.map((t) => [t.key, scoped.filter(t.match).length])) as Record<CountKey, number>,
    [scoped, tiles],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const tile = tiles.find((t) => t.key === countFilter);
    return scoped.filter((c) => {
      if (tile && !tile.match(c)) return false;
      if (!needle) return true;
      return `${c.reference} ${c.title} ${ownerLabel(c)} ${c.linkedBatch} ${findingLabel(c.findingType)}`.toLowerCase().includes(needle);
    });
  }, [scoped, q, countFilter, tiles]);

  const toggleCount = (key: CountKey) => {
    setCountFilter((cur) => (cur === key ? null : key));
    setPage(1);
  };

  const sorted = useMemo(() => sortRows(filtered, sortKey, sortDir, accessor), [filtered, sortKey, sortDir]);
  const pageRows = useMemo(() => sorted.slice((page - 1) * pageSize, page * pageSize), [sorted, page, pageSize]);

  // Keep the page in range when a filter shrinks the result set.
  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(sorted.length / pageSize));
    if (page > maxPage) setPage(maxPage);
  }, [sorted.length, pageSize, page]);

  const activeCase = activeId ? cases.find((c) => c.id === activeId) ?? null : null;

  const updateCase = (next: AssuranceCase) => persist(cases.map((c) => (c.id === next.id ? next : c)));

  const exportCsv = () => {
    const cols = COLUMNS.filter((c) => visibleCols.has(c.key));
    const rows = [cols.map((c) => c.label), ...sorted.map((r) => cols.map((c) => cellValue(r, c.key)))];
    downloadBlob(new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" }), "cases.csv");
    toast.success(`Exported ${sorted.length} cases`);
  };

  const addCase = () => {
    if (!form.title.trim()) return;
    const n = cases.reduce((m, c) => Math.max(m, Number(c.reference.replace(/\D/g, "")) || 0), 2031) + 1;
    const now = new Date().toISOString();
    const c: AssuranceCase = {
      id: newId(), reference: `CASE-${n}`, title: form.title.trim(),
      description: form.description.trim() || "Raised manually by an analyst.",
      origin: "analyst_raised", findingType: form.findingType, severity: form.severity,
      status: "Open", action: "NA", owner: form.owner.trim() || CURRENT_ANALYST,
      stream: form.stream, nodeId: "NODE-1", linkedTxnId: "—", linkedBatch: "—",
      estimatedImpact: 0, affectedCount: 0, createdAt: now, updatedAt: now,
      evidence: null, trace: [], comments: [], savedInsights: [],
    };
    persist([c, ...cases]);
    setForm(EMPTY_FORM);
    setAddOpen(false);
    toast.success(`${c.reference} created`);
  };

  const assignToMe = (c: AssuranceCase) => {
    updateCase({ ...c, owner: CURRENT_ANALYST, updatedAt: new Date().toISOString() });
    setMenuFor(null);
    toast.success(`${c.reference} assigned to you`);
  };

  const closeCase = (c: AssuranceCase) => {
    updateCase({ ...c, status: "Closed", updatedAt: new Date().toISOString() });
    setMenuFor(null);
    toast.success(`${c.reference} closed`);
  };

  const cols = COLUMNS.filter((c) => visibleCols.has(c.key));
  // Prefer the active tile's wording — "No unassigned cases." beats a generic
  // "No cases match." when the user just clicked that tile.
  const emptyMessage = q.trim()
    ? "No cases match."
    : tiles.find((t) => t.key === countFilter)?.empty ?? "No cases match.";

  return (
    <AppShell>
      <PageHeader
        title="Case Management"
        description="Findings raised by the assurance reports — triage, investigate and resolve."
      />

      {/* Tabs */}
      <div className="border-b border-border mb-4">
        <div className="flex gap-6">
          {([["all", "Cases"], ["self", "Self Assigned Cases"]] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              aria-current={tab === key}
              className={`relative pb-2.5 text-sm font-medium transition-colors ${
                tab === key ? "text-primary" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
              {tab === key && <span className="absolute inset-x-0 -bottom-px h-0.5 bg-primary rounded-full" />}
            </button>
          ))}
        </div>
      </div>

      {/* Counts — click to filter the list below */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        {tiles.map((t) => (
          <StatTile
            key={t.key}
            icon={t.icon}
            label={t.label}
            value={String(counts[t.key])}
            active={countFilter === t.key}
            onClick={() => toggleCount(t.key)}
          />
        ))}
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex items-center gap-2 flex-1 min-w-[240px] bg-card border border-border rounded-lg px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1); }}
            placeholder={`Search ${scoped.length} records…`}
            aria-label="Search cases"
            className="flex-1 bg-transparent text-sm focus:outline-none"
          />
          {q && (
            <button onClick={() => setQ("")} aria-label="Clear search" className="text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {tab === "all" ? (
          <>
            <MultiSelect
              options={COLUMNS.map((c) => ({ value: c.key, label: c.label }))}
              selected={visibleCols}
              onChange={(next) => setVisibleCols(next)}
              placeholder="Columns"
              minWidth={190}
              allowEmpty={false}
            />
            <button
              onClick={exportCsv}
              aria-label="Export CSV"
              className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-card text-sm hover:bg-muted"
            >
              <Download className="h-4 w-4" />
            </button>
          </>
        ) : (
          <button
            onClick={() => { setForm(EMPTY_FORM); setAddOpen(true); }}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90"
          >
            <Plus className="h-4 w-4" /> Add Case
          </button>
        )}
      </div>

      {tab === "all" ? (
        /* ---------- Cases: table ---------- */
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="text-left font-medium px-4 py-3 w-10">#</th>
                  {cols.map((c) => (
                    <SortHeader
                      key={c.key}
                      label={c.label}
                      colKey={c.key}
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                      thClassName="text-left font-medium px-4 py-3 whitespace-nowrap"
                    />
                  ))}
                  <th className="text-center font-medium px-4 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((c, i) => (
                  <tr key={c.id} className="border-t border-border hover:bg-muted/30">
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">{(page - 1) * pageSize + i + 1}</td>
                    {cols.map((col) => (
                      <td key={col.key} className="px-4 py-3 whitespace-nowrap">{renderCell(c, col.key)}</td>
                    ))}
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-center gap-1.5">
                        {isUnassigned(c) && (
                          <button
                            onClick={() => assignToMe(c)}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-primary/40 px-2.5 py-1 text-xs font-medium text-primary hover:bg-primary/5 whitespace-nowrap"
                          >
                            <UserPlus className="h-3 w-3" /> Assign to me
                          </button>
                        )}
                        <button
                          onClick={() => setActiveId(c.id)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted whitespace-nowrap"
                        >
                          <Sparkles className="h-3 w-3" /> Investigate
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr>
                    <td colSpan={cols.length + 2} className="px-4 py-14 text-center text-sm text-muted-foreground">
                      {emptyMessage}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {sorted.length > 0 && (
            <TablePagination
              page={page}
              pageSize={pageSize}
              total={sorted.length}
              onPage={setPage}
              onPageSize={(n) => { setPageSize(n); setPage(1); }}
            />
          )}
        </div>
      ) : (
        /* ---------- Self Assigned: cards ---------- */
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {sorted.map((c) => (
            <div key={c.id} className="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-semibold text-foreground truncate">{c.reference}</div>
                  <div className="text-xs text-muted-foreground font-mono">{c.origin}</div>
                </div>
                <div className="relative">
                  <button
                    onClick={() => setMenuFor(menuFor === c.id ? null : c.id)}
                    aria-label={`Actions for ${c.reference}`}
                    className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted"
                  >
                    <MoreVertical className="h-4 w-4" />
                  </button>
                  {menuFor === c.id && (
                    <>
                      <button className="fixed inset-0 z-10 cursor-default" aria-hidden="true" tabIndex={-1} onClick={() => setMenuFor(null)} />
                      <div className="absolute right-0 top-8 z-20 w-44 rounded-lg border border-border bg-card shadow-lg py-1">
                        <MenuItem onClick={() => assignToMe(c)}>Assign to me</MenuItem>
                        <MenuItem onClick={() => { setActiveId(c.id); setMenuFor(null); }}>Open investigation</MenuItem>
                        <MenuItem onClick={() => closeCase(c)}>Close case</MenuItem>
                      </div>
                    </>
                  )}
                </div>
              </div>

              <div className="border-t border-border pt-3">
                <p className="text-sm font-medium text-foreground leading-snug">{c.title}</p>
                <p className="mt-1 text-xs text-muted-foreground leading-relaxed line-clamp-3">{c.description}</p>
              </div>

              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                <Kv k="Date" v={fmtDate(c.createdAt).split(",")[0]} />
                <Kv k="Priority" v={c.severity} badge />
                <Kv k="Status" v={c.status} badge />
                <Kv k="Action" v={c.action} />
                <Kv k="Assigned to" v={ownerLabel(c)} />
              </dl>

              <div className="flex items-center justify-between pt-2 border-t border-border mt-auto">
                <span className="text-[11px] text-muted-foreground">Updated {relative(c.updatedAt)}</span>
                <button
                  onClick={() => setActiveId(c.id)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/5"
                >
                  Investigate
                </button>
              </div>
            </div>
          ))}
          {sorted.length === 0 && (
            <div className="col-span-full rounded-xl border border-dashed border-border bg-muted/10 py-16 text-center text-sm text-muted-foreground">
              {countFilter ? emptyMessage : "No cases assigned to you."}
            </div>
          )}
        </div>
      )}

      {activeCase && (
        <CaseInvestigation activeCase={activeCase} onClose={() => setActiveId(null)} onUpdate={updateCase} />
      )}

      {addOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <button className="absolute inset-0 bg-black/50" aria-hidden="true" tabIndex={-1} onClick={() => setAddOpen(false)} />
          <div className="relative w-full max-w-lg rounded-2xl border border-border bg-card shadow-xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="font-semibold text-foreground">Add Case</h2>
              <button onClick={() => setAddOpen(false)} aria-label="Close" className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <Fld label="Title">
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Describe the finding…" className={inputCls} />
              </Fld>
              <Fld label="Description">
                <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional detail" className={inputCls} />
              </Fld>
              <div className="grid grid-cols-2 gap-4">
                <Fld label="Finding type">
                  <select value={form.findingType} onChange={(e) => setForm({ ...form, findingType: e.target.value })} className={inputCls}>
                    {FINDING_TYPES.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                  </select>
                </Fld>
                <Fld label="Priority">
                  <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })} className={inputCls}>
                    {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Fld>
                <Fld label="Stream">
                  <select value={form.stream} onChange={(e) => setForm({ ...form, stream: e.target.value })} className={inputCls}>
                    {STREAMS.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Fld>
                <Fld label="Assign to">
                  <input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} className={inputCls} />
                </Fld>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setAddOpen(false)} className="rounded-lg px-4 py-2 text-sm text-muted-foreground hover:bg-muted">Cancel</button>
                <button
                  onClick={addCase}
                  disabled={!form.title.trim()}
                  className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
                >
                  <Plus className="h-4 w-4" /> Create
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function renderCell(c: AssuranceCase, key: ColKey) {
  switch (key) {
    case "date":
      return <span className="text-muted-foreground">{fmtDate(c.createdAt)}</span>;
    case "reference":
      return <span className="font-medium text-foreground">{c.reference}</span>;
    case "title":
      return <span className="block max-w-[320px] truncate text-foreground/80" title={c.title}>{c.title}</span>;
    case "origin":
      return <span className="font-mono text-xs text-muted-foreground">{c.origin}</span>;
    case "status":
      return <StatusBadge value={c.status} />;
    case "severity":
      return <StatusBadge value={c.severity} />;
    case "action":
      return <span className="text-foreground/80">{c.action}</span>;
    case "owner":
      return isUnassigned(c)
        ? <span className="text-muted-foreground italic">Unassigned</span>
        : <span className="text-foreground/80">{c.owner}</span>;
    case "findingType":
      return <span className="text-foreground/80">{findingLabel(c.findingType)}</span>;
  }
}

const inputCls = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/40";

function Fld({ label, children }: Readonly<{ label: string; children: React.ReactNode }>) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function MenuItem({ onClick, children }: Readonly<{ onClick: () => void; children: React.ReactNode }>) {
  return (
    <button onClick={onClick} className="w-full text-left px-3 py-1.5 text-xs text-foreground/85 hover:bg-muted">
      {children}
    </button>
  );
}

function Kv({ k, v, badge }: Readonly<{ k: string; v: string; badge?: boolean }>) {
  return (
    <div>
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="mt-0.5">{badge ? <StatusBadge value={v} /> : <span className="text-foreground font-medium">{v}</span>}</dd>
    </div>
  );
}
