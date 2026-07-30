import { createFileRoute } from "@tanstack/react-router";
import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui-kit/ConfirmDialog";

export const Route = createFileRoute("/recon-workflows")({ component: ReconWorkflowsPage });

// DEMO-ONLY. Client-side only, persisted to localStorage; no backend calls. The
// backend has no recon-config surface at all — /api/recon only *reads* results
// that were computed elsewhere, it never defines the rules. Create/edit/delete
// are real local-state interactions so the module feels alive in a demo.
const STORAGE_KEY = "radonaix_recon_workflows_v1";

// Total <th> count — the expanded row's colSpan must match exactly or the whole
// table misaligns. Keep in sync with HEADERS below.
const COLS = 9;
const HEADERS = ["", "#", "Name", "Description", "Type", "Table 1", "Table 2", "Edit", "Delete"];

const WORKFLOW_TYPES = ["Single", "Multiple"] as const;

// The staging tables a workflow can reconcile over. Attributes are picked from
// the selected table's columns rather than typed free-hand, so a configuration
// can't reference a column that doesn't exist.
interface TableDef {
  id: string;
  label: string;
  columns: string[];
}

const TABLES: TableDef[] = [
  { id: "stg_air_cdr", label: "AIR CDR", columns: ["subscriber_id", "service", "transaction_datetime", "amount_used", "node_id", "txn_id"] },
  { id: "stg_sdp_cdr", label: "SDP CDR", columns: ["subscriber_id", "service", "transaction_datetime", "amount_used", "node_id", "txn_id"] },
  { id: "stg_msc_cdr", label: "MSC CDR", columns: ["subscriber_id", "service", "transaction_datetime", "call_duration", "amount_used"] },
  { id: "stg_air_cdr_records", label: "AIR CDR RECORDS", columns: ["record_id", "file_name", "load_datetime", "record_count"] },
  { id: "stg_sdp_cdr_files", label: "SDP CDR FILES", columns: ["file_id", "file_name", "file_seq", "load_datetime"] },
];

const tableLabel = (id: string) => TABLES.find((t) => t.id === id)?.label ?? id;
const tableColumns = (id: string) => TABLES.find((t) => t.id === id)?.columns ?? [];
// Attributes are stored as bare column names and qualified only for display.
const qualify = (tableId: string, column: string) => (column ? `${tableId}.${column}` : "—");

interface AttrPair {
  id: string;
  left: string;
  right: string;
}

interface SingleAttr {
  id: string;
  attribute: string;
}

interface WorkflowBase {
  id: string;
  name: string;
  description: string;
  table1: string;
}

// A Single workflow compares a table against itself (one attribute list, no
// keys); a Multiple workflow compares two tables (paired metrics + join keys).
// The union makes it impossible to read `keys` off a Single workflow.
type ReconWorkflow =
  | (WorkflowBase & { type: "Single"; metrics: SingleAttr[] })
  | (WorkflowBase & { type: "Multiple"; table2: string; metrics: AttrPair[]; keys: AttrPair[] });

const SEED: ReconWorkflow[] = [
  {
    id: "seed-missing-record",
    name: "Missing_record_check",
    description: "Flags AIR CDR records absent from the processed stream.",
    type: "Single",
    table1: "stg_air_cdr_records",
    metrics: [{ id: "m1", attribute: "record_id" }],
  },
  {
    id: "seed-missing-file",
    name: "Missing_file_check",
    description: "Detects gaps in the SDP CDR file sequence.",
    type: "Single",
    table1: "stg_sdp_cdr_files",
    metrics: [{ id: "m1", attribute: "file_name" }],
  },
  {
    id: "seed-air-vs-sdp",
    name: "AIR_vs_SDP_CDR",
    description: "Reconciles charged amounts between the AIR and SDP streams.",
    type: "Multiple",
    table1: "stg_air_cdr",
    table2: "stg_sdp_cdr",
    metrics: [{ id: "m1", left: "amount_used", right: "amount_used" }],
    keys: [
      { id: "k1", left: "subscriber_id", right: "subscriber_id" },
      { id: "k2", left: "service", right: "service" },
      { id: "k3", left: "transaction_datetime", right: "transaction_datetime" },
    ],
  },
];

