// ---------------------------------------------------------------------------
// Rule Report Sample — the records that actually tripped an auto-raised case.
//
// A case raised by the engine says "207,809 breached rows" and nothing about
// what any of them looked like, which leaves an analyst reading a number and
// then hunting for the report by hand. This shows the first few of those rows
// inline, with every field the rule's report carries, and a link into the full
// report for the rest.
//
// Shown ONLY for a case the engine raised from a rule (origin=auto_detected
// AND a rule id). A hand-raised case has no rule and therefore no report.
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { ExternalLink, Table2 } from "lucide-react";
import type { AssuranceCase } from "@/lib/cases";
import {
  fetchReconPage,
  fetchReconReports,
  type ReconPage,
} from "@/lib/assurance/reconciliation-api";

/** Rows shown inline. The point is a shape, not a dataset. */
const SAMPLE_SIZE = 5;

/**
 * Unfiltered rows fetched first, for the report's shape — its columns, its key
 * columns, and which statuses it can produce. Doubles as the fallback sample if
 * the per-status reads below come back empty.
 */
const PROBE_SIZE = 40;

/** Rows read per status. Only a few are needed; the rest is the full report. */
const PER_STATUS = 12;

/**
 * Status order when filling the sample.
 *
 * Breaches first — the case exists because of them — then the clean rows, which
 * earn their place by contrast: seeing a MATCH beside a MISMATCH is what shows
 * that the control is discriminating rather than failing everything.
 */
const STATUS_PRIORITY = [
  "MISMATCH",
  "TABLE1_MISSING",
  "TABLE2_MISSING",
  "RAW_MISSING",
  "PROCESSED_MISSING",
  "GAP",
  "DUPLICATE",
  "MATCH",
  "PRESENT",
];

const statusRank = (status: string) => {
  const i = STATUS_PRIORITY.indexOf(status.toUpperCase());
  return i === -1 ? STATUS_PRIORITY.length : i;
};

/** A row that reconciled cleanly — counted separately from the breach total. */
const CLEAN_STATUSES = new Set(["MATCH", "PRESENT"]);

export interface RuleReportSampleData {
  /** Column names, in report order. */
  columns: string[];
  rows: Record<string, unknown>[];
  /** Total breached rows the run produced — the denominator in "10 of N". */
  breachedTotal: number;
  /** Report key, for the link into the full report. Absent when mocked. */
  reportKey?: string;
  /** True when the numbers came from the fallback rather than the engine. */
  mocked: boolean;
}

/**
 * Whether this case has a rule report behind it at all.
 *
 * Both halves matter: `auto_detected` alone covers cases ingested from other
 * systems that carry no rule, and a rule id alone appears on hand-raised cases
 * that an analyst filed against a control.
 */
export const hasRuleReport = (c: AssuranceCase) =>
  c.origin === "auto_detected" && !!(c.ruleId ?? "").trim();

// --- Fallback ----------------------------------------------------------------
// Used when the reconciliation service can't be reached or has no report for
// this rule. Deliberately labelled on screen: a sample that cannot be told from
// live rows would misrepresent what the run found.

function mockSample(c: AssuranceCase): RuleReportSampleData {
  const columns = [
    "calling_party_number",
    "called_party_number",
    "event_time",
    "expected_value",
    "actual_value",
    "status",
  ];
  // A distinct subscriber and a spread of statuses, for the same reasons the
  // live sampler works that way: one repeated number says nothing about the
  // spread, and one repeated status nothing about what the control separates.
  const statuses = ["MISMATCH", "TABLE1_MISSING", "TABLE2_MISSING", "MATCH", "MISMATCH"];
  const rows = Array.from({ length: SAMPLE_SIZE }, (_, i) => ({
    calling_party_number: `98760000${String(i + 1).padStart(2, "0")}`,
    called_party_number: i % 3 === 0 ? "9812345678" : `8559${String(89284 + i * 7)}`,
    event_time: new Date(Date.parse(c.detectedAt) - i * 61_000).toISOString(),
    expected_value: (12 + (i % 5)).toFixed(2),
    actual_value: statuses[i] === "MATCH" ? (12 + (i % 5)).toFixed(2) : (50 + (i % 5) * 3).toFixed(2),
    status: statuses[i],
  }));
  return {
    columns,
    rows,
    breachedTotal: c.affectedCount || rows.length,
    mocked: true,
  };
}

