import { useCallback, useEffect, useState } from "react";
import type { AppMetadata } from "./platform-metadata";
import { type AssuranceDashboard, getDashboard } from "./dashboard-config";
import {
  LIVE_DASHBOARD_APP_IDS,
  fetchAssuranceDashboard,
} from "./dashboard-api";

// ---------------------------------------------------------------------------
// The dataset behind the executive dashboard, from whichever source the chosen
// assurance has.
//
// Rating reads real reconciliation results from the API. The other seven have
// no canonical results table, so they keep the synthetic profiles in
// dashboard-config.ts and never touch the network — `loading` is false and
// `error` null for them from the first render, exactly as before this hook
// existed.
// ---------------------------------------------------------------------------

export interface AssuranceDashboardState {
  dashboard: AssuranceDashboard;
  /** True while the API call for a live assurance is in flight. */
  loading: boolean;
  /** Set when the last fetch failed. `dashboard` still holds the last good data. */
  error: string | null;
  /** True when `dashboard` came from the API rather than the local profiles. */
  live: boolean;
  reload: () => void;
}

export function useAssuranceDashboard(
  app: AppMetadata,
): AssuranceDashboardState {
  const isLive = LIVE_DASHBOARD_APP_IDS.has(app.id);
  const fallback = getDashboard(app);

  const [dashboard, setDashboard] = useState<AssuranceDashboard>(fallback);
  const [loading, setLoading] = useState(isLive);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!LIVE_DASHBOARD_APP_IDS.has(app.id)) return;
    setLoading(true);
    fetchAssuranceDashboard(app.id)
      .then((next) => {
        setDashboard(next);
        setError(null);
      })
      // Leave `dashboard` alone: a failed refresh should not blank a screen the
      // user is reading. The message drives an inline banner instead.
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [app.id]);

  useEffect(() => {
    // Switching assurance must never leave the previous one's numbers on
    // screen, so this one DOES replace — unlike reload above, what is showing
    // belongs to a different app.
    const local = getDashboard(app);
    setDashboard(local);
    setError(null);

    if (!LIVE_DASHBOARD_APP_IDS.has(app.id)) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    fetchAssuranceDashboard(app.id)
      .then((next) => {
        if (!cancelled) {
          setDashboard(next);
          setError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Safe as an object dependency: `app` is always an entry of the APPS module
    // constant (getApp is a find over it), so the identity changes only when the
    // chosen assurance does.
  }, [app]);

  // `live` describes the data being rendered, not the app: a Rating fetch that
  // failed is showing synthetic numbers, and the money formatters must follow
  // the data or a ₹ Cr figure would be labelled as whole rupees.
  return {
    dashboard,
    loading,
    error,
    live: Boolean(dashboard.currency),
    reload,
  };
}
