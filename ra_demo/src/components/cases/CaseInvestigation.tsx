import { useEffect, useState, type ReactNode } from "react";
import {
  Activity, Brain, ChevronDown, ClipboardList, FileText, Lightbulb, MessageSquarePlus, Paperclip, PencilLine, Save, X,
} from "lucide-react";
import { toast } from "sonner";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import { AssistantPanel } from "@/components/cases/AssistantPanel";
import {
  ACTIONS, SEVERITIES, STATUSES, CURRENT_ANALYST,
  findingLabel, fmtDate, fmtMoney, newId, relative,
  type AssuranceCase,
} from "@/lib/casesDemo";

// Collapsible section. Four hand-rolled copies of this pattern exist across the
// app and none is shared, so this one is local to the investigation modal.
function Section({
  title, icon, defaultOpen = false, badge, children,
}: Readonly<{ title: string; icon: ReactNode; defaultOpen?: boolean; badge?: string; children: ReactNode }>) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2.5 px-4 py-3 text-left hover:bg-muted/40 transition-colors"
      >
        <span className="shrink-0 text-muted-foreground">{icon}</span>
        <span className="flex-1 text-sm font-medium text-foreground">{title}</span>
        {badge && <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground">{badge}</span>}
        <ChevronDown className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div className="px-4 pb-4 pt-1 border-t border-border bg-muted/10">{children}</div>}
    </div>
  );
}

const Empty = ({ text }: { text: string }) => (
  <p className="py-6 text-center text-xs text-muted-foreground">{text}</p>
);

