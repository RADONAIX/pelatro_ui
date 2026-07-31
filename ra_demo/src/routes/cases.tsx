import { createFileRoute, useNavigate, useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCheck, CheckCircle2, Clock, Download, Eye, FileText, FolderOpen, Loader2,
  MoreVertical, Paperclip, Plus, RefreshCw, ScanSearch, Search, Sparkles, Upload, UserCheck,
  UserPlus, UserX, X,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import { StatTile } from "@/components/ui-kit/StatTile";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { Select } from "@/components/ui-kit/Select";
import { DateRangePicker } from "@/components/ui-kit/DateRangePicker";
import { useSort, SortHeader } from "@/components/ui-kit/Sortable";
import { TablePagination } from "@/components/reports/ReportFilters";
import { CaseInvestigation } from "@/components/cases/CaseInvestigation";
import { downloadBlob } from "@/services";
import {
  CURRENT_ANALYST, FALLBACK_CATALOG, SEVERITIES, STATUSES,
  addComment as apiAddComment, addInsight as apiAddInsight, assignCase, createCase,
  exportCsvUrl, fetchCase, fetchCatalog, fetchFacets, fetchRules, fetchSummary, fmtBytes,
  fmtDate, fmtDay, isUnassigned, listCases, mismatchLabel, ownerLabel, relative, ruleLabel,
  updateCase, uploadAttachments, validateUploadFile,
  type AssuranceCase, type CaseFacets, type CaseQuery, type CaseSummary, type CatalogMeta,
  type ControlRule,
} from "@/lib/cases";
import { MANUAL_INVESTIGATION } from "../lib/billInvestigation";

export const Route = createFileRoute("/cases")({ component: CasesPage });

// Case Management is the single queue for every assurance: the rule engine
// posts a case whenever a control fails (carrying the assurance, sub-module,
// rule id and the mismatch values), and analysts raise them by hand here.
// Filtering, sorting and pagination all run server-side against /api/cases, so
// the screen works the same with 15 cases or 15,000.

type TileKey = "unassigned" | "assigned" | "open" | "inProgress" | "resolved" | "closed";

interface Tile {
  key: TileKey;
  label: string;
  icon: typeof UserX;
  /** How the tile narrows the query when it's active. */
  narrow: (q: CaseQuery) => CaseQuery;
  count: (s: CaseSummary) => number;
  empty: string;
}

const statusTile = (
  key: TileKey, label: string, icon: typeof UserX, status: string, empty: string,
): Tile => ({
  key, label, icon, empty,
  // A status tile replaces the Status filter rather than intersecting with it —
  // clicking "Closed" always shows closed cases, whatever the dropdown says.
  narrow: (q) => ({ ...q, status: [status] }),
  count: (s) => s.byStatus[status] ?? 0,
});

// Cases tab: the list mixes owned and unowned work, so assignment is the useful
// axis. These mix two dimensions (assignment + status) and intentionally
// overlap — they do not sum to the total.
const ALL_TILES: Tile[] = [
  {
    key: "unassigned", label: "Unassigned", icon: UserX, empty: "No unassigned cases.",
    narrow: (q) => ({ ...q, assignment: "unassigned" }), count: (s) => s.unassigned,
  },
  {
    key: "assigned", label: "Assigned", icon: UserCheck, empty: "No assigned cases.",
    narrow: (q) => ({ ...q, assignment: "assigned" }), count: (s) => s.assigned,
  },
  statusTile("inProgress", "In Progress", Clock, "In Progress", "No cases in progress."),
  statusTile("closed", "Closed", CheckCircle2, "Closed", "No closed cases."),
];

// Self Assigned tab: everything here is already yours, so assignment is
// constant. Show the status lifecycle of your own queue instead.
const SELF_TILES: Tile[] = [
  statusTile("open", "Open", FolderOpen, "Open", "No open cases."),
  statusTile("inProgress", "In Progress", Clock, "In Progress", "No cases in progress."),
  statusTile("resolved", "Resolved", CheckCircle2, "Resolved", "No resolved cases."),
  statusTile("closed", "Closed", CheckCheck, "Closed", "No closed cases."),
];

type ColKey =
  | "date" | "reference" | "assurance" | "module" | "rule" | "category" | "title"
  | "origin" | "status" | "action" | "severity" | "owner" | "mismatch" | "affected";

// `sort` is the backend sort key (app/case_service.SORTABLE); a column without
// one is not sortable server-side.
const COLUMNS: { key: ColKey; label: string; sort?: string }[] = [
  { key: "date", label: "Date", sort: "createdAt" },
  { key: "reference", label: "Case", sort: "reference" },
  { key: "assurance", label: "Assurance", sort: "assurance" },
  { key: "module", label: "Module", sort: "module" },
  { key: "rule", label: "Rule", sort: "ruleId" },
  { key: "category", label: "Issue Type", sort: "category" },
  { key: "title", label: "Title", sort: "title" },
  { key: "mismatch", label: "Mismatch" },
  { key: "origin", label: "Origin", sort: "origin" },
  { key: "status", label: "Status", sort: "status" },
  { key: "action", label: "Action", sort: "action" },
  { key: "severity", label: "Priority", sort: "severity" },
  { key: "owner", label: "Assigned To", sort: "owner" },
  { key: "affected", label: "Affected", sort: "affected" },
];

// Columns shown by default — the rest stay available in the Columns picker so
// the default table doesn't overflow on a laptop.
const DEFAULT_COLS = new Set<ColKey>([
  "date", "reference", "assurance", "module", "rule", "category", "title",
  "status", "severity", "owner",
]);

const cellValue = (c: AssuranceCase, key: ColKey): string => {
  switch (key) {
    case "date": return fmtDate(c.createdAt);
    case "reference": return c.reference;
    case "assurance": return c.assuranceName;
    case "module": return c.subModule ? `${c.module} · ${c.subModule}` : c.module;
    case "rule": return ruleLabel(c);
    case "category": return c.ruleCategory;
    case "title": return c.title;
    case "mismatch": return mismatchLabel(c);
    case "origin": return c.origin;
    case "status": return c.status;
    case "action": return c.action;
    case "severity": return c.severity;
    case "owner": return ownerLabel(c);
    case "affected": return c.affectedCount.toLocaleString();
  }
};

function toCsv(rows: string[][]): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return rows.map((r) => r.map(esc).join(",")).join("\r\n");
}

