/**
 * Canonical platform metadata. Every application is a *configuration* of the
 * same universal services — entities, rule categories, dashboards,
 * investigation chains and administration objects are all metadata.
 */

export const RULE_CATEGORIES = [
  "Completeness",
  "Reconciliation",
  "Comparison",
  "Aggregation",
  "Calculation",
  "Threshold",
  "Sequence",
  "Duplicate",
  "Existence",
  "Referential Integrity",
  "Pattern Matching",
  "Statistical",
  "ML Prediction",
  "Temporal",
  "Graph Relationship",
] as const;

export type RuleCategory = (typeof RULE_CATEGORIES)[number];

export const UNIVERSAL_SERVICES = [
  { name: "Rule Engine", desc: "Evaluates 15 primitive rule categories" },
  { name: "Execution Engine", desc: "Schedules and runs control batches" },
  { name: "Metadata Engine", desc: "Resolves app scope, entities and controls" },
  { name: "Reconciliation Engine", desc: "Cross-system matching and gaps" },
  { name: "Analytics Engine", desc: "KPIs, leakage and trend aggregation" },
  { name: "ML Engine", desc: "Anomaly detection and predictive scoring" },
];

export const DATA_PLATFORM = [
  { layer: "TIM", desc: "Telecom information model — canonical schema" },
  { layer: "Bronze", desc: "Raw ingested feeds, untouched" },
  { layer: "Silver", desc: "Cleansed, conformed, deduplicated" },
  { layer: "Gold", desc: "Curated assurance marts" },
];

export const SHARED_COMPONENTS = [
  "Dashboard",
  "Rule Explorer",
  "Exception Explorer",
  "KPI Widgets",
  "Investigation",
  "Case Management",
  "Workflow",
  "Reports",
];

export type Workspace = {
  id: string;
  name: string;
  tagline: string;
  apps: string[];
};

export type KpiDef = { label: string; value: string; delta: number; intent: "good" | "bad" | "neutral" };

export type AppMetadata = {
  id: string;
  name: string;
  short: string;
  workspace: string;
  prefix: string;
  controlRange: string;
  controlCount: number;
  summary: string;
  entities: string[];
  ruleTypes: RuleCategory[];
  /**
   * The demo use cases for this application. `category` is the primary type
   * (drives filtering); `categories` carries the full combination when a use
   * case is evaluated by more than one rule type.
   */
  ruleLibrary: {
    name: string;
    category: RuleCategory;
    categories?: RuleCategory[];
    severity: "critical" | "high" | "medium";
  }[];
  dashboards: string[];
  kpis: KpiDef[];
  leakageSeries: { period: string; detected: number; recovered: number }[];
  breakdown: { name: string; value: number }[];
  investigationChain: string[];
  adminObjects: string[];
};

export const WORKSPACES: Workspace[] = [
  {
    id: "commercial",
    name: "Commercial Assurance",
    tagline: "Offers, pricing and product integrity",
    apps: ["rating", "partner"],
  },
  {
    id: "customer",
    name: "Customer Assurance",
    tagline: "Subscriber lifecycle and experience",
    apps: ["migration"],
  },
  {
    id: "revenue",
    name: "Revenue Assurance",
    tagline: "End-to-end revenue chain integrity",
    apps: ["usage", "charging", "billing"],
  },
  {
    id: "operations",
    name: "Operations Assurance",
    tagline: "Network and platform controls",
    apps: ["network"],
  },
  {
    id: "financial",
    name: "Financial Assurance",
    tagline: "Ledger, collection and settlement",
    apps: ["collection"],
  },
];

