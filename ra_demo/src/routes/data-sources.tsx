import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Database, Plus, Trash2, X, Activity, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { loadConnections, connectionTarget, type DbConnection } from "@/lib/dbConnections";

export const Route = createFileRoute("/data-sources")({ component: DataSourcesPage });

// DEMO-ONLY feature. Everything here is client-side and persisted to
// localStorage — it does NOT call the backend, so it can't affect the pipeline,
// reports, or any real stream. Mirrors how the header "Assurance Scope" switcher
// works: purely presentational.
//
// v3: sources no longer carry their own host/database. They reference a
// connection defined in the admin Database Connections screen, so a target is
// configured once and reused. The key is bumped because JSON.parse can't catch
// the shape change and v2 rows would deserialize with no connectionId.
//
// v4: a feed usually serves more than one assurance — AIR CDRs back both Usage
// and Mediation — so `useCase: string` became `useCases: string[]`. Unlike the
// v2→v3 bump this one migrates rather than discards: the old single value maps
// cleanly onto a one-element array, so nobody loses sources they onboarded.
const STORAGE_KEY = "radonaix_data_sources_v4";
const LEGACY_KEY_V3 = "radonaix_data_sources_v3";

const USE_CASES = [
  "Mediation Assurance",
  "Usage Assurance",
  "Billing Assurance",
  "Rating Assurance",
  "Roaming Assurance",
  "Subscription Assurance",
] as const;

const SOURCE_TYPES = ["AIR CDR", "SDP CDR", "Diameter", "Mediation Feed", "Roaming TAP", "Custom"] as const;

interface DataSource {
  id: string;
  name: string;
  key: string;
  type: string;
  /** One feed can serve several assurances. */
  useCases: string[];
  // References a connection from lib/dbConnections (admin → Database Connections).
  connectionId: string;
  recordsPerDay: number;
  enabled: boolean;
}

/** The v3 row shape, kept only so stored rows can be migrated forward. */
type LegacyDataSourceV3 = Omit<DataSource, "useCases"> & { useCase?: string };

// Seeded with the two real streams (AIR, SDP) plus dummy feeds so the demo opens
// looking populated — every source type + use case represented, one disabled.
// Note SDP, MSC and Exception Handler all share conn-rafms-replica: that reuse is
// the reason connections were pulled out of the per-source rows.
const SEED: DataSource[] = [
  { id: "seed-air", name: "AIR", key: "air", type: "AIR CDR", useCases: ["Usage Assurance", "Mediation Assurance"], connectionId: "conn-rafms-primary", recordsPerDay: 4_050_000, enabled: true },
  { id: "seed-sdp", name: "SDP", key: "sdp", type: "SDP CDR", useCases: ["Rating Assurance", "Mediation Assurance"], connectionId: "conn-rafms-replica", recordsPerDay: 40_550_000, enabled: true },
  { id: "seed-msc", name: "MSC Voice", key: "msc", type: "Diameter", useCases: ["Usage Assurance"], connectionId: "conn-rafms-replica", recordsPerDay: 12_800_000, enabled: true },
  { id: "seed-ocs", name: "OCS Charging", key: "ocs", type: "Diameter", useCases: ["Billing Assurance", "Rating Assurance"], connectionId: "conn-ocs-charging", recordsPerDay: 28_300_000, enabled: true },
  { id: "seed-exc", name: "Exception Handler", key: "exception", type: "Custom", useCases: ["Mediation Assurance"], connectionId: "conn-rafms-replica", recordsPerDay: 850_000, enabled: true },
  { id: "seed-rech", name: "Prepaid Recharge", key: "recharge", type: "Mediation Feed", useCases: ["Subscription Assurance"], connectionId: "conn-recharge-store", recordsPerDay: 3_600_000, enabled: false },
  { id: "seed-bill", name: "Postpaid Billing", key: "billing", type: "Mediation Feed", useCases: ["Billing Assurance"], connectionId: "conn-billing-core", recordsPerDay: 2_100_000, enabled: true },
];

/** Widen a stored row to the current shape. Safe to run on already-v4 rows. */
function migrate(row: DataSource & LegacyDataSourceV3): DataSource {
  const { useCase, ...rest } = row;
  if (Array.isArray(rest.useCases) && rest.useCases.length) return rest as DataSource;
  return { ...rest, useCases: useCase ? [useCase] : [] } as DataSource;
}

