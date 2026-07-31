/**
 * The action bar that appears once rules are ticked in the catalogue.
 *
 * "Delete" is the word the operator uses, so it is the word on the button — but
 * the backend does the safest thing that achieves it, and this has to say so
 * before the click rather than after. A rule that has never been published is
 * removed; one with history is *retired*, because a rating result from March
 * still references it and "why was this call charged 0.02?" must stay
 * answerable. Retiring takes it out of the next snapshot, which is what the
 * operator actually wanted.
 *
 * Preview is not decoration here. It is the same call with `dry_run`, so the
 * confirmation shows exactly which rules would be deleted and which retired,
 * from the server, rather than this component guessing from a status column.
 */
import { useState } from "react";
import { AlertTriangle, Loader2, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import { useBulkAction, useCanApproveRules } from "@/lib/rating/hooks";
import type { BulkResponse } from "@/lib/rating/types";

export function BulkDeleteBar({
  ruleKeys,
  onClear,
  onDone,
}: {
  ruleKeys: string[];
  onClear: () => void;
  onDone: () => void;
}) {
  const t = useT();
  const canApprove = useCanApproveRules();
  const remove = useBulkAction("delete");

  const [preview, setPreview] = useState<BulkResponse | null>(null);
  // The exact selection the preview describes. Without this, previewing 25 and
  // then unticking five leaves a confirmation on screen for a set that is no
  // longer the one Remove would act on — the worst possible state for the one
  // action that cannot be undone.
  const [previewedKeys, setPreviewedKeys] = useState("");
  const [comment, setComment] = useState("");

  if (ruleKeys.length === 0) return null;

  const signature = [...ruleKeys].sort().join(",");
  const previewIsCurrent = preview !== null && previewedKeys === signature;

  const run = async (dryRun: boolean) => {
    try {
      const data = await remove.mutateAsync({
        rule_keys: ruleKeys,
        comment,
        dry_run: dryRun,
        atomic: true,
      });
      if (dryRun) {
        setPreview(data);
        setPreviewedKeys(signature);
        return;
      }
      const deleted = data.counts.DELETED ?? 0;
      const retired = data.counts.RETIRED ?? 0;
      toast.success(
        [
          deleted ? `${deleted} ${t("deleted")}` : "",
          retired ? `${retired} ${t("retired")}` : "",
        ]
          .filter(Boolean)
          .join(", ") || t("Nothing changed"),
      );
      setPreview(null);
      setPreviewedKeys("");
      onClear();
      onDone();
    } catch (err) {
      toast.error(t("Couldn't remove those rules"), {
        description: ratingError(err),
      });
    }
  };

  const deleted = preview?.counts.DELETED ?? 0;
  const retired = preview?.counts.RETIRED ?? 0;
  const held = preview?.blocked ?? [];

  return (
    <div className="sticky bottom-4 z-10 mt-4">
      <div className="bg-card border border-border rounded-xl shadow-lg p-4 space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <span className="text-sm font-medium text-foreground">
            {ruleKeys.length} {t("selected")}
          </span>
          <button
            onClick={onClear}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition"
          >
            <X className="h-3.5 w-3.5" /> {t("Clear")}
          </button>
        </div>

        {!canApprove && (
          <p className="text-[12px] text-warning flex items-start gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              "Removing a rule that is already live needs rule-approval rights.",
            )}
          </p>
        )}

        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={t(
            "Why (kept on the audit trail after the rule is gone)",
          )}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />

        {preview && !previewIsCurrent && (
          <p className="text-[12px] text-muted-foreground">
            {t(
              "The selection changed since that preview. Preview again to see what would happen now.",
            )}
          </p>
        )}

        {previewIsCurrent && preview && (
          <div className="rounded-lg border border-border p-3 space-y-2">
            <p className="text-[13px] text-foreground">
              {deleted > 0 && (
                <>
                  <span className="font-semibold tabular-nums">{deleted}</span>{" "}
                  {t("will be deleted outright")}
                  {retired > 0 && " · "}
                </>
              )}
              {retired > 0 && (
                <>
                  <span className="font-semibold tabular-nums">{retired}</span>{" "}
                  {t("will be retired — they have history that is referenced")}
                </>
              )}
              {deleted === 0 && retired === 0 && t("Nothing would change.")}
            </p>
            {retired > 0 && (
              <p className="text-[11px] text-muted-foreground">
                {t(
                  "A retired rule stops reaching new snapshots but its row survives, so past charges stay explainable.",
                )}
              </p>
            )}
            {held.map((b, i) => (
              <p
                key={i}
                className="text-[12px] text-destructive flex items-start gap-1.5"
              >
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span>
                  <span className="tabular-nums font-semibold">×{b.count}</span>{" "}
                  {b.reason || b.outcome}
                  {b.examples.length > 0 && (
                    <span className="font-mono text-muted-foreground">
                      {" "}
                      ({b.examples.join(", ")})
                    </span>
                  )}
                </span>
              </p>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3 justify-end flex-wrap">
          {/* Why Remove is disabled, in the open. A greyed-out button whose only
              explanation is a hover tooltip reads as a broken screen, and the
              person hitting it concludes the feature does not work. */}
          {!previewIsCurrent && (
            <p className="text-[12px] text-muted-foreground mr-auto">
              {t(
                "Preview first — removing rules cannot be undone, so the confirmation shows exactly what would happen.",
              )}
            </p>
          )}
          <button
            onClick={() => void run(true)}
            disabled={remove.isPending}
            className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-40 ${
              previewIsCurrent
                ? "border border-border hover:bg-muted"
                : "bg-primary text-primary-foreground hover:opacity-90"
            }`}
          >
            {remove.isPending && !previewIsCurrent && (
              <Loader2 className="h-4 w-4 animate-spin" />
            )}
            {t("Preview")}
          </button>
          <button
            onClick={() => void run(false)}
            disabled={remove.isPending || !previewIsCurrent}
            className="inline-flex items-center gap-2 rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground transition hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {remove.isPending && previewIsCurrent ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Trash2 className="h-4 w-4" />
            )}
            {t("Remove")} {ruleKeys.length}
          </button>
        </div>
      </div>
    </div>
  );
}
