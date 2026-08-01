import { createFileRoute, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Cpu, Database, Monitor, Server } from "lucide-react";
import { useT } from "@/lib/i18n";
import { MonitoringDashboard, type PanelGroup } from "@/components/monitoring/Dashboard";

export const Route = createFileRoute("/monitoring")({ component: MonitoringPage });

type MonIcon = typeof Cpu;
interface MonTab {
  id: string;
  label: string;
  // Which panel groups this host gets. A database box adds the postgres
  // panels; a box running the API adds the API panels. Absent = a host that is
  // not provisioned yet, which renders the "not monitored" placeholder.
  groups?: readonly PanelGroup[];
  varServer?: string; // Prometheus `server` label, for the IP shown on the tab
}
interface MonCategory {
  title: string;
  description: string;
  icon: MonIcon;
  tabs: MonTab[];
}

// System Monitoring sections — selected from the sidebar via /monitoring?view=<key>.
// Each tab is one monitored host; `groups` says which panels that host earns.
//
// The panels used to be embedded Grafana dashboards. Grafana is still the source
// of truth on the deployed host, where the SPA and Grafana share an origin; it
// cannot be framed from anywhere else (X-Frame-Options), so this screen renders
// the same measurements from simulated data instead. Swapping a group back onto
// live queries is a change to MonitoringDashboard, not to this table.

// LAN IP per Prometheus `server` label (deploy/prometheus/targets/nodes.json).
// Shown beside each tab name; keyed by tab.varServer so there's a single source
// of truth. Tabs without a provisioned server have no varServer → no IP shown.
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
      // same host. Exception (APP 4) runs on 69, the SAME box as the DB
      // (ch-master), so it reuses that node target. MSC isn't provisioned yet.
      { id: "air", label: "APP 1 (AIR)", groups: ["system"], varServer: "app-air" },
      { id: "sdp", label: "APP 2 (SDP)", groups: ["system"], varServer: "rpt-master" },
      { id: "msc", label: "APP 3 (MSC)" },
      { id: "exception", label: "APP 4 (Exception)", groups: ["system"], varServer: "ch-master" },
    ],
  },
  databases: {
    title: "Databases",
    description: "Clickhouse & Postgres master/slave health — system dashboards",
    icon: Database,
    tabs: [
      // ClickHouse + Postgres masters share one box (69) today. The Postgres
      // tabs add the connection panels; the ClickHouse ones do not.
      { id: "ch-master", label: "Clickhouse Master", groups: ["system"], varServer: "ch-master" },
      { id: "ch-slave", label: "Clickhouse Slave" },
      { id: "pg-master", label: "Postgres Master", groups: ["system", "postgres"], varServer: "ch-master" },
      { id: "pg-slave", label: "Postgres Slave" },
    ],
  },
  reportservers: {
    title: "Report Servers",
    description: "Report Server 1 & 2 — system + API health",
    icon: Server,
    tabs: [
      // Report Server 1 (142) runs the API and the app database, so it is the
      // one host that shows all three groups.
      { id: "rs1-system", label: "Report Server 1 · System", groups: ["system"], varServer: "rpt-master" },
      { id: "rs1-api", label: "Report Server 1 · API", groups: ["api"], varServer: "rpt-master" },
      { id: "rs-2", label: "Report Server 2" },
    ],
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

  return (
    <AppShell>
      <PageHeader
        title={t("System Monitoring")}
        description={t("System, application and database health across the monitored fleet.")}
        info={t("Host, API and database metrics for every monitored server.")}
      />

      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        {/* Section header — icon + title + description. */}
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

        {/* Tab strip — the section's dashboards. Hidden for single-tab sections
            */}
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

        {/* The panels for the selected host.

            A faint plane behind the panels: they are near-white cards, and a
            white card on a white page is a border and nothing else. */}
        <div className="bg-muted/25 p-5">
          {tab.groups ? (
            // Remounted per host and per section, so switching tabs re-seeds the
            // window instead of carrying the previous host's history across.
            <MonitoringDashboard
              key={`${view}-${tab.id}`}
              groups={tab.groups}
              hostLabel={`${t(tab.label)}${tab.varServer ? ` · ${SERVER_IPS[tab.varServer]}` : ""}`}
            />
          ) : (
            <div className="flex h-[calc(100vh-360px)] min-h-[400px] flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/10 text-center">
              <Monitor className="h-12 w-12 text-muted-foreground/40" />
              <p className="mt-3 text-sm text-muted-foreground">
                {t("Not monitored yet")} —{" "}
                <span className="font-medium text-foreground">{t(tab.label)}</span>
              </p>
              <p className="text-xs text-muted-foreground/70">
                {t("This host has no scrape target provisioned.")}
              </p>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
