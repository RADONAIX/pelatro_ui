import { createFileRoute, Navigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import {
  TrendingUp,
  AlertTriangle,
  CheckCircle2,
  DollarSign,
  ExternalLink,
  RefreshCw,
} from "lucide-react";

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

// Superset host the browser loads the iframe from (override per env if needed).
const SUPERSET_DOMAIN = import.meta.env.VITE_SUPERSET_URL ?? "http://10.200.37.142:8088";
const DASHBOARD_ID = "357ecae5-5f29-49f8-8dbd-b3f8d3f6be63";
// Guest-token endpoint on OUR backend. Same base as auth: localhost in dev,
// empty (relative -> nginx proxies /api) in the single-origin production build.
const GUEST_TOKEN_URL = `${import.meta.env.VITE_AUTH_API_BASE ?? ""}/api/superset/guest-token`;

function DashboardPage() {
  const t = useT();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [embedError, setEmbedError] = useState<string | null>(null);
  // Callback ref kept in state: the embed effect re-runs the moment the mount
  // node is actually attached (it may mount a tick after `authed` flips while
  // the AppShell waits for the auth context to hydrate).
  const [mountNode, setMountNode] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    setAuthed(!!sessionStorage.getItem("radonaix_token"));
  }, []);

  useEffect(() => {
    if (!authed || !mountNode) return;

    let cancelled = false;
    mountNode.innerHTML = "";
    setEmbedError(null);

    // The SDK is CommonJS and touches the DOM, so import it lazily on the
    // client only — a top-level import crashes Start's SSR with
    // "Named export 'embedDashboard' not found".
    import("@superset-ui/embedded-sdk")
      .then(({ embedDashboard }) =>
        embedDashboard({
          id: DASHBOARD_ID,
          supersetDomain: SUPERSET_DOMAIN,
          mountPoint: mountNode,
          fetchGuestToken: async () => {
            const res = await fetch(GUEST_TOKEN_URL);

            if (!res.ok) {
              throw new Error(`Guest token API failed with status ${res.status}`);
            }

            const data = await res.json();

            if (!data.token) {
              throw new Error("Guest token not found in API response");
            }

            return data.token;
          },
          dashboardUiConfig: {
            hideTitle: true,
            hideChartControls: true,
            hideTab: true,
            urlParams: {
              standalone: "3",
              show_filters: "0",
              expand_filters: "0",
            },
          },
        }),
      )
      .then(() => {
        if (cancelled) return;
        const iframe = mountNode.querySelector("iframe");
        if (iframe) {
          iframe.style.width = "100%";
          iframe.style.height = "720px";
          iframe.style.border = "0";
        }
      })
      .catch((err: any) => {
        if (cancelled) return;
        console.error("Superset embed error:", err);
        setEmbedError(err?.message || "Superset embedded authentication failed");
      });

    return () => { cancelled = true; };
  }, [authed, refreshKey, mountNode]);

  if (authed === null) return null;
  if (!authed) return <Navigate to="/login" />;

  const kpis = [
    {
      label: "Assured Revenue (24h)",
      value: "$8.42M",
      change: "+2.4%",
      icon: DollarSign,
      tone: "text-success",
    },
    {
      label: "Reconciliation Match",
      value: "99.92%",
      change: "+0.03%",
      icon: CheckCircle2,
      tone: "text-success",
    },
    {
      label: "Open Leakage Risk",
      value: "$112.4K",
      change: "-8.1%",
      icon: TrendingUp,
      tone: "text-warning",
    },
    {
      label: "Critical Alerts",
      value: "3",
      change: "+1",
      icon: AlertTriangle,
      tone: "text-destructive",
    },
  ];

  return (
    <AppShell>
      <PageHeader
        title={t("Dashboard & KPIs")}
        description=""
        info={t("Live Revenue Assurance KPIs and the embedded Superset analytics dashboard.")}
      />

      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div>
            <h3 className="font-semibold text-foreground">
              {t("Embedded Analytics — Superset Dashboard")}
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              {t("Live Superset view embedded from the analytics platform.")}
            </p>
          </div>

          <Tooltip label={t("Refresh the dashboard")} side="bottom">
            <button
              onClick={() => setRefreshKey((k) => k + 1)}
              className="h-9 px-3 inline-flex items-center gap-2 rounded-lg border border-border text-sm hover:bg-muted"
            >
              <RefreshCw className="h-4 w-4" /> {t("Refresh")}
            </button>
          </Tooltip>
        </div>

        <div className="bg-background h-[720px] relative">
          <div
            ref={setMountNode}
            id="superset-container"
            className="w-full h-full"
          />

          {embedError && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-6 bg-background">
              <AlertTriangle className="h-6 w-6 text-warning" />
              <div className="text-sm font-medium text-foreground">
                {t("Couldn't load the embedded dashboard")}
              </div>
              <div className="text-xs text-muted-foreground max-w-md">
                {embedError}
              </div>
              <a
                href={`${SUPERSET_DOMAIN}/superset/dashboard/${DASHBOARD_ID}/`}
                target="_blank"
                rel="noreferrer"
                className="mt-1 h-9 px-3 inline-flex items-center gap-2 rounded-lg border border-border text-sm hover:bg-muted"
              >
                <ExternalLink className="h-4 w-4" /> {t("Open in Superset")}
              </a>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}

export default DashboardPage;