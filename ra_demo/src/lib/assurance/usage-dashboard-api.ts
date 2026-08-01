import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// The Usage Assurance dashboard's data.
//
// Every figure comes from assurance.voice_sms_match_report — the MSC-versus-IN
// match report for voice and SMS — aggregated in SQL by the backend. Nothing on
// that dashboard is generated or blended in from elsewhere, so any number on
// screen can be traced back to rows in that one table.
// ---------------------------------------------------------------------------

export interface UsageHeadline {
  evaluated: number;
  matched: number;
  unmatched: number;
  matchRatePct: number;
  unmatchedRatePct: number;
  debitMatched: number;
  debitTotal: number;
  services: number;
  firstSeen: string | null;
  lastSeen: string | null;
}

export interface UsageService {
  service: string;
  evaluated: number;
  matched: number;
  unmatched: number;
  debit: number;
  matchRatePct: number;
}

export interface UsageFinding {
  id: number;
  service: string;
  mscCalling: string | null;
  inCalling: string | null;
  mscCalled: string | null;
  inCalled: string | null;
  debit: number;
  status: string;
  createdAt: string;
  gap: string;
}

export interface UsageDashboard {
  source: string;
  headline: UsageHeadline;
  byService: UsageService[];
  statusSplit: { status: string; count: number; debit: number }[];
  topCalling: { number: string; unmatched: number }[];
  topCalled: { number: string; unmatched: number }[];
  daily: {
    day: string;
    evaluated: number;
    matched: number;
    unmatched: number;
    debit: number;
  }[];
  findings: UsageFinding[];
}

export async function fetchUsageDashboard(): Promise<UsageDashboard> {
  const { data } = await api.get<UsageDashboard>("/dashboards/usage");
  return data;
}
