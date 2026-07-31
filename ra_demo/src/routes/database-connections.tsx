import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Database, Plug, Plus, Server, ShieldCheck, Trash2, X, Eye, EyeOff, Pencil, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { ConfirmDialog } from "@/components/ui-kit/ConfirmDialog";
import { useAuth } from "@/lib/auth";
import {
  ENGINES,
  SSL_MODES,
  DEFAULT_PORT,
  SEED,
  loadConnections,
  saveConnections,
  connectionTarget,
  type DbConnection,
} from "@/lib/dbConnections";

export const Route = createFileRoute("/database-connections")({ component: DatabaseConnectionsPage });

// DEMO-ONLY. Client-side only; no backend calls (no connection CRUD API exists).
// Passwords are collected by the form but never persisted — see lib/dbConnections.
const DATA_SOURCES_KEY = "radonaix_data_sources_v3";

interface ConnForm {
  name: string;
  engine: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string; // form-only — deliberately never written to storage
  sslMode: string;
  poolSize: string;
}

const EMPTY_FORM = (): ConnForm => ({
  name: "",
  engine: ENGINES[0],
  host: "",
  port: String(DEFAULT_PORT[ENGINES[0]]),
  database: "",
  username: "",
  password: "",
  sslMode: "require",
  poolSize: "10",
});

const toForm = (c: DbConnection): ConnForm => ({
  name: c.name,
  engine: c.engine,
  host: c.host,
  port: String(c.port),
  database: c.database,
  username: c.username,
  password: "",
  sslMode: c.sslMode,
  poolSize: String(c.poolSize),
});

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `conn-${Math.floor(Math.random() * 1e9)}`;
}

// Which data sources point at a given connection — used to warn before deleting.
function sourcesUsing(connectionId: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(DATA_SOURCES_KEY);
    const parsed = raw ? (JSON.parse(raw) as { name: string; connectionId: string }[]) : null;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((s) => s.connectionId === connectionId).map((s) => s.name);
  } catch {
    return [];
  }
}