function load(): DataSource[] {
  if (typeof window === "undefined") return SEED;
  const read = (key: string) => {
    try {
      const raw = window.localStorage.getItem(key);
      const parsed = raw ? (JSON.parse(raw) as (DataSource & LegacyDataSourceV3)[]) : null;
      // Non-empty stored list wins; empty/blank (incl. a previously clobbered
      // "[]") falls through so the demo never shows a blank screen on refresh.
      return Array.isArray(parsed) && parsed.length ? parsed.map(migrate) : null;
    } catch {
      return null; /* ignore malformed storage */
    }
  };
  // v4 first; otherwise carry v3 rows forward rather than silently reseeding
  // over sources someone onboarded.
  return read(STORAGE_KEY) ?? read(LEGACY_KEY_V3) ?? SEED;
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `ds-${Math.floor(Math.random() * 1e9)}`;
}

const fmt = (n: number) => new Intl.NumberFormat("en-US").format(n);

// `type`/`useCase` are widened to string: they're bound to <select> elements
// whose onChange yields a plain string, not the literal union.
interface SourceForm {
  name: string;
  key: string;
  type: string;
  useCases: Set<string>;
  connectionId: string;
  recordsPerDay: string;
}

const EMPTY_FORM: SourceForm = {
  name: "",
  key: "",
  type: SOURCE_TYPES[0],
  useCases: new Set<string>(),
  connectionId: "",
  recordsPerDay: "",
};

