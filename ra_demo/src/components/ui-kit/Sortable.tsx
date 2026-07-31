import { useState } from "react";
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react";

// ---------------------------------------------------------------------------
// Reusable client-side table sorting: a tiny hook that tracks the active
// column + direction, a comparator that handles numbers/dates/text, and a
// sortable <th> that shows the current sort state.
// ---------------------------------------------------------------------------

export type SortDir = "asc" | "desc";

export function useSort(initialKey: string | null = null, initialDir: SortDir = "asc") {
  const [sortKey, setSortKey] = useState<string | null>(initialKey);
  const [sortDir, setSortDir] = useState<SortDir>(initialDir);
  // Click a column to cycle through three states: unsorted → asc → desc →
  // unsorted. Clicking a different column starts it ascending.
  const onSort = (key: string) => {
    if (sortKey !== key) { setSortKey(key); setSortDir("asc"); }
    else if (sortDir === "asc") setSortDir("desc");
    else { setSortKey(null); setSortDir("asc"); } // desc → back to unsorted
  };
  return { sortKey, sortDir, onSort };
}

// Compare two cell values: numeric when both look like numbers, otherwise a
// locale-aware string compare (nulls/blank sort last).
function compare(a: unknown, b: unknown): number {
  const aEmpty = a == null || a === "";
  const bEmpty = b == null || b === "";
  if (aEmpty && bEmpty) return 0;
  if (aEmpty) return 1;
  if (bEmpty) return -1;
  const as = String(a);
  const bs = String(b);
  const an = Number(as.replace(/[^0-9.\-]/g, ""));
  const bn = Number(bs.replace(/[^0-9.\-]/g, ""));
  const bothNumeric = /\d/.test(as) && /\d/.test(bs) && !isNaN(an) && !isNaN(bn) && /^[\s$₹€£0-9.,\-%]+$/.test(as) && /^[\s$₹€£0-9.,\-%]+$/.test(bs);
  if (bothNumeric) return an - bn;
  return as.localeCompare(bs, undefined, { numeric: true, sensitivity: "base" });
}

/** Return a sorted copy of `rows`; `accessor` maps (row, key) → the value to compare. */
export function sortRows<T>(
  rows: T[],
  key: string | null,
  dir: SortDir,
  accessor: (row: T, key: string) => unknown,
): T[] {
  if (!key) return rows;
  const arr = [...rows];
  arr.sort((a, b) => compare(accessor(a, key), accessor(b, key)));
  if (dir === "desc") arr.reverse();
  return arr;
}

/** A sortable table header cell. `thClassName` fully controls the <th> styling. */
export function SortHeader({
  label,
  colKey,
  activeKey,
  dir,
  onSort,
  thClassName,
  justify = "start",
}: {
  label: React.ReactNode;
  colKey: string;
  activeKey: string | null;
  dir: SortDir;
  onSort: (key: string) => void;
  thClassName?: string;
  justify?: "start" | "center" | "end";
}) {
  const active = activeKey === colKey;
  const j = justify === "center" ? "justify-center" : justify === "end" ? "justify-end" : "justify-start";
  return (
    <th className={thClassName}>
      <button
        type="button"
        onClick={() => onSort(colKey)}
        title="Sort"
        className={`inline-flex items-center gap-1 w-full ${j} [text-transform:inherit] transition select-none cursor-pointer hover:text-foreground ${active ? "text-foreground font-semibold" : ""}`}
      >
        <span>{label}</span>
        {active ? (
          dir === "asc"
            ? <ChevronUp className="h-3 w-3 shrink-0" />
            : <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          // Idle indicator so every sortable column reads as sortable.
          <ChevronsUpDown className="h-3 w-3 shrink-0 opacity-40" />
        )}
      </button>
    </th>
  );
}
