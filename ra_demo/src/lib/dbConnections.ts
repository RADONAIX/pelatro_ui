// DEMO-ONLY. Database connection targets, persisted to localStorage; no backend
// calls (there is no connection CRUD API — /system/config is a single-row global
// config and stream/table names come from server-side YAML + DATA_STREAMS env).
//
// Lives in lib/ rather than inside a route because two screens read it: the
// admin Database Connections page owns it, and Data Sources references it.
//
// NOTE ON CREDENTIALS: there is deliberately no password field on this type.
// The form collects one, but it is never persisted — writing database passwords
// into browser storage would be a real security problem if this were ever wired
// to a backend. In a real deployment credentials come from env/vault server-side.

const STORAGE_KEY = "radonaix_db_connections_v1";

export const ENGINES = ["PostgreSQL", "ClickHouse", "MySQL", "Oracle", "SQL Server"] as const;

export const SSL_MODES = ["disable", "require", "verify-ca", "verify-full"] as const;

// Sensible listener port per engine, used to prefill the form.
export const DEFAULT_PORT: Record<string, number> = {
  PostgreSQL: 5432,
  ClickHouse: 8123,
  MySQL: 3306,
  Oracle: 1521,
  "SQL Server": 1433,
};

export interface DbConnection {
  id: string;
  name: string;
  engine: string;
  host: string;
  port: number;
  database: string;
  username: string;
  sslMode: string;
  poolSize: number;
  enabled: boolean;
  lastTestedAt: string | null;
}

// Fewer connections than data sources on purpose: rafms-replica backs three
// separate streams, which is the whole point of pulling connections out of the
// per-source rows.
export const SEED: DbConnection[] = [
  {
    id: "conn-rafms-primary",
    name: "rafms-primary",
    engine: "PostgreSQL",
    host: "10.200.37.133",
    port: 5432,
    database: "rafms",
    username: "ra_app",
    sslMode: "require",
    poolSize: 20,
    enabled: true,
    lastTestedAt: null,
  },
  {
    id: "conn-rafms-replica",
    name: "rafms-replica",
    engine: "PostgreSQL",
    host: "10.200.37.142",
    port: 5432,
    database: "rafms",
    username: "ra_readonly",
    sslMode: "require",
    poolSize: 40,
    enabled: true,
    lastTestedAt: null,
  },
  {
    id: "conn-ocs-charging",
    name: "ocs-charging",
    engine: "Oracle",
    host: "10.200.37.145",
    port: 1521,
    database: "ocs_db",
    username: "ocs_reader",
    sslMode: "verify-ca",
    poolSize: 10,
    enabled: true,
    lastTestedAt: null,
  },
  {
    id: "conn-billing-core",
    name: "billing-core",
    engine: "PostgreSQL",
    host: "10.200.37.152",
    port: 5432,
    database: "billing",
    username: "billing_reader",
    sslMode: "require",
    poolSize: 10,
    enabled: true,
    lastTestedAt: null,
  },
  {
    id: "conn-recharge-store",
    name: "recharge-store",
    engine: "MySQL",
    host: "10.200.37.151",
    port: 3306,
    database: "recharge",
    username: "recharge_reader",
    sslMode: "disable",
    poolSize: 8,
    enabled: false,
    lastTestedAt: null,
  },
];

export function loadConnections(): DbConnection[] {
  if (typeof window === "undefined") return SEED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as DbConnection[]) : null;
    // Non-empty stored list wins; empty/blank falls back to the seed so the demo
    // never opens on a blank screen after everything was deleted.
    if (Array.isArray(parsed) && parsed.length) return parsed;
  } catch {
    /* ignore malformed storage */
  }
  return SEED;
}

// Callers invoke this from explicit user actions only — never from a reactive
// effect, which would race the initial mount load and clobber saved data with
// the empty initial state (notably under StrictMode's double-invoked effects).
export function saveConnections(next: DbConnection[]) {
  if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

export const findConnection = (conns: DbConnection[], id: string): DbConnection | undefined =>
  conns.find((c) => c.id === id);

// "10.200.37.133:5432/rafms" — the resolved target, for display on cards.
export const connectionTarget = (c: DbConnection): string => `${c.host}:${c.port}/${c.database}`;