export const APPS: AppMetadata[] = [
  {
    id: "usage",
    name: "Usage Assurance",
    short: "Usage",
    workspace: "revenue",
    prefix: "UA",
    controlRange: "",
    controlCount: 250,
    summary:
      "Assures every network event is captured, mediated, rated and billed — from switch to invoice.",
    entities: ["Usage Events", "Subscriber", "MSC", "CDR", "Mediation", "Rating", "Billing"],
    ruleTypes: ["Completeness", "Aggregation", "Threshold", "Reconciliation"],
    ruleLibrary: [
      // { name: "Missing Usage Data Files", category: "Completeness", severity: "critical" },
      // { name: "Incorrect Duration / Volume Normalization", category: "Calculation", severity: "high" },
      // { name: "Incorrect Timestamp / Time Zone", category: "Temporal", severity: "high" },
      // { name: "Incorrect A/B Number Format", category: "Pattern Matching", severity: "medium" },
      // { name: "Customer Not Identified Correctly", category: "Referential Integrity", severity: "critical" },
      // {
      //   name: "Usage Loss During High Utilization",
      //   category: "Threshold",
      //   categories: ["Threshold", "Statistical"],
      //   severity: "critical",
      // },
      // { name: "Retail vs Interconnect Record Mismatch", category: "Comparison", severity: "high" },
    ],
    dashboards: ["Usage Leakage", "Usage KPIs"],
    kpis: [
      { label: "Missing Usage", value: "18,402 events", delta: -12.4, intent: "good" },
      { label: "Revenue Leakage", value: "$412K", delta: 6.1, intent: "bad" },
      { label: "Control Pass %", value: "96.2%", delta: 1.4, intent: "good" },
      { label: "Daily Usage Volume", value: "1.42B", delta: 0.8, intent: "neutral" },
    ],
    leakageSeries: [
      { period: "Week 18", detected: 512, recovered: 300 },
      { period: "Week 19", detected: 470, recovered: 320 },
      { period: "Week 20", detected: 610, recovered: 390 },
      { period: "Week 21", detected: 455, recovered: 355 },
      { period: "Week 22", detected: 398, recovered: 331 },
      { period: "Week 23", detected: 412, recovered: 366 },
    ],
    breakdown: [
      { name: "MSC-BLR-01", value: 4210 },
      { name: "MSC-DEL-04", value: 3180 },
      { name: "MSC-MUM-02", value: 2740 },
      { name: "Mediation NODE-7", value: 1980 },
      { name: "Mediation NODE-3", value: 1410 },
    ],
    investigationChain: ["MSC Record", "Decoded ASN.1", "Mediation Record", "Rating Record", "Billing Record"],
    adminObjects: ["MSC", "IMS", "Mediation", "Usage Rules"],
  },
  {
    id: "billing",
    name: "Billing Assurance",
    short: "Billing",
    workspace: "revenue",
    prefix: "BA",
    controlRange: "",
    controlCount: 180,
    summary: "Assures invoice accuracy, tax correctness and bill cycle completeness.",
    entities: ["Invoice", "Bill Cycle", "Account", "Tax", "Payment"],
    ruleTypes: ["Calculation", "Aggregation", "Duplicate", "Comparison", "Reconciliation"],
    ruleLibrary: [
      // { name: "Invoice Total ≠ Sum of Rated Charges", category: "Aggregation", severity: "critical" },
    ],
    dashboards: ["Billing Leakage", "Billing Accuracy"],
    kpis: [
      { label: "Invoice Accuracy", value: "99.31%", delta: 0.2, intent: "good" },
      { label: "Tax Leakage", value: "$88K", delta: -9.5, intent: "good" },
      { label: "Duplicate Bills", value: "134", delta: 22.0, intent: "bad" },
      { label: "Revenue at Risk", value: "$1.2M", delta: 3.3, intent: "bad" },
    ],
    leakageSeries: [
      { period: "Cycle 01", detected: 210, recovered: 150 },
      { period: "Cycle 02", detected: 265, recovered: 190 },
      { period: "Cycle 03", detected: 240, recovered: 205 },
      { period: "Cycle 04", detected: 310, recovered: 240 },
      { period: "Cycle 05", detected: 288, recovered: 251 },
      { period: "Cycle 06", detected: 262, recovered: 238 },
    ],
    breakdown: [
      { name: "Postpaid Cycle 12", value: 2210 },
      { name: "Enterprise Cycle 03", value: 1810 },
      { name: "Tax Zone GST-27", value: 1240 },
      { name: "Discount Plan D-88", value: 910 },
      { name: "Prepaid Adj Batch", value: 640 },
    ],
    investigationChain: ["Invoice", "Bill Details", "Charges", "Taxes", "Payments"],
    adminObjects: ["Bill Cycle", "Invoice", "Tax", "Discount", "Payment"],
  },
  {
    id: "rating",
    name: "Rating Assurance",
    short: "Rating",
    workspace: "commercial",
    prefix: "RA",
    controlRange: "",
    controlCount: 140,
    summary: "Assures tariff application, price plan integrity and rate correctness.",
    entities: ["Rated Event", "Tariff", "Price Plan", "Product", "Subscriber"],
    ruleTypes: ["Calculation", "Comparison", "Threshold", "Pattern Matching"],
    ruleLibrary: [
      // {
      //   name: "Expected Charge vs Actual Charge",
      //   category: "Calculation",
      //   categories: ["Calculation", "Comparison"],
      //   severity: "critical",
      // },
      // {
      //   name: "Zero Rated / Default Rated Events",
      //   category: "Existence",
      //   categories: ["Existence", "Comparison"],
      //   severity: "high",
      // },
      // { name: "Bundle / Discount Applied Incorrectly", category: "Graph Relationship", severity: "high" },
      // { name: "Account Not Debited for Charged Event", category: "Reconciliation", severity: "critical" },
      // { name: "Customer Charged More Than Once", category: "Duplicate", severity: "critical" },
    ],
    dashboards: ["Rating Leakage", "Tariff Accuracy"],
    kpis: [
      { label: "Rating Accuracy", value: "98.7%", delta: 0.5, intent: "good" },
      { label: "Zero-Rated Events", value: "42,110", delta: 14.2, intent: "bad" },
      { label: "Under-charge Value", value: "$233K", delta: -4.1, intent: "good" },
      { label: "Control Pass %", value: "94.8%", delta: -0.6, intent: "bad" },
    ],
    leakageSeries: [
      { period: "Week 18", detected: 190, recovered: 120 },
      { period: "Week 19", detected: 233, recovered: 160 },
      { period: "Week 20", detected: 201, recovered: 171 },
      { period: "Week 21", detected: 245, recovered: 190 },
      { period: "Week 22", detected: 219, recovered: 188 },
      { period: "Week 23", detected: 208, recovered: 181 },
    ],
    breakdown: [
      { name: "Plan PP-Gold", value: 1820 },
      { name: "Plan PP-Flex", value: 1410 },
      { name: "Roaming IOT", value: 980 },
      { name: "Data Bundle 5G", value: 720 },
      { name: "Promo SUMMER", value: 460 },
    ],
    investigationChain: ["Usage Event", "Tariff Lookup", "Rated Record", "Discount Applied", "Charge Output"],
    adminObjects: ["Tariff", "Price Plan", "Product Catalog", "Rating Rules"],
  },
  {
    id: "charging",
    name: "Charging Assurance",
    short: "Charging",
    workspace: "revenue",
    prefix: "CA",
    controlRange: "",
    controlCount: 120,
    summary: "Assures online charging, balance movements and session integrity.",
    entities: ["Session", "Balance", "OCS Account", "Reservation", "Top-up"],
    ruleTypes: ["Reconciliation", "Sequence", "Threshold", "Temporal"],
    ruleLibrary: [
      // { name: "Reconciliation between AIR Raw vs AIR Processed", category: "Reconciliation", severity: "critical" },
      // { name: "Duplicate Usage Records", category: "Duplicate", severity: "high" },
      // { name: "Missing File Sequence", category: "Sequence", severity: "critical" },
    ],
    dashboards: ["Charging Leakage", "Balance Integrity"],
    kpis: [
      { label: "Balance Drift", value: "$61K", delta: -18.0, intent: "good" },
      { label: "Stuck Reservations", value: "8,921", delta: 5.5, intent: "bad" },
      { label: "Session Success %", value: "99.1%", delta: 0.1, intent: "good" },
      { label: "Control Pass %", value: "97.4%", delta: 0.9, intent: "good" },
    ],
    leakageSeries: [
      { period: "Day 1", detected: 88, recovered: 60 },
      { period: "Day 2", detected: 102, recovered: 80 },
      { period: "Day 3", detected: 74, recovered: 62 },
      { period: "Day 4", detected: 121, recovered: 96 },
      { period: "Day 5", detected: 96, recovered: 84 },
      { period: "Day 6", detected: 81, recovered: 75 },
    ],
    breakdown: [
      { name: "OCS Node 2", value: 1320 },
      { name: "OCS Node 5", value: 1105 },
      { name: "Voucher Gateway", value: 640 },
      { name: "Data Session 4G", value: 520 },
      { name: "VoLTE Session", value: 310 },
    ],
    investigationChain: ["Session Record", "Reservation", "Balance Movement", "Charge", "Ledger Entry"],
    adminObjects: ["OCS Node", "Balance Type", "Reservation Policy", "Charging Rules"],
  },
  {
    id: "collection",
    name: "Collection Assurance",
    short: "Collection",
    workspace: "financial",
    prefix: "CL",
    controlRange: "",
    controlCount: 90,
    summary: "Assures payment capture, allocation and dunning effectiveness.",
    entities: ["Payment", "Receipt", "Dunning Case", "Account", "Bank File"],
    ruleTypes: ["Reconciliation", "Duplicate", "Temporal", "Comparison"],
    ruleLibrary: [
      // { name: "Unallocated Payment", category: "Existence", severity: "critical" },
      // { name: "Bank File Mismatch", category: "Reconciliation", severity: "critical" },
      // { name: "Duplicate Receipt", category: "Duplicate", severity: "high" },
      // { name: "Dunning Not Triggered", category: "Temporal", severity: "medium" },
      // { name: "Write-off Deviation", category: "Comparison", severity: "medium" },
    ],
    dashboards: ["Collection Leakage", "Dunning Effectiveness"],
    kpis: [
      { label: "Unallocated Cash", value: "$740K", delta: -7.2, intent: "good" },
      { label: "Bank Recon Breaks", value: "312", delta: 11.0, intent: "bad" },
      { label: "Collection Rate", value: "93.6%", delta: 0.4, intent: "good" },
      { label: "Overdue > 90d", value: "$2.1M", delta: 2.7, intent: "bad" },
    ],
    leakageSeries: [
      { period: "Jan", detected: 140, recovered: 90 },
      { period: "Feb", detected: 162, recovered: 118 },
      { period: "Mar", detected: 133, recovered: 111 },
      { period: "Apr", detected: 178, recovered: 140 },
      { period: "May", detected: 151, recovered: 132 },
      { period: "Jun", detected: 144, recovered: 128 },
    ],
    breakdown: [
      { name: "Bank HDFC", value: 980 },
      { name: "Bank ICICI", value: 760 },
      { name: "Card Gateway", value: 540 },
      { name: "UPI Channel", value: 420 },
      { name: "Cheque Batch", value: 180 },
    ],
    investigationChain: ["Bank File", "Payment", "Receipt", "Allocation", "Account Ledger"],
    adminObjects: ["Bank File Format", "Payment Method", "Dunning Policy", "Collection Rules"],
  },
  {
    id: "partner",
    name: "Partner Assurance",
    short: "Partner",
    workspace: "commercial",
    prefix: "PA",
    controlRange: "",
    controlCount: 110,
    summary: "Assures interconnect, roaming and content partner settlements.",
    entities: ["Partner", "Settlement", "Interconnect CDR", "Agreement", "Invoice"],
    ruleTypes: ["Reconciliation", "Calculation", "Comparison", "Aggregation"],
    ruleLibrary: [
      // {
      //   name: "Partner Settlement Invoice Volume Mismatch",
      //   category: "Reconciliation",
      //   categories: ["Reconciliation", "Comparison"],
      //   severity: "critical",
      // },
      // { name: "Partner Traffic Routed to Wrong Partner", category: "Graph Relationship", severity: "high" },
      // {
      //   name: "Partner Invoice Pricing Incorrect",
      //   category: "Calculation",
      //   categories: ["Calculation", "Comparison"],
      //   severity: "high",
      // },
    ],
    dashboards: ["Partner Leakage", "Settlement Accuracy"],
    kpis: [
      { label: "Settlement Variance", value: "$318K", delta: -3.9, intent: "good" },
      { label: "Disputed Volume", value: "1.9M min", delta: 8.4, intent: "bad" },
      { label: "Partners at Risk", value: "7", delta: 0, intent: "neutral" },
      { label: "Control Pass %", value: "95.5%", delta: 1.1, intent: "good" },
    ],
    leakageSeries: [
      { period: "Jan", detected: 120, recovered: 70 },
      { period: "Feb", detected: 145, recovered: 101 },
      { period: "Mar", detected: 118, recovered: 96 },
      { period: "Apr", detected: 166, recovered: 130 },
      { period: "May", detected: 139, recovered: 122 },
      { period: "Jun", detected: 128, recovered: 117 },
    ],
    breakdown: [
      { name: "Partner VF-IN", value: 880 },
      { name: "Partner ATT-US", value: 610 },
      { name: "Content OTT-A", value: 470 },
      { name: "Roaming EU-Hub", value: 380 },
      { name: "SMS Aggregator", value: 210 },
    ],
    investigationChain: ["Interconnect CDR", "Rate Card", "Settlement Line", "Partner Invoice", "Dispute"],
    adminObjects: ["Partner", "Agreement", "Rate Card", "Settlement Rules"],
  },
  {
    id: "network",
    name: "Network Assurance",
    short: "Network",
    workspace: "operations",
    prefix: "NA",
    controlRange: "",
    controlCount: 160,
    summary: "Assures element availability, feed continuity and configuration integrity.",
    entities: ["Network Element", "Feed", "Alarm", "Config Item", "Site"],
    ruleTypes: ["Completeness", "Threshold", "Temporal", "Graph Relationship"],
    ruleLibrary: [
      // { name: "Feed Not Received", category: "Completeness", severity: "critical" },
      // { name: "Element Silent", category: "Temporal", severity: "critical" },
      // { name: "Config Drift", category: "Comparison", severity: "high" },
      // { name: "Topology Break", category: "Graph Relationship", severity: "high" },
      // { name: "Alarm Storm", category: "Statistical", severity: "medium" },
    ],
    dashboards: ["Feed Health", "Element Coverage"],
    kpis: [
      { label: "Feeds On Time", value: "98.4%", delta: -0.7, intent: "bad" },
      { label: "Silent Elements", value: "23", delta: -30.0, intent: "good" },
      { label: "Config Drift Items", value: "146", delta: 4.5, intent: "bad" },
      { label: "Control Pass %", value: "97.9%", delta: 0.6, intent: "good" },
    ],
    leakageSeries: [
      { period: "Day 1", detected: 44, recovered: 30 },
      { period: "Day 2", detected: 58, recovered: 41 },
      { period: "Day 3", detected: 36, recovered: 33 },
      { period: "Day 4", detected: 62, recovered: 50 },
      { period: "Day 5", detected: 48, recovered: 44 },
      { period: "Day 6", detected: 39, recovered: 37 },
    ],
    breakdown: [
      { name: "Region North", value: 620 },
      { name: "Region West", value: 510 },
      { name: "Core IMS", value: 340 },
      { name: "RAN Cluster 7", value: 260 },
      { name: "Transport Ring 2", value: 150 },
    ],
    investigationChain: ["Site", "Network Element", "Feed File", "Parsed Record", "Downstream Load"],
    adminObjects: ["Network Element", "Feed Schedule", "Topology", "Network Rules"],
  },
  {
    id: "migration",
    name: "Migration Assurance",
    short: "Migration",
    workspace: "customer",
    prefix: "MA",
    controlRange: "",
    controlCount: 75,
    summary: "Assures data and subscriber migration fidelity between stacks.",
    entities: ["Migration Batch", "Source Record", "Target Record", "Subscriber", "Contract"],
    ruleTypes: ["Reconciliation", "Comparison", "Completeness", "Referential Integrity"],
    ruleLibrary: [
      // { name: "Record Count Mismatch", category: "Reconciliation", severity: "critical" },
      // { name: "Field Value Drift", category: "Comparison", severity: "high" },
      // { name: "Orphan Target Record", category: "Referential Integrity", severity: "high" },
      // { name: "Batch Not Loaded", category: "Completeness", severity: "critical" },
      // { name: "Post-migration Anomaly", category: "ML Prediction", severity: "medium" },
    ],
    dashboards: ["Migration Fidelity", "Cutover Readiness"],
    kpis: [
      { label: "Records Matched", value: "99.86%", delta: 0.1, intent: "good" },
      { label: "Open Mismatches", value: "4,118", delta: -21.0, intent: "good" },
      { label: "Batches Pending", value: "12", delta: 0, intent: "neutral" },
      { label: "Cutover Readiness", value: "88%", delta: 6.0, intent: "good" },
    ],
    leakageSeries: [
      { period: "Wave 1", detected: 320, recovered: 280 },
      { period: "Wave 2", detected: 280, recovered: 250 },
      { period: "Wave 3", detected: 190, recovered: 175 },
      { period: "Wave 4", detected: 150, recovered: 141 },
      { period: "Wave 5", detected: 110, recovered: 104 },
      { period: "Wave 6", detected: 88, recovered: 85 },
    ],
    breakdown: [
      { name: "Wave 4 Postpaid", value: 1420 },
      { name: "Wave 4 Prepaid", value: 980 },
      { name: "Contracts", value: 610 },
      { name: "Device Inventory", value: 340 },
      { name: "Loyalty Points", value: 180 },
    ],
    investigationChain: ["Migration Batch", "Source Record", "Transform Log", "Target Record", "Validation Result"],
    adminObjects: ["Migration Wave", "Field Mapping", "Reconciliation Set", "Migration Rules"],
  },
];