function load(): ReconWorkflow[] {
  if (typeof window === "undefined") return SEED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as ReconWorkflow[]) : null;
    // Non-empty stored list wins; empty/blank falls back to the seed so the demo
    // never opens on a blank screen after everything was deleted.
    if (Array.isArray(parsed) && parsed.length) return parsed;
  } catch {
    /* ignore malformed storage */
  }
  return SEED;
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `rw-${Math.floor(Math.random() * 1e9)}`;
}

// Form state is a flat superset of both union arms: it always carries table2 and
// keys, so toggling Multiple → Single → Multiple mid-edit doesn't discard work.
// The narrowing to ReconWorkflow happens once, on save.
interface WorkflowForm {
  name: string;
  description: string;
  type: string; // widened — bound to a native <select>, which yields string
  table1: string;
  table2: string;
  metrics: AttrPair[];
  keys: AttrPair[];
}

const emptyPair = (): AttrPair => ({ id: newId(), left: "", right: "" });

const EMPTY_FORM = (): WorkflowForm => ({
  name: "",
  description: "",
  type: "Single",
  table1: "",
  table2: "",
  metrics: [emptyPair()],
  keys: [emptyPair()],
});

// Expand a stored workflow back into the superset form shape.
function toForm(w: ReconWorkflow): WorkflowForm {
  const base = { name: w.name, description: w.description, type: w.type, table1: w.table1 };
  if (w.type === "Single") {
    return {
      ...base,
      table2: "",
      metrics: w.metrics.map((m) => ({ id: m.id, left: m.attribute, right: "" })),
      keys: [emptyPair()],
    };
  }
  return {
    ...base,
    table2: w.table2,
    metrics: w.metrics.map((m) => ({ ...m })),
    keys: w.keys.length ? w.keys.map((k) => ({ ...k })) : [emptyPair()],
  };
}

// Narrow the superset form down to the union arm the type demands, dropping the
// fields the chosen arm doesn't carry.
function toWorkflow(f: WorkflowForm, id: string): ReconWorkflow {
  const base = { id, name: f.name.trim(), description: f.description.trim(), table1: f.table1 };
  if (f.type === "Single") {
    return {
      ...base,
      type: "Single",
      metrics: f.metrics.filter((m) => m.left).map((m) => ({ id: m.id, attribute: m.left })),
    };
  }
  return {
    ...base,
    type: "Multiple",
    table2: f.table2,
    metrics: f.metrics.filter((m) => m.left && m.right),
    keys: f.keys.filter((k) => k.left && k.right),
  };
}

