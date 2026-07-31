import type { ReactNode } from "react";
import { AlertCircle, Loader2, ServerCrash } from "lucide-react";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";

// Shared loading / error / empty panels for the rating screens, so a backend
// that isn't running yet produces one clear explanation instead of a blank page
// or a red console trace.

export function RatingLoading({ label = "Loading…" }: { label?: string }) {
  const t = useT();
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      {t(label)}
    </div>
  );
}

export function RatingError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const t = useT();
  const status = (error as { response?: { status?: number } })?.response
    ?.status;
  const offline = status === undefined;
  return (
    <div className="bg-card border border-border rounded-xl p-6 flex items-start gap-4">
      <span className="h-10 w-10 shrink-0 rounded-lg bg-destructive/10 text-destructive flex items-center justify-center">
        {offline ? (
          <ServerCrash className="h-5 w-5" />
        ) : (
          <AlertCircle className="h-5 w-5" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-foreground">
          {offline
            ? t("The Rating Assurance service is not reachable")
            : t("Couldn't load this view")}
        </div>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          {offline
            ? t(
                "Start it with `uvicorn app.main:app --port 8010` in ra_rating_backend, or check that nginx proxies /api/rating.",
              )
            : ratingError(error)}
        </p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-3 inline-flex items-center rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted transition"
          >
            {t("Try again")}
          </button>
        )}
      </div>
    </div>
  );
}

export function RatingEmpty({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: typeof AlertCircle;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  const t = useT();
  return (
    <div className="bg-card border border-border rounded-xl py-14 px-6 flex flex-col items-center text-center">
      <span className="h-12 w-12 rounded-xl bg-primary/10 text-primary flex items-center justify-center">
        <Icon className="h-6 w-6" />
      </span>
      <div className="mt-4 text-sm font-semibold text-foreground">
        {t(title)}
      </div>
      <p className="mt-1 text-sm text-muted-foreground max-w-md leading-relaxed">
        {t(description)}
      </p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** A neutral "this arrives in a later phase" panel for the stub screens. */
export function RatingPhasePanel({
  phase,
  blurb,
}: {
  phase: number;
  blurb: string;
}) {
  const t = useT();
  return (
    <div className="bg-card border border-border border-dashed rounded-xl p-8 text-center">
      <div className="text-xs font-semibold tracking-widest text-primary/80">
        {t("PHASE")} {phase}
      </div>
      <p className="mt-2 text-sm text-muted-foreground max-w-xl mx-auto leading-relaxed">
        {t(blurb)}
      </p>
    </div>
  );
}
