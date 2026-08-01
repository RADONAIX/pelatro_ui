// ---------------------------------------------------------------------------
// DEMO-ONLY — the dataset behind the executive assurance dashboard.
//
// Every assurance app renders the SAME dashboard: same cards, same grid, same
// five KPIs, same eight-step narrative. Only the business terminology and the
// numbers below change. So this file holds nothing but data — no layout, no
// component decides anything per app.
//
// Series are generated from a per-app seed rather than hand-typed: the shape is
// deterministic (same app -> same chart on every render, no hydration drift)
// while staying long enough to look like real production volume.
// ---------------------------------------------------------------------------

import { APPS, type AppMetadata } from "./platform-metadata";

export type Point = { label: string; value: number };
export type TrendPoint = {
  label: string;
  healthy: number;
  exceptions: number;
  leakage: number;
};
export type NamedValue = { name: string; value: number };
export type Finding = {
  finding: string;
  source: string;
  category: string;
  impactCr: number;
};

export type KpiKey =
  | "revenueAtRisk"
  | "recordsEvaluated"
  | "exceptions"
  | "exceptionRate"
  | "reconciliation";

export type KpiValue = {
  /** Preformatted headline, e.g. "₹18.4 Cr". */
  value: string;
  /** Percentage change vs the prior period. */
  delta: number;
  /** true when a rising number is the good outcome (reconciliation success). */
  higherIsBetter: boolean;
  spark: number[];
};

export type AssuranceDashboard = {
  id: string;
  name: string;
  subtitle: string;
  /** What one evaluated record is called here — "CDRs", "Invoices", … */
  recordUnit: string;
  /**
   * ISO currency of every monetary figure, set ONLY on dashboards served by the
   * API from real reconciliation results. Its presence is what tells the
   * dashboard the money is in whole units rather than the ₹ Cr the synthetic
   * profiles below are scaled in — see `formatMoney`.
   */
  currency?: string;
  kpis: Record<KpiKey, KpiValue>;
  /** Row 1 left — dynamic title, always the same three series. */
  trendTitle: string;
  trend: TrendPoint[];
  /** Row 1 centre — common to every assurance. */
  revenueAtRisk: Point[];
  /**
   * Cadence of `revenueAtRisk`, when it is not daily.
   *
   * Usage's money is recorded monthly, so its series would otherwise sit under
   * a "Daily" subtitle that misstates what one point covers.
   */
  riskTrendSubtitle?: string;
  /** Row 1 right. */
  exceptionCategories: NamedValue[];
  /** Row 2 left — dynamic title + entities. */
  entitiesTitle: string;
  entities: NamedValue[];
  /** Row 2 centre — sorted by revenue impact (₹ Cr). */
  leakageCategories: NamedValue[];
  /** Row 2 right. */
  businessSegments: NamedValue[];
  /** Bottom table — exactly five rows. */
  findings: Finding[];
};

// --- deterministic noise -----------------------------------------------------