function ReconWorkflowsPage() {
  const [workflows, setWorkflows] = useState<ReconWorkflow[]>([]);
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ReconWorkflow | null>(null);
  const [form, setForm] = useState<WorkflowForm>(EMPTY_FORM);
  const [pendingDelete, setPendingDelete] = useState<ReconWorkflow | null>(null);

  useEffect(() => {
    setWorkflows(load());
  }, []);

  // Persist ONLY on explicit user actions — never from a reactive effect, which
  // would race the initial mount load and clobber saved data with the empty
  // initial state (notably under StrictMode's double-invoked effects in dev).
  const persist = (next: ReconWorkflow[]) => {
    setWorkflows(next);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return workflows;
    return workflows.filter((w) => `${w.name} ${w.description}`.toLowerCase().includes(needle));
  }, [workflows, q]);

  const isMultiple = form.type === "Multiple";

  const duplicateName = useMemo(() => {
    const name = form.name.trim().toLowerCase();
    if (!name) return false;
    return workflows.some((w) => w.name.toLowerCase() === name && w.id !== editing?.id);
  }, [workflows, form.name, editing]);

  const canSave =
    !!form.name.trim() &&
    !duplicateName &&
    !!form.table1 &&
    (!isMultiple || !!form.table2) &&
    form.metrics.some((m) => m.left && (!isMultiple || m.right)) &&
    (!isMultiple || form.keys.some((k) => k.left && k.right));

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM());
    setOpen(true);
  };

  const openEdit = (w: ReconWorkflow) => {
    setEditing(w);
    setForm(toForm(w));
    setOpen(true);
  };

  const save = () => {
    if (!canSave) return;
    const w = toWorkflow(form, editing?.id ?? newId());
    persist(editing ? workflows.map((x) => (x.id === editing.id ? w : x)) : [...workflows, w]);
    setOpen(false);
    setEditing(null);
    toast.success(editing ? `"${w.name}" updated` : `Workflow "${w.name}" created`, {
      description: `${w.type} · ${tableLabel(w.table1)}${w.type === "Multiple" ? ` vs ${tableLabel(w.table2)}` : ""}`,
    });
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    persist(workflows.filter((w) => w.id !== pendingDelete.id));
    if (expanded === pendingDelete.id) setExpanded(null);
    toast.success(`Removed "${pendingDelete.name}"`);
    setPendingDelete(null);
  };

  // Changing a table invalidates every attribute picked from the old one — a
  // native <select> whose value has no matching <option> silently renders blank,
  // so clear them explicitly rather than leaving a stale mismatch.
  const setTable1 = (table1: string) =>
    setForm((f) => ({
      ...f,
      table1,
      metrics: f.metrics.map((m) => ({ ...m, left: "" })),
      keys: f.keys.map((k) => ({ ...k, left: "" })),
    }));

  const setTable2 = (table2: string) =>
    setForm((f) => ({
      ...f,
      table2,
      metrics: f.metrics.map((m) => ({ ...m, right: "" })),
      keys: f.keys.map((k) => ({ ...k, right: "" })),
    }));

  return (
    <AppShell>
      <PageHeader
        title="Recon Workflow Configuration"
        description="Define the reconciliation rules that compare staged records across streams — the metrics to measure and the keys that join them."
        actions={
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
          >
            <Plus className="h-4 w-4" /> Create Workflow
          </button>
        }
      />

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex items-center gap-2 flex-1 min-w-[220px] bg-card border border-border rounded-lg px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search workflow name…"
            className="flex-1 bg-transparent text-sm focus:outline-none"
          />
        </div>
        <span className="text-xs text-muted-foreground">
          {visible.length} of {workflows.length} workflows
        </span>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              {HEADERS.map((h, i) => (
                <th
                  key={h || `col-${i}`}
                  className={`font-medium px-4 py-3 ${i >= HEADERS.length - 2 ? "text-center" : "text-left"}`}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((w, idx) => (
              <WorkflowRow
                key={w.id}
                workflow={w}
                index={idx + 1}
                open={expanded === w.id}
                onToggle={() => setExpanded(expanded === w.id ? null : w.id)}
                onEdit={() => openEdit(w)}
                onDelete={() => setPendingDelete(w)}
              />
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={COLS} className="px-4 py-14 text-center text-sm text-muted-foreground">
                  {workflows.length === 0 ? "No recon workflows configured yet." : "No workflows match."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {open && (
        <Modal
          title={editing ? `Edit ${editing.name}` : "Create Workflow"}
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
                <Plus className="h-4 w-4" /> {editing ? "Save changes" : "Create workflow"}
              </button>
            </>
          }
        >
          <div className="space-y-5">
            {/* Basics */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Name">
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. AIR_vs_SDP_CDR"
                  className={inputCls}
                />
                {duplicateName && <span className="text-[11px] text-destructive">A workflow with this name already exists.</span>}
              </Field>
              <Field label="Type">
                <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className={inputCls}>
                  {WORKFLOW_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                {!isMultiple && (
                  <span className="text-[11px] text-muted-foreground">Table 2 and comparison keys aren't saved for Single workflows.</span>
                )}
              </Field>
              <Field label="Table 1">
                <select value={form.table1} onChange={(e) => setTable1(e.target.value)} className={inputCls}>
                  <option value="">Select a table…</option>
                  {TABLES.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </Field>
              {isMultiple && (
                <Field label="Table 2">
                  <select value={form.table2} onChange={(e) => setTable2(e.target.value)} className={inputCls}>
                    <option value="">Select a table…</option>
                    {TABLES.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </Field>
              )}
              <div className="sm:col-span-2">
                <Field label="Description">
                  <input
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                    placeholder="What does this workflow check?"
                    className={inputCls}
                  />
                </Field>
              </div>
            </div>

            {/* Comparison metric */}
            <div className="border-t border-border pt-4">
              <PairRows
                title="Comparison Metric"
                hint={isMultiple ? "The measured values compared between the two tables." : "The attributes checked within the table."}
                rows={form.metrics}
                dual={isMultiple}
                leftCols={tableColumns(form.table1)}
                rightCols={tableColumns(form.table2)}
                leftLabel={isMultiple ? "Attribute 1" : "Attributes"}
                rightLabel="Attribute 2"
                leftDisabled={!form.table1}
                rightDisabled={!form.table2}
                onChange={(metrics) => setForm({ ...form, metrics })}
              />
            </div>

            {/* Comparison keys — Multiple only */}
            {isMultiple && (
              <div className="border-t border-border pt-4">
                <PairRows
                  title="Comparison Keys"
                  hint="The attributes used to join records across the two tables."
                  rows={form.keys}
                  dual
                  leftCols={tableColumns(form.table1)}
                  rightCols={tableColumns(form.table2)}
                  leftLabel="Attribute 1"
                  rightLabel="Attribute 2"
                  leftDisabled={!form.table1}
                  rightDisabled={!form.table2}
                  onChange={(keys) => setForm({ ...form, keys })}
                />
              </div>
            )}
          </div>
        </Modal>
      )}

      {pendingDelete && (
        <ConfirmDialog
          open
          tone="danger"
          title="Delete workflow"
          message={
            <>
              Delete <span className="font-medium text-foreground">{pendingDelete.name}</span>? Its comparison metrics and keys will be removed.
            </>
          }
          note="This can't be undone."
          confirmLabel="Delete workflow"
          confirmIcon={<Trash2 className="h-4 w-4" />}
          heroIcon={<Trash2 className="h-9 w-9" />}
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </AppShell>
  );
}

function WorkflowRow({
  workflow: w,
  index,
  open,
  onToggle,
  onEdit,
  onDelete,
}: Readonly<{
  workflow: ReconWorkflow;
  index: number;
  open: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}>) {
  return (
    <Fragment>
      <tr className="border-t border-border hover:bg-muted/30">
        <td className="px-4 py-3 w-10">
          <button
            onClick={onToggle}
            aria-expanded={open}
            aria-label={open ? `Collapse ${w.name}` : `Expand ${w.name}`}
            className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground transition"
          >
            <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        </td>
        <td className="px-4 py-3 text-muted-foreground tabular-nums w-10">{index}</td>
        <td className="px-4 py-3 font-medium text-foreground">{w.name}</td>
        <td className="px-4 py-3 text-muted-foreground max-w-xs truncate">{w.description || "—"}</td>
        <td className="px-4 py-3">
          <span
            className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium ${
              w.type === "Multiple" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
            }`}
          >
            {w.type}
          </span>
        </td>
        <td className="px-4 py-3 text-foreground/80">{tableLabel(w.table1)}</td>
        <td className="px-4 py-3 text-foreground/80">{w.type === "Multiple" ? tableLabel(w.table2) : "—"}</td>
        <td className="px-4 py-3 text-center">
          <button
            onClick={onEdit}
            aria-label={`Edit ${w.name}`}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-primary transition"
          >
            <Pencil className="h-4 w-4" />
          </button>
        </td>
        <td className="px-4 py-3 text-center">
          <button
            onClick={onDelete}
            aria-label={`Delete ${w.name}`}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-destructive transition"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </td>
      </tr>

      {open && (
        <tr className="border-t border-border bg-muted/20">
          <td colSpan={COLS} className="px-4 py-4">
            {w.type === "Multiple" ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <SubTable
                  title="Comparison Metric"
                  columns={["Attribute 1", "Attribute 2"]}
                  rows={w.metrics.map((m) => [qualify(w.table1, m.left), qualify(w.table2, m.right)])}
                />
                <SubTable
                  title="Comparison Keys"
                  columns={["Attribute 1", "Attribute 2"]}
                  rows={w.keys.map((k) => [qualify(w.table1, k.left), qualify(w.table2, k.right)])}
                />
              </div>
            ) : (
              <div className="max-w-md">
                <SubTable
                  title="Comparison Metric"
                  columns={["Attributes"]}
                  rows={w.metrics.map((m) => [qualify(w.table1, m.attribute)])}
                />
              </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function SubTable({ title, columns, rows }: Readonly<{ title: string; columns: string[]; rows: string[][] }>) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-foreground mb-2">{title}</h4>
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr>
              {columns.map((c) => (
                <th key={c} className="text-left font-medium px-3 py-2">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((cells) => (
              <tr key={cells.join("|")} className="border-t border-border">
                {cells.map((cell, i) => (
                  <td key={`${cell}-${i}`} className="px-3 py-2 font-mono text-foreground/80">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-3 py-6 text-center text-muted-foreground">
                  None configured.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// A variable-length attribute repeater. `dual` renders the paired
// Attribute 1 / Attribute 2 form used by Multiple workflows; when false it
// collapses to the single "Attributes" column a Single workflow needs.
function PairRows({
  title,
  hint,
  rows,
  dual,
  leftCols,
  rightCols,
  leftLabel,
  rightLabel,
  leftDisabled,
  rightDisabled,
  onChange,
}: Readonly<{
  title: string;
  hint: string;
  rows: AttrPair[];
  dual: boolean;
  leftCols: string[];
  rightCols: string[];
  leftLabel: string;
  rightLabel: string;
  leftDisabled: boolean;
  rightDisabled: boolean;
  onChange: (rows: AttrPair[]) => void;
}>) {
  // Rows carry a stable id used as the React key — keying by array index makes
  // deleting a middle row visibly shift the remaining values up by one.
  const set = (id: string, patch: Partial<AttrPair>) => onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const add = () => onChange([...rows, emptyPair()]);
  // Never drop to zero rows — clearing the last one keeps the section legible.
  const remove = (id: string) =>
    onChange(rows.length === 1 ? [emptyPair()] : rows.filter((r) => r.id !== id));

  const gridCls = dual ? "grid-cols-[1fr_1fr_auto]" : "grid-cols-[1fr_auto]";

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <h4 className="text-sm font-semibold text-foreground">{title}</h4>
        <span className="text-[11px] text-muted-foreground">{hint}</span>
      </div>

      <div className={`grid ${gridCls} gap-2 mb-1.5`}>
        <span className="text-xs font-medium text-muted-foreground">{leftLabel}</span>
        {dual && <span className="text-xs font-medium text-muted-foreground">{rightLabel}</span>}
        <span className="w-8" />
      </div>

      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.id} className={`grid ${gridCls} gap-2 items-center`}>
            <select value={r.left} onChange={(e) => set(r.id, { left: e.target.value })} disabled={leftDisabled} className={inputCls}>
              <option value="">{leftDisabled ? "Select a table first" : "Select attribute…"}</option>
              {leftCols.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            {dual && (
              <select value={r.right} onChange={(e) => set(r.id, { right: e.target.value })} disabled={rightDisabled} className={inputCls}>
                <option value="">{rightDisabled ? "Select a table first" : "Select attribute…"}</option>
                {rightCols.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
            <button
              onClick={() => remove(r.id)}
              aria-label="Remove row"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-destructive transition"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>

      <button
        onClick={add}
        className="mt-2 w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition"
      >
        <Plus className="h-3.5 w-3.5" /> Add {dual ? "pair" : "attribute"}
      </button>
    </div>
  );
}

const inputCls = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-50 disabled:cursor-not-allowed";

function Field({ label, children }: Readonly<{ label: string; children: React.ReactNode }>) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

// Header and footer stay pinned while the body scrolls — with several metric
// rows added the actions would otherwise scroll out of reach.
function Modal({
  title,
  onClose,
  footer,
  children,
}: Readonly<{ title: string; onClose: () => void; footer: React.ReactNode; children: React.ReactNode }>) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      {/* Click-outside target only — kept out of the a11y tree and the tab order
          so the header button below is the single announced "Close" control. */}
      <button className="absolute inset-0 bg-black/50" aria-hidden="true" tabIndex={-1} onClick={onClose} />
      <div className="relative flex w-full max-w-3xl max-h-[85vh] flex-col rounded-2xl border border-border bg-card shadow-xl">
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
