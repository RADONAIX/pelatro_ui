import { useEffect, useRef, useState } from "react";
import { Bot, Paperclip, Pin, Send, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { fmtMoney, type AssuranceCase } from "@/lib/casesDemo";

// DEMO-ONLY. There is no LLM anywhere in this project — no dependency, no
// /chat endpoint — so every reply here is scripted and keyword-matched against
// the open case. Wiring this to a real model means a new backend integration.

interface Message {
  id: string;
  role: "user" | "assistant";
  body: string;
  pinned?: boolean;
}

// Ingestion-side findings (feed problems) vs reconciliation findings (value/match
// problems). The remediation and ownership answers differ between the two.
const INGESTION_FINDINGS = ["file_sequence_check", "record_sequence_check", "file_exception"];

// Finding-specific remediation, so the answer fits the actual check that fired.
function remediation(c: AssuranceCase): string {
  switch (c.findingType) {
    case "air_reconciliation":
    case "sdp_reconciliation":
      return `verify the active rating-table version for ${c.linkedBatch}, re-run rating for the batch, then adjust & rebill the raw-vs-processed delta.`;
    case "air_sdp_cross":
      return `confirm whether the unmatched AIR transactions were dropped in mediation or genuinely absent from SDP, request re-mediation of ${c.linkedBatch}, then re-run the cross reconciliation.`;
    case "file_sequence_check":
      return `request re-delivery of the missing files in ${c.linkedBatch} from the upstream node, re-ingest, then re-run the file sequence check to confirm the gap closes.`;
    case "record_sequence_check":
      return `pin the collector gap window on ${c.nodeId}, request a re-pull for that window, and raise a config change if it traces back to a collector restart.`;
    case "file_exception":
      return `the file in ${c.linkedBatch} was rejected at decode — request a corrected re-delivery or apply the decoder rule fix, then re-ingest.`;
    default:
      return `confirm the upstream state of ${c.linkedBatch}, correct it, and re-run the ${c.findingType.replace(/_/g, " ")} check.`;
  }
}

// Suggested questions shown as clickable chips before the first message.
export const SUGGESTIONS = [
  "Which subscribers are affected?",
  "Show the match breakdown",
  "How do we remediate this?",
  "What's the revenue impact?",
];

// Canned answers, written for revenue assurance. First matching rule wins, so the
// more specific intents are checked before the general ones.
function reply(question: string, c: AssuranceCase): string {
  const q = question.toLowerCase();
  const traceN = c.trace.length;
  const matched = c.trace.filter((t) => t.status === "MATCHED").length;
  const mismatches = c.trace.filter((t) => t.status === "AMOUNT_MISMATCH").length;
  const rawOnly = c.trace.filter((t) => t.status === "RAW_ONLY").length;
  const procOnly = c.trace.filter((t) => t.status === "PROC_ONLY").length;
  const ingestion = INGESTION_FINDINGS.includes(c.findingType);

  const total = c.affectedCount.toLocaleString();
  // Which records / subscribers are affected
  if (q.includes("affect") || q.includes("subscriber") || q.includes("customer")) {
    if (!traceN) return `${c.reference} affects ${total} records but was raised at file level (${c.linkedBatch}), so there is no subscriber-level list yet — the file is pending re-ingest.`;
    const subs = c.trace.slice(0, 3).map((t) => t.subscriberNum).join(", ");
    return `${total} records are affected on ${c.stream} · ${c.nodeId}. Sample subscribers from the reviewed set: ${subs}. The full sample with raw vs processed amounts is in the Record Trace tab.`;
  }
  // Reconciliation / match breakdown
  if (q.includes("breakdown") || q.includes("match") || q.includes("how many") || q.includes("split")) {
    if (!traceN) return `No record-level breakdown — ${c.reference} is a file-level finding on ${c.linkedBatch} (${total} records), so there are no per-record match counts.`;
    return `${total} records are flagged. In the reviewed sample of ${traceN}: ${matched} matched, ${mismatches} amount-mismatch, ${rawOnly} raw-only (present in ${c.stream}, missing downstream), ${procOnly} processed-only. That points to ${rawOnly > mismatches ? "records lost between raw and processed" : "a rating delta rather than dropped records"}.`;
  }
  if (q.includes("analyz") || q.includes("linked record") || q.includes("trace")) {
    if (!traceN) return `${c.reference} flags ${total} records on batch ${c.linkedBatch}, but it was raised at file level so there is nothing to trace at record level.`;
    return `Batch ${c.linkedBatch} (${c.stream} · ${c.nodeId}) flags ${total} records; the reviewed sample of ${traceN} is ${mismatches} amount-mismatch and ${rawOnly} raw-only. Estimated exposure ${fmtMoney(c.estimatedImpact)}. The spread is consistent — a rating-table drift rather than random loss.`;
  }
  if (q.includes("impact") || q.includes("leak") || q.includes("revenue") || q.includes("cost")) {
    return `Estimated revenue at risk for ${c.reference} is ${fmtMoney(c.estimatedImpact)}, derived from the raw-vs-processed delta across the linked records. Treat it as an upper bound until the re-delivery lands.`;
  }
  // Who owns it / should it be escalated. Checked BEFORE remediation so a phrase
  // like "who owns the fix" routes here rather than matching "fix".
  if (q.includes("escalat") || q.includes("who owns") || q.includes("who own") || q.includes("owner") || q.includes("ownership") || q.includes("own this") || q.includes("own it") || q.includes("should own") || q.includes("route") || q.includes("team") || q.includes("responsible") || q.includes("hand off") || q.includes("hand-off")) {
    return ingestion
      ? `The Mediation / upstream feed team owns the fix for ${c.reference}; RA tracks it to closure and re-runs the check once the feed is corrected.`
      : `The Rating / RA analyst team owns the adjustment for ${c.reference}; escalate to the carrier only if the counterpart records for ${c.linkedBatch} are genuinely absent rather than mediation-dropped.`;
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
    return `The ${c.findingType.replace(/_/g, " ")} check fired on ${c.linkedBatch}. The pattern is confined to ${c.nodeId}, which points at a node-local cause — ${ingestion ? "a collector or upstream feed issue" : "a stale rating table or mediation drop"} — rather than a platform-wide problem.`;
  }
  if (q.includes("recommend") || q.includes("next") || q.includes("action") || q.includes("should")) {
    return `Suggested next step for ${c.reference}: confirm whether ${c.linkedBatch} was re-delivered upstream. If it was, re-run the reconciliation and close as adjusted. If not, escalate to the upstream team and set the action to "Escalated to carrier".`;
  }
  if (q.includes("similar") || q.includes("before") || q.includes("history") || q.includes("recur")) {
    return `Two comparable findings on ${c.stream}/${c.nodeId} in the last 30 days, both ${c.findingType.replace(/_/g, " ")}. Both closed as adjusted & rebilled within 48h. Recurrence on the same node suggests a standing config issue worth raising.`;
  }
  return `I can help with ${c.reference}. Try: "which subscribers are affected", "show the match breakdown", "how do we remediate this", "what's the revenue impact", "who owns the fix", or "how urgent is it".`;
}

export function AssistantPanel({
  activeCase,
  onPin,
}: Readonly<{ activeCase: AssuranceCase; onPin: (body: string) => void }>) {
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
      setMessages((m) => [...m, { id: `a-${m.length}`, role: "assistant", body: reply(q, activeCase) }]);
      setThinking(false);
    }, 700);
  };

  const pin = (m: Message) => {
    onPin(m.body);
    setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, pinned: true } : x)));
    toast.success("Pinned to Saved Insights");
  };

  return (
    <div className="flex flex-col h-full rounded-xl border border-border bg-card overflow-hidden">
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="h-7 w-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Bot className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground leading-none">Assurance Assistant</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-[220px]">
        {messages.length === 0 && !thinking && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-3 py-6">
            <Sparkles className="h-7 w-7 text-muted-foreground/40" />
            <p className="text-xs text-muted-foreground max-w-[220px]">
              Ask about {activeCase.reference}, or start with a suggested question:
            </p>
            <div className="flex flex-wrap justify-center gap-1.5 max-w-[260px]">
              {SUGGESTIONS.map((s) => (
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
          onClick={() => ask("Analyze linked records")}
          disabled={thinking}
          className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-primary/40 bg-primary/5 py-2 text-xs font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
        >
          <Sparkles className="h-3.5 w-3.5" /> Analyze linked records
        </button>
      </div>
    </div>
  );
}