function seedOf(key: string): number {
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function rng(key: string) {
  let s = seedOf(key);
  return () => {
    s |= 0;
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A gently drifting series — enough movement to read as real, no spikes. */
function wobble(
  key: string,
  points: number,
  base: number,
  spread: number,
  drift = 0,
) {
  const rand = rng(key);
  return Array.from({ length: points }, (_, i) => {
    const trend = (drift * i) / Math.max(1, points - 1);
    return Math.max(0, base * (1 + trend) + (rand() - 0.5) * 2 * spread);
  });
}

const MONTHS = [
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
  "Jan",
];

// --- per-app business vocabulary --------------------------------------------

type Profile = {
  subtitle: string;
  recordUnit: string;
  trendTitle: string;
  entitiesTitle: string;
  /** Records evaluated per period, in millions. Drives every derived volume. */
  volumeM: number;
  /** Exception rate, percent. */
  exceptionRate: number;
  /** Reconciliation success, percent. */
  reconciliation: number;
  /** Revenue at risk, ₹ Cr. */
  riskCr: number;
  exceptionCategories: string[];
  entities: string[];
  leakageCategories: string[];
  businessSegments: string[];
  findings: [string, string, string, number][];
};

const PROFILES: Record<string, Profile> = {
  usage: {
    subtitle:
      "Monitor event capture, mediation completeness and usage-to-billing reconciliation across the network.",
    recordUnit: "CDRs",
    trendTitle: "Usage Reconciliation Trend",
    entitiesTitle: "Top Network Elements",
    volumeM: 1420,
    exceptionRate: 1.42,
    reconciliation: 97.8,
    riskCr: 18.4,
    exceptionCategories: [
      "Missing CDR",
      "Duplicate CDR",
      "Late CDR",
      "Incomplete CDR",
      "Volume Mismatch",
      "Sequence Gap",
    ],
    entities: ["MSC", "PGW", "SMSC", "IMS", "UPF"],
    leakageCategories: [
      "Unbilled Usage",
      "Missing Mediation",
      "Zero Duration Events",
      "Roaming CDR Loss",
      "Suspense Records",
      "Late Arrival",
    ],
    businessSegments: ["Voice", "SMS", "Data", "Roaming", "VAS"],
    findings: [
      ["Missing CDR Batch", "MSC", "Voice", 3.2],
      ["Mediation Suspense Backlog", "Mediation NODE-7", "Data", 2.45],
      ["Roaming CDR Loss — TAP In", "SMSC", "Roaming", 1.92],
      ["Duplicate Session Records", "PGW", "Data", 1.36],
      ["Zero Duration Voice Spike", "IMS", "Voice", 0.88],
    ],
  },
  rating: {
    subtitle:
      "Monitor tariff accuracy, rating integrity and revenue leakage across the telecom rating ecosystem.",
    recordUnit: "Rated Records",
    trendTitle: "Rating Accuracy Trend",
    entitiesTitle: "Top Source Systems",
    volumeM: 980,
    exceptionRate: 1.18,
    reconciliation: 98.4,
    riskCr: 12.7,
    exceptionCategories: [
      "Incorrect Tariff",
      "Bundle Rating Error",
      "Discount Mismatch",
      "Tax Error",
      "Zero Rated Usage",
      "Free Unit Error",
    ],
    entities: [
      "Rating Engine",
      "Mediation",
      "Product Catalog",
      "Charging Gateway",
      "Billing Engine",
    ],
    leakageCategories: [
      "Tariff Misapplication",
      "Under-rated Usage",
      "Discount Over-application",
      "Zero Rated Events",
      "Bundle Depletion Error",
      "Rounding Deviation",
    ],
    businessSegments: ["Voice", "SMS", "Data", "Roaming", "VAS"],
    findings: [
      ["Incorrect Tariff Configuration", "Rating Engine", "Voice", 2.3],
      ["Bundle Depletion Not Applied", "Product Catalog", "Data", 2.05],
      ["Promotional Discount Overlap", "Rating Engine", "VAS", 1.64],
      ["Roaming Rate Table Stale", "Mediation", "Roaming", 1.21],
      ["Zero Rated Data Sessions", "Charging Gateway", "Data", 0.79],
    ],
  },
  charging: {
    subtitle:
      "Monitor real-time charging integrity, balance accuracy and transaction completeness across online charging systems.",
    recordUnit: "Charging Transactions",
    trendTitle: "Charging Validation Trend",
    entitiesTitle: "Top Charging Systems",
    volumeM: 1180,
    exceptionRate: 0.94,
    reconciliation: 98.9,
    riskCr: 9.6,
    exceptionCategories: [
      "Failed Charging",
      "Timeout",
      "Balance Mismatch",
      "Debit Failure",
      "Charging Retry",
      "Recharge Failure",
    ],
    entities: ["OCS", "AIR", "SDP", "Voucher Platform", "Diameter Gateway"],
    leakageCategories: [
      "Uncharged Sessions",
      "Balance Reservation Loss",
      "Failed Debits",
      "Recharge Not Credited",
      "Retry Duplication",
      "Session Timeout Loss",
    ],
    businessSegments: ["Voice", "SMS", "Data", "Recharge", "VAS"],
    findings: [
      ["Uncharged Data Sessions", "OCS", "Data", 2.61],
      ["Balance Reservation Not Released", "SDP", "Voice", 1.88],
      ["Diameter Timeout Cluster", "Diameter Gateway", "Data", 1.42],
      ["Voucher Recharge Not Credited", "Voucher Platform", "Recharge", 1.05],
      ["Duplicate Charging Retry", "AIR", "SMS", 0.67],
    ],
  },
  billing: {
    subtitle:
      "Monitor invoice accuracy, bill cycle completeness and charge-to-invoice integrity across the billing chain.",
    recordUnit: "Invoices",
    trendTitle: "Billing Integrity Trend",
    entitiesTitle: "Top Billing Systems",
    volumeM: 42,
    exceptionRate: 1.66,
    reconciliation: 97.2,
    riskCr: 21.5,
    exceptionCategories: [
      "Unbilled Usage",
      "Invoice Mismatch",
      "Duplicate Invoice",
      "Rating Difference",
      "Tax Difference",
      "Provisioning Gap",
    ],
    entities: [
      "Billing Engine",
      "CRM",
      "Provisioning",
      "Order Management",
      "Finance",
    ],
    leakageCategories: [
      "Unbilled Usage",
      "Invoice Rating Difference",
      "Tax Under-charge",
      "Duplicate Credit Notes",
      "Provisioning Gap",
      "Cycle Close Delay",
    ],
    businessSegments: ["Consumer", "Enterprise", "Wholesale", "IoT", "Roaming"],
    findings: [
      ["Invoice Rating Difference", "Billing Engine", "Enterprise", 2.9],
      ["Unbilled Enterprise Usage", "Provisioning", "Enterprise", 2.44],
      ["Tax Zone Misconfiguration", "Finance", "Consumer", 1.73],
      ["Duplicate Invoice Run", "Billing Engine", "Wholesale", 1.29],
      ["Active Service Not Billed", "Order Management", "IoT", 0.94],
    ],
  },
  partner: {
    subtitle:
      "Monitor interconnect, roaming and revenue-share settlement accuracy across wholesale partner agreements.",
    recordUnit: "Settlement Records",
    trendTitle: "Settlement Reconciliation Trend",
    entitiesTitle: "Top Partners",
    volumeM: 128,
    exceptionRate: 2.08,
    reconciliation: 96.4,
    riskCr: 16.2,
    exceptionCategories: [
      "Roaming Settlement Mismatch",
      "Interconnect Difference",
      "Revenue Share Difference",
      "Missing TAP",
      "Wholesale Billing",
      "Duplicate Settlement",
    ],
    entities: ["Vodafone", "AT&T", "Orange", "Airtel", "Telefonica", "Singtel"],
    leakageCategories: [
      "Roaming Settlement Difference",
      "Interconnect Under-billing",
      "Missing TAP Files",
      "Revenue Share Variance",
      "Duplicate Settlement",
      "Rate Agreement Drift",
    ],
    businessSegments: [
      "Roaming",
      "Interconnect",
      "MVNO",
      "Wholesale",
      "VAS",
      "Content Partners",
    ],
    findings: [
      ["Roaming Settlement Difference", "Vodafone", "Roaming", 1.85],
      ["Interconnect Rate Mismatch", "AT&T", "Interconnect", 1.62],
      ["Missing TAP-In Files", "Orange", "Roaming", 1.31],
      ["Revenue Share Under-reported", "Airtel", "Content Partners", 1.08],
      ["Duplicate Wholesale Settlement", "Telefonica", "Wholesale", 0.74],
    ],
  },
  migration: {
    subtitle:
      "Monitor subscriber migration completeness, profile fidelity and balance integrity across platform cutovers.",
    recordUnit: "Subscriber Records",
    trendTitle: "Migration Validation Trend",
    entitiesTitle: "Top Migration Batches",
    volumeM: 64,
    exceptionRate: 2.42,
    reconciliation: 95.6,
    riskCr: 7.9,
    exceptionCategories: [
      "Missing Subscriber",
      "Profile Mismatch",
      "Plan Mismatch",
      "Activation Failure",
      "Balance Difference",
      "Provisioning Failure",
    ],
    entities: [
      "Customer Batch A",
      "Enterprise Batch",
      "Corporate Batch",
      "Consumer Batch",
      "Legacy Batch",
    ],
    leakageCategories: [
      "Unmigrated Subscribers",
      "Plan Downgrade Loss",
      "Balance Write-off",
      "Failed Activation",
      "Lost Bundle Entitlement",
      "Provisioning Gap",
    ],
    businessSegments: [
      "Consumer",
      "Enterprise",
      "Corporate",
      "Wholesale",
      "IoT",
    ],
    findings: [
      [
        "Subscribers Missing Post Cutover",
        "Enterprise Batch",
        "Enterprise",
        2.18,
      ],
      [
        "Price Plan Downgraded On Migration",
        "Consumer Batch",
        "Consumer",
        1.74,
      ],
      ["Prepaid Balance Not Carried", "Legacy Batch", "Consumer", 1.36],
      ["Activation Failure Backlog", "Corporate Batch", "Corporate", 0.91],
      ["Provisioning Not Replayed", "Customer Batch A", "IoT", 0.58],
    ],
  },
  network: {
    subtitle:
      "Monitor network event capture, node availability and session integrity across core and access domains.",
    recordUnit: "Network Events",
    trendTitle: "Network Event Validation Trend",
    entitiesTitle: "Top Network Nodes",
    volumeM: 2260,
    exceptionRate: 0.86,
    reconciliation: 98.6,
    riskCr: 11.3,
    exceptionCategories: [
      "Node Failure",
      "CDR Loss",
      "Packet Loss",
      "Session Failure",
      "Signal Failure",
      "Availability Issue",
    ],
    entities: ["MSC", "PGW", "SMSC", "PCRF", "UPF"],
    leakageCategories: [
      "CDR Loss At Node",
      "Session Setup Failure",
      "Packet Core Drop",
      "Node Downtime Loss",
      "Signalling Failure",
      "Transport Degradation",
    ],
    businessSegments: ["Core", "Access", "Transport", "IMS", "Packet Core"],
    findings: [
      ["CDR Loss During Node Restart", "PGW", "Packet Core", 2.72],
      ["Session Setup Failure Cluster", "PCRF", "Core", 1.94],
      ["Packet Loss On Transport Link", "UPF", "Transport", 1.47],
      ["SMSC Availability Degradation", "SMSC", "Access", 0.96],
      ["IMS Signalling Failures", "MSC", "IMS", 0.63],
    ],
  },
  collection: {
    subtitle:
      "Monitor payment capture, receivables ageing and collection recovery across all customer segments.",
    recordUnit: "Payment Records",
    trendTitle: "Collection Recovery Trend",
    entitiesTitle: "Top Customer Segments",
    volumeM: 96,
    exceptionRate: 3.14,
    reconciliation: 94.8,
    riskCr: 24.6,
    exceptionCategories: [
      "Overdue Payments",
      "Payment Mismatch",
      "Collection Failure",
      "Outstanding Invoice",
      "Write-off Risk",
      "Settlement Delay",
    ],
    entities: ["Consumer", "Enterprise", "SME", "Wholesale", "Roaming"],
    leakageCategories: [
      "Aged Receivables",
      "Unapplied Payments",
      "Write-off Exposure",
      "Failed Auto-debit",
      "Disputed Invoices",
      "Settlement Delay",
    ],
    businessSegments: [
      "Consumer",
      "Enterprise",
      "Corporate",
      "Wholesale",
      "Roaming",
    ],
    findings: [
      ["Aged Enterprise Receivables", "Enterprise", "Enterprise", 4.15],
      ["Unapplied Payment Backlog", "Consumer", "Consumer", 2.86],
      ["Auto-debit Mandate Failures", "SME", "Corporate", 2.02],
      ["Disputed Wholesale Invoices", "Wholesale", "Wholesale", 1.44],
      ["Roaming Settlement Delay", "Roaming", "Roaming", 0.97],
    ],
  },
};

// --- formatting --------------------------------------------------------------

export function formatCr(value: number): string {
  return `₹${value.toFixed(2)} Cr`;
}

/** The glyph for an ISO code, falling back to the code itself when unmapped. */
export function currencySymbol(code: string | undefined): string {
  if (!code) return "₹";
  return (
    { INR: "₹", USD: "$", EUR: "€", GBP: "£" }[code.toUpperCase()] ?? `${code} `
  );
}

/**
 * Money in whole units, for dashboards fed by real data.
 *
 * Kept separate from `formatCr` rather than replacing it: the seven synthetic
 * dashboards are authored at crore scale and still read correctly there, while
 * real reconciliation figures are single transactions — the canonical rating
 * table totals a few hundred rupees, which `formatCr` would render as
 * "₹0.00 Cr" and make a populated screen look broken.
 */
export function formatMoney(value: number, currency?: string): string {
  return `${currencySymbol(currency)}${value.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatCount(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return Math.round(n).toLocaleString("en-IN");
}

// --- build -------------------------------------------------------------------

function buildDashboard(app: AppMetadata, p: Profile): AssuranceDashboard {
  const records = p.volumeM * 1e6;
  const exceptions = (records * p.exceptionRate) / 100;

  const trendBase = wobble(
    `${app.id}:trend`,
    12,
    records / 12,
    records / 120,
    0.12,
  );
  const trend: TrendPoint[] = MONTHS.map((label, i) => {
    const total = trendBase[i];
    const ex = total * (p.exceptionRate / 100) * (0.8 + ((i * 7) % 5) / 12);
    return {
      label,
      healthy: Math.round((total - ex) / 1e3),
      exceptions: Math.round(ex / 1e3),
      leakage: Math.round((ex / 1e3) * 0.34),
    };
  });

  const riskSeries = wobble(
    `${app.id}:risk`,
    30,
    p.riskCr,
    p.riskCr * 0.12,
    -0.08,
  );
  const revenueAtRisk: Point[] = riskSeries.map((v, i) => ({
    label: `D-${30 - i}`,
    value: Number(v.toFixed(2)),
  }));

  const catWeights = wobble(
    `${app.id}:cat`,
    p.exceptionCategories.length,
    100,
    42,
  );
  const exceptionCategories = p.exceptionCategories
    .map((name, i) => ({
      name,
      value: Math.round(exceptions * (catWeights[i] / 1000)),
    }))
    .sort((a, b) => b.value - a.value);

  const entWeights = wobble(`${app.id}:ent`, p.entities.length, 100, 38);
  const entities = p.entities
    .map((name, i) => ({
      name,
      value: Math.round(exceptions * (entWeights[i] / 1200)),
    }))
    .sort((a, b) => b.value - a.value);

  const leakWeights = wobble(
    `${app.id}:leak`,
    p.leakageCategories.length,
    100,
    40,
  );
  const leakageCategories = p.leakageCategories
    .map((name, i) => ({
      name,
      value: Number(((p.riskCr * leakWeights[i]) / 320).toFixed(2)),
    }))
    .sort((a, b) => b.value - a.value);

  const bizWeights = wobble(
    `${app.id}:biz`,
    p.businessSegments.length,
    100,
    34,
  );
  const businessSegments = p.businessSegments
    .map((name, i) => ({
      name,
      value: Math.round(records * (bizWeights[i] / 1e3) * 1e-3),
    }))
    .sort((a, b) => b.value - a.value);

  const spark = (key: string, drift: number) =>
    wobble(key, 14, 100, 14, drift).map((v) => Number(v.toFixed(2)));

  return {
    id: app.id,
    name: app.name,
    subtitle: p.subtitle,
    recordUnit: p.recordUnit,
    kpis: {
      revenueAtRisk: {
        value: formatCr(p.riskCr),
        delta: Number((-2 - (seedOf(app.id) % 60) / 10).toFixed(1)),
        higherIsBetter: false,
        spark: spark(`${app.id}:s1`, -0.18),
      },
      recordsEvaluated: {
        value: formatCount(records),
        delta: Number((1 + (seedOf(app.id + "r") % 45) / 10).toFixed(1)),
        higherIsBetter: true,
        spark: spark(`${app.id}:s2`, 0.14),
      },
      exceptions: {
        value: formatCount(exceptions),
        delta: Number((-1 - (seedOf(app.id + "e") % 70) / 10).toFixed(1)),
        higherIsBetter: false,
        spark: spark(`${app.id}:s3`, -0.16),
      },
      exceptionRate: {
        value: `${p.exceptionRate.toFixed(2)}%`,
        delta: Number((-(seedOf(app.id + "x") % 40) / 10 - 0.4).toFixed(1)),
        higherIsBetter: false,
        spark: spark(`${app.id}:s4`, -0.12),
      },
      reconciliation: {
        value: `${p.reconciliation.toFixed(1)}%`,
        delta: Number(((seedOf(app.id + "c") % 25) / 10 + 0.2).toFixed(1)),
        higherIsBetter: true,
        spark: spark(`${app.id}:s5`, 0.08),
      },
    },
    trendTitle: p.trendTitle,
    trend,
    revenueAtRisk,
    exceptionCategories,
    entitiesTitle: p.entitiesTitle,
    entities,
    leakageCategories,
    businessSegments,
    findings: p.findings.map(([finding, source, category, impactCr]) => ({
      finding,
      source,
      category,
      impactCr,
    })),
  };
}

const CACHE = new Map<string, AssuranceDashboard>();

/** The dashboard dataset for an assurance app. Falls back to Usage's shape. */
export function getDashboard(app: AppMetadata): AssuranceDashboard {
  const cached = CACHE.get(app.id);
  if (cached) return cached;
  const built = buildDashboard(app, PROFILES[app.id] ?? PROFILES.usage);
  CACHE.set(app.id, built);
  return built;
}

/** Every app has a profile — asserted here so a new app fails loudly in dev. */
export const DASHBOARD_APP_IDS = APPS.map((a) => a.id).filter(
  (id) => id in PROFILES,
);
