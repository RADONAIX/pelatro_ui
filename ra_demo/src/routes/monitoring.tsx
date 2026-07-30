import { createFileRoute, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Cpu, Database, Monitor, Server, ServerCog } from "lucide-react";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/monitoring")({ component: MonitoringPage });

// Grafana is served on the same HTTPS origin behind nginx at /grafana/.
// Override with VITE_GRAFANA_URL if Grafana runs on a different host (e.g. local dev).
const GRAFANA_URL = import.meta.env.VITE_GRAFANA_URL ?? "/grafana";

// The ServerOps monitoring app (Complete_Monitoring_NoAuth) runs as its own
// Next.js service, proxied same-origin at /serverops/. Embedded in the Report
// Servers section as the "Server Operations" tab.
const SERVEROPS_URL = import.meta.env.VITE_SERVEROPS_URL ?? "/serverops/";

type MonIcon = typeof Cpu;
interface MonTab {
  id: string;
  label: string;
  // Grafana dashboard uid for this tab. When unset (and no `embed`), the panel
  // shows a "not configured" placeholder.
  uid?: string;
  varServer?: string; // optional `var-server` template value
  // A ready-made iframe URL to embed instead of a Grafana dashboard (e.g. the
  // ServerOps app). Takes precedence over `uid`.
  embed?: string;
}
interface MonCategory {
  title: string;
  description: string;
  icon: MonIcon;
  tabs: MonTab[];
}

// System Monitoring sections — selected from the sidebar via /monitoring?view=<key>.
// Each tab embeds a Grafana dashboard: `uid` = the dashboard, `varServer` = the
// Prometheus `server` label of that host (deploy/prometheus/targets/nodes.json).
// The System dashboard (uid "radonaix-system") is per-server via var-server, so
// every host reuses it. A tab with NO uid = a box not provisioned yet → clean
// "not configured" placeholder (slaves + Report Server 2 don't exist yet).
const SYSTEM_UID = "radonaix-system";
const API_UID = "radonaix-api";

// Canonical Grafana slugs for each dashboard uid. We embed the FULL
// /d/{uid}/{slug} path on purpose: a slug-less (or wrong-slug) URL makes Grafana
// do a client-side redirect to add the slug, and that redirect DROPS the
// ?var-server= param. With no server value, `$server` falls back to match-all and
// every server's gauge renders at once (the "3 gauges on SDP/Report" bug). The
// full slug URL is served directly — no redirect — so var-server always sticks.
// Keep in sync with the dashboard titles (deploy/grafana/dashboards/*.json):
//   /api/search?type=dash-db → the `url` field is the source of truth.
const SLUGS: Record<string, string> = {
  [SYSTEM_UID]: "radonaix-e28094-system-node-exporter",
  [API_UID]: "radonaix-e28094-api",
};
const dashUrl = (uid: string) => `${GRAFANA_URL}/d/${uid}/${SLUGS[uid] ?? ""}`;

// LAN IP per Prometheus `server` label (deploy/prometheus/targets/nodes.json).
// Shown beside each tab name; keyed by tab.varServer so there's a single source
// of truth. Tabs without a provisioned server (MSC, Exception, slaves, RS2) have
// no varServer → no IP shown.
const SERVER_IPS: Record<string, string> = {
  "app-air": "10.200.37.133", // AIR app server
  "rpt-master": "10.200.37.142", // SDP + Report Server 1 (app_db)
  "ch-master": "10.200.36.69", // ClickHouse + Postgres master
};

const CATEGORIES: Record<string, MonCategory> = {
  applications: {
    title: "Applications",
    description: "AIR · SDP · MSC · Exception — node_exporter system health",
    icon: Cpu,
    tabs: [
      // AIR runs on 133; SDP runs on 142, which IS the reporting server — one box,
      // one node target (rpt-master), so the SDP tab and Report Server 1 show the
      // same host (no duplicate series). Exception (APP 4) runs on 69 — the SAME
      // box as the DB (ch-master), so it reuses that node target (never add a 2nd
      // label to the same endpoint → duplicate scrape). MSC isn't provisioned yet.
      { id: "air", label: "APP 1 (AIR)", uid: SYSTEM_UID, varServer: "app-air" },
      { id: "sdp", label: "APP 2 (SDP)", uid: SYSTEM_UID, varServer: "rpt-master" },
      { id: "msc", label: "APP 3 (MSC)" },
      { id: "exception", label: "APP 4 (Exception)", uid: SYSTEM_UID, varServer: "ch-master" },
    ],
  },
  databases: {
    title: "Databases",
    description: "Clickhouse & Postgres master/slave health — system dashboards",
    icon: Database,
    tabs: [
      // ClickHouse + Postgres masters share one box (69) today, so both show the
      // 69 host metrics. When Postgres moves to its own server, add a pg-master
      // node target and repoint this tab's varServer.
      { id: "ch-master", label: "Clickhouse Master", uid: SYSTEM_UID, varServer: "ch-master" },
      { id: "ch-slave", label: "Clickhouse Slave" },
      { id: "pg-master", label: "Postgres Master", uid: SYSTEM_UID, varServer: "ch-master" },
      { id: "pg-slave", label: "Postgres Slave" },
    ],
  },
  reportservers: {
    title: "Report Servers",
    description: "Report Server 1 & 2 — system + API health",
    icon: Server,
    tabs: [
      // Report Server 1 (142) runs the API + app_db, so it shows BOTH dashboards.
      { id: "rs1-system", label: "Report Server 1 · System", uid: SYSTEM_UID, varServer: "rpt-master" },
      { id: "rs1-api", label: "Report Server 1 · API", uid: API_UID },
      { id: "rs-2", label: "Report Server 2" },
    ],
  },
  // Its own section (like Applications / Databases / Report Servers), embedding the
  // ServerOps app full-panel — it has its own internal nav, so a single tab (the
  // tab strip is hidden for single-tab sections).
  serverops: {
    title: "Server Operations",
    description: "Live service status & control across the fleet — powered by ServerOps",
    icon: ServerCog,
    tabs: [{ id: "fleet", label: "Fleet", embed: SERVEROPS_URL }],
  },
};