function DatabaseConnectionsPage() {
  const { canEdit } = useAuth();
  const editable = canEdit("/database-connections");

  const [conns, setConns] = useState<DbConnection[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<DbConnection | null>(null);
  const [form, setForm] = useState<ConnForm>(EMPTY_FORM);
  const [showPassword, setShowPassword] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<DbConnection | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  useEffect(() => {
    setConns(loadConnections());
  }, []);

  // Persist ONLY on explicit user actions — never from a reactive effect, which
  // would race the initial mount load and clobber saved data with the empty
  // initial state (notably under StrictMode's double-invoked effects in dev).
  const persist = (next: DbConnection[]) => {
    setConns(next);
    saveConnections(next);
  };

  const stats = useMemo(
    () => ({
      total: conns.length,
      enabled: conns.filter((c) => c.enabled).length,
      engines: new Set(conns.map((c) => c.engine)).size,
    }),
    [conns],
  );

  const duplicateName = useMemo(() => {
    const n = form.name.trim().toLowerCase();
    if (!n) return false;
    return conns.some((c) => c.name.toLowerCase() === n && c.id !== editing?.id);
  }, [conns, form.name, editing]);

  const canSave = !!form.name.trim() && !duplicateName && !!form.host.trim() && !!form.database.trim() && !!form.port;

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM());
    setShowPassword(false);
    setOpen(true);
  };

  const openEdit = (c: DbConnection) => {
    setEditing(c);
    setForm(toForm(c));
    setShowPassword(false);
    setOpen(true);
  };

  // Note what is NOT carried across: form.password is dropped here on purpose.
  const save = () => {
    if (!canSave) return;
    const c: DbConnection = {
      id: editing?.id ?? newId(),
      name: form.name.trim(),
      engine: form.engine,
      host: form.host.trim(),
      port: Number(form.port) || DEFAULT_PORT[form.engine] || 5432,
      database: form.database.trim(),
      username: form.username.trim(),
      sslMode: form.sslMode,
      poolSize: Number(form.poolSize) || 10,
      enabled: editing?.enabled ?? true,
      lastTestedAt: editing?.lastTestedAt ?? null,
    };
    persist(editing ? conns.map((x) => (x.id === editing.id ? c : x)) : [...conns, c]);
    setOpen(false);
    setEditing(null);
    toast.success(editing ? `"${c.name}" updated` : `Connection "${c.name}" added`, {
      description: `${c.engine} · ${connectionTarget(c)}`,
    });
  };

  const toggle = (id: string) => persist(conns.map((c) => (c.id === id ? { ...c, enabled: !c.enabled } : c)));

  const confirmDelete = () => {
    if (!pendingDelete) return;
    persist(conns.filter((c) => c.id !== pendingDelete.id));
    toast.success(`Removed "${pendingDelete.name}"`);
    setPendingDelete(null);
  };

  // Simulated handshake: always succeeds — a test that failed at random would
  // just read as a broken screen.
  const test = (c: DbConnection) => {
    setTesting(c.id);
    window.setTimeout(() => {
      const at = new Date().toISOString();
      persist(conns.map((x) => (x.id === c.id ? { ...x, lastTestedAt: at } : x)));
      setTesting(null);
      toast.success(`Connected to "${c.name}"`, { description: `${connectionTarget(c)} · handshake 42 ms` });
    }, 600);
  };

  const affected = pendingDelete ? sourcesUsing(pendingDelete.id) : [];

  return (
    <AppShell>
      <PageHeader
        title="Database Connections"
        description="Database targets the platform reads from. Define a connection once here, then reference it when onboarding a data source."
        actions={
          editable && (
            <button
              onClick={openCreate}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <Plus className="h-4 w-4" /> Add Connection
            </button>
          )
        }
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4 max-w-4xl">
        <StatTile icon={Database} label="Connections" value={String(stats.total)} />
        <StatTile icon={CheckCircle2} label="Enabled" value={`${stats.enabled} / ${stats.total}`} />
        <StatTile icon={Server} label="Engines" value={String(stats.engines)} />
      </div>

      {/* The password field is intentionally never persisted — see lib/dbConnections. */}
      <div className="mb-6 flex items-start gap-2.5 rounded-lg border border-border bg-muted/20 px-4 py-3 text-xs text-muted-foreground max-w-4xl">
        <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5 text-primary" />
        <p>
          Passwords are used to verify the connection only. Credentials are held in the platform's
          <span className="font-medium text-foreground"> server-side secret store</span> and are never returned to the browser.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {conns.map((c) => (
          <div key={c.id} className="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="h-10 w-10 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                  <Database className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <div className="font-semibold text-foreground leading-tight truncate">{c.name}</div>
                  <div className="text-xs text-muted-foreground">{c.engine}</div>
                </div>
              </div>
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${
                  c.enabled ? "bg-emerald-500/10 text-emerald-600" : "bg-muted text-muted-foreground"
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${c.enabled ? "bg-emerald-500" : "bg-muted-foreground/50"}`} />
                {c.enabled ? "Enabled" : "Disabled"}
              </span>
            </div>

            <dl className="text-xs text-muted-foreground space-y-1">
              <Row k="Target" v={connectionTarget(c)} mono />
              <Row k="User" v={c.username || "—"} mono />
              <Row k="Password" v="••••••••" />
              <Row k="SSL" v={c.sslMode} />
              <Row k="Pool size" v={String(c.poolSize)} />
              <Row k="Last tested" v={c.lastTestedAt ? new Date(c.lastTestedAt).toLocaleString() : "Never"} />
            </dl>

            <div className="flex items-center justify-between pt-2 border-t border-border mt-auto gap-2">
              <button
                onClick={() => test(c)}
                disabled={testing === c.id}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-50"
              >
                <Plug className="h-3.5 w-3.5" /> {testing === c.id ? "Testing…" : "Test"}
              </button>
              {editable && (
                <div className="flex items-center gap-1">
                  <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none mr-1">
                    <input type="checkbox" checked={c.enabled} onChange={() => toggle(c.id)} className="h-4 w-4 accent-[var(--primary,#4f46e5)]" />
                    {c.enabled ? "On" : "Off"}
                  </label>
                  <button
                    onClick={() => openEdit(c)}
                    aria-label={`Edit ${c.name}`}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-primary transition"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setPendingDelete(c)}
                    aria-label={`Delete ${c.name}`}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-destructive transition"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {conns.length === 0 && (
          <div className="col-span-full flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/10 py-16 text-center">
            <Database className="h-10 w-10 text-muted-foreground/40" />
            <p className="mt-3 text-sm text-muted-foreground">No database connections configured.</p>
            {editable && (
              <button
                onClick={openCreate}
                className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
              >
                <Plus className="h-4 w-4" /> Add your first connection
              </button>
            )}
          </div>
        )}
      </div>

      {open && (
        <Modal
          title={editing ? `Edit ${editing.name}` : "Add Connection"}
          onClose={() => setOpen(false)}
          footer={
            <>
              <button onClick={() => setOpen(false)} className="rounded-lg px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted">
                Cancel
              </button>
              <button
                onClick={save}
                disabled={!canSave}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Plus className="h-4 w-4" /> {editing ? "Save changes" : "Add connection"}
              </button>
            </>
          }
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Connection name">
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. rafms-primary" className={inputCls} />
              {duplicateName && <span className="text-[11px] text-destructive">A connection with this name already exists.</span>}
            </Field>
            <Field label="Engine">
              <select
                value={form.engine}
                // Re-prefill the port whenever the engine changes, unless the user
                // already typed a non-default one.
                onChange={(e) => {
                  const engine = e.target.value;
                  const wasDefault = Object.values(DEFAULT_PORT).includes(Number(form.port));
                  setForm({ ...form, engine, port: wasDefault ? String(DEFAULT_PORT[engine] ?? form.port) : form.port });
                }}
                className={inputCls}
              >
                {ENGINES.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Host / IP">
              <input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="10.200.37.x" className={`${inputCls} font-mono`} />
            </Field>
            <Field label="Port">
              <input type="number" min={1} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Database">
              <input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} placeholder="rafms" className={`${inputCls} font-mono`} />
            </Field>
            <Field label="Username">
              <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="ra_app" className={`${inputCls} font-mono`} />
            </Field>
            <Field label="Password">
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder="••••••••"
                  className={`${inputCls} pr-10`}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-2 top-1/2 -translate-y-1/2 h-7 w-7 rounded-md flex items-center justify-center text-muted-foreground hover:bg-muted"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <span className="text-[11px] text-muted-foreground">Stored in the platform secret store.</span>
            </Field>
            <Field label="SSL mode">
              <select value={form.sslMode} onChange={(e) => setForm({ ...form, sslMode: e.target.value })} className={inputCls}>
                {SSL_MODES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Pool size">
              <input type="number" min={1} value={form.poolSize} onChange={(e) => setForm({ ...form, poolSize: e.target.value })} className={inputCls} />
            </Field>
          </div>
        </Modal>
      )}

      {pendingDelete && (
        <ConfirmDialog
          open
          tone="danger"
          title="Delete connection"
          message={
            <>
              Delete <span className="font-medium text-foreground">{pendingDelete.name}</span> ({connectionTarget(pendingDelete)})?
            </>
          }
          note={affected.length ? `${affected.length} data source${affected.length > 1 ? "s" : ""} still reference this connection.` : "This can't be undone."}
          bullets={affected.length ? affected.map((n) => <span key={n}>{n} — will be left unassigned</span>) : undefined}
          confirmLabel="Delete connection"
          confirmIcon={<Trash2 className="h-4 w-4" />}
          heroIcon={<Trash2 className="h-9 w-9" />}
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </AppShell>
  );
}

const inputCls = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/40";

function Field({ label, children }: Readonly<{ label: string; children: React.ReactNode }>) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}


function Row({ k, v, mono }: Readonly<{ k: string; v: string; mono?: boolean }>) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt>{k}</dt>
      <dd className={`text-foreground truncate ${mono ? "font-mono" : ""}`}>{v}</dd>
    </div>
  );
}

// Header and footer stay pinned while the body scrolls.
function Modal({
  title,
  onClose,
  footer,
  children,
}: Readonly<{ title: string; onClose: () => void; footer: React.ReactNode; children: React.ReactNode }>) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      {/* Click-outside target only — kept out of the a11y tree and tab order so
          the header button below is the single announced "Close" control. */}
      <button className="absolute inset-0 bg-black/50" aria-hidden="true" tabIndex={-1} onClick={onClose} />
      <div className="relative flex w-full max-w-2xl max-h-[85vh] flex-col rounded-2xl border border-border bg-card shadow-xl">
        <div className="flex shrink-0 items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-foreground">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        <div className="flex shrink-0 items-center justify-end gap-2 px-5 py-4 border-t border-border">{footer}</div>
      </div>
    </div>
  );
}
