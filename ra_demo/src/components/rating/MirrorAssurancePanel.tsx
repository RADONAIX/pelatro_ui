import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Database, Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { ratingError } from "@/lib/rating/api";
import {
  useMirrorAssuranceResults,
  useMirrorAssuranceRun,
  useMirrorAssuranceRuns,
  useMirrorAssuranceSchedule,
  useSaveMirrorAssuranceSchedule,
  useStartMirrorAssurance,
} from "@/lib/rating/hooks";

const TONES: Record<string, string> = {
  QUEUED: "bg-muted text-muted-foreground border-border",
  RUNNING: "bg-[#F8C800]/15 text-[#9a7d00] border-[#F8C800]/40",
  COMPLETED: "bg-success/10 text-success border-success/20",
  FAILED: "bg-destructive/10 text-destructive border-destructive/20",
  MATCHED: "bg-success/10 text-success border-success/20",
  UNDERCHARGED: "bg-warning/15 text-warning-foreground border-warning/30",
  OVERCHARGED: "bg-destructive/10 text-destructive border-destructive/20",
};

const fieldClass =
  "h-9 rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:opacity-60";

function localDateTime(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

export function MirrorAssurancePanel({ canEdit }: { canEdit: boolean }) {
  const defaultEnd = useMemo(() => new Date(), []);
  const [windowStart, setWindowStart] = useState(
    localDateTime(new Date(defaultEnd.getTime() - 24 * 60 * 60_000)),
  );
  const [windowEnd, setWindowEnd] = useState(localDateTime(defaultEnd));
  const [manualTolerance, setManualTolerance] = useState("0.01");
  const [selectedId, setSelectedId] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");

  const { data: schedule } = useMirrorAssuranceSchedule();
  const { data: runs = [] } = useMirrorAssuranceRuns();
  const currentId = selectedId ?? runs[0]?.id;
  const { data: current } = useMirrorAssuranceRun(currentId);
  const currentStatus = current?.status;
  const {
    data: results,
    isFetching: resultsLoading,
    refetch: refetchResults,
  } = useMirrorAssuranceResults(
    currentId,
    offset,
    status,
    !!currentStatus && ["QUEUED", "RUNNING"].includes(currentStatus),
  );
  const start = useStartMirrorAssurance();
  const save = useSaveMirrorAssuranceSchedule();

  const [enabled, setEnabled] = useState(false);
  const [intervalMinutes, setIntervalMinutes] = useState(1440);
  const [windowHours, setWindowHours] = useState(24);
  const [scheduleTolerance, setScheduleTolerance] = useState("0.01");

  useEffect(() => {
    if (!schedule) return;
    setEnabled(schedule.enabled);
    setIntervalMinutes(schedule.interval_minutes);
    setWindowHours(schedule.window_hours);
    setScheduleTolerance(String(schedule.tolerance));
  }, [schedule]);

  useEffect(() => setOffset(0), [currentId, status]);

  useEffect(() => {
    if (currentStatus && ["COMPLETED", "FAILED"].includes(currentStatus)) {
      void refetchResults();
    }
  }, [currentStatus, refetchResults]);

  const trigger = async () => {
    try {
      const run = await start.mutateAsync({
        window_start: new Date(windowStart).toISOString(),
        window_end: new Date(windowEnd).toISOString(),
        tolerance: Number(manualTolerance),
      });
      setSelectedId(run.id);
      toast.success("Rating Assurance started", {
        description:
          "The mirror is read-only while the reconciliation query runs.",
      });
    } catch (error) {
      toast.error("Could not start Rating Assurance", {
        description: ratingError(error),
      });
    }
  };

  const saveSchedule = async () => {
    try {
      await save.mutateAsync({
        enabled,
        interval_minutes: intervalMinutes,
        window_hours: windowHours,
        tolerance: Number(scheduleTolerance),
      });
      toast.success(enabled ? "Schedule saved" : "Schedule disabled");
    } catch (error) {
      toast.error("Could not save the schedule", {
        description: ratingError(error),
      });
    }
  };

  const badWindow =
    !windowStart ||
    !windowEnd ||
    Number.isNaN(Number(manualTolerance)) ||
    Number(manualTolerance) < 0 ||
    new Date(windowEnd) <= new Date(windowStart);

  return (
    <section className="bg-card border border-border rounded-xl overflow-hidden mb-6">
      <div className="px-5 py-4 border-b border-border flex flex-wrap items-start gap-3">
        <div className="rounded-lg bg-primary/10 p-2">
          <Database className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-foreground">
            Rating Assurance
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Reconcile expected and actual charges from the rafms_rating_new
            mirror. The job cannot write to the mirror database.
          </p>
        </div>
        {schedule && (
          <span
            className={`ml-auto text-[11px] font-medium px-2 py-0.5 rounded-md border ${
              schedule.enabled ? TONES.COMPLETED : TONES.QUEUED
            }`}
          >
            {schedule.enabled ? "SCHEDULED" : "MANUAL"}
          </span>
        )}
      </div>

      {canEdit && (
        <div className="grid lg:grid-cols-2 border-b border-border">
          <div className="p-5 lg:border-r border-border">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Play className="h-4 w-4 text-primary" /> Run now
            </div>
            <div className="grid sm:grid-cols-2 gap-3 mt-4">
              <label className="text-xs text-muted-foreground">
                Window start
                <input
                  type="datetime-local"
                  value={windowStart}
                  onChange={(e) => setWindowStart(e.target.value)}
                  className={`${fieldClass} w-full mt-1`}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                Window end
                <input
                  type="datetime-local"
                  value={windowEnd}
                  onChange={(e) => setWindowEnd(e.target.value)}
                  className={`${fieldClass} w-full mt-1`}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                Reconciliation tolerance
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={manualTolerance}
                  onChange={(e) => setManualTolerance(e.target.value)}
                  className={`${fieldClass} w-full mt-1`}
                />
              </label>
              <div className="flex items-end">
                <button
                  onClick={trigger}
                  disabled={
                    start.isPending ||
                    badWindow ||
                    runs.some((run) =>
                      ["QUEUED", "RUNNING"].includes(run.status),
                    )
                  }
                  className="h-9 inline-flex items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  {start.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  Trigger pipeline
                </button>
              </div>
            </div>
          </div>

          <div className="p-5">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <CalendarClock className="h-4 w-4 text-primary" /> Schedule
            </div>
            <div className="grid sm:grid-cols-2 gap-3 mt-4">
              <label className="text-xs text-muted-foreground">
                Frequency
                <select
                  value={intervalMinutes}
                  onChange={(e) => setIntervalMinutes(Number(e.target.value))}
                  className={`${fieldClass} w-full mt-1`}
                >
                  <option value={15}>Every 15 minutes</option>
                  <option value={60}>Hourly</option>
                  <option value={360}>Every 6 hours</option>
                  <option value={1440}>Daily</option>
                  <option value={10080}>Weekly</option>
                </select>
              </label>
              <label className="text-xs text-muted-foreground">
                Lookback window (hours)
                <input
                  type="number"
                  min="1"
                  max="8760"
                  value={windowHours}
                  onChange={(e) => setWindowHours(Number(e.target.value))}
                  className={`${fieldClass} w-full mt-1`}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                Reconciliation tolerance
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={scheduleTolerance}
                  onChange={(e) => setScheduleTolerance(e.target.value)}
                  className={`${fieldClass} w-full mt-1`}
                />
              </label>
              <div className="flex items-end gap-3">
                <label className="h-9 inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                  />{" "}
                  Enabled
                </label>
                <button
                  onClick={saveSchedule}
                  disabled={save.isPending}
                  className="h-9 rounded-lg border border-border px-4 text-sm font-medium hover:bg-muted disabled:opacity-50"
                >
                  {save.isPending ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
            {schedule?.next_run_at && schedule.enabled && (
              <p className="text-[11px] text-muted-foreground mt-3">
                Next run: {new Date(schedule.next_run_at).toLocaleString()}
              </p>
            )}
          </div>
        </div>
      )}

      {current && (
        <div className="border-b border-border">
          <div className="px-5 py-3 flex flex-wrap items-center gap-3 bg-muted/20">
            <div>
              <div className="text-sm font-medium">Current assurance run</div>
              <div className="text-[11px] text-muted-foreground">
                {current.trigger} ·{" "}
                {new Date(current.window_start).toLocaleString()} –{" "}
                {new Date(current.window_end).toLocaleString()}
              </div>
            </div>
            <span
              className={`ml-auto text-[11px] font-medium px-2 py-0.5 rounded-md border ${TONES[current.status] ?? TONES.QUEUED}`}
            >
              {current.status}
            </span>
          </div>
          {current.error && (
            <div className="px-5 py-3 text-sm text-destructive bg-destructive/5">
              {current.error}
            </div>
          )}
          {current.status === "COMPLETED" && (
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 px-5 py-4 border-t border-border bg-muted/20">
              {[
                ["Events", current.row_count],
                ["Matched", current.matched_count],
                ["Undercharged", current.undercharged_count],
                ["Overcharged", current.overcharged_count],
                ["Total variance", current.total_variance],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <div className="text-[10px] uppercase text-muted-foreground">
                    {label}
                  </div>
                  <div className="text-sm font-semibold tabular-nums">
                    {shown(value)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {currentId && (
        <div className="border-b border-border">
          <div className="px-5 py-3 flex flex-wrap items-center gap-3">
            <h3 className="text-sm font-semibold">Assurance results</h3>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className={`${fieldClass} ml-auto`}
            >
              <option value="">All statuses</option>
              <option value="MATCHED">Matched</option>
              <option value="UNDERCHARGED">Undercharged</option>
              <option value="OVERCHARGED">Overcharged</option>
              <option value="NO_MATCHING_TARIFF">No matching tariff</option>
              <option value="MISSING_REFERENCE_DATA">
                Missing reference data
              </option>
              <option value="ERROR">Error</option>
            </select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  {[
                    "Event",
                    "Called number",
                    "Service",
                    "Destination",
                    "Expected",
                    "Actual",
                    "Variance",
                    "Status",
                    "Event time",
                  ].map((head) => (
                    <th
                      key={head}
                      className="px-4 py-2 text-left font-medium whitespace-nowrap"
                    >
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {results?.rows.map((row) => (
                  <tr key={`${row.ordinal}-${row.event_id}`}>
                    <td className="px-4 py-2 font-medium whitespace-nowrap">
                      {row.event_id}
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      {shown(row.payload.called_number)}
                    </td>
                    <td className="px-4 py-2">{shown(row.service_type)}</td>
                    <td className="px-4 py-2">
                      {shown(row.payload.destination_zone)}
                    </td>
                    <td className="px-4 py-2 tabular-nums">
                      {shown(row.payload.expected_final_charge)}
                    </td>
                    <td className="px-4 py-2 tabular-nums">
                      {shown(row.payload.actual_charge)}
                    </td>
                    <td className="px-4 py-2 tabular-nums">
                      {shown(row.payload.charge_variance)}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`px-1.5 py-0.5 rounded border ${TONES[row.reconciliation_status] ?? TONES.QUEUED}`}
                      >
                        {row.reconciliation_status}
                      </span>
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      {row.event_time
                        ? new Date(row.event_time).toLocaleString()
                        : "—"}
                    </td>
                  </tr>
                ))}
                {!resultsLoading && results?.rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={9}
                      className="px-5 py-8 text-center text-muted-foreground"
                    >
                      No result rows for this run.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="px-5 py-3 flex items-center justify-between border-t border-border text-xs text-muted-foreground">
            <span>
              {resultsLoading ? "Loading…" : `${results?.total ?? 0} rows`}
            </span>
            <div className="flex gap-2">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 50))}
                className="rounded border border-border px-3 py-1 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                disabled={!results || offset + 50 >= results.total}
                onClick={() => setOffset(offset + 50)}
                className="rounded border border-border px-3 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}

      {runs.length > 0 && (
        <div>
          <div className="px-5 py-3 text-sm font-semibold border-b border-border">
            Recent Rating Assurance runs
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <tbody className="divide-y divide-border">
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    onClick={() => setSelectedId(run.id)}
                    className={`cursor-pointer hover:bg-muted/30 ${currentId === run.id ? "bg-primary/5" : ""}`}
                  >
                    <td className="px-5 py-3">
                      <div className="font-medium">{run.trigger} run</div>
                      <div className="text-[11px] text-muted-foreground">
                        {new Date(run.created_at).toLocaleString()} ·{" "}
                        {run.triggered_by_name ?? "Scheduler"}
                      </div>
                    </td>
                    <td className="px-5 py-3 tabular-nums">
                      {run.row_count.toLocaleString()} events
                    </td>
                    <td className="px-5 py-3 tabular-nums">
                      {run.exception_count.toLocaleString()} exceptions
                    </td>
                    <td className="px-5 py-3 text-right">
                      <span
                        className={`px-2 py-0.5 rounded border ${TONES[run.status] ?? TONES.QUEUED}`}
                      >
                        {run.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
