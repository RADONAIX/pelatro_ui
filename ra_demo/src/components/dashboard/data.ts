export const revenueTrend = [
  { day: "May 12", total: 18.2, billed: 15.4, leakage: 0.55 },
  { day: "May 13", total: 20.1, billed: 17.0, leakage: 0.6 },
  { day: "May 14", total: 21.4, billed: 18.2, leakage: 0.58 },
  { day: "May 15", total: 22.6, billed: 19.4, leakage: 0.72 },
  { day: "May 16", total: 24.0, billed: 20.6, leakage: 0.64 },
  { day: "May 17", total: 24.4, billed: 21.2, leakage: 0.61 },
  { day: "May 18", total: 24.58, billed: 21.4, leakage: 0.57 },
];

export const leakageBreakdown = [
  {
    name: "Billing",
    value: 1.32,
    display: "$1.32M",
    pct: "34.6%",
    color: "#ef4444",
  },
  {
    name: "Rating",
    value: 0.91,
    display: "$0.91M",
    pct: "23.8%",
    color: "#f59e0b",
  },
  {
    name: "Mediation",
    value: 0.84,
    display: "$0.84M",
    pct: "22.0%",
    color: "#3b82f6",
  },
  {
    name: "Usage",
    value: 0.45,
    display: "$0.45M",
    pct: "11.8%",
    color: "#22c55e",
  },
  {
    name: "Roaming",
    value: 0.2,
    display: "$0.20M",
    pct: "5.2%",
    color: "#8b5cf6",
  },
  {
    name: "Subscription",
    value: 0.1,
    display: "$0.10M",
    pct: "2.6%",
    color: "#06b6d4",
  },
];

export const leakageDrivers = [
  {
    driver: "Tariff Mismatch",
    module: "Rating Assurance",
    leakage: "$1.18M",
    impact: 30.9,
    color: "#ef4444",
  },
  {
    driver: "Missing CDRs",
    module: "Mediation Assurance",
    leakage: "$0.84M",
    impact: 22.0,
    color: "#f59e0b",
  },
  {
    driver: "Incorrect Charging",
    module: "Usage Assurance",
    leakage: "$0.75M",
    impact: 19.6,
    color: "#3b82f6",
  },
  {
    driver: "Discount Misconfiguration",
    module: "Billing Assurance",
    leakage: "$0.48M",
    impact: 12.6,
    color: "#22c55e",
  },
  {
    driver: "Roaming Settlement",
    module: "Roaming Assurance",
    leakage: "$0.20M",
    impact: 5.2,
    color: "#8b5cf6",
  },
  {
    driver: "Others",
    module: "Subscription Assurance",
    leakage: "$0.10M",
    impact: 2.6,
    color: "#06b6d4",
  },
];

export const controlHealth = [
  { type: "Mediation Controls", effectiveness: "98.7%", trend: "1.1 pp" },
  { type: "Usage Controls", effectiveness: "98.2%", trend: "0.9 pp" },
  { type: "Billing Controls", effectiveness: "98.5%", trend: "1.3 pp" },
  { type: "Rating Controls", effectiveness: "97.8%", trend: "0.8 pp" },
  { type: "Roaming Controls", effectiveness: "98.1%", trend: "1.0 pp" },
  { type: "Subscription Controls", effectiveness: "99.0%", trend: "1.5 pp" },
];

export const aiActions = [
  {
    label: "Investigate top tariff mismatch",
    impact: "$1.18M",
    priority: "High",
  },
  {
    label: "Resolve missing CDRs in mediation",
    impact: "$0.84M",
    priority: "High",
  },
  {
    label: "Review incorrect charging patterns",
    impact: "$0.75M",
    priority: "Medium",
  },
  {
    label: "Optimize roaming settlement",
    impact: "$0.20M",
    priority: "Medium",
  },
  {
    label: "Review discount misconfiguration",
    impact: "$0.48M",
    priority: "Low",
  },
];

export const prepaidTech = [
  {
    label: "Data",
    value: "$6.25M",
    share: "46.1% of Total",
    delta: "12.3%",
    icon: "wifi",
  },
  {
    label: "Voice",
    value: "$5.09M",
    share: "37.5% of Total",
    delta: "10.8%",
    icon: "phone",
  },
  {
    label: "SMS",
    value: "$2.24M",
    share: "16.4% of Total",
    delta: "8.9%",
    icon: "sms",
  },
];

export const postpaidTech = [
  {
    label: "Voice",
    value: "$5.30M",
    share: "48.2% of Total",
    delta: "14.2%",
    icon: "phone",
  },
  {
    label: "Data",
    value: "$4.18M",
    share: "38.0% of Total",
    delta: "12.4%",
    icon: "wifi",
  },
  {
    label: "SMS",
    value: "$1.52M",
    share: "13.8% of Total",
    delta: "9.1%",
    icon: "sms",
  },
];

export const prepaidFlow = [
  { label: "Data Ingestion", value: "$6.30M", share: "1.2%", delta: "6.3%" },
  { label: "Usage Assurance", value: "$5.86M", share: "23.9%", delta: "10.4%" },
  { label: "OCS Assurance", value: "$4.12M", share: "16.8%", delta: "10.1%" },
  {
    label: "Balance Assurance",
    value: "$5.18M",
    share: "21.1%",
    delta: "9.4%",
  },
  {
    label: "Mediation Assurance",
    value: "$6.42M",
    share: "26.1%",
    delta: "11.2%",
  },
];

export const postpaidFlow = [
  { label: "Data Ingestion", value: "$0.30M", share: "1.2%", delta: "6.3%" },
  {
    label: "Mediation Assurance",
    value: "$6.42M",
    share: "26.1%",
    delta: "11.2%",
  },
  {
    label: "Rating Assurance",
    value: "$4.12M",
    share: "16.8%",
    delta: "10.1%",
  },
  {
    label: "Billing Assurance",
    value: "$6.73M",
    share: "27.4%",
    delta: "12.7%",
  },
  {
    label: "Balance Assurance",
    value: "$5.18M",
    share: "21.1%",
    delta: "9.4%",
  },
];

export const dataHealth = [
  {
    label: "Files Received",
    value: "18,452",
    delta: "5.4%",
    up: true,
    good: true,
  },
  {
    label: "Files Processed",
    value: "18,210",
    delta: "5.1%",
    up: true,
    good: true,
  },
  {
    label: "Processing Success",
    value: "98.7%",
    delta: "1.3 pp",
    up: true,
    good: true,
  },
  {
    label: "Invalid Files",
    value: "242",
    delta: "6.2%",
    up: true,
    good: false,
  },
  {
    label: "Duplicate Files",
    value: "186",
    delta: "7.5%",
    up: true,
    good: false,
  },
  {
    label: "Data Delay (Avg)",
    value: "12 min",
    delta: "4 min",
    up: false,
    good: true,
  },
];
