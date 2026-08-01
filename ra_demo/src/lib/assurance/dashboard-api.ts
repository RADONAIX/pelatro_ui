import { api } from "@/lib/api";
import { type AssuranceDashboard, currencySymbol } from "./dashboard-config";

// ---------------------------------------------------------------------------
// The executive dashboard, served by RA_Backend.
//
// The wire shape IS AssuranceDashboard — the server emits camelCase fields with
// the same names the charts already read — so nothing is remapped here beyond
// the one thing the server cannot know: which glyph stands for its ISO currency
// code. See `withCurrency` below.
//
// Rating reads canonical_rating.rating_reconciliation and Usage reads
// assurance.voice_sms_match_report; the server 404s any other assurance rather
// than returning an empty dashboard, and the hook keeps those on the synthetic
// profiles.
// ---------------------------------------------------------------------------

/** Assurance ids the API can serve. Everything else stays on local data. */
export const LIVE_DASHBOARD_APP_IDS: ReadonlySet<string> = new Set([
  "rating",
  // Usage reads assurance.voice_sms_match_report and returns this same shape,
  // so it renders through the same six charts rather than a lookalike.
  "usage",
  "charging",
]);

/**
 * Prefix the Revenue at Risk headline with the currency's symbol.
 *
 * The server sends that KPI as a bare formatted number because it knows the ISO
 * code and not the glyph. Doing it here rather than in the component keeps all
 * five KPI cards uniform — each is still spread straight onto KPICard.
 */
function withCurrency(d: AssuranceDashboard): AssuranceDashboard {
  const symbol = currencySymbol(d.currency);
  const risk = d.kpis.revenueAtRisk;
  if (risk.value.startsWith(symbol)) return d;
  return {
    ...d,
    kpis: {
      ...d.kpis,
      revenueAtRisk: { ...risk, value: `${symbol}${risk.value}` },
    },
  };
}

/**
 * The server's own explanation, when it sent one.
 *
 * The shared interceptor does not unwrap the `{error:{message}}` envelope, so
 * an axios rejection carries "Request failed with status code 503" — true but
 * useless in a banner. Unwrapped here rather than in the interceptor, which
 * every other screen's error text also flows through.
 */
function reason(e: unknown): string {
  const envelope = (
    e as { response?: { data?: { error?: { message?: string } } } }
  )?.response?.data?.error?.message;
  return envelope ?? (e as Error).message;
}

export async function fetchAssuranceDashboard(
  assurance: string,
): Promise<AssuranceDashboard> {
  try {
    const { data } = await api.get<AssuranceDashboard>("/assurance-dashboard", {
      params: { assurance },
    });
    return withCurrency(data);
  } catch (e) {
    throw new Error(reason(e));
  }
}