export function CaseInvestigation({
  activeCase, onClose, onUpdate,
}: Readonly<{ activeCase: AssuranceCase; onClose: () => void; onUpdate: (next: AssuranceCase) => void }>) {
  const c = activeCase;
  const [form, setForm] = useState({ status: c.status, severity: c.severity, action: c.action, owner: c.owner });
  const [comment, setComment] = useState("");

  useEffect(() => {
    setForm({ status: c.status, severity: c.severity, action: c.action, owner: c.owner });
    setComment("");
  }, [c.id, c.status, c.severity, c.action, c.owner]);

  // Close on Escape — the app's other modals don't do this, but a full-screen
  // investigation surface is the one place it really matters.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const dirty = form.status !== c.status || form.severity !== c.severity || form.action !== c.action || form.owner !== c.owner;

  const saveUpdate = () => {
    onUpdate({ ...c, ...form, updatedAt: new Date().toISOString() });
    toast.success(`${c.reference} updated`, { description: `${form.status} · ${form.severity} · ${form.action}` });
  };

  const addComment = () => {
    const body = comment.trim();
    if (!body) return;
    onUpdate({
      ...c,
      comments: [...c.comments, { id: newId(), author: CURRENT_ANALYST, body, createdAt: new Date().toISOString() }],
      updatedAt: new Date().toISOString(),
    });
    setComment("");
    toast.success("Note added");
  };

  const pinInsight = (body: string) =>
    onUpdate({ ...c, savedInsights: [...c.savedInsights, { id: newId(), body, at: new Date().toISOString() }] });

  const mismatches = c.trace.filter((t) => t.status === "AMOUNT_MISMATCH").length;
  const rawOnly = c.trace.filter((t) => t.status === "RAW_ONLY").length;
  // A believable model-confidence band (high-70s to high-80s) — scaled by
  // severity and how much of the population was flagged, never a suspicious 97%.
  const severityWeight = c.severity === "critical" ? 12 : c.severity === "high" ? 7 : 3;
  const confidence = Math.min(91, 71 + severityWeight + Math.min(8, Math.round(c.affectedCount / 1500)));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={`Investigation ${c.reference}`}>
      <button className="absolute inset-0 bg-black/50" aria-hidden="true" tabIndex={-1} onClick={onClose} />
      <div className="relative flex w-full max-w-6xl max-h-[92vh] flex-col rounded-2xl border border-border bg-background shadow-xl">
        <div className="shrink-0 flex items-center justify-between px-5 py-4 border-b border-border">
          <div className="min-w-0">
            <h2 className="font-semibold text-foreground">Case Investigation</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {c.reference} · {findingLabel(c.findingType)}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <div className="grid grid-cols-1 lg:grid-cols-[1.6fr_1fr] gap-4 items-start">
            {/* Left: summary + sections */}
            <div className="space-y-3">
              <div className="rounded-xl border border-border bg-card">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
                  <ClipboardList className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">Case Summary</span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <StatusBadge value={c.severity} />
                    <StatusBadge value={c.status} />
                  </span>
                </div>
                <div className="px-4 py-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2.5">
                  <Kv k="Case ID" v={c.reference} mono />
                  <Kv k="Assigned to" v={c.owner} />
                  <Kv k="Created" v={fmtDate(c.createdAt)} />
                  <Kv k="Origin" v={c.origin} mono />
                  <Kv k="Stream / Node" v={`${c.stream} · ${c.nodeId}`} mono />
                  <Kv k="Linked batch" v={c.linkedBatch} mono />
                  <Kv k="Linked txn" v={c.linkedTxnId} mono />
                  <Kv k="Est. revenue at risk" v={fmtMoney(c.estimatedImpact)} />
                  <div className="sm:col-span-2">
                    <div className="text-[11px] text-muted-foreground mb-1">Description</div>
                    <p className="text-sm text-foreground/90 leading-relaxed">{c.description}</p>
                  </div>
                </div>
              </div>

              <Section title="AI Impact Insight" icon={<Brain className="h-4 w-4" />} defaultOpen badge="generated">
                <div className="pt-3 space-y-3">
                  <div className="flex items-center gap-3">
                    <div className="flex-1">
                      <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                        <span>Confidence</span>
                        <span className="tabular-nums text-foreground font-medium">{confidence}%</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                        <div className="h-full bg-primary rounded-full" style={{ width: `${confidence}%` }} />
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[11px] text-muted-foreground">Revenue at risk</div>
                      <div className="text-sm font-semibold text-foreground tabular-nums">{fmtMoney(c.estimatedImpact)}</div>
                    </div>
                  </div>
                  <ul className="text-xs text-foreground/85 space-y-1.5">
                    <Signal>Raised by the <span className="font-medium">{findingLabel(c.findingType)}</span> check on batch {c.linkedBatch}.</Signal>
                    <Signal>{c.affectedCount.toLocaleString()} records flagged{c.trace.length > 0 ? ` — reviewed sample: ${mismatches} amount-mismatch, ${rawOnly} raw-only` : ""}.</Signal>
                    <Signal>Concentrated on {c.nodeId}, suggesting a node-local cause rather than a platform-wide issue.</Signal>
                    <Signal>Two comparable findings on this node in the last 30 days.</Signal>
                  </ul>
                </div>
              </Section>

              <Section title="Attached Evidence" icon={<Paperclip className="h-4 w-4" />} badge={c.evidence ? "1" : undefined}>
                {c.evidence ? (
                  <div className="pt-3 flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2.5">
                    <span className="h-9 w-9 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                      <FileText className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-foreground font-medium truncate">{c.evidence.name}</div>
                      <div className="text-[11px] text-muted-foreground">{c.evidence.kind} · {c.evidence.size}</div>
                    </div>
                    <button
                      onClick={() => toast.info("Evidence download isn't available yet")}
                      className="text-xs rounded-lg border border-border px-2.5 py-1 hover:bg-muted"
                    >
                      Open
                    </button>
                  </div>
                ) : (
                  <Empty text="No evidence attached to this case." />
                )}
              </Section>

              <Section title="Record Trace" icon={<Activity className="h-4 w-4" />} badge={c.trace.length ? c.affectedCount.toLocaleString() : undefined}>
                {c.trace.length ? (
                  <div className="pt-3 space-y-2">
                    <p className="text-[11px] text-muted-foreground">
                      Showing a sample of {c.trace.length} of {c.affectedCount.toLocaleString()} flagged records. Amounts in USD.
                    </p>
                    <div className="-mx-1 overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
                          <tr>
                            {["Txn ID", "Node", "Subscriber", "Raw $", "Processed $", "Status"].map((h) => (
                              <th key={h} className="text-left font-medium px-2.5 py-2 whitespace-nowrap">{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {c.trace.map((t) => (
                            <tr key={t.txnId} className="border-t border-border">
                              <td className="px-2.5 py-2 font-mono text-foreground/80">{t.txnId}</td>
                              <td className="px-2.5 py-2 font-mono text-muted-foreground">{t.nodeId}</td>
                              <td className="px-2.5 py-2 font-mono text-muted-foreground">{t.subscriberNum}</td>
                              <td className="px-2.5 py-2 tabular-nums text-foreground/80">{t.rawAmount == null ? "—" : `$${t.rawAmount.toFixed(2)}`}</td>
                              <td className="px-2.5 py-2 tabular-nums text-foreground/80">{t.procAmount == null ? "—" : `$${t.procAmount.toFixed(2)}`}</td>
                              <td className="px-2.5 py-2"><StatusBadge value={t.status.replace(/_/g, " ").toLowerCase()} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : (
                  <Empty text={`Raised at file level — ${c.affectedCount.toLocaleString()} records affected, no record-level trace for this finding.`} />
                )}
              </Section>

              <Section title="Saved Insights" icon={<Lightbulb className="h-4 w-4" />} badge={c.savedInsights.length ? String(c.savedInsights.length) : undefined}>
                {c.savedInsights.length ? (
                  <ul className="pt-3 space-y-2">
                    {c.savedInsights.map((s) => (
                      <li key={s.id} className="rounded-lg border border-border bg-background px-3 py-2">
                        <p className="text-xs text-foreground/85 leading-relaxed">{s.body}</p>
                        <span className="text-[10px] text-muted-foreground">Pinned {relative(s.at)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty text="Pin an assistant reply to keep it with the case." />
                )}
              </Section>

              <Section title="Investigation Panel" icon={<MessageSquarePlus className="h-4 w-4" />} badge={c.comments.length ? String(c.comments.length) : undefined}>
                <div className="pt-3 space-y-3">
                  <ol className="space-y-2.5">
                    <li className="flex gap-2.5">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                      <div className="text-xs">
                        <span className="text-foreground/85">Case opened by <span className="font-medium">{c.origin === "auto_detected" ? "the scheduled report run" : c.owner}</span></span>
                        <div className="text-[10px] text-muted-foreground">{fmtDate(c.createdAt)}</div>
                      </div>
                    </li>
                    {c.comments.map((cm) => (
                      <li key={cm.id} className="flex gap-2.5">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
                        <div className="text-xs">
                          <span className="text-foreground/85"><span className="font-medium">{cm.author}</span> — {cm.body}</span>
                          <div className="text-[10px] text-muted-foreground">{fmtDate(cm.createdAt)}</div>
                        </div>
                      </li>
                    ))}
                  </ol>
                  <div className="flex items-center gap-2 pt-1">
                    <input
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addComment()}
                      placeholder="Add an investigation note…"
                      aria-label="Add an investigation note"
                      className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs outline-none focus:ring-2 focus:ring-primary/40"
                    />
                    <button
                      onClick={addComment}
                      disabled={!comment.trim()}
                      className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
                    >
                      Add
                    </button>
                  </div>
                </div>
              </Section>

              <Section title="Update Case" icon={<PencilLine className="h-4 w-4" />} defaultOpen>
                <div className="pt-3 space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <Fld label="Status">
                      <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className={sel} aria-label="Status">
                        {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </Fld>
                    <Fld label="Priority">
                      <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })} className={sel} aria-label="Priority">
                        {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </Fld>
                    <Fld label="Action">
                      <select value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })} className={sel} aria-label="Action">
                        {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                      </select>
                    </Fld>
                    <Fld label="Assigned to">
                      <input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} className={sel} aria-label="Assigned to" />
                    </Fld>
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={saveUpdate}
                      disabled={!dirty}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Save className="h-4 w-4" /> Save changes
                    </button>
                  </div>
                </div>
              </Section>
            </div>

            {/* Right: assistant */}
            <div className="lg:sticky lg:top-0 h-[520px]">
              <AssistantPanel activeCase={c} onPin={pinInsight} />
            </div>
          </div>
        </div>

        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-t border-border">
          <span className="text-[11px] text-muted-foreground">Last updated {relative(c.updatedAt)}</span>
          <button onClick={onClose} className="rounded-lg px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

const sel = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/40";

function Fld({ label, children }: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function Kv({ k, v, mono }: Readonly<{ k: string; v: string; mono?: boolean }>) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-muted-foreground">{k}</div>
      <div className={`text-sm text-foreground truncate ${mono ? "font-mono text-[13px]" : ""}`}>{v}</div>
    </div>
  );
}

function Signal({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <li className="flex gap-2">
      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary" />
      <span>{children}</span>
    </li>
  );
}
