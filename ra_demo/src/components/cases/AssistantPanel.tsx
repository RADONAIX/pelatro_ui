import { useEffect, useRef, useState } from "react";
import { Bot, Paperclip, Pin, Send, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { fmtMoney, mismatchLabel, ruleLabel, type AssuranceCase } from "@/lib/cases";
import {
  INVESTIGATION_SUGGESTIONS, investigationAnswer, type Investigation,
} from "@/lib/investigation";

// There is no LLM anywhere in this project — no dependency, no /chat endpoint —
// so every reply here is composed and keyword-matched against the open case.
// Wiring this to a real model means a new backend integration.
//
// The figures are not invented: for a case whose investigation has been run,
// the answers are built from that payload, which is the same data the panels
// render. The assistant can therefore never quote a number the screen disagrees
// with.

interface Message {
  id: string;
  role: "user" | "assistant";
  body: string;
  pinned?: boolean;
}

// Ingestion-side findings (feed problems) vs value findings (amount/match
// problems). The remediation and ownership answers differ between the two, and
// the case's issue type — the rule's primitive category — is what tells them
// apart across every assurance.
const INGESTION_CATEGORIES = ["Sequence", "Existence", "Temporal", "Completeness"];

const batchLabel = (c: AssuranceCase) => c.linkedBatch || c.module || "the affected feed";

// Category-specific remediation, so the answer fits the control that fired.
function remediation(c: AssuranceCase): string {
  switch (c.ruleCategory) {
    case "Reconciliation":
    case "Comparison":
      return `compare ${c.sourceFeed || "the source feed"} against ${c.targetFeed || "the target feed"} for ${batchLabel(c)}, establish whether the delta was dropped in transit or never produced, then re-run the reconciliation.`;
    case "Calculation":
      return `verify the active pricing/tariff version applied to ${batchLabel(c)}, re-run the calculation for the affected window, then adjust & rebill the delta.`;
    case "Sequence":
      return `request re-delivery of the missing sequence in ${batchLabel(c)} from the upstream node, re-ingest, then re-run the sequence check to confirm the gap closes.`;
    case "Completeness":
      return `establish where the missing volume was lost between ${c.sourceFeed || "source"} and ${c.targetFeed || "target"}, request a re-pull for the window, and re-run the completeness control.`;
    case "Duplicate":
      return `identify the replayed batch behind the duplicates in ${batchLabel(c)}, suppress the second submission, then re-run the duplicate control.`;
    case "Existence":
      return `the records in ${batchLabel(c)} were rejected or never arrived — request a corrected re-delivery or apply the decode/config fix, then re-ingest.`;
    case "Referential Integrity":
      return `re-link the orphaned records in ${batchLabel(c)} to their parent entity, correct the matching rule if the key format drifted, then re-run the integrity check.`;
    case "Temporal":
      return `confirm whether the feed outage on ${c.nodeId || c.module} was collector-side or element-side, recover the missing window, then re-run the control.`;
    default:
      return `confirm the upstream state of ${batchLabel(c)}, correct it, and re-run the ${(c.ruleCategory || "assurance").toLowerCase()} control.`;
  }
}

// Suggested questions shown as clickable chips before the first message. A
// case raised from a customer bill asks different questions than one a control
// raised, so the chips follow the case.
const RULE_SUGGESTIONS = [
  "Which subscribers are affected?",
  "Show the match breakdown",
  "How do we remediate this?",
  "What's the revenue impact?",
];

export const suggestionsFor = (_c: AssuranceCase, investigation?: Investigation | null) =>
  investigation ? INVESTIGATION_SUGGESTIONS : RULE_SUGGESTIONS;

// Composed answers, written for revenue assurance. First matching rule wins, so
// the more specific intents are checked before the general ones.
function reply(question: string, c: AssuranceCase, investigation?: Investigation | null): string {
  // Once the investigation has run, answer from it: the rule-oriented replies
  // below talk about controls and batches, which say nothing about a rated
  // event and an invoice.
  if (investigation) return investigationAnswer(question, investigation);

  const q = question.toLowerCase();
  const rows = c.mismatches ?? [];
  const sampleN = rows.length;
  const byStatus = rows.reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});
  const topStatus = Object.entries(byStatus).sort((a, b) => b[1] - a[1])[0];
  const ingestion = INGESTION_CATEGORIES.includes(c.ruleCategory);

  const total = c.affectedCount.toLocaleString();
  const where = [c.stream, c.nodeId].filter(Boolean).join(" · ") || c.module || c.assuranceName;

  // Which records / subscribers are affected
  if (q.includes("affect") || q.includes("subscriber") || q.includes("customer")) {
    if (!sampleN) return `${c.reference} affects ${total} records but was raised at ${c.module || "feed"} level (${batchLabel(c)}), so there is no record-level list yet.`;
    const subs = rows.slice(0, 3).map((r) => r.subscriber || r.recordRef).filter(Boolean).join(", ");
    return `${total} records are affected on ${where}. Sample from the reviewed set: ${subs || "—"}. The full sample with expected vs actual values is in Mismatch Records.`;
  }
  // Match / mismatch breakdown
  if (q.includes("breakdown") || q.includes("match") || q.includes("how many") || q.includes("split")) {
    if (!sampleN) return `No record-level breakdown — ${c.reference} is a ${c.module || "feed"}-level finding on ${batchLabel(c)} (${total} records), so there are no per-record counts.`;
    const parts = Object.entries(byStatus).map(([s, n]) => `${n} ${s.replace(/_/g, " ").toLowerCase()}`).join(", ");
    return `${total} records are flagged, headline variance ${mismatchLabel(c)}. In the reviewed sample of ${sampleN}: ${parts}.`;
  }
  if (q.includes("analyz") || q.includes("linked record") || q.includes("trace")) {
    if (!sampleN) return `${c.reference} flags ${total} records on ${batchLabel(c)}, but it was raised at ${c.module || "feed"} level so there is nothing to trace at record level.`;
    return `${batchLabel(c)} (${where}) flags ${total} records; the reviewed sample of ${sampleN} is mostly ${(topStatus?.[0] ?? "mismatch").replace(/_/g, " ").toLowerCase()}. Measured ${mismatchLabel(c)}, estimated exposure ${fmtMoney(c.estimatedImpact)}. The spread is consistent — a systematic drift rather than random loss.`;
  }
  if (q.includes("impact") || q.includes("leak") || q.includes("revenue") || q.includes("cost")) {
    return `Estimated revenue at risk for ${c.reference} is ${fmtMoney(c.estimatedImpact)}, derived from the ${mismatchLabel(c)} delta across the linked records. Treat it as an upper bound until the correction lands.`;
  }
  // Who owns it / should it be escalated. Checked BEFORE remediation so a phrase
  // like "who owns the fix" routes here rather than matching "fix".
  if (q.includes("escalat") || q.includes("who owns") || q.includes("who own") || q.includes("owner") || q.includes("ownership") || q.includes("own this") || q.includes("own it") || q.includes("should own") || q.includes("route") || q.includes("team") || q.includes("responsible") || q.includes("hand off") || q.includes("hand-off")) {
    return ingestion
      ? `The Mediation / upstream feed team owns the fix for ${c.reference}; RA tracks it to closure and re-runs the check once the feed is corrected.`
      : `The ${c.assuranceName} analyst team owns the adjustment for ${c.reference}; escalate to the carrier only if the counterpart records for ${batchLabel(c)} are genuinely absent rather than dropped in transit.`;
  }
  // How to remediate / fix / recover
  if (q.includes("remediat") || q.includes("fix") || q.includes("recover") || q.includes("re-run") || q.includes("re-deliver") || q.includes("redeliver") || q.includes("correct")) {
    return `To remediate ${c.reference}: ${remediation(c)}`;
  }
  // Priority / urgency / SLA
  if (q.includes("urgent") || q.includes("priority") || q.includes("sla") || q.includes("deadline") || q.includes("due")) {
    const sla = c.severity === "critical" ? "a 4-hour response target" : c.severity === "high" ? "a same-day target" : "the standard 48-hour window";
    return `${c.reference} is ${c.severity} priority — ${sla}. With ${fmtMoney(c.estimatedImpact)} at risk and status "${c.status}", it ${c.status === "Open" ? "still needs an owner and a first action" : "is already in flight"}.`;
  }
  if (q.includes("why") || q.includes("cause") || q.includes("root")) {
    return `${ruleLabel(c)} fired on ${batchLabel(c)} against a ${c.threshold ?? "configured"} tolerance, measuring ${mismatchLabel(c)}. The pattern is confined to ${c.nodeId || c.module || "one module"}, which points at a local cause — ${ingestion ? "a collector or upstream feed issue" : "a stale reference table or a drop in transit"} — rather than a platform-wide problem.`;
  }
  if (q.includes("recommend") || q.includes("next") || q.includes("action") || q.includes("should")) {
    return `Suggested next step for ${c.reference}: confirm whether ${batchLabel(c)} was re-delivered or corrected upstream. If it was, re-run ${c.ruleId ?? "the control"} and close as adjusted. If not, escalate to the upstream team and set the action to "Escalated to carrier".`;
  }
  if (q.includes("similar") || q.includes("before") || q.includes("history") || q.includes("recur")) {
    return `Two comparable ${c.ruleCategory.toLowerCase() || "assurance"} findings on ${where} in the last 30 days, both from ${c.assuranceName}. Both closed as adjusted & rebilled within 48h. Recurrence on the same node suggests a standing config issue worth raising.`;
  }
  return `I can help with ${c.reference}. Try: "which subscribers are affected", "show the match breakdown", "how do we remediate this", "what's the revenue impact", "who owns the fix", or "how urgent is it".`;
}