// --- Loading -----------------------------------------------------------------

/**
 * One row per subject, up to the sample size.
 *
 * A breach repeats per subscriber — one wrong tariff produces a row per rated
 * event — so an unfiltered slice is the same number ten times over. That shows
 * the shape of the fault but not its spread; keeping the first row per distinct
 * subject makes the sample answer "who is affected" instead.
 *
 * The subject is the report's KEY columns, joined. Both sides of each key pair
 * take part, and nulls are dropped rather than stringified: a row missing from
 * one table carries its identity only on the other, so keying on a single
 * column would collapse every such row onto the same empty subject — which is
 * exactly what a sample of them must not do.
 */
/**
 * Pick the sample: one row per status in turn, and never the same subject
 * twice.
 *
 * Round-robin rather than "first N of the breached rows". A run's rows arrive
 * grouped, so taking a slice off the top gave five rows of one status about one
 * subscriber — true, but it showed neither what the control distinguishes nor
 * how far the fault spreads. Cycling the statuses covers the outcomes; the
 * subject check covers the spread.
 *
 * The subject is the report's KEY columns, joined, with nulls dropped: a row
 * missing from one table carries its identity only on the other, so keying on a
 * single column would collapse every such row onto the same empty subject.
 */
function pickSample(
  byStatus: Map<string, Record<string, unknown>[]>,
  columns: string[],
  keyColumns: string[] | undefined,
): Record<string, unknown>[] {
  // Falls back to the leading column for a report served before the backend
  // published its key list.
  const keys = keyColumns?.length ? keyColumns : columns.slice(0, 1);
  const subjectOf = (row: Record<string, unknown>) =>
    keys
      .map((k) => row[k])
      .filter((v) => v !== null && v !== undefined && v !== "")
      .join("|");

  const queues = [...byStatus.entries()]
    .sort(([a], [b]) => statusRank(a) - statusRank(b))
    .map(([, rows]) => [...rows]);

  const seen = new Set<string>();
  const picked: Record<string, unknown>[] = [];

  // Two passes over the queues: the first insists on an unseen subject, the
  // second accepts a repeat rather than leaving the table short. Coverage of
  // the statuses is worth more than absolute uniqueness once the report has
  // genuinely run out of distinct subjects.
  for (const strict of [true, false]) {
    let progressed = true;
    while (picked.length < SAMPLE_SIZE && progressed) {
      progressed = false;
      for (const queue of queues) {
        if (picked.length === SAMPLE_SIZE) break;
        while (queue.length) {
          const row = queue.shift()!;
          const subject = subjectOf(row);
          if (strict && seen.has(subject)) continue;
          seen.add(subject);
          picked.push(row);
          progressed = true;
          break;
        }
      }
    }
    if (picked.length === SAMPLE_SIZE) break;
  }
  return picked;
}

