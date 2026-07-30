import { Link } from "@tanstack/react-router";
import { Download } from "lucide-react";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { useDownloads } from "@/lib/downloads";

/**
 * Header entry point for the Download Center: an icon button (with a live badge
 * counting in-flight downloads) that navigates to the full /downloads page.
 */
export function DownloadCenter() {
  const t = useT();
  const { activeCount, exportsEnabled } = useDownloads();

  // Bulk exports turned off server-side (503) → hide the entry point entirely.
  if (!exportsEnabled) return null;

  return (
    <Tooltip label={t("Download center")} side="bottom">
      <Link
        to="/downloads"
        className="relative h-9 w-9 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground transition"
        aria-label={t("Download center")}
        activeProps={{ className: "text-primary bg-primary/10" }}
      >
        <Download className="h-4 w-4" />
        {activeCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 h-4 min-w-4 px-1 rounded-full bg-primary text-primary-foreground text-[9px] font-semibold flex items-center justify-center">
            {activeCount}
          </span>
        )}
      </Link>
    </Tooltip>
  );
}
