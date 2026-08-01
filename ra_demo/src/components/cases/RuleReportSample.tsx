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
const SAMPLE_SIZE = 10;

/**
 * Rows fetched to fill that sample.
 *
 * The report page filters by ONE status, and "breached" is three of them for a
 * reconciliation, so the breach filter happens here — over a page big enough
 * that a healthy run's matches don't crowd the sample out.
 */
const FETCH_SIZE = 120;

/** A row that reconciled cleanly is not why the case exists. */
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
  const rows = Array.from({ length: SAMPLE_SIZE }, (_, i) => ({
    // A distinct subscriber per row, for the same reason distinctBySubject
    // exists: a column of one repeated number says nothing about the spread.
    calling_party_number: `98760000${String(i + 1).padStart(2, "0")}`,
    called_party_number: i % 3 === 0 ? "9812345678" : `8559${String(89284 + i * 7)}`,
    event_time: new Date(Date.parse(c.detectedAt) - i * 61_000).toISOString(),
    expected_value: (12 + (i % 5)).toFixed(2),
    actual_value: (50 + (i % 5) * 3).toFixed(2),
    status: i % 4 === 0 ? "MISMATCH" : "TABLE1_MISSING",
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
function distinctBySubject(
  rows: Record<string, unknown>[],
  columns: string[],
  keyColumns: string[] | undefined,
): Record<string, unknown>[] {
  // Falls back to the leading column for a report served before the backend
  // published its key list.
  const keys = keyColumns?.length ? keyColumns : columns.slice(0, 1);
  if (!keys.length) return rows.slice(0, SAMPLE_SIZE);

  const subjectOf = (row: Record<string, unknown>) =>
    keys
      .map((k) => row[k])
      .filter((v) => v !== null && v !== undefined && v !== "")
      .join("|");

  const seen = new Set<string>();
  const picked: Record<string, unknown>[] = [];
  for (const row of rows) {
    const subject = subjectOf(row);
    if (seen.has(subject)) continue;
    seen.add(subject);
    picked.push(row);
    if (picked.length === SAMPLE_SIZE) break;
  }
  // Fewer than SAMPLE_SIZE distinct subjects means fewer rows, deliberately:
  // padding back out with repeats would undo the point, and the line above the
  // table already says how many are shown.
  return picked;
}

async function loadSample(c: AssuranceCase): Promise<RuleReportSampleData> {
  const ruleId = (c.ruleId ?? "").trim();
  const reports = await fetchReconReports();
  const report = reports.find((r) => r.ruleId === ruleId);
  if (!report?.available) throw new Error(`No generated report for ${ruleId}`);

  const page: ReconPage = await fetchReconPage(report.key, {
    limit: FETCH_SIZE,
    offset: 0,
  });

  const breached = page.rows.filter(
    (row) => !CLEAN_STATUSES.has(String(row.status ?? "").toUpperCase()),
  );
  // If a run produced nothing but clean rows, show the page as it came rather
  // than an empty table — the case's own count still names the breach.
  const rows = distinctBySubject(
    breached.length ? breached : page.rows,
    page.columns,
    page.keyColumns,
  );

  return {
    columns: page.columns,
    rows,
    // The case counted the breach at the moment it was raised; the report is
    // the LATEST run, whose totals may since have moved. The case's number is
    // the one this case is about.
    breachedTotal: c.affectedCount || page.total,
    reportKey: page.key,
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
        <p className="text-[11px] text-muted-foreground">
          Showing <span className="font-medium text-foreground">{shown}</span> of{" "}
          <span className="font-medium text-foreground">
            {data.breachedTotal.toLocaleString()}
          </span>{" "}
          breached records
          {c.ruleId ? (
            <>
              {" "}
              from <span className="font-mono text-foreground">{c.ruleId}</span>
            </>
          ) : null}
          .
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