export const NAV_SECTIONS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "controls", label: "Controls" },
  { id: "exceptions", label: "Exceptions" },
  { id: "investigations", label: "Investigations" },
  { id: "analytics", label: "Analytics" },
  { id: "administration", label: "Administration" },
] as const;

export function getApp(id: string): AppMetadata | undefined {
  return APPS.find((a) => a.id === id);
}

export function getWorkspace(id: string) {
  return WORKSPACES.find((w) => w.id === id);
}

/* ---------- deterministic derived records ---------- */

function hash(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Every rule type a library entry (or control) is evaluated by. */
export function ruleCategories(rule: { category: RuleCategory; categories?: RuleCategory[] }): RuleCategory[] {
  return rule.categories ?? [rule.category];
}

export type Control = {
  id: string;
  name: string;
  category: RuleCategory;
  categories: RuleCategory[];
  entity: string;
  severity: "critical" | "high" | "medium";
  frequency: "Real-time" | "Hourly" | "Daily" | "Cycle";
  status: "Pass" | "Fail" | "Warning";
  passRate: number;
  exceptions: number;
  lastRun: string;
};

/** One control per rule-library entry — the app's demo use cases, nothing synthetic. */
export function buildControls(app: AppMetadata, limit = 24): Control[] {
  const rnd = hash(app.id + "controls");
  const freqs: Control["frequency"][] = ["Real-time", "Hourly", "Daily", "Cycle"];
  return Array.from({ length: Math.min(limit, app.ruleLibrary.length) }, (_, i) => {
    const rule = app.ruleLibrary[i];
    const r = rnd();
    const status: Control["status"] = r > 0.82 ? "Fail" : r > 0.64 ? "Warning" : "Pass";
    return {
      id: `${app.prefix}${String(i + 1).padStart(3, "0")}`,
      name: rule.name,
      category: rule.category,
      categories: ruleCategories(rule),
      entity: app.entities[Math.floor(rnd() * app.entities.length)],
      severity: rule.severity,
      frequency: freqs[Math.floor(rnd() * freqs.length)],
      status,
      passRate: Number((88 + rnd() * 12).toFixed(1)),
      exceptions: status === "Pass" ? Math.floor(rnd() * 20) : Math.floor(rnd() * 900) + 40,
      lastRun: `${Math.floor(rnd() * 58) + 1} min ago`,
    };
  });
}

export type ExceptionRecord = {
  id: string;
  controlId: string;
  control: string;
  entity: string;
  category: RuleCategory;
  severity: "critical" | "high" | "medium";
  impact: string;
  status: "Open" | "Investigating" | "Assigned" | "Resolved";
  owner: string;
  age: string;
};

const OWNERS = ["A. Menon", "R. Kaur", "T. Okafor", "S. Lindqvist", "M. Haddad", "Unassigned"];

export function buildExceptions(app: AppMetadata, limit = 28): ExceptionRecord[] {
  const rnd = hash(app.id + "exceptions");
  const statuses: ExceptionRecord["status"][] = ["Open", "Investigating", "Assigned", "Resolved"];
  return Array.from({ length: limit }, (_, i) => {
    const rule = app.ruleLibrary[Math.floor(rnd() * app.ruleLibrary.length)];
    return {
      id: `EX-${app.prefix}-${String(10248 + i)}`,
      controlId: `${app.prefix}${String(Math.floor(rnd() * app.controlCount) + 1).padStart(3, "0")}`,
      control: rule.name,
      entity: app.entities[Math.floor(rnd() * app.entities.length)],
      category: rule.category,
      severity: rule.severity,
      impact: `$${(rnd() * 90 + 1).toFixed(1)}K`,
      status: statuses[Math.floor(rnd() * statuses.length)],
      owner: OWNERS[Math.floor(rnd() * OWNERS.length)],
      age: `${Math.floor(rnd() * 14) + 1}d`,
    };
  });
}

export type Investigation = {
  id: string;
  title: string;
  exceptionId: string;
  stage: number;
  status: "Open" | "In Progress" | "Pending Review" | "Closed";
  owner: string;
  impact: string;
  opened: string;
};

export function buildInvestigations(app: AppMetadata, limit = 7): Investigation[] {
  const rnd = hash(app.id + "cases");
  const statuses: Investigation["status"][] = ["Open", "In Progress", "Pending Review", "Closed"];
  return Array.from({ length: limit }, (_, i) => {
    const rule = app.ruleLibrary[Math.floor(rnd() * app.ruleLibrary.length)];
    return {
      id: `CASE-${app.prefix}-${String(401 + i)}`,
      title: `${rule.name} cluster on ${app.entities[Math.floor(rnd() * app.entities.length)]}`,
      exceptionId: `EX-${app.prefix}-${String(10248 + Math.floor(rnd() * 28))}`,
      stage: Math.floor(rnd() * app.investigationChain.length),
      status: statuses[Math.floor(rnd() * statuses.length)],
      owner: OWNERS[Math.floor(rnd() * (OWNERS.length - 1))],
      impact: `$${(rnd() * 240 + 10).toFixed(0)}K`,
      opened: `${Math.floor(rnd() * 20) + 1} Jun`,
    };
  });
}