async function loadSample(c: AssuranceCase): Promise<RuleReportSampleData> {
  const ruleId = (c.ruleId ?? "").trim();
  const reports = await fetchReconReports();
  const report = reports.find((r) => r.ruleId === ruleId);
  if (!report?.available) throw new Error(`No generated report for ${ruleId}`);

  const probe: ReconPage = await fetchReconPage(report.key, {
    limit: PROBE_SIZE,
    offset: 0,
  });

  // One read per status. An unfiltered page is grouped by whatever order the
  // scan returned — for this rule, 120 consecutive TABLE1_MISSING rows — so
  // asking for each status by name is the only way to be sure the sample can
  // show all of them.
  const statuses = probe.statuses ?? [];
  const perStatus = await Promise.allSettled(
    statuses.map((status) =>
      fetchReconPage(report.key, { status, limit: PER_STATUS, offset: 0 }),
    ),
  );

  const byStatus = new Map<string, Record<string, unknown>[]>();
  perStatus.forEach((result, i) => {
    if (result.status !== "fulfilled" || !result.value.rows.length) return;
    byStatus.set(statuses[i], result.value.rows);
  });

  // Nothing came back per status — an older backend, or every read failed. Group
  // the probe's own rows instead so the section still shows real records.
  if (!byStatus.size) {
    for (const row of probe.rows) {
      const status = String(row.status ?? "—");
      (byStatus.get(status) ?? byStatus.set(status, []).get(status)!).push(row);
    }
  }

  const rows = pickSample(byStatus, probe.columns, probe.keyColumns);
  if (!rows.length) throw new Error("The latest run produced no rows");

  return {
    columns: probe.columns,
    rows,
    // The case counted the breach at the moment it was raised; the report is
    // the LATEST run, whose totals may since have moved. The case's number is
    // the one this case is about.
    breachedTotal: c.affectedCount || probe.total,
    reportKey: probe.key,
    mocked: false,
  };
}

// --- Rendering ---------------------------------------------------------------

const cell = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "NULL";
  return String(value);
};

const humanise = (column: string) =>
  column.replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());

export function RuleReportSample({ case: c }: { case: AssuranceCase }) {
  const [data, setData] = useState<RuleReportSampleData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Keyed on the case's identity rather than the object: the parent rebuilds
  // `c` on every refresh, and depending on it would refetch the report each
  // time a comment was posted.
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    loadSample(c)
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch((e: Error) => {
        // The section still renders, from the fallback — the case's own breach
        // count is real either way, and a blank panel would say less.
        if (cancelled) return;
        setError(e.message);
        setData(mockSample(c));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.id, c.ruleId, c.affectedCount]);

  if (!data) {
    return <p className="py-6 text-center text-xs text-muted-foreground">Loading sample…</p>;
  }

  const shown = data.rows.length;

  return (
    <div className="space-y-3 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {/* "of N breached" would misread the table now that it deliberately
            includes a matched row for contrast — so the sample size and the
            breach count are stated as the separate facts they are. */}
        <p className="text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">{shown}</span> sample record
          {shown === 1 ? "" : "s"}
          {c.ruleId ? (
            <>
              {" "}
              from <span className="font-mono text-foreground">{c.ruleId}</span>
            </>
          ) : null}{" "}
          · <span className="font-medium text-foreground">
            {data.breachedTotal.toLocaleString()}
          </span>{" "}
          breached in this run.
        </p>

        {data.reportKey ? (
          <Link
            to="/reports"
            search={{ report: data.reportKey }}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-muted/60"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            View full report
          </Link>
        ) : (
          <span className="text-[11px] text-muted-foreground">Full report unavailable</span>
        )}
      </div>

      {data.mocked && (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-foreground/85">
          Representative sample — the reconciliation service did not return this rule&apos;s report
          {error ? ` (${error})` : ""}. The breach count above is the case&apos;s own.
        </p>
      )}

      {/* Its own scroll container: a wide report must not push the dialog
          sideways. */}
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-max text-[11px]">
          <thead>
            <tr className="border-b border-border bg-muted/40 text-left">
              {data.columns.map((column) => (
                <th
                  key={column}
                  className="whitespace-nowrap px-3 py-2 font-medium uppercase tracking-wide text-muted-foreground"
                >
                  {humanise(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => (
              <tr key={i} className="border-b border-border/60 last:border-0">
                {data.columns.map((column) => {
                  const value = cell(row[column]);
                  const isStatus = column.toLowerCase() === "status";
                  return (
                    <td
                      key={column}
                      className={`whitespace-nowrap px-3 py-2 tabular-nums ${
                        value === "NULL" ? "text-muted-foreground/60" : "text-foreground/90"
                      }`}
                    >
                      {isStatus ? (
                        <span className="rounded-md bg-muted px-1.5 py-0.5 font-medium text-foreground/80">
                          {value}
                        </span>
                      ) : (
                        value
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