export function AssistantPanel({
  activeCase,
  onPin,
  onClose,
  investigation,
}: Readonly<{
  activeCase: AssuranceCase;
  onPin: (body: string) => void;
  /** Supplied when the panel is a slide-over: renders its own dismiss control
   *  and drops the card chrome, since the slide-over already provides it. */
  onClose?: () => void;
  /** Present once Analyze Bill has run. Every billing answer is composed from
   *  it, so the assistant quotes the figures on screen rather than its own. */
  investigation?: Investigation | null;
}>) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // Reset the thread whenever a different case is opened.
  useEffect(() => {
    setMessages([]);
    setInput("");
    setThinking(false);
  }, [activeCase.id]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, thinking]);

  const ask = (text: string) => {
    const q = text.trim();
    if (!q || thinking) return;
    setMessages((m) => [...m, { id: `u-${m.length}`, role: "user", body: q }]);
    setInput("");
    setThinking(true);
    // Small delay so the exchange reads as a live interaction.
    window.setTimeout(() => {
      setMessages((m) => [...m, { id: `a-${m.length}`, role: "assistant", body: reply(q, activeCase, investigation) }]);
      setThinking(false);
    }, 700);
  };

  const pin = (m: Message) => {
    onPin(m.body);
    setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, pinned: true } : x)));
    toast.success("Pinned to Saved Insights");
  };

  return (
    <div className={`flex flex-col h-full bg-card overflow-hidden ${onClose ? "" : "rounded-xl border border-border"}`}>
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="h-7 w-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Bot className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-foreground leading-none">Assurance Assistant</div>
          <div className="text-[11px] text-muted-foreground mt-1 truncate">{activeCase.reference}</div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            aria-label="Close the assistant"
            className="h-7 w-7 shrink-0 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-[220px]">
        {messages.length === 0 && !thinking && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-3 py-6">
            <Sparkles className="h-7 w-7 text-muted-foreground/40" />
            <p className="text-xs text-muted-foreground max-w-[220px]">
              Ask about {activeCase.reference}, or start with a suggested question:
            </p>
            <div className="flex flex-wrap justify-center gap-1.5 max-w-[260px]">
              {suggestionsFor(activeCase, investigation).map((s) => (
                <button
                  key={s}
                  onClick={() => ask(s)}
                  className="rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground transition"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-xl rounded-br-sm bg-primary px-3 py-2 text-xs text-primary-foreground">{m.body}</div>
            </div>
          ) : (
            <div key={m.id} className="flex flex-col gap-1 items-start">
              <div className="max-w-[92%] rounded-xl rounded-bl-sm border border-border bg-muted/30 px-3 py-2 text-xs text-foreground/90 leading-relaxed">
                {m.body}
              </div>
              <div className="flex items-center gap-2 pl-1">
                <button
                  onClick={() => pin(m)}
                  disabled={m.pinned}
                  className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-primary disabled:opacity-40 disabled:hover:text-muted-foreground"
                >
                  <Pin className="h-3 w-3" /> {m.pinned ? "Pinned" : "Pin"}
                </button>
              </div>
            </div>
          ),
        )}

        {thinking && (
          <div className="flex items-center gap-1.5 pl-1" aria-label="Assistant is typing">
            {[0, 150, 300].map((d) => (
              <span key={d} className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50 animate-pulse" style={{ animationDelay: `${d}ms` }} />
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 border-t border-border p-3 space-y-2">
        <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-2.5 py-1.5">
          <Paperclip className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask(input)}
            placeholder="Ask a question…"
            aria-label="Ask the assistant"
            className="flex-1 bg-transparent text-xs focus:outline-none"
          />
          <button
            onClick={() => ask(input)}
            disabled={!input.trim() || thinking}
            aria-label="Send"
            className="h-6 w-6 shrink-0 rounded-md flex items-center justify-center text-muted-foreground hover:bg-muted hover:text-primary disabled:opacity-40"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>
        <button
          onClick={() => ask(investigation ? "Why was the customer overcharged?" : "Analyze linked records")}
          disabled={thinking}
          className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-primary/40 bg-primary/5 py-2 text-xs font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {investigation ? "Explain the overcharge" : "Analyze linked records"}
        </button>
      </div>
    </div>
  );
}
