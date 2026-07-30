import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  Loader2,
  Upload,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  rejectedRowsUrl,
  useCanEditRules,
  useCommitImport,
  useImportCapabilities,
  useImportColumns,
  useImportHistory,
  usePreviewImport,
  useRuleSets,
} from "@/lib/rating/hooks";
import { RatingEmpty } from "@/components/rating/RatingState";
import type { ImportBatch, ImportPreview } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/rules/import")({
  component: ImportRulesPage,
});

type Step = "upload" | "map" | "done";

function ImportRulesPage() {
  const t = useT();
  const canEdit = useCanEditRules();
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: columns = [] } = useImportColumns();
  const { data: capabilities } = useImportCapabilities();
  const { data: ruleSets = [] } = useRuleSets();
  const { data: history = [], refetch: refetchHistory } = useImportHistory();

  const previewMutation = usePreviewImport();
  const commitMutation = useCommitImport();

  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [ruleSetId, setRuleSetId] = useState("");
  const [result, setResult] = useState<ImportBatch | null>(null);

  // Column options, grouped so a 40-entry list stays navigable.
  const columnOptions = useMemo(
    () => [
      { value: "", label: t("— ignore this column —") },
      ...columns.map((c) => ({
        value: c.key,
        label: `${c.kind === "HEADER" ? "Rule" : c.kind === "CONDITION" ? "Condition" : "Action"} · ${c.label}`,
      })),
    ],
    [columns, t],
  );

  const columnByKey = useMemo(
    () => new Map(columns.map((c) => [c.key, c])),
    [columns],
  );

  const runPreview = async (
    selected: File,
    withMapping?: Record<string, string | null>,
  ) => {
    try {
      const data = await previewMutation.mutateAsync({
        file: selected,
        mapping: withMapping,
      });
      setPreview(data);
      setMapping(data.mapping);
      setStep("map");
    } catch (err) {
      toast.error(t("Couldn't read that file"), {
        description: ratingError(err),
      });
    }
  };

  const onPick = (selected: File | null) => {
    if (!selected) return;
    setFile(selected);
    setResult(null);
    void runPreview(selected);
  };

  const remap = (heading: string, key: string) => {
    const next = { ...mapping, [heading]: key || null };
    setMapping(next);
    // Re-validate against the new mapping so the row outcomes shown always
    // match the mapping on screen — a preview that lags the controls is worse
    // than no preview.
    if (file) void runPreview(file, next);
  };

  const commit = async () => {
    if (!file) return;
    try {
      const batch = await commitMutation.mutateAsync({
        file,
        mapping,
        rule_set_id: ruleSetId || undefined,
        source_system: "FILE_IMPORT",
      });
      setResult(batch);
      setStep("done");
      void refetchHistory();
      toast.success(
        `${batch.imported_rows} ${t("rules imported")}`,
        batch.rejected_rows
          ? {
              description: `${batch.rejected_rows} ${t("rows rejected — download them below.")}`,
            }
          : undefined,
      );
    } catch (err) {
      toast.error(t("Import failed"), { description: ratingError(err) });
    }
  };

  const reset = () => {
    setStep("upload");
    setFile(null);
    setPreview(null);
    setMapping({});
    setResult(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  if (!canEdit) {
    return (
      <AppShell>
        <PageHeader title={t("Import Rules")} />
        <RatingEmpty
          icon={FileSpreadsheet}
          title="You don't have rule-authoring rights"
          description="Your role can view the rule catalogue but not import rules."
          action={
            <Link
              to="/rating/rules"
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition"
            >
              <ArrowLeft className="h-4 w-4" /> {t("Back to the catalogue")}
            </Link>
          }
        />
      </AppShell>
    );
  }

  const accepted = capabilities?.excel_available
    ? ".csv,.tsv,.json,.xml,.xlsx"
    : ".csv,.tsv,.json,.xml";

  return (
    <AppShell>
      <PageHeader
        title={t("Import Rules")}
        description={t(
          "Upload a tariff sheet — one row per priced combination. Each row becomes a draft rule; nothing is written until you commit.",
        )}
        info={t(
          "Column headings are matched automatically against the importer's vocabulary, including common vendor aliases. Correct any it gets wrong before committing.",
        )}
        actions={
          <Link
            to="/rating/rules"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
          >
            <ArrowLeft className="h-4 w-4" /> {t("Catalogue")}
          </Link>
        }
      />

      {/* --- Step 1: upload -------------------------------------------------- */}
      {step === "upload" && (
        <>
          <div className="bg-card border border-dashed border-border rounded-xl p-10 text-center">
            <span className="h-12 w-12 mx-auto rounded-xl bg-primary/10 text-primary flex items-center justify-center">
              <Upload className="h-6 w-6" />
            </span>
            <h2 className="mt-4 text-sm font-semibold text-foreground">
              {t("Choose a tariff sheet")}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground max-w-lg mx-auto leading-relaxed">
              {capabilities
                ? `${capabilities.formats.join(", ").toUpperCase()} · ${t("up to")} ${capabilities.max_upload_mb} MB · ${capabilities.max_rows.toLocaleString()} ${t("rows")}`
                : t("CSV, TSV, JSON or Excel.")}
            </p>
            {capabilities && !capabilities.excel_available && (
              <p className="mt-2 text-[11px] text-muted-foreground">
                {t(
                  "Excel support is unavailable on this server — save the sheet as CSV.",
                )}
              </p>
            )}
            <input
              ref={fileInput}
              type="file"
              accept={accepted}
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button
              onClick={() => fileInput.current?.click()}
              disabled={previewMutation.isPending}
              className="mt-5 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
            >
              {previewMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {t("Select file")}
            </button>
          </div>

          {/* The column spec doubles as the document you hand a vendor. */}
          <section className="mt-6 bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Columns the importer understands")}
              </h2>
              <p className="text-xs text-muted-foreground mt-1">
                {t(
                  "Headings are matched case-insensitively, ignoring spaces and underscores. Send this list to whoever produces the sheet.",
                )}
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40 text-left">
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Column")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Becomes")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Also accepted as")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {columns.map((c) => (
                    <tr key={c.key}>
                      <td className="px-4 py-2.5 align-top">
                        <span className="font-mono text-[12px] text-foreground">
                          {c.key}
                        </span>
                        {c.required && (
                          <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded border bg-destructive/10 text-destructive border-destructive/20">
                            {t("required")}
                          </span>
                        )}
                        {c.description && (
                          <div className="text-[11px] text-muted-foreground mt-0.5 max-w-xl">
                            {c.description}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2.5 align-top text-muted-foreground whitespace-nowrap">
                        {c.kind === "HEADER"
                          ? t("Rule field")
                          : c.kind === "CONDITION"
                            ? t("Condition")
                            : t("Action")}
                      </td>
                      <td className="px-4 py-2.5 align-top text-[11px] text-muted-foreground">
                        {c.aliases.length ? c.aliases.join(", ") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {/* --- Step 2: map and preview ----------------------------------------- */}
      {step === "map" && preview && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <StatTile
              icon={FileSpreadsheet}
              label={t("Rows in file")}
              value={String(preview.total_rows)}
            />
            <StatTile
              icon={CheckCircle2}
              label={t("Ready to import")}
              value={String(preview.valid_rows)}
            />
            <StatTile
              icon={XCircle}
              label={t("Will be rejected")}
              value={String(preview.rejected_rows)}
            />
          </div>

          {preview.missing_required.length > 0 && (
            <div className="rounded-xl border border-destructive/20 bg-destructive/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
              <div>
                <div className="text-sm font-semibold text-foreground">
                  {t("Required columns are not mapped")}
                </div>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {preview.missing_required.join(", ")} —{" "}
                  {t("map them below, or every row will be rejected.")}
                </p>
              </div>
            </div>
          )}

          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3 flex-wrap">
              <div>
                <h2 className="text-sm font-semibold text-foreground">
                  {t("Column mapping")}
                </h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {preview.filename} · {preview.headings.length} {t("columns")}
                </p>
              </div>
              {previewMutation.isPending && (
                <span className="text-xs text-muted-foreground inline-flex items-center gap-1.5">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
                  {t("re-validating…")}
                </span>
              )}
            </div>
            <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-3">
              {preview.headings.map((heading) => {
                const key = mapping[heading];
                const col = key ? columnByKey.get(key) : undefined;
                return (
                  <div key={heading} className="flex items-center gap-3">
                    <div className="w-40 shrink-0 min-w-0">
                      <div
                        className="text-sm text-foreground truncate"
                        title={heading}
                      >
                        {heading}
                      </div>
                      {!key && (
                        <div className="text-[11px] text-muted-foreground">
                          {t("ignored")}
                        </div>
                      )}
                    </div>
                    <span className="text-muted-foreground">→</span>
                    <div className="flex-1 min-w-0 flex items-center gap-1.5">
                      <Select
                        value={key ?? ""}
                        onChange={(v) => remap(heading, v)}
                        options={columnOptions}
                        minWidth={0}
                        className="w-full"
                        size="sm"
                      />
                      {col?.description && <InfoHint text={col.description} />}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Row preview")}
                {preview.truncated && (
                  <span className="ml-2 text-[11px] font-normal text-muted-foreground">
                    {t("first")} {preview.rows.length} {t("of")}{" "}
                    {preview.total_rows}
                  </span>
                )}
              </h2>
            </div>
            <div className="overflow-x-auto max-h-[28rem]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted/90 backdrop-blur">
                  <tr className="border-b border-border text-left">
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Row")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Becomes")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Outcome")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {preview.rows.map((row) => {
                    const rejected = row.status === "REJECTED";
                    const canonical = row.canonical as {
                      name?: string;
                      rule_type?: string;
                      conditions?: unknown[];
                      actions?: unknown[];
                    } | null;
                    return (
                      <tr
                        key={row.row_number}
                        className={rejected ? "bg-destructive/5" : ""}
                      >
                        <td className="px-4 py-2.5 align-top text-muted-foreground tabular-nums">
                          {row.row_number}
                        </td>
                        <td className="px-4 py-2.5 align-top">
                          {canonical ? (
                            <>
                              <div className="text-foreground">
                                {canonical.name}
                              </div>
                              <div className="text-[11px] text-muted-foreground">
                                {canonical.rule_type} ·{" "}
                                {canonical.conditions?.length ?? 0}{" "}
                                {t("conditions")} ·{" "}
                                {canonical.actions?.length ?? 0} {t("actions")}
                              </div>
                            </>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 align-top">
                          {rejected ? (
                            <div className="space-y-0.5">
                              {row.errors.map((e, i) => (
                                <div
                                  key={i}
                                  className="text-[12px] text-destructive flex items-start gap-1.5"
                                >
                                  <XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                                  <span>{e.message}</span>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <span className="text-[12px] text-success inline-flex items-center gap-1.5">
                              <CheckCircle2 className="h-3.5 w-3.5" />{" "}
                              {t("Ready")}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {t("Add to rule set")}
              </span>
              <Select
                value={ruleSetId}
                onChange={setRuleSetId}
                options={[
                  { value: "", label: t("None") },
                  ...ruleSets.map((r) => ({
                    value: r.id,
                    label: `${r.code} — ${r.name}`,
                  })),
                ]}
                minWidth={220}
                size="sm"
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={reset}
                className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
              >
                {t("Start over")}
              </button>
              <button
                onClick={commit}
                disabled={commitMutation.isPending || preview.valid_rows === 0}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {commitMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4" />
                )}
                {t("Import")} {preview.valid_rows} {t("rules")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- Step 3: outcome -------------------------------------------------- */}
      {step === "done" && result && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <StatTile
              icon={FileSpreadsheet}
              label={t("Rows")}
              value={String(result.total_rows)}
            />
            <StatTile
              icon={CheckCircle2}
              label={t("Imported")}
              value={String(result.imported_rows)}
            />
            <StatTile
              icon={XCircle}
              label={t("Rejected")}
              value={String(result.rejected_rows)}
            />
          </div>

          {result.rejected_rows > 0 && (
            <section className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <h2 className="text-sm font-semibold text-foreground">
                    {t("Why rows were rejected")}
                  </h2>
                  <ul className="mt-3 space-y-1.5">
                    {(result.summary?.reasons ?? []).map((r) => (
                      <li
                        key={r.message}
                        className="text-[13px] text-muted-foreground flex items-start gap-2"
                      >
                        <span className="text-[11px] font-semibold tabular-nums text-foreground min-w-8">
                          ×{r.count}
                        </span>
                        <span>{r.message}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <a
                  href={rejectedRowsUrl(result.id)}
                  className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
                >
                  <Download className="h-4 w-4" /> {t("Download rejected rows")}
                </a>
              </div>
              <p className="mt-4 text-xs text-muted-foreground">
                {t(
                  "The download has the original columns plus a reason, so the source team can correct it in place and re-send only those rows.",
                )}
              </p>
            </section>
          )}

          <div className="flex items-center gap-2">
            <Link
              to="/rating/rules"
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              {t("View the imported rules")}
            </Link>
            <button
              onClick={reset}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
            >
              <Upload className="h-4 w-4" /> {t("Import another file")}
            </button>
          </div>
        </div>
      )}

      {/* --- History --------------------------------------------------------- */}
      {history.length > 0 && step !== "map" && (
        <section className="mt-8 bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-border">
            <h2 className="text-sm font-semibold text-foreground">
              {t("Recent imports")}
            </h2>
          </div>
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border">
              {history.map((b) => (
                <tr key={b.id}>
                  <td className="px-5 py-3">
                    <div className="text-foreground">{b.filename}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {b.created_by_name ?? "—"} ·{" "}
                      {new Date(b.created_at).toLocaleString()}
                    </div>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground whitespace-nowrap">
                    {b.imported_rows} {t("imported")}
                    {b.rejected_rows > 0 &&
                      `, ${b.rejected_rows} ${t("rejected")}`}
                  </td>
                  <td className="px-5 py-3 text-right">
                    {b.rejected_rows > 0 && (
                      <a
                        href={rejectedRowsUrl(b.id)}
                        className="text-xs font-medium text-primary hover:underline inline-flex items-center gap-1"
                      >
                        <Download className="h-3.5 w-3.5" />{" "}
                        {t("rejected rows")}
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </AppShell>
  );
}