const DEFAULT_VIEW = "applications";

function MonitoringPage() {
  const t = useT();
  const viewParam = useRouterState({
    select: (s) => (s.location.search as { view?: string } | undefined)?.view,
  });
  const view = viewParam && CATEGORIES[viewParam] ? viewParam : DEFAULT_VIEW;
  const category = CATEGORIES[view];
  const CatIcon = category.icon;

  const [tabId, setTabId] = useState(category.tabs[0].id);
  // Reset to the first tab whenever the category (sidebar section) changes.
  useEffect(() => { setTabId(category.tabs[0].id); }, [view]);
  const tab = category.tabs.find((x) => x.id === tabId) ?? category.tabs[0];

  // A tab embeds either a ready-made URL (`embed`, e.g. ServerOps) or a Grafana
  // dashboard (`uid`); anything else falls through to the not-configured panel.
  const embedSrc = tab.embed
    ? tab.embed
    : tab.uid
      ? `${dashUrl(tab.uid)}?kiosk&theme=light&refresh=30s${tab.varServer ? `&var-server=${tab.varServer}` : ""}`
      : null;

  return (
    <AppShell>
      <PageHeader
        title={t("System Monitoring")}
        description={t("Live system and application health, powered by Prometheus + Grafana — embedded below.")}
        info={t("System and application health metrics, powered by Prometheus and Grafana.")}
      />

      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        {/* Section header — icon + title + description. Hidden for Server
            Operations, which embeds a full app that carries its own header. */}
        {view !== "serverops" && (
          <div className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-border">
            <div className="flex items-center gap-3 min-w-0">
              <span className="h-10 w-10 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                <CatIcon className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <h2 className="font-semibold text-foreground leading-tight">{t(category.title)}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">{t(category.description)}</p>
              </div>
            </div>
          </div>
        )}

        {/* Tab strip — the section's dashboards. Hidden for single-tab sections
            (e.g. Server Operations, which embeds a full app with its own nav). */}
        {category.tabs.length > 1 && (
        <div className="px-5 pt-4">
          <div className="inline-flex flex-wrap gap-1 rounded-lg border border-border bg-muted/20 p-1">
            {category.tabs.map((tb) => {
              const isActive = tb.id === tab.id;
              const ip = tb.varServer ? SERVER_IPS[tb.varServer] : undefined;
              return (
                <button
                  key={tb.id}
                  onClick={() => setTabId(tb.id)}
                  className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    isActive
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                  }`}
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${isActive ? "bg-primary-foreground/80" : "bg-primary/60"}`} />
                  {t(tb.label)}
                  {ip && (
                    <span className={`font-mono text-[11px] tabular-nums ${isActive ? "text-primary-foreground/70" : "text-muted-foreground/70"}`}>
                      {ip}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
        )}

        {/* Embedded Grafana dashboard, or a not-configured placeholder. Server
            Operations has no header/tab strip above it, so it gets tighter
            padding and a much taller frame — near full-screen. */}
        <div className={view === "serverops" ? "p-2" : "p-5"}>
          {embedSrc ? (
            <div className="overflow-hidden rounded-xl border border-border bg-card">
              <iframe
                key={`${view}-${tab.id}`}
                title={`${tab.embed ? "" : "Grafana — "}${t(tab.label)}`}
                src={embedSrc}
                className={`w-full border-0 min-h-[560px] ${
                  view === "serverops" ? "h-[calc(100vh-215px)]" : "h-[calc(100vh-360px)]"
                }`}
              />
            </div>
          ) : (
            <div className="flex h-[calc(100vh-360px)] min-h-[400px] flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/10 text-center">
              <Monitor className="h-12 w-12 text-muted-foreground/40" />
              <p className="mt-3 text-sm text-muted-foreground">
                {t("Grafana URL not configured for")}{" "}
                <span className="font-medium text-foreground">{t(tab.label)}</span>
              </p>
              <p className="text-xs text-muted-foreground/70">{t("Set the dashboard to connect.")}</p>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
