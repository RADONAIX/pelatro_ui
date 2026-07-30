import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { useT } from "@/lib/i18n";
import type { ValidationReport } from "@/lib/rating/types";

const TONE = {
  ERROR: { icon: XCircle, cls: "text-destructive", bg: "bg-destructive/10" },
  WARNING: {
    icon: AlertTriangle,
    cls: "text-warning-foreground",
    bg: "bg-warning/15",
  },
  INFO: { icon: Info, cls: "text-info", bg: "bg-info/10" },
} as const;

/** Collect the `path` of every ERROR so the builders can highlight those rows. */
export function errorPathsOf(
  report: ValidationReport | undefined,
): Set<string> {
  return new Set(
    (report?.issues ?? [])
      .filter((i) => i.severity === "ERROR" && i.path)
      .map((i) => i.path),
  );
}

export function ValidationPanel({
  report,
}: {
  report: ValidationReport | undefined;
}) {
  const t = useT();
  if (!report) return null;

  if (report.valid && report.warning_count === 0) {
    return (
      <div className="rounded-xl border border-success/20 bg-success/10 p-4 flex items-start gap-3">
        <CheckCircle2 className="h-5 w-5 text-success shrink-0 mt-0.5" />
        <div>
          <div className="text-sm font-semibold text-foreground">
            {t("Validation passed")}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">
            {t("No structural errors. This rule can be submitted for review.")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center gap-3 flex-wrap">
        <span className="text-sm font-semibold text-foreground">
          {t("Validation")}
        </span>
        {report.error_count > 0 && (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-md border bg-destructive/10 text-destructive border-destructive/20">
            {report.error_count} {t("errors")}
          </span>
        )}
        {report.warning_count > 0 && (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-md border bg-warning/15 text-warning-foreground border-warning/30">
            {report.warning_count} {t("warnings")}
          </span>
        )}
        {report.valid && (
          <span className="text-[11px] text-muted-foreground">
            {t("Warnings do not block submission.")}
          </span>
        )}
      </div>
      <ul className="divide-y divide-border">
        {report.issues.map((issue, i) => {
          const tone = TONE[issue.severity];
          const Icon = tone.icon;
          return (
            <li
              key={`${issue.code}-${i}`}
              className="px-4 py-3 flex items-start gap-3"
            >
              <span
                className={`h-6 w-6 shrink-0 rounded-md flex items-center justify-center ${tone.bg}`}
              >
                <Icon className={`h-3.5 w-3.5 ${tone.cls}`} />
              </span>
              <div className="min-w-0">
                <div className="text-sm text-foreground">{issue.message}</div>
                {issue.hint && (
                  <div className="text-[12px] text-muted-foreground mt-0.5">
                    {issue.hint}
                  </div>
                )}
                {issue.path && (
                  <div className="text-[11px] font-mono text-muted-foreground/70 mt-0.5">
                    {issue.path}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
