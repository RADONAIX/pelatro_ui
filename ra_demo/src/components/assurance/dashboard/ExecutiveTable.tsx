import { formatCr, type Finding } from "@/lib/assurance/dashboard-config";

// Highest Revenue Impact Findings — five rows, read-only. No workflow, no
// assignee, no priority, no actions: this is the executive read-out, not the
// case queue (Investigations owns that).

export function ExecutiveTable({ findings }: { findings: Finding[] }) {
  const max = Math.max(...findings.map((f) => f.impactCr), 1);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border/70">
            <th className="px-5 py-2.5 text-left text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
              Finding
            </th>
            <th className="px-5 py-2.5 text-left text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
              Source
            </th>
            <th className="px-5 py-2.5 text-left text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
              Category
            </th>
            <th className="px-5 py-2.5 text-right text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
              Revenue Impact
            </th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => (
            <tr
              key={f.finding}
              className="border-b border-border/50 last:border-0 hover:bg-muted/40"
            >
              <td className="px-5 py-3 font-medium text-foreground">
                {f.finding}
              </td>
              <td className="px-5 py-3 text-muted-foreground">{f.source}</td>
              <td className="px-5 py-3">
                <span className="inline-flex items-center rounded-md border border-border/70 bg-muted/50 px-2 py-0.5 text-[11px] text-muted-foreground">
                  {f.category}
                </span>
              </td>
              <td className="px-5 py-3">
                <div className="flex items-center justify-end gap-3">
                  <span
                    className="hidden h-[6px] rounded-full sm:block"
                    style={{
                      width: `${Math.max(12, (f.impactCr / max) * 96)}px`,
                      background: "var(--viz-leakage)",
                    }}
                    aria-hidden
                  />
                  <span className="tabular w-[86px] text-right font-semibold text-foreground">
                    {formatCr(f.impactCr)}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