function DataSourcesPage() {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [connections, setConnections] = useState<DbConnection[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<typeof EMPTY_FORM>(EMPTY_FORM);

  useEffect(() => {
    setSources(load());
    setConnections(loadConnections());
  }, []);

  // A source can outlive the connection it referenced (deleting one is allowed,
  // with a warning) — resolve defensively rather than assuming it's still there.
  const connOf = (id: string) => connections.find((c) => c.id === id);

  // Persist ONLY on explicit user actions — never from a reactive effect, which
  // would race the initial mount load and clobber saved data with the empty
  // initial state (notably under StrictMode's double-invoked effects in dev).
  const persist = (next: DataSource[]) => {
    setSources(next);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const stats = useMemo(() => {
    const connected = sources.filter((s) => s.enabled).length;
    const total = sources.filter((s) => s.enabled).reduce((a, s) => a + (s.recordsPerDay || 0), 0);
    return { count: sources.length, connected, recordsPerDay: total };
  }, [sources]);

  // At least one assurance is required: a feed nothing consumes has no reason to
  // be onboarded, and the table would show a blank cell for it.
  const canSave = form.name.trim() && form.key.trim() && form.connectionId && form.useCases.size > 0;

  const addSource = () => {
    if (!canSave) return;
    const ds: DataSource = {
      id: newId(),
      name: form.name.trim(),
      key: form.key.trim().toLowerCase().replace(/\s+/g, "_"),
      type: form.type,
      // Ordered by USE_CASES rather than click order, so the table column reads
      // consistently across rows.
      useCases: USE_CASES.filter((u) => form.useCases.has(u)),
      connectionId: form.connectionId,
      recordsPerDay: Number(form.recordsPerDay) || 0,
      enabled: true,
    };
    persist([...sources, ds]);
    setForm(EMPTY_FORM);
    setOpen(false);
    toast.success(`Data source "${ds.name}" onboarded`, {
      description: `${ds.type} · ${ds.useCases.length} assurance${ds.useCases.length === 1 ? "" : "s"}`,
    });
  };

  const toggle = (id: string) =>
    persist(sources.map((d) => (d.id === id ? { ...d, enabled: !d.enabled } : d)));

  const remove = (id: string) => {
    const gone = sources.find((d) => d.id === id);
    persist(sources.filter((d) => d.id !== id));
    if (gone) toast.success(`Removed "${gone.name}"`);
  };

  return (
    <AppShell>
      <PageHeader
        title="Data Sources"
        description="Revenue-assurance feeds connected to the platform. Onboard a new source to bring its stream into the pipeline and every report."
        actions={
          <button
            onClick={() => { setForm(EMPTY_FORM); setOpen(true); }}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
          >
            <Plus className="h-4 w-4" /> Add Data Source
          </button>
        }
      />

      {/* Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6 max-w-4xl">
        <StatTile icon={Database} label="Data sources" value={String(stats.count)} />
        <StatTile icon={CheckCircle2} label="Connected" value={`${stats.connected} / ${stats.count}`} />
        <StatTile icon={Activity} label="Records / day" value={fmt(stats.recordsPerDay)} />
      </div>

      {/* Source table.
          Deliberately narrow: what identifies the feed, who consumes it, where
          it comes from, how much it carries, and whether it's on. The
          connection's host:port/database is infrastructure detail that the
          connection name already stands for — it's a tooltip, not a column. */}
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="text-left text-[11px] uppercase tracking-wider text-muted-foreground bg-muted/40">
              <tr className="border-b border-border">
                <th className="px-4 py-2.5 font-medium">Source</th>
                <th className="px-4 py-2.5 font-medium">Type</th>
                <th className="px-4 py-2.5 font-medium">Assurance use cases</th>
                <th className="px-4 py-2.5 font-medium">Connection</th>
                <th className="px-4 py-2.5 font-medium text-right">Records / day</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const conn = connOf(s.connectionId);
                return (
                  <tr key={s.id} className="border-b border-border/60 last:border-0 hover:bg-muted/30">
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-foreground leading-tight">{s.name}</div>
                      <div className="text-[11px] font-mono text-muted-foreground">{s.key}</div>
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">{s.type}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {s.useCases.length === 0 ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : (
                          s.useCases.map((u) => (
                            <Badge key={u} tone="primary">
                              {u.replace(" Assurance", "")}
                            </Badge>
                          ))
                        )}
                      </div>
                    </td>
                    <td
                      className="px-4 py-2.5 text-muted-foreground"
                      title={conn ? connectionTarget(conn) : undefined}
                    >
                      {conn ? (
                        <span className="font-mono text-xs">{conn.name}</span>
                      ) : (
                        <span className="text-xs text-destructive">connection missing</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-foreground">
                      {fmt(s.recordsPerDay)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${s.enabled ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${s.enabled ? "bg-success" : "bg-muted-foreground/50"}`}
                        />
                        {s.enabled ? "Connected" : "Disabled"}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-3">
                        <button
                          onClick={() => toggle(s.id)}
                          className="text-xs text-muted-foreground hover:text-foreground transition"
                        >
                          {s.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          onClick={() => remove(s.id)}
                          aria-label={`Remove ${s.name}`}
                          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-destructive transition"
                        >
                          <Trash2 className="h-3.5 w-3.5" /> Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}

              {sources.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <div className="flex flex-col items-center justify-center py-16 text-center">
                      <Database className="h-10 w-10 text-muted-foreground/40" />
                      <p className="mt-3 text-sm text-muted-foreground">No data sources yet.</p>
                      <button
                        onClick={() => setOpen(true)}
                        className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
                      >
                        <Plus className="h-4 w-4" /> Add your first source
                      </button>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add dialog */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="relative w-full max-w-lg rounded-2xl border border-border bg-card shadow-xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="font-semibold text-foreground">Add Data Source</h2>
              <button onClick={() => setOpen(false)} className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="px-5 py-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
              <FormField label="Display name">
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. MSC" className={inputCls} />
              </FormField>
              <FormField label="Stream key">
                <input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} placeholder="e.g. msc" className={`${inputCls} font-mono`} />
              </FormField>
              <FormField label="Source type">
                <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className={inputCls}>
                  {SOURCE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </FormField>
              <div className="sm:col-span-2">
                {/* A feed usually serves more than one assurance, so this is a
                    multi-select. Rendered outside FormField: MultiSelect owns
                    its own label and a <label> wrapper would steal its clicks. */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-muted-foreground">
                    Assurance use cases
                  </span>
                  <MultiSelect
                    options={USE_CASES.map((u) => ({ value: u, label: u }))}
                    selected={form.useCases}
                    onChange={(next) => setForm({ ...form, useCases: next as Set<string> })}
                    placeholder="Select one or more…"
                    minWidth={240}
                    allowEmpty
                  />
                  {form.useCases.size === 0 && (
                    <span className="text-[11px] text-muted-foreground">
                      Pick at least one — it decides which assurances consume this feed.
                    </span>
                  )}
                </div>
              </div>
              <div className="sm:col-span-2">
                <FormField label="Database connection">
                  <select value={form.connectionId} onChange={(e) => setForm({ ...form, connectionId: e.target.value })} className={inputCls}>
                    <option value="">Select a connection…</option>
                    {connections.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} — {c.engine} · {connectionTarget(c)}
                      </option>
                    ))}
                  </select>
                  <span className="text-[11px] text-muted-foreground">
                    Managed in{" "}
                    <Link to="/database-connections" className="text-primary hover:underline">
                      Database Connections
                    </Link>
                    .
                  </span>
                </FormField>
              </div>
              <FormField label="Records / day (est.)">
                <input type="number" min={0} value={form.recordsPerDay} onChange={(e) => setForm({ ...form, recordsPerDay: e.target.value })} placeholder="0" className={inputCls} />
              </FormField>
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border">
              <button onClick={() => setOpen(false)} className="rounded-lg px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted">Cancel</button>
              <button onClick={addSource} disabled={!canSave} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed">
                <Plus className="h-4 w-4" /> Add source
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

const inputCls = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/40";

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}


function Badge({ children, tone }: { children: React.ReactNode; tone?: "primary" }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium ${tone === "primary" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}>
      {children}
    </span>
  );
}

