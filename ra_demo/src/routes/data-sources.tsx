import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Database, Plus, Trash2, X, Server, Activity, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
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
const STORAGE_KEY = "radonaix_data_sources_v3";

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
  useCase: string;
  // References a connection from lib/dbConnections (admin → Database Connections).
  connectionId: string;
  recordsPerDay: number;
  enabled: boolean;
}

// Seeded with the two real streams (AIR, SDP) plus dummy feeds so the demo opens
// looking populated — every source type + use case represented, one disabled.
// Note SDP, MSC and Exception Handler all share conn-rafms-replica: that reuse is
// the reason connections were pulled out of the per-source rows.
const SEED: DataSource[] = [
  { id: "seed-air", name: "AIR", key: "air", type: "AIR CDR", useCase: "Usage Assurance", connectionId: "conn-rafms-primary", recordsPerDay: 4_050_000, enabled: true },
  { id: "seed-sdp", name: "SDP", key: "sdp", type: "SDP CDR", useCase: "Rating Assurance", connectionId: "conn-rafms-replica", recordsPerDay: 40_550_000, enabled: true },
  { id: "seed-msc", name: "MSC Voice", key: "msc", type: "Diameter", useCase: "Usage Assurance", connectionId: "conn-rafms-replica", recordsPerDay: 12_800_000, enabled: true },
  { id: "seed-ocs", name: "OCS Charging", key: "ocs", type: "Diameter", useCase: "Billing Assurance", connectionId: "conn-ocs-charging", recordsPerDay: 28_300_000, enabled: true },
  { id: "seed-exc", name: "Exception Handler", key: "exception", type: "Custom", useCase: "Mediation Assurance", connectionId: "conn-rafms-replica", recordsPerDay: 850_000, enabled: true },
  { id: "seed-rech", name: "Prepaid Recharge", key: "recharge", type: "Mediation Feed", useCase: "Subscription Assurance", connectionId: "conn-recharge-store", recordsPerDay: 3_600_000, enabled: false },
  { id: "seed-bill", name: "Postpaid Billing", key: "billing", type: "Mediation Feed", useCase: "Billing Assurance", connectionId: "conn-billing-core", recordsPerDay: 2_100_000, enabled: true },
];

function load(): DataSource[] {
  if (typeof window === "undefined") return SEED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as DataSource[]) : null;
    // Non-empty stored list wins; empty/blank (incl. a previously clobbered "[]")
    // falls back to the seed so the demo never shows a blank screen on refresh.
    if (Array.isArray(parsed) && parsed.length) return parsed;
  } catch {
    /* ignore malformed storage */
  }
  return SEED;
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
  useCase: string;
  connectionId: string;
  recordsPerDay: string;
}

const EMPTY_FORM: SourceForm = { name: "", key: "", type: SOURCE_TYPES[0], useCase: USE_CASES[0], connectionId: "", recordsPerDay: "" };

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

  const canSave = form.name.trim() && form.key.trim() && form.connectionId;

  const addSource = () => {
    if (!canSave) return;
    const ds: DataSource = {
      id: newId(),
      name: form.name.trim(),
      key: form.key.trim().toLowerCase().replace(/\s+/g, "_"),
      type: form.type,
      useCase: form.useCase,
      connectionId: form.connectionId,
      recordsPerDay: Number(form.recordsPerDay) || 0,
      enabled: true,
    };
    persist([...sources, ds]);
    setForm(EMPTY_FORM);
    setOpen(false);
    toast.success(`Data source "${ds.name}" onboarded`, { description: `${ds.type} · ${connOf(ds.connectionId)?.name ?? ds.useCase}` });
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

      {/* Source cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {sources.map((s) => (
          <div key={s.id} className="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="h-10 w-10 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                  <Server className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <div className="font-semibold text-foreground leading-tight truncate">{s.name}</div>
                  <div className="text-xs text-muted-foreground font-mono">{s.key}</div>
                </div>
              </div>
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${s.enabled ? "bg-emerald-500/10 text-emerald-600" : "bg-muted text-muted-foreground"}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${s.enabled ? "bg-emerald-500" : "bg-muted-foreground/50"}`} />
                {s.enabled ? "Connected" : "Disabled"}
              </span>
            </div>

            <div className="flex flex-wrap gap-2">
              <Badge>{s.type}</Badge>
              <Badge tone="primary">{s.useCase}</Badge>
            </div>

            {(() => {
              const conn = connOf(s.connectionId);
              return (
                <dl className="text-xs text-muted-foreground space-y-1">
                  <Row k="Connection" v={conn?.name ?? "— connection missing"} mono={!!conn} />
                  <Row k="Target" v={conn ? connectionTarget(conn) : "—"} mono={!!conn} />
                  <Row k="Records / day" v={fmt(s.recordsPerDay)} />
                </dl>
              );
            })()}

            <div className="flex items-center justify-between pt-2 border-t border-border mt-auto">
              <label className="inline-flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none">
                <input type="checkbox" checked={s.enabled} onChange={() => toggle(s.id)} className="h-4 w-4 accent-[var(--primary,#4f46e5)]" />
                {s.enabled ? "Enabled" : "Enable"}
              </label>
              <button onClick={() => remove(s.id)} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-red-600 transition">
                <Trash2 className="h-3.5 w-3.5" /> Remove
              </button>
            </div>
          </div>
        ))}

        {sources.length === 0 && (
          <div className="col-span-full flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/10 py-16 text-center">
            <Database className="h-10 w-10 text-muted-foreground/40" />
            <p className="mt-3 text-sm text-muted-foreground">No data sources yet.</p>
            <button onClick={() => setOpen(true)} className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90">
              <Plus className="h-4 w-4" /> Add your first source
            </button>
          </div>
        )}
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
              <FormField label="Assurance use case">
                <select value={form.useCase} onChange={(e) => setForm({ ...form, useCase: e.target.value })} className={inputCls}>
                  {USE_CASES.map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              </FormField>
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

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt>{k}</dt>
      <dd className={`text-foreground ${mono ? "font-mono" : "tabular-nums"}`}>{v}</dd>
    </div>
  );
}
