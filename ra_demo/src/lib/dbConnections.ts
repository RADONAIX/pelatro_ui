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

// v2: four more targets, for the systems the newer assurances read — partner
// settlement, network probes, payments and the legacy BSS that Migration
// Assurance compares against. The key is bumped so those reach anyone who has
// already used the screen; see loadConnections for how stored rows survive it.
const STORAGE_KEY = "radonaix_db_connections_v2";
const LEGACY_KEY_V1 = "radonaix_db_connections_v1";

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
  {
    id: "conn-partner-settlement",
    name: "partner-settlement",
    engine: "PostgreSQL",
    host: "10.200.37.161",
    port: 5432,
    database: "partner_settlement",
    username: "partner_reader",
    sslMode: "require",
    poolSize: 10,
    enabled: true,
    lastTestedAt: null,
  },
  {
    // ClickHouse rather than Postgres: probe records arrive at a volume no OLTP
    // store would carry, and every network query over them is an aggregate.
    id: "conn-network-probe",
    name: "network-probe",
    engine: "ClickHouse",
    host: "10.200.37.171",
    port: 8123,
    database: "network_probe",
    username: "probe_reader",
    sslMode: "disable",
    poolSize: 16,
    enabled: true,
    lastTestedAt: null,
  },
  {
    id: "conn-payments",
    name: "payments",
    engine: "PostgreSQL",
    host: "10.200.37.155",
    port: 5432,
    database: "payments",
    username: "payments_reader",
    sslMode: "require",
    poolSize: 12,
    enabled: true,
    lastTestedAt: null,
  },
  {
    // The system being migrated FROM. Read-only by nature, and small pool: it is
    // a decommissioning platform, not one to load up.
    id: "conn-legacy-bss",
    name: "legacy-bss",
    engine: "Oracle",
    host: "10.200.37.180",
    port: 1521,
    database: "legacy_bss",
    username: "legacy_reader",
    sslMode: "verify-ca",
    poolSize: 6,
    enabled: true,
    lastTestedAt: null,
  },
];

function read(key: string): DbConnection[] | null {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? (JSON.parse(raw) as DbConnection[]) : null;
    // Non-empty stored list wins; empty/blank falls through so the demo never
    // opens on a blank screen after everything was deleted.
    return Array.isArray(parsed) && parsed.length ? parsed : null;
  } catch {
    return null; /* ignore malformed storage */
  }
}

export function loadConnections(): DbConnection[] {
  if (typeof window === "undefined") return SEED;

  const current = read(STORAGE_KEY);
  if (current) return current;

  // Upgrading from v1: keep everything the user has, and append the seeds they
  // have never seen. Matching on id, so a connection someone edited keeps their
  // version rather than being reset to ours.
  //
  // This runs only while no v2 key exists, so a seed deleted AFTER the upgrade
  // stays deleted. One deleted before it does come back — the cost of shipping
  // new targets to an existing browser, and preferable to them never arriving.
  const legacy = read(LEGACY_KEY_V1);
  if (legacy) {
    const known = new Set(legacy.map((c) => c.id));
    return [...legacy, ...SEED.filter((c) => !known.has(c.id))];
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
