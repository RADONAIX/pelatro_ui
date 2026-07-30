import { useT } from "@/lib/i18n";

/**
 * "Live · every Ns · updated HH:MM:SS" pill — a green status chip for screens
 * that auto-refresh on a timer. Shared so every module's live indicator matches.
 */
export function LiveBadge({
  intervalSec,
  lastUpdated,
  className,
}: {
  intervalSec: number;
  lastUpdated?: Date | null;
  className?: string;
}) {
  const t = useT();
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-3 py-1 text-xs font-medium text-success ${className ?? ""}`}
    >
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-70" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
      </span>
      <span className="font-semibold">{t("Live")}</span>
      <span>· {t("every")} {intervalSec}s</span>
      {lastUpdated && (
        <span className="hidden sm:inline">· {t("updated")} {lastUpdated.toLocaleTimeString()}</span>
      )}
    </span>
  );
}
