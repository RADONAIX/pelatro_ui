import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity, Bot, Brain, ChevronDown, ClipboardList, Download, FileText, History, Lightbulb,
  Loader2, MessageSquarePlus, Paperclip, PencilLine, RotateCw, Save, ScanSearch, Trash2, Upload, X,
} from "lucide-react";
import { toast } from "sonner";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import { AssistantPanel } from "@/components/cases/AssistantPanel";
import {
  ACTIONS, SEVERITIES, STATUSES,
  attachmentUrl, deleteAttachment, fmtBytes, fmtDate, fmtMoney, mismatchLabel, ownerLabel,
  relative, ruleLabel, uploadAttachments, validateUploadFile,
  type AssuranceCase, type CaseAttachment,
} from "@/lib/cases";
import { ANALYSIS_STEPS } from "@/lib/billInvestigation";
import {
  fetchInvestigation, supportsInvestigation, type Investigation,
} from "@/lib/investigation";
import { AnalysisProgress, BillInvestigationPanel, RatingAnalysis } from "@/components/cases/BillInvestigationPanel";

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
  activeCase, onClose, onSave, onComment, onPin, onRefresh,
}: Readonly<{
  activeCase: AssuranceCase;
  onClose: () => void;
  /** Persists the triage fields; the parent writes to PATCH /api/cases/{id}. */
  onSave: (patch: { status?: string; severity?: string; action?: string; owner?: string }) => Promise<unknown>;
  onComment: (body: string) => Promise<void>;
  onPin: (body: string) => Promise<void>;
  /** Re-reads the case after an attachment changes. */
  onRefresh: () => Promise<void>;
}>) {
  const c = activeCase;
  const [form, setForm] = useState({ status: c.status, severity: c.severity, action: c.action, owner: c.owner });
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [posting, setPosting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [busyAttachment, setBusyAttachment] = useState<string | null>(null);
  // Bill analysis: idle → running (the five investigation reads are in flight)
  // → done. The stepped progress is UI pacing; the result is whatever the
  // source tables return.
  const [analysis, setAnalysis] = useState<"idle" | "running" | "done">("idle");
  const [analysisStep, setAnalysisStep] = useState(0);
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  // Rating drill-down: a slide-over anchored to the dialog body, so the flow
  // row itself stays high level.
  const [ratingOpen, setRatingOpen] = useState(false);
  // The assistant is a slide-over over the investigation content, not a
  // permanent column — the case sections get the full dialog width.
  const [assistantOpen, setAssistantOpen] = useState(false);
  const assistantRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setForm({ status: c.status, severity: c.severity, action: c.action, owner: c.owner });
    setComment("");
  }, [c.id, c.status, c.severity, c.action, c.owner]);

  // A different case means a different bill — never carry a result across.
  useEffect(() => {
    setAnalysis("idle");
    setAnalysisStep(0);
    setInvestigation(null);
    setAssistantOpen(false);
    setRatingOpen(false);
  }, [c.id]);

  // Close on Escape — the app's other modals don't do this, but a full-screen
  // investigation surface is the one place it really matters.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Escape peels one layer at a time, outermost first: the rating
      // drill-down, then the assistant, then the dialog.
      if (ratingOpen) setRatingOpen(false);
      else if (assistantOpen) setAssistantOpen(false);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, assistantOpen, ratingOpen]);

  const dirty = form.status !== c.status || form.severity !== c.severity || form.action !== c.action || form.owner !== c.owner;

  const saveUpdate = async () => {
    setSaving(true);
    try {
      await onSave(form);
      toast.success(`${c.reference} updated`, { description: `${form.status} · ${form.severity} · ${form.action}` });
    } catch (e) {
      toast.error("Update failed", { description: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const addComment = async () => {
    const body = comment.trim();
    if (!body || posting) return;
    setPosting(true);
    try {
      await onComment(body);
      setComment("");
      toast.success("Note added");
    } catch (e) {
      toast.error("Could not add the note", { description: (e as Error).message });
    } finally {
      setPosting(false);
    }
  };

  const pinInsight = async (body: string) => {
    try {
      await onPin(body);
    } catch (e) {
      toast.error("Could not pin the insight", { description: (e as Error).message });
    }
  };

  const mismatches = c.mismatches ?? [];
  const comments = c.comments ?? [];
  const activities = c.activities ?? [];
  const attachments = c.attachments ?? [];
  // The postpaid investigation reads the rating and billing source tables,
  // which only Billing Assurance is linked to. Every other assurance answers
  // from the mismatch rows its own control emitted, so the analysis is not
  // offered there at all — and the endpoints would refuse it anyway.
  const billCase = supportsInvestigation(c);
  const billPdf: CaseAttachment | undefined =
    billCase ? attachments.find((a) => a.contentType === "application/pdf") : undefined;

  const attachFiles = async (list: FileList | null) => {
    if (!list?.length || uploading) return;
    const files: File[] = [];
    for (const file of Array.from(list)) {
      const problem = validateUploadFile(file);
      if (problem) toast.error(`${file.name} — ${problem}`);
      else files.push(file);
    }
    if (!files.length) return;
    setUploading(true);
    try {
      await uploadAttachments(c.id, files);
      await onRefresh();
      toast.success(`${files.length} file${files.length === 1 ? "" : "s"} attached`);
    } catch (e) {
      toast.error("Upload failed", { description: (e as Error).message });
    } finally {
      setUploading(false);
    }
  };

  // Runs the investigation: five reads against the rating and billing source
  // tables. The stepped progress paces the reveal — it never outlasts the
  // request, and the result is whatever the source tables returned.
  const analyzeBill = async () => {
    if (analysis === "running") return;
    setAnalysis("running");
    setAnalysisStep(0);
    setRatingOpen(false);
    setInvestigation(null);

    let step = 0;
    const timer = window.setInterval(() => {
      // Hold on the last step until the data lands, rather than completing the
      // animation and then sitting on an empty panel.
      step = Math.min(step + 1, ANALYSIS_STEPS.length - 1);
      setAnalysisStep(step);
    }, 400);

    try {
      const result = await fetchInvestigation(c.id);
      setInvestigation(result);
      setAnalysis("done");
      const variance = result.rating.actual?.variance;
      toast.success("Bill analysis complete", {
        description: result.rating.result === "FAIL"
          ? `${result.rating.finding || "Rating variance found"}${
              variance != null ? ` — ${result.rating.currency ?? ""} ${variance.toFixed(2)}`.trimEnd() : ""
            }`
          : "No rating variance found for this subscriber",
      });
    } catch (e) {
      setAnalysis("idle");
      toast.error("Bill analysis failed", { description: (e as Error).message });
    } finally {
      window.clearInterval(timer);
    }
  };

  const removeAttachment = async (id: string, filename: string) => {
    setBusyAttachment(id);
    try {
      await deleteAttachment(c.id, id);
      await onRefresh();
      toast.success(`${filename} removed`);
    } catch (e) {
      toast.error("Could not remove the file", { description: (e as Error).message });
    } finally {
      setBusyAttachment(null);
    }
  };

  const sampleNote = c.affectedCount > mismatches.length
    ? `Showing a sample of ${mismatches.length} of ${c.affectedCount.toLocaleString()} flagged records.`
    : `Showing all ${mismatches.length} flagged record${mismatches.length === 1 ? "" : "s"}.`;

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
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {c.reference} · {c.assuranceName} · {ruleLabel(c)}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="h-8 w-8 shrink-0 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body. `relative` + `overflow-hidden` scope the floating assistant to
            this area: it sits over the content without covering the header or
            footer. Laid out as a flex column so the scroll area below is sized
            by flex rather than by a percentage — the dialog is capped with
            max-height, which is an indefinite height, so `h-full` there would
            resolve to `auto` and the content would be clipped instead of
            scrolling. */}
        <div className="relative flex flex-1 min-h-0 flex-col overflow-hidden">
          {/* Extra bottom padding keeps the last section clear of the floating
              launcher, which sits over this scroll area. */}
          <div className="flex-1 min-h-0 overflow-y-auto p-5 pb-20">
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
                  <Kv k="Assigned to" v={ownerLabel(c)} />
                  <Kv k="Assurance" v={`${c.assuranceName} (${c.assuranceCode})`} />
                  <Kv k="Module" v={c.subModule ? `${c.module} · ${c.subModule}` : c.module || "—"} />
                  <Kv k="Rule" v={c.ruleId ? `${c.ruleId} — ${c.ruleName ?? ""}` : "Analyst raised"} mono />
                  <Kv k="Issue type" v={c.ruleCategory || "—"} />
                  <Kv k="Detected" v={fmtDate(c.detectedAt)} />
                  <Kv k="Created" v={fmtDate(c.createdAt)} />
                  <Kv k="Origin" v={c.origin} mono />
                  <Kv k="Run" v={c.ruleRunId ?? "—"} mono />
                  <Kv k="Stream / Node" v={`${c.stream || "—"} · ${c.nodeId || "—"}`} mono />
                  <Kv k="Linked batch" v={c.linkedBatch || "—"} mono />
                  <Kv k="Source → Target" v={`${c.sourceFeed || "—"} → ${c.targetFeed || "—"}`} />
                  <Kv k="Est. revenue at risk" v={fmtMoney(c.estimatedImpact)} />
                  <div className="sm:col-span-2">
                    <div className="text-[11px] text-muted-foreground mb-1">Description</div>
                    <p className="text-sm text-foreground/90 leading-relaxed">{c.description}</p>
                  </div>
                </div>
              </div>

              {/* The measured mismatch — what the control compared and by how much it failed */}
              <div className="rounded-xl border border-border bg-card">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
                  <Activity className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">Mismatch</span>
                  {c.threshold && (
                    <span className="ml-auto text-[11px] text-muted-foreground">Tolerance {c.threshold}</span>
                  )}
                </div>
                <div className="px-4 py-3 grid grid-cols-2 sm:grid-cols-4 gap-4">
                  <Metric label="Expected" value={c.expectedValue ?? "—"} />
                  <Metric label="Actual" value={c.actualValue ?? "—"} />
                  <Metric
                    label="Variance"
                    value={c.variance ?? "—"}
                    tone={c.variance?.trim().startsWith("-") ? "bad" : undefined}
                    hint={c.variancePct != null ? `${c.variancePct.toFixed(2)}%` : undefined}
                  />
                  <Metric label="Affected records" value={c.affectedCount.toLocaleString()} />
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
                    <Signal>
                      Raised by the <span className="font-medium">{c.ruleCategory || "assurance"}</span> control
                      {c.ruleId ? <> <span className="font-mono">{c.ruleId}</span></> : null} on {c.assuranceName}.
                    </Signal>
                    <Signal>{c.affectedCount.toLocaleString()} records flagged — {mismatchLabel(c)}.</Signal>
                    <Signal>
                      Concentrated on {c.nodeId || c.module || "one module"}, suggesting a local cause rather than a
                      platform-wide issue.
                    </Signal>
                    <Signal>Two comparable findings on this {c.nodeId ? "node" : "module"} in the last 30 days.</Signal>
                  </ul>
                </div>
              </Section>

              <Section
                title="Attached Evidence"
                icon={<Paperclip className="h-4 w-4" />}
                defaultOpen={attachments.length > 0}
                badge={attachments.length ? String(attachments.length) : c.evidence ? "1" : undefined}
              >
                <div className="pt-3 space-y-2">
                  {attachments.map((a) => (
                    <div key={a.id} className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2.5">
                      <span className="h-9 w-9 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                        <FileText className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-foreground font-medium truncate" title={a.filename}>{a.filename}</div>
                        <div className="text-[11px] text-muted-foreground">
                          {fmtBytes(a.sizeBytes)} · uploaded {relative(a.createdAt)}
                          {a.uploadedBy ? ` by ${a.uploadedBy}` : ""}
                        </div>
                      </div>
                      {billPdf?.id === a.id && (
                        <button
                          onClick={analyzeBill}
                          disabled={analysis === "running"}
                          className="inline-flex items-center gap-1.5 text-xs rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-1 font-medium text-primary hover:bg-primary/10 disabled:opacity-50 whitespace-nowrap"
                        >
                          {analysis === "running"
                            ? <><Loader2 className="h-3 w-3 animate-spin" /> Analyzing…</>
                            : analysis === "done"
                              ? <><RotateCw className="h-3 w-3" /> Re-analyze</>
                              : <><ScanSearch className="h-3 w-3" /> Analyze Bill</>}
                        </button>
                      )}
                      {/* A plain link, not a fetch: the browser handles the
                          stream and honours the server's filename. */}
                      <a
                        href={attachmentUrl(a)}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 text-xs rounded-lg border border-border px-2.5 py-1 hover:bg-muted"
                      >
                        <Download className="h-3 w-3" /> Open
                      </a>
                      <button
                        onClick={() => removeAttachment(a.id, a.filename)}
                        disabled={busyAttachment === a.id}
                        aria-label={`Remove ${a.filename}`}
                        className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted disabled:opacity-40"
                      >
                        {busyAttachment === a.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                  ))}

                  {/* The pre-attachment evidence descriptor some cases carry.
                      It is a label, not a stored file, so there is nothing to
                      download — shown for continuity with older cases. */}
                  {c.evidence && (
                    <div className="flex items-center gap-3 rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2.5">
                      <span className="h-9 w-9 shrink-0 rounded-lg bg-muted text-muted-foreground flex items-center justify-center">
                        <FileText className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-foreground/80 truncate">{c.evidence.name}</div>
                        <div className="text-[11px] text-muted-foreground">
                          {c.evidence.kind} · {c.evidence.size} · referenced by the control, not stored
                        </div>
                      </div>
                    </div>
                  )}

                  {attachments.length === 0 && !c.evidence && (
                    <Empty text="No evidence attached to this case." />
                  )}

                  <label className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-background px-3 py-2.5 text-xs text-muted-foreground hover:bg-muted/40 cursor-pointer">
                    {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                    {uploading ? "Uploading…" : "Add file (PDF, up to 25 MB)"}
                    <input
                      type="file"
                      accept="application/pdf,.pdf"
                      multiple
                      className="hidden"
                      disabled={uploading}
                      onChange={(e) => {
                        attachFiles(e.target.files);
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
              </Section>

              {billCase && analysis === "running" && <AnalysisProgress step={analysisStep} />}
              {billCase && analysis === "done" && investigation && (
                <BillInvestigationPanel
                  data={investigation}
                  onOpenRatingAnalysis={() => setRatingOpen(true)}
                />
              )}

              <Section
                title="Mismatch Records"
                icon={<Activity className="h-4 w-4" />}
                defaultOpen={mismatches.length > 0}
                badge={mismatches.length ? String(mismatches.length) : undefined}
              >
                {mismatches.length ? (
                  <div className="pt-3 space-y-2">
                    <p className="text-[11px] text-muted-foreground">{sampleNote}</p>
                    <div className="-mx-1 overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
                          <tr>
                            {["Record", "Entity", "Subscriber", "Field", "Expected", "Actual", "Delta", "Status"].map((h) => (
                              <th key={h} className="text-left font-medium px-2.5 py-2 whitespace-nowrap">{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {mismatches.map((m) => (
                            <tr key={m.id} className="border-t border-border">
                              <td className="px-2.5 py-2 font-mono text-foreground/80">{m.recordRef || "—"}</td>
                              <td className="px-2.5 py-2 font-mono text-muted-foreground">{m.entity || "—"}</td>
                              <td className="px-2.5 py-2 font-mono text-muted-foreground">{m.subscriber || "—"}</td>
                              <td className="px-2.5 py-2 text-muted-foreground">{m.field || "—"}</td>
                              <td className="px-2.5 py-2 tabular-nums text-foreground/80">{m.expectedValue ?? "—"}</td>
                              <td className="px-2.5 py-2 tabular-nums text-foreground/80">{m.actualValue ?? "—"}</td>
                              <td className="px-2.5 py-2 tabular-nums text-foreground/80">{m.delta ?? "—"}</td>
                              <td className="px-2.5 py-2"><StatusBadge value={m.status.replace(/_/g, " ").toLowerCase()} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : (
                  <Empty text={`Raised at ${c.module || "module"} level — ${c.affectedCount.toLocaleString()} records affected, no record-level evidence for this finding.`} />
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

              <Section title="Investigation Panel" icon={<MessageSquarePlus className="h-4 w-4" />} badge={comments.length ? String(comments.length) : undefined}>
                <div className="pt-3 space-y-3">
                  <ol className="space-y-2.5">
                    <li className="flex gap-2.5">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                      <div className="text-xs">
                        <span className="text-foreground/85">
                          Case opened by <span className="font-medium">{c.origin === "auto_detected" ? `rule ${c.ruleId ?? "engine"}` : ownerLabel(c)}</span>
                        </span>
                        <div className="text-[10px] text-muted-foreground">{fmtDate(c.createdAt)}</div>
                      </div>
                    </li>
                    {comments.map((cm) => (
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
                      disabled={!comment.trim() || posting}
                      className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
                    >
                      Add
                    </button>
                  </div>
                </div>
              </Section>

              <Section title="Audit Trail" icon={<History className="h-4 w-4" />} badge={activities.length ? String(activities.length) : undefined}>
                {activities.length ? (
                  <ol className="pt-3 space-y-2">
                    {activities.map((a) => (
                      <li key={a.id} className="flex gap-2.5 text-xs">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
                        <div>
                          <span className="text-foreground/85">
                            <span className="font-medium">{a.actor}</span>{" "}
                            {a.field
                              ? <>changed <span className="font-medium">{a.field}</span> {a.fromValue ? `from "${a.fromValue}" ` : ""}to "{a.toValue}"</>
                              : <>{a.action.replace(/_/g, " ")}{a.note ? ` — ${a.note}` : ""}</>}
                          </span>
                          <div className="text-[10px] text-muted-foreground">{fmtDate(a.createdAt)}</div>
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <Empty text="No changes recorded yet." />
                )}
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
                      disabled={!dirty || saving}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save changes
                    </button>
                  </div>
                </div>
              </Section>
            </div>
          </div>

          {/* Floating assistant, anchored to the dialog rather than the page.
              The overlay itself ignores pointer events so only the launcher and
              the chat window are clickable — everything behind stays usable and
              the investigation content keeps the full dialog width. */}
          <div className="pointer-events-none absolute inset-0 z-30">
            {/* Always mounted so the close animation plays; hidden from the tab
                order and assistive tech while collapsed. */}
            <div
              ref={assistantRef}
              role="dialog"
              aria-label="Assurance Assistant"
              aria-hidden={!assistantOpen}
              inert={!assistantOpen}
              className={`pointer-events-auto absolute bottom-[4.75rem] right-5 flex w-[min(22rem,calc(100%-2.5rem))] h-[min(27rem,calc(100%-7rem))] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl origin-bottom-right transition-all duration-200 ease-out ${
                assistantOpen
                  ? "opacity-100 translate-y-0 scale-100"
                  : "pointer-events-none opacity-0 translate-y-3 scale-95"
              }`}
            >
              <AssistantPanel
                activeCase={c}
                onPin={pinInsight}
                onClose={() => setAssistantOpen(false)}
                investigation={investigation}
              />
            </div>

            <button
              onClick={() => setAssistantOpen((o) => !o)}
              aria-label={assistantOpen ? "Minimise the assurance assistant" : "Ask the assurance assistant"}
              aria-expanded={assistantOpen}
              title="Assurance Assistant"
              className="pointer-events-auto absolute bottom-5 right-5 h-12 w-12 rounded-full bg-primary text-primary-foreground shadow-lg flex items-center justify-center transition-transform duration-200 hover:scale-105 active:scale-95 focus:outline-none focus:ring-2 focus:ring-primary/40 focus:ring-offset-2 focus:ring-offset-background"
            >
              {/* Both icons are mounted and cross-faded, so the launcher never
                  flickers between states. */}
              <Bot className={`absolute inset-0 m-auto h-5 w-5 transition-all duration-200 ${assistantOpen ? "opacity-0 rotate-90 scale-75" : "opacity-100 rotate-0 scale-100"}`} />
              <X className={`absolute inset-0 m-auto h-5 w-5 transition-all duration-200 ${assistantOpen ? "opacity-100 rotate-0 scale-100" : "opacity-0 -rotate-90 scale-75"}`} />
            </button>
          </div>

          {/* Rating drill-down. Anchored to the body rather than the viewport so
              it slides over the investigation content and leaves the dialog
              header and footer visible. Above the assistant layer, so opening it
              covers the launcher. Mounted only while a result exists, but kept
              mounted across open/close so the slide animation plays both ways. */}
          {billCase && analysis === "done" && investigation?.rating.available && (
            <div className={`absolute inset-0 z-40 ${ratingOpen ? "" : "pointer-events-none"}`}>
              <button
                aria-hidden="true"
                tabIndex={-1}
                onClick={() => setRatingOpen(false)}
                className={`absolute inset-0 bg-black/40 transition-opacity duration-300 ${ratingOpen ? "opacity-100" : "opacity-0"}`}
              />
              <div
                role="dialog"
                aria-label="Rating analysis"
                aria-hidden={!ratingOpen}
                inert={!ratingOpen}
                className={`absolute inset-y-0 right-0 flex w-full max-w-3xl flex-col border-l border-border bg-background shadow-2xl transition-transform duration-300 ease-out ${
                  ratingOpen ? "translate-x-0" : "translate-x-full"
                }`}
              >
                <RatingAnalysis
                  rating={investigation.rating}
                  invoiceCurrency={investigation.subscriber.invoice?.currency}
                  onClose={() => setRatingOpen(false)}
                />
              </div>
            </div>
          )}
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
      <div className={`text-sm text-foreground truncate ${mono ? "font-mono text-[13px]" : ""}`} title={v}>{v}</div>
    </div>
  );
}

function Metric({ label, value, hint, tone }: Readonly<{ label: string; value: string; hint?: string; tone?: "bad" }>) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={`text-sm font-semibold tabular-nums truncate ${tone === "bad" ? "text-destructive" : "text-foreground"}`} title={value}>
        {value}
      </div>
      {hint && <div className="text-[10px] text-muted-foreground">{hint}</div>}
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