interface CaseForm {
  title: string;
  description: string;
  assurance: string;
  ruleId: string;
  module: string;
  ruleCategory: string;
  severity: string;
  status: string;
  owner: string;
  expectedValue: string;
  actualValue: string;
  variance: string;
  affectedCount: string;
  estimatedImpact: string;
}

const EMPTY_FORM: CaseForm = {
  // No linked rule means an analyst is investigating something by hand — the
  // issue type follows from that rather than being picked.
  title: "", description: "", assurance: "", ruleId: "", module: "",
  ruleCategory: MANUAL_INVESTIGATION,
  severity: "medium", status: "Open", owner: CURRENT_ANALYST,
  expectedValue: "", actualValue: "", variance: "", affectedCount: "", estimatedImpact: "",
};

function CasesPage() {
  const navigate = useNavigate();
  const tabParam = useRouterState({ select: (s) => (s.location.search as { tab?: string } | undefined)?.tab });
  const tab: "all" | "self" = tabParam === "self" ? "self" : "all";

  // --- filter state -------------------------------------------------------
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [assurance, setAssurance] = useState<Set<string>>(new Set());
  const [module, setModule] = useState<Set<string>>(new Set());
  const [category, setCategory] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<Set<string>>(new Set());
  const [severity, setSeverity] = useState<Set<string>>(new Set());
  const [origin, setOrigin] = useState<Set<string>>(new Set());
  const [ruleIds, setRuleIds] = useState<Set<string>>(new Set());
  const [range, setRange] = useState<{ start: Date; end: Date } | null>(null);
  const [tile, setTile] = useState<TileKey | null>(null);

  // --- table state --------------------------------------------------------
  const [visibleCols, setVisibleCols] = useState<Set<ColKey>>(DEFAULT_COLS);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const { sortKey, sortDir, onSort } = useSort("date", "desc");

  // --- server data --------------------------------------------------------
  const [rows, setRows] = useState<AssuranceCase[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [facets, setFacets] = useState<CaseFacets | null>(null);
  // Seeded from the compiled-in copy so the dropdowns are populated on the
  // first paint and stay usable even if the catalog request fails.
  const [catalog, setCatalog] = useState<CatalogMeta>(FALLBACK_CATALOG);
  const [rules, setRules] = useState<ControlRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  // --- modals -------------------------------------------------------------
  const [activeCase, setActiveCase] = useState<AssuranceCase | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  // Files chosen in the dialog. They can only be uploaded once the case exists,
  // so they are held here until Create succeeds.
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);

  // Debounce the search box so each keystroke isn't a round trip.
  useEffect(() => {
    const id = window.setTimeout(() => { setQ(qInput); setPage(1); }, 300);
    return () => window.clearTimeout(id);
  }, [qInput]);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  // Filters shared by the list and the tiles. The tile narrowing is applied on
  // top for the list only — otherwise the counts would collapse to whatever
  // tile is selected and you could never get back.
  const baseQuery = useMemo<CaseQuery>(() => ({
    q: q || undefined,
    assurance: [...assurance],
    module: [...module],
    category: [...category],
    status: [...status],
    severity: [...severity],
    origin: [...origin],
    ruleId: [...ruleIds],
    assignment: tab === "self" ? "mine" : "all",
    me: CURRENT_ANALYST,
    dateFrom: range?.start.toISOString(),
    dateTo: range?.end.toISOString(),
    dateField: "createdAt",
  }), [q, assurance, module, category, status, severity, origin, ruleIds, tab, range]);

  const tiles = tab === "self" ? SELF_TILES : ALL_TILES;
  const activeTile = tiles.find((t) => t.key === tile) ?? null;

  // useSort cycles asc → desc → unsorted; "unsorted" here means the backend's
  // default (newest first) rather than an unordered page.
  const sortCol = COLUMNS.find((c) => c.key === sortKey);
  const sortBy = sortCol?.sort ?? "createdAt";
  const effectiveDir = sortCol?.sort ? sortDir : "desc";

  const listQuery = useMemo<CaseQuery>(() => ({
    ...(activeTile ? activeTile.narrow(baseQuery) : baseQuery),
    page,
    pageSize,
    sortBy,
    sortDir: effectiveDir,
  }), [baseQuery, activeTile, page, pageSize, sortBy, effectiveDir]);

  // Serialised query as the effect dependency: a string compares by value, so
  // an unrelated re-render (opening a menu, typing in the modal) can't retrigger
  // a fetch the way a fresh object identity would.
  const listKey = JSON.stringify(listQuery);
  const baseKey = JSON.stringify(baseQuery);

  // Reference data: the assurance catalog (every option, even ones with no
  // cases yet), the facets (live counts) and the rule catalog (labels for the
  // Rule filter). Each is best-effort — a failure leaves the compiled-in
  // fallback in place rather than emptying the dropdowns.
  useEffect(() => {
    let cancelled = false;
    fetchCatalog()
      .then((meta) => { if (!cancelled) { setCatalog(meta); setOffline(false); } })
      .catch(() => { if (!cancelled) setOffline(true); });
    fetchFacets()
      .then((f) => { if (!cancelled) setFacets(f); })
      .catch(() => { if (!cancelled) setFacets(null); });
    fetchRules({ pageSize: 500 })
      .then((r) => { if (!cancelled) setRules(r.items); })
      .catch(() => { if (!cancelled) setRules([]); });
    return () => { cancelled = true; };
  }, [reloadTick]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listCases(JSON.parse(listKey) as CaseQuery)
      .then((res) => {
        if (cancelled) return;
        setRows(res.items);
        setTotal(res.total);
        setError(null);
        // A filter change can leave us past the last page — step back onto it.
        if (res.page > res.pageCount) setPage(res.pageCount);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setRows([]);
        setTotal(0);
        setError(e.message);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [listKey, reloadTick]);

  useEffect(() => {
    let cancelled = false;
    fetchSummary(JSON.parse(baseKey) as CaseQuery)
      .then((s) => { if (!cancelled) setSummary(s); })
      .catch(() => { if (!cancelled) setSummary(null); });
    return () => { cancelled = true; };
  }, [baseKey, reloadTick]);

  const setTab = (next: "all" | "self") => {
    setPage(1);
    setTile(null);
    navigate({ to: "/cases", search: next === "self" ? { tab: "self" } : {} });
  };

  const toggleTile = (key: TileKey) => {
    setTile((cur) => (cur === key ? null : key));
    setPage(1);
  };

  const filterCount =
    assurance.size + module.size + category.size + status.size + severity.size + origin.size +
    ruleIds.size + (range ? 1 : 0) + (q ? 1 : 0);

  const clearFilters = () => {
    setQInput("");
    setQ("");
    setAssurance(new Set());
    setModule(new Set());
    setCategory(new Set());
    setStatus(new Set());
    setSeverity(new Set());
    setOrigin(new Set());
    setRuleIds(new Set());
    setRange(null);
    setTile(null);
    setPage(1);
  };

  // --- actions ------------------------------------------------------------

  const openInvestigation = async (c: AssuranceCase) => {
    setMenuFor(null);
    setDetailLoading(true);
    try {
      // The list rows are a projection — pull the full record so the modal has
      // the mismatch evidence, notes and audit trail.
      setActiveCase(await fetchCase(c.id));
    } catch (e) {
      toast.error(`Could not open ${c.reference}`, { description: (e as Error).message });
    } finally {
      setDetailLoading(false);
    }
  };

  const assignToMe = async (c: AssuranceCase) => {
    setMenuFor(null);
    try {
      await assignCase(c.id, CURRENT_ANALYST);
      toast.success(`${c.reference} assigned to you`);
      reload();
    } catch (e) {
      toast.error("Assign failed", { description: (e as Error).message });
    }
  };

  const closeCase = async (c: AssuranceCase) => {
    setMenuFor(null);
    try {
      await updateCase(c.id, { status: "Closed" });
      toast.success(`${c.reference} closed`);
      reload();
    } catch (e) {
      toast.error("Close failed", { description: (e as Error).message });
    }
  };

  const saveCase = async (patch: Parameters<typeof updateCase>[1]) => {
    if (!activeCase) return;
    const next = await updateCase(activeCase.id, patch);
    setActiveCase(next);
    reload();
    return next;
  };

  const commentOnCase = async (body: string) => {
    if (!activeCase) return;
    await apiAddComment(activeCase.id, body);
    setActiveCase(await fetchCase(activeCase.id));
    reload();
  };

  const pinInsight = async (body: string) => {
    if (!activeCase) return;
    setActiveCase(await apiAddInsight(activeCase.id, body));
  };

  // Re-read the open case after an attachment is added or removed, and refresh
  // the list behind it (the attachment count rides on the case's updatedAt).
  const refreshActiveCase = async () => {
    if (!activeCase) return;
    setActiveCase(await fetchCase(activeCase.id));
    reload();
  };

  const exportCsv = async () => {
    try {
      // Server-side export: the whole filtered set, not just the current page.
      const res = await fetch(exportCsvUrl(activeTile ? activeTile.narrow(baseQuery) : baseQuery));
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      downloadBlob(await res.blob(), "cases.csv");
      toast.success(`Exported ${total} cases`);
    } catch {
      // Fall back to the rows already on screen so the button always does
      // something useful even if the backend is unreachable.
      const cols = COLUMNS.filter((c) => visibleCols.has(c.key));
      const csv = toCsv([cols.map((c) => c.label), ...rows.map((r) => cols.map((c) => cellValue(r, c.key)))]);
      downloadBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "cases.csv");
      toast.success(`Exported ${rows.length} cases (current page)`);
    }
  };

  const submitCase = async () => {
    if (!form.title.trim() || !form.assurance) return;
    setSaving(true);
    try {
      const created = await createCase({
        title: form.title.trim(),
        description: form.description.trim() || "Raised manually by an analyst.",
        assurance: form.assurance,
        module: form.module,
        ruleId: form.ruleId || null,
        ruleCategory: form.ruleCategory,
        severity: form.severity,
        status: form.status,
        owner: form.owner.trim(),
        expectedValue: form.expectedValue.trim() || null,
        actualValue: form.actualValue.trim() || null,
        variance: form.variance.trim() || null,
        affectedCount: Number(form.affectedCount) || 0,
        estimatedImpact: Number(form.estimatedImpact) || 0,
        createdBy: CURRENT_ANALYST,
      });

      // The case has to exist before anything can be attached to it, so the
      // upload is a second call. A failure here must not read as "nothing
      // happened" — the case is already saved.
      if (pendingFiles.length) {
        try {
          await uploadAttachments(created.id, pendingFiles);
          toast.success(`${created.reference} created`, {
            description: `${pendingFiles.length} file${pendingFiles.length === 1 ? "" : "s"} attached`,
          });
        } catch (e) {
          toast.warning(`${created.reference} created without its attachment`, {
            description: `${(e as Error).message} — attach it from the investigation view.`,
          });
        }
      } else {
        toast.success(`${created.reference} created`);
      }

      setForm(EMPTY_FORM);
      setPendingFiles([]);
      setAddOpen(false);
      reload();
    } catch (e) {
      toast.error("Could not create the case", { description: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const cols = COLUMNS.filter((c) => visibleCols.has(c.key));
  const emptyMessage = error
    ? `Could not load cases — ${error}`
    : q
      ? "No cases match."
      : activeTile?.empty ?? "No cases match.";

  // Options come from the catalog — every assurance, module and issue type the
  // platform defines, not only the ones that happen to have a case today — with
  // the live counts from the facets appended where there are any.
  const facetCount = (list: { value: string; count: number }[] | undefined, value: string) =>
    list?.find((f) => f.value === value)?.count ?? 0;

  const assuranceOptions = useMemo(() => {
    const names = catalog.assurances.length
      ? catalog.assurances.map((a) => a.name)
      : facets?.assurances.map((f) => f.value) ?? [];
    return names.map((name) => {
      const n = facetCount(facets?.assurances, name);
      return { value: name, label: n ? `${name} (${n})` : name };
    });
  }, [catalog, facets]);

  // Modules narrow to the selected assurances — filtering by "Usage Events"
  // makes no sense while only Billing Assurance is selected.
  const moduleOptions = useMemo(() => {
    const scoped = catalog.assurances.filter((a) => assurance.size === 0 || assurance.has(a.name));
    const names = scoped.length
      ? [...new Set(scoped.flatMap((a) => a.modules))]
      : facets?.modules.map((f) => f.value) ?? [];
    return names.sort((a, b) => a.localeCompare(b)).map((m) => {
      const n = facetCount(facets?.modules, m);
      return { value: m, label: n ? `${m} (${n})` : m };
    });
  }, [catalog, assurance, facets]);

  const categoryOptions = useMemo(() => {
    const list = catalog.ruleCategories.length
      ? catalog.ruleCategories
      : facets?.categories.map((f) => f.value) ?? [];
    return list.map((c) => {
      const n = facetCount(facets?.categories, c);
      return { value: c, label: n ? `${c} (${n})` : c };
    });
  }, [catalog, facets]);

  // Rules narrow to the selected assurances too, and are labelled with the rule
  // name so "UA001" is recognisable without cross-referencing the explorer.
  const ruleOptions = useMemo(() => {
    const named = new Map(rules.map((r) => [r.id, r]));
    const inScope = (code: string, name: string) =>
      assurance.size === 0 || assurance.has(name) || [...assurance].some((a) => a.startsWith(code));
    const fromRules = rules
      .filter((r) => inScope(r.assuranceCode, r.assuranceName))
      .map((r) => ({ value: r.id, label: `${r.id} · ${r.name}` }));
    if (fromRules.length) return fromRules;
    // The rules service is unreachable — fall back to the rule ids the cases
    // themselves reference so the filter still works.
    return (facets?.rules ?? []).map((f) => ({
      value: f.value,
      label: named.has(f.value) ? `${f.value} · ${named.get(f.value)!.name}` : f.value,
    }));
  }, [rules, facets, assurance]);

  return (
    <AppShell>
      <PageHeader
        title="Case Management"
        description="Findings raised by the assurance rules — triage, investigate and resolve."
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
            value={summary ? String(t.count(summary)) : "—"}
            active={tile === t.key}
            onClick={() => toggleTile(t.key)}
          />
        ))}
      </div>

      {/* The case service is unreachable: say so once, plainly, instead of
          letting the screen read as "there are no cases". */}
      {(offline || error) && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-destructive" />
          <div className="text-sm">
            <p className="font-medium text-foreground">Case service unavailable</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {error ?? "Could not reach the case management API."} Filters below show the
              configured options; counts and cases will appear once the service responds.
            </p>
          </div>
          <button onClick={reload} className="ml-auto shrink-0 rounded-lg border border-border bg-background px-3 py-1.5 text-xs hover:bg-muted">
            Retry
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="bg-card border border-border rounded-xl p-4 mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[220px]">
            <span className="block text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1.5">Search</span>
            <div className="flex items-center gap-2 h-9 bg-background border border-border rounded-lg px-3">
              <Search className="h-4 w-4 text-muted-foreground shrink-0" />
              <input
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                placeholder="Case, title, rule, batch, owner…"
                aria-label="Search cases"
                className="flex-1 bg-transparent text-sm focus:outline-none"
              />
              {qInput && (
                <button onClick={() => setQInput("")} aria-label="Clear search" className="text-muted-foreground hover:text-foreground">
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>

          <MultiSelect label="Assurance" options={assuranceOptions} selected={assurance}
            onChange={(next) => { setAssurance(next); setModule(new Set()); setPage(1); }}
            placeholder="All assurances" minWidth={200} allowEmpty />
          <MultiSelect label="Module" options={moduleOptions} selected={module}
            onChange={(next) => { setModule(next); setPage(1); }}
            placeholder="All modules" minWidth={170} allowEmpty />
          <MultiSelect label="Issue Type" options={categoryOptions} selected={category}
            onChange={(next) => { setCategory(next); setPage(1); }}
            placeholder="All issue types" minWidth={170} allowEmpty />
          <MultiSelect label="Status" options={[...STATUSES]} selected={status}
            onChange={(next) => { setStatus(next); setTile(null); setPage(1); }}
            placeholder="All statuses" minWidth={150} allowEmpty />
          <MultiSelect label="Priority" options={[...SEVERITIES]} selected={severity}
            onChange={(next) => { setSeverity(next); setPage(1); }}
            placeholder="All priorities" minWidth={140} allowEmpty />
          <MultiSelect label="Origin" options={[{ value: "auto_detected", label: "Rule engine" }, { value: "analyst_raised", label: "Analyst raised" }]}
            selected={origin} onChange={(next) => { setOrigin(next); setPage(1); }}
            placeholder="Any origin" minWidth={150} allowEmpty />
          <MultiSelect label="Rule" options={ruleOptions} selected={ruleIds}
            onChange={(next) => { setRuleIds(next); setPage(1); }}
            placeholder="All rules" minWidth={200} allowEmpty />

          <div>
            <span className="block text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1.5">Raised between</span>
            <div className="flex items-center gap-1.5">
              <DateRangePicker
                start={range?.start ?? null}
                end={range?.end ?? null}
                showTime={false}
                placeholder="Any date"
                onChange={(r) => { setRange(r); setPage(1); }}
              />
              {range && (
                <button onClick={() => { setRange(null); setPage(1); }} aria-label="Clear date range"
                  className="h-9 w-9 shrink-0 rounded-lg border border-border flex items-center justify-center text-muted-foreground hover:bg-muted">
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 mt-3 pt-3 border-t border-border">
          <span className="text-xs text-muted-foreground">
            {loading ? "Loading…" : `${total.toLocaleString()} case${total === 1 ? "" : "s"}`}
            {filterCount > 0 && !loading && ` · ${filterCount} filter${filterCount === 1 ? "" : "s"} applied`}
          </span>
          {filterCount > 0 && (
            <button onClick={clearFilters} className="text-xs text-primary hover:underline">Clear all</button>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button onClick={reload} aria-label="Refresh"
              className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-background text-sm hover:bg-muted">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
            {tab === "all" ? (
              <>
                <MultiSelect
                  options={COLUMNS.map((c) => ({ value: c.key, label: c.label }))}
                  selected={visibleCols}
                  onChange={(next) => setVisibleCols(next)}
                  placeholder="Columns"
                  minWidth={150}
                />
                <button onClick={exportCsv} aria-label="Export CSV"
                  className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-background text-sm hover:bg-muted">
                  <Download className="h-4 w-4" />
                </button>
              </>
            ) : (
              <button
                onClick={() => {
                  setForm({ ...EMPTY_FORM, assurance: catalog.assurances[0]?.name ?? "" });
                  setPendingFiles([]);
                  setAddOpen(true);
                }}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90"
              >
                <Plus className="h-4 w-4" /> Add Case
              </button>
            )}
          </div>
        </div>
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
                    c.sort ? (
                      <SortHeader
                        key={c.key}
                        label={c.label}
                        colKey={c.key}
                        activeKey={sortKey}
                        dir={sortDir}
                        onSort={onSort}
                        thClassName="text-left font-medium px-4 py-3 whitespace-nowrap"
                      />
                    ) : (
                      // No server-side sort key — render a plain header rather
                      // than a control that looks sortable but does nothing.
                      <th key={c.key} className="text-left font-medium px-4 py-3 whitespace-nowrap">{c.label}</th>
                    )
                  ))}
                  <th className="text-center font-medium px-4 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c, i) => (
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
                          onClick={() => openInvestigation(c)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted whitespace-nowrap"
                        >
                          <Sparkles className="h-3 w-3" /> Investigate
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={cols.length + 2} className="px-4 py-14 text-center text-sm text-muted-foreground">
                      {loading ? "Loading cases…" : emptyMessage}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {total > 0 && (
            <TablePagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPage={setPage}
              onPageSize={(n) => { setPageSize(n); setPage(1); }}
            />
          )}
        </div>
      ) : (
        /* ---------- Self Assigned: cards ---------- */
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {rows.map((c) => (
              <div key={c.id} className="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-semibold text-foreground truncate">{c.reference}</div>
                    <div className="text-xs text-muted-foreground font-mono truncate">
                      {c.assuranceName} · {c.ruleId ?? c.origin}
                    </div>
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
                          <MenuItem onClick={() => openInvestigation(c)}>Open investigation</MenuItem>
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
                  <Kv k="Date" v={fmtDay(c.createdAt)} />
                  <Kv k="Priority" v={c.severity} badge />
                  <Kv k="Module" v={c.subModule ? `${c.module} · ${c.subModule}` : c.module || "—"} />
                  <Kv k="Issue type" v={c.ruleCategory || "—"} />
                  <Kv k="Status" v={c.status} badge />
                  <Kv k="Action" v={c.action} />
                  <div className="col-span-2">
                    <dt className="text-muted-foreground">Mismatch</dt>
                    <dd className="mt-0.5 font-mono text-[11px] text-foreground truncate" title={mismatchLabel(c)}>
                      {mismatchLabel(c)}
                    </dd>
                  </div>
                </dl>

                <div className="flex items-center justify-between pt-2 border-t border-border mt-auto">
                  <span className="text-[11px] text-muted-foreground">Updated {relative(c.updatedAt)}</span>
                  <button
                    onClick={() => openInvestigation(c)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/5"
                  >
                    Investigate
                  </button>
                </div>
              </div>
            ))}
            {rows.length === 0 && (
              <div className="col-span-full rounded-xl border border-dashed border-border bg-muted/10 py-16 text-center text-sm text-muted-foreground">
                {loading ? "Loading cases…" : tile || q ? emptyMessage : "No cases assigned to you."}
              </div>
            )}
          </div>
          {total > pageSize && (
            <div className="bg-card border border-border rounded-xl mt-4 overflow-hidden">
              <TablePagination
                page={page}
                pageSize={pageSize}
                total={total}
                onPage={setPage}
                onPageSize={(n) => { setPageSize(n); setPage(1); }}
              />
            </div>
          )}
        </>
      )}

      {detailLoading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" aria-label="Loading case">
          <Loader2 className="h-6 w-6 animate-spin text-primary-foreground" />
        </div>
      )}

      {activeCase && (
        <CaseInvestigation
          activeCase={activeCase}
          onClose={() => setActiveCase(null)}
          onSave={saveCase}
          onComment={commentOnCase}
          onPin={pinInsight}
          onRefresh={refreshActiveCase}
        />
      )}

      {addOpen && (
        <AddCaseDialog
          form={form}
          setForm={setForm}
          catalog={catalog}
          rules={rules}
          files={pendingFiles}
          setFiles={setPendingFiles}
          saving={saving}
          onClose={() => { setAddOpen(false); setPendingFiles([]); }}
          onSubmit={submitCase}
        />
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
    case "assurance":
      return (
        <span className="text-foreground/80" title={c.assuranceGroup}>
          {c.assuranceName}
        </span>
      );
    case "module":
      return (
        <span className="text-foreground/80">
          {c.module || "—"}
          {c.subModule && <span className="text-muted-foreground"> · {c.subModule}</span>}
        </span>
      );
    case "rule":
      return c.ruleId ? (
        <span className="font-mono text-xs text-foreground/80" title={c.ruleName ?? undefined}>{c.ruleId}</span>
      ) : (
        <span className="text-muted-foreground">—</span>
      );
    case "category":
      return <span className="text-foreground/80">{c.ruleCategory || "—"}</span>;
    case "title":
      return <span className="block max-w-[320px] truncate text-foreground/80" title={c.title}>{c.title}</span>;
    case "mismatch":
      return (
        <span className="block max-w-[240px] truncate font-mono text-xs text-foreground/80" title={mismatchLabel(c)}>
          {mismatchLabel(c)}
        </span>
      );
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
    case "affected":
      return <span className="tabular-nums text-foreground/80">{c.affectedCount.toLocaleString()}</span>;
  }
}

// A manually raised case still has to classify like a rule-raised one —
// assurance, module and issue type are required so it lands in the same
// filters as everything the engine produces.
function AddCaseDialog({
  form, setForm, catalog, rules, files, setFiles, saving, onClose, onSubmit,
}: Readonly<{
  form: CaseForm;
  setForm: (f: CaseForm) => void;
  catalog: CatalogMeta;
  rules: ControlRule[];
  files: File[];
  setFiles: (files: File[]) => void;
  saving: boolean;
  onClose: () => void;
  onSubmit: () => void;
}>) {
  const selected = catalog.assurances.find((a) => a.name === form.assurance);
  const modules = selected?.modules ?? [];
  // Only rules belonging to the chosen assurance can be linked.
  const scopedRules = rules.filter((r) => !selected || r.assuranceCode === selected.code);

  // Linking a rule adopts its module and issue type — a manually raised case
  // then classifies exactly like one the engine would have raised.
  const pickRule = (id: string) => {
    const rule = scopedRules.find((r) => r.id === id);
    setForm(rule
      ? { ...form, ruleId: rule.id, module: rule.entityScope, ruleCategory: rule.primitiveCategory,
          severity: rule.severity }
      // Unlinking reverts to a manual investigation.
      : { ...form, ruleId: "", ruleCategory: MANUAL_INVESTIGATION });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const valid = form.title.trim().length > 0 && form.assurance.length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Add case">
      <button className="absolute inset-0 bg-black/50" aria-hidden="true" tabIndex={-1} onClick={onClose} />
      <div className="relative w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-2xl border border-border bg-card shadow-xl">
        <div className="sticky top-0 flex items-center justify-between px-5 py-4 border-b border-border bg-card">
          <div>
            <h2 className="font-semibold text-foreground">Add Case</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Raised manually — classified the same way a rule-raised case is.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <Fld label="Title">
            <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Describe the finding…" className={inputCls} />
          </Fld>
          <Fld label="Description">
            <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What was observed, and where?" rows={2} className={inputCls} />
          </Fld>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Fld label="Assurance">
              <Select
                value={form.assurance}
                onChange={(v) => setForm({ ...form, assurance: v, module: "", ruleId: "" })}
                options={catalog.assurances.map((a) => ({ value: a.name, label: `${a.name} (${a.code})` }))}
                placeholder="Select an assurance"
                minWidth={220}
                ariaLabel="Assurance"
              />
            </Fld>
            <Fld label="Module">
              <Select
                value={form.module}
                onChange={(v) => setForm({ ...form, module: v })}
                options={modules.map((m) => ({ value: m, label: m }))}
                placeholder={form.assurance ? "Select a module" : "Pick an assurance first"}
                minWidth={220}
                ariaLabel="Module"
              />
            </Fld>
            <Fld label="Linked rule (optional)">
              <Select
                value={form.ruleId}
                onChange={pickRule}
                options={[
                  { value: "", label: "None — analyst raised" },
                  ...scopedRules.map((r) => ({ value: r.id, label: `${r.id} · ${r.name}` })),
                ]}
                placeholder={form.assurance ? "Link a control rule" : "Pick an assurance first"}
                minWidth={220}
                ariaLabel="Linked rule"
              />
            </Fld>
            <Fld label="Issue type">
              {form.ruleId ? (
                <Select
                  value={form.ruleCategory}
                  onChange={(v) => setForm({ ...form, ruleCategory: v })}
                  options={catalog.ruleCategories.map((c) => ({ value: c, label: c }))}
                  placeholder="Select an issue type"
                  minWidth={220}
                  ariaLabel="Issue type"
                />
              ) : (
                // Derived, not chosen: with no rule behind the case there is no
                // primitive category to inherit.
                <div className="h-9 px-3 inline-flex items-center gap-2 rounded-lg border border-border bg-muted/40 text-sm text-foreground">
                  <ScanSearch className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <span className="truncate">{MANUAL_INVESTIGATION}</span>
                  <span className="ml-auto text-[10px] text-muted-foreground shrink-0">auto</span>
                </div>
              )}
            </Fld>
            <Fld label="Priority">
              <Select
                value={form.severity}
                onChange={(v) => setForm({ ...form, severity: v })}
                options={SEVERITIES.map((s) => ({ value: s, label: s }))}
                minWidth={220}
                ariaLabel="Priority"
              />
            </Fld>
            <Fld label="Status">
              <Select
                value={form.status}
                onChange={(v) => setForm({ ...form, status: v })}
                options={STATUSES.map((s) => ({ value: s, label: s }))}
                minWidth={220}
                ariaLabel="Status"
              />
            </Fld>
            <Fld label="Assign to">
              <input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })}
                placeholder="Leave empty to keep it unassigned" className={inputCls} />
            </Fld>
          </div>

          {/* <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Mismatch values
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Fld label="Expected">
                <input value={form.expectedValue} onChange={(e) => setForm({ ...form, expectedValue: e.target.value })}
                  placeholder="1,284,322" className={inputCls} />
              </Fld>
              <Fld label="Actual">
                <input value={form.actualValue} onChange={(e) => setForm({ ...form, actualValue: e.target.value })}
                  placeholder="1,281,904" className={inputCls} />
              </Fld>
              <Fld label="Variance">
                <input value={form.variance} onChange={(e) => setForm({ ...form, variance: e.target.value })}
                  placeholder="-2,418" className={inputCls} />
              </Fld>
              <Fld label="Affected records">
                <input value={form.affectedCount} onChange={(e) => setForm({ ...form, affectedCount: e.target.value })}
                  inputMode="numeric" placeholder="0" className={inputCls} />
              </Fld>
              <Fld label="Revenue at risk">
                <input value={form.estimatedImpact} onChange={(e) => setForm({ ...form, estimatedImpact: e.target.value })}
                  inputMode="numeric" placeholder="0" className={inputCls} />
              </Fld>
            </div>
          </div> */}

          <AttachmentPicker files={files} setFiles={setFiles} disabled={saving} />

          <div className="flex justify-end gap-2 pt-1">
            <button onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-muted-foreground hover:bg-muted">Cancel</button>
            <button
              onClick={onSubmit}
              disabled={!valid || saving}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Create
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// PDF evidence picker. The files can't be sent until the case exists, so this
// only collects them — the dialog uploads them once Create succeeds. Supports
// click-to-browse and drag-and-drop, and rejects the obvious mistakes locally
// (the server re-validates type, magic bytes and size regardless).
function AttachmentPicker({
  files, setFiles, disabled,
}: Readonly<{ files: File[]; setFiles: (files: File[]) => void; disabled?: boolean }>) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const add = (incoming: FileList | null) => {
    if (!incoming?.length) return;
    const accepted: File[] = [];
    for (const file of Array.from(incoming)) {
      const problem = validateUploadFile(file);
      if (problem) {
        toast.error(`${file.name} — ${problem}`);
        continue;
      }
      // Same name and size twice is a double-pick, not two documents.
      if (files.some((f) => f.name === file.name && f.size === file.size)) continue;
      accepted.push(file);
    }
    if (accepted.length) setFiles([...files, ...accepted]);
  };

  const remove = (index: number) => setFiles(files.filter((_, i) => i !== index));

  // Preview before upload: the file only exists in the browser at this point,
  // so it is shown from an object URL. Revoked on a timer rather than
  // immediately — revoking before the new tab has loaded blanks it.
  const preview = (file: File) => {
    const url = URL.createObjectURL(file);
    window.open(url, "_blank", "noopener");
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  return (
    <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Paperclip className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Attachments
        </span>
        <span className="text-[11px] text-muted-foreground">PDF only, up to 25 MB each</span>
      </div>

      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); add(e.dataTransfer.files); }}
        disabled={disabled}
        className={`w-full rounded-lg border border-dashed px-4 py-5 text-center transition disabled:opacity-50 ${
          dragging ? "border-primary bg-primary/5" : "border-border bg-background hover:bg-muted/40"
        }`}
      >
        <Upload className="mx-auto h-5 w-5 text-muted-foreground" />
        <span className="mt-2 block text-sm text-foreground">Add file</span>
        <span className="block text-[11px] text-muted-foreground">
          Click to browse or drop a PDF here
        </span>
      </button>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="hidden"
        aria-label="Attach PDF evidence"
        onChange={(e) => {
          add(e.target.files);
          // Reset so re-picking the same file after removing it still fires.
          e.target.value = "";
        }}
      />

      {files.length > 0 && (
        <ul className="space-y-2">
          {files.map((file, i) => (
            <li key={`${file.name}-${file.size}-${i}`}
                className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2">
              <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                <FileText className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-foreground" title={file.name}>{file.name}</div>
                <div className="text-[11px] text-muted-foreground">{fmtBytes(file.size)} · ready to upload</div>
              </div>
              <button
                type="button"
                onClick={() => preview(file)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted"
              >
                <Eye className="h-3 w-3" /> Preview
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                disabled={disabled}
                aria-label={`Remove ${file.name}`}
                className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted disabled:opacity-40"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
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
    <div className="min-w-0">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="mt-0.5 truncate">{badge ? <StatusBadge value={v} /> : <span className="text-foreground font-medium">{v}</span>}</dd>
    </div>
  );
}
