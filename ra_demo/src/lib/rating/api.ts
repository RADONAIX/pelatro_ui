import axios from "axios";
import { losslessParse } from "@/lib/api";

// ---------------------------------------------------------------------------
// Client for the Rating Assurance service — a SEPARATE backend on its own port
// (dev) or behind `location /api/rating/` in nginx (prod). Deliberately its own
// axios instance rather than a second baseURL on `@/lib/api`: the two services
// have different timeouts and different failure semantics, and a rating outage
// must never look like an auth outage.
//
// Auth is shared: the rating service verifies the same bearer token the main
// API issues, so the interceptor reads the same sessionStorage key.
// ---------------------------------------------------------------------------

const baseURL = import.meta.env.VITE_RATING_API_BASE_URL || "/api/rating";

export const ratingApi = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
  // Compiles and rating runs are long-lived; the default 0 (no timeout) is
  // correct for those, but list calls get an explicit one at the call site.
  transformResponse: [losslessParse],
});

ratingApi.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem("radonaix_token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/** Pull a readable message out of the shared `{error:{code,message}}` envelope. */
export function ratingError(
  err: unknown,
  fallback = "Something went wrong.",
): string {
  const e = err as {
    response?: {
      data?: {
        error?: { message?: string; details?: Record<string, unknown> };
      };
    };
    message?: string;
  };
  const envelope = e?.response?.data?.error;
  if (envelope?.message) {
    const hint = envelope.details?.hint;
    return typeof hint === "string"
      ? `${envelope.message} ${hint}`
      : envelope.message;
  }
  return e?.message || fallback;
}
