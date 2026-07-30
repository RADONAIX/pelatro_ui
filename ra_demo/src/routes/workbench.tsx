import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Search, Filter, Sparkles, Play, X, Plus } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";

export const Route = createFileRoute("/workbench")({ component: WorkbenchPage });

// DEMO-ONLY. Client-side only, persisted to localStorage; no backend calls
// (the /workbench API is disabled server-side). Search, run, and add are real
// local-state interactions so the module feels alive in a demo.
const STORAGE_KEY = "radonaix_workbench_v1";

interface Query {
  id: string;
  name: string;
  count: number;
  owner: string;
}

const SEED: Query[] = [
  { id: "Q-512", name: "CDRs with mismatched rating", count: 4218, owner: "Priya Shah" },
  { id: "Q-511", name: "Roaming records without partner agreement", count: 312, owner: "Aarav Mehta" },
  { id: "Q-510", name: "Duplicate billed events (48h)", count: 86, owner: "Mei Tanaka" },
  { id: "Q-509", name: "Zero-rated events over threshold", count: 1543, owner: "Mei Tanaka" },
];

function load(): Query[] {
  if (typeof window === "undefined") return SEED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Query[]) : null;
    if (Array.isArray(parsed) && parsed.length) return parsed;
  } catch { /* ignore */ }
  return SEED;
}

function WorkbenchPage() {
  const [queries, setQueries] = useState<Query[]>([]);
  const [q, setQ] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({ name: "", owner: "" });

  useEffect(() => { setQueries(load()); }, []);
  const persist = (next: Query[]) => {
    setQueries(next);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return queries.filter((x) => !needle || `${x.id} ${x.name} ${x.owner}`.toLowerCase().includes(needle));
  }, [queries, q]);

  const run = (x: Query) => toast.success(`Running "${x.name}"`, { description: `${x.count.toLocaleString()} records matched` });

  const addQuery = () => {
    if (!form.name.trim()) return;
    const n = queries.reduce((m, x) => Math.max(m, Number(x.id.replace(/\D/g, "")) || 0), 512) + 1;
    const nq: Query = { id: `Q-${n}`, name: form.name.trim(), owner: form.owner.trim() || "Unassigned", count: Math.floor(Math.random() * 5000) };
    persist([nq, ...queries]);
    setForm({ name: "", owner: "" });
    setAddOpen(false);
    toast.success(`${nq.id} saved`);
  };

  return (
    <AppShell>
      <PageHeader
        title="Assurance Workbench"
        description="Investigate anomalies, run ad-hoc queries and drill into source records."
        actions={
          <button onClick={() => { setForm({ name: "", owner: "" }); setAddOpen(true); }} className="inline-flex items-center gap-2 h-9 px-3 rounded-lg bg-primary text-primary-foreground text-sm hover:opacity-90">
            <Sparkles className="h-4 w-4" /> New Investigation
          </button>
        }
      />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-3">
          <div className="bg-card border border-border rounded-xl p-4 flex items-center gap-3">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input value={q} onChange={(e) => setQ(e.target.value)} className="flex-1 bg-transparent text-sm focus:outline-none" placeholder="Search saved queries…" />
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Filter className="h-3.5 w-3.5" /> {visible.length}</span>
          </div>
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>{["ID", "Saved Query", "Records", "Owner", ""].map((h) => (
                  <th key={h} className="text-left font-medium px-4 py-3">{h}</th>
                ))}</tr>
              </thead>
              <tbody>
                {visible.map((x) => (
                  <tr key={x.id} className="border-t border-border hover:bg-muted/30">
                    <td className="px-4 py-3 font-medium text-foreground">{x.id}</td>
                    <td className="px-4 py-3 text-foreground/80">{x.name}</td>
                    <td className="px-4 py-3 text-foreground/80 tabular-nums">{x.count.toLocaleString()}</td>
                    <td className="px-4 py-3 text-muted-foreground">{x.owner}</td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => run(x)} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted">
                        <Play className="h-3 w-3" /> Run
                      </button>
                    </td>
                  </tr>
                ))}
                {visible.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-10 text-center text-muted-foreground">No saved queries match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="font-semibold text-foreground mb-2">Quick stats</h3>
          <ul className="text-sm space-y-2 text-foreground/80">
            <li className="flex justify-between"><span>Saved queries</span><span className="font-medium tabular-nums">{queries.length}</span></li>
            <li className="flex justify-between"><span>Total flagged records</span><span className="font-medium tabular-nums">{queries.reduce((a, x) => a + x.count, 0).toLocaleString()}</span></li>
            <li className="flex justify-between"><span>Closed this week</span><span className="font-medium">23</span></li>
            <li className="flex justify-between"><span>Avg. resolution</span><span className="font-medium">2.4d</span></li>
          </ul>
        </div>
      </div>

      {addOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button className="absolute inset-0 bg-black/50" aria-label="Close" onClick={() => setAddOpen(false)} />
          <div className="relative w-full max-w-lg rounded-2xl border border-border bg-card shadow-xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="font-semibold text-foreground">New Investigation</h2>
              <button onClick={() => setAddOpen(false)} className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground"><X className="h-4 w-4" /></button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <label className="flex flex-col gap-1.5"><span className="text-xs font-medium text-muted-foreground">Query name</span>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Unbilled roaming events" className={inputCls} />
              </label>
              <label className="flex flex-col gap-1.5"><span className="text-xs font-medium text-muted-foreground">Owner</span>
                <input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} placeholder="Analyst" className={inputCls} />
              </label>
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setAddOpen(false)} className="rounded-lg px-4 py-2 text-sm text-muted-foreground hover:bg-muted">Cancel</button>
                <button onClick={addQuery} disabled={!form.name.trim()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40">
                  <Plus className="h-4 w-4" /> Save query
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

const inputCls = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/40";
