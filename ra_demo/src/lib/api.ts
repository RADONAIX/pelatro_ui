import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL || "/api";

// A JS number is a 64-bit float, so it can't hold integers beyond 2^53
// (Number.MAX_SAFE_INTEGER = 9,007,199,254,740,991). The browser's JSON.parse
// silently ROUNDS bigger integers — e.g. a 19-digit sequence number
// 1565077624802594398 becomes 1565077624802594400. To preserve them we wrap any
// oversized INTEGER literal in quotes so it arrives as an exact string (report
// cells are rendered as strings anyway).
//
// One regex, one pass: each match is either a whole JSON string or a number.
// Strings match first, so digits inside them are consumed and skipped — only
// bare number tokens are ever considered.
const JSON_STRING_OR_NUMBER = /"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

function quoteUnsafeInts(text: string): string {
  return text.replace(JSON_STRING_OR_NUMBER, (tok) =>
    // Leave strings and non-integer numbers as-is; quote only integers that a
    // JS number can't represent exactly.
    tok[0] === '"' || /[.eE]/.test(tok) || Number.isSafeInteger(Number(tok)) ? tok : `"${tok}"`,
  );
}

// Replaces axios's default JSON transform. Non-string data (Blob for downloads,
// already-parsed objects) passes through untouched; on any parse error we fall
// back to a plain parse so behaviour never regresses. Also exported for the
// fetch-based pipelines client, which parses responses itself.
export function losslessParse(data: unknown): unknown {
  if (typeof data !== "string" || data.length === 0) return data;
  try { return JSON.parse(quoteUnsafeInts(data)); }
  catch { try { return JSON.parse(data); } catch { return data; } }
}

export const api = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
  transformResponse: [losslessParse],
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem("radonaix_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // Only a 401 from the auth/session check itself means the session is dead.
    // A 401 from an incidental data call (e.g. a best-effort widget fetch like
    // /pipelines/kpis) must NOT wipe the token — otherwise one flaky or
    // permission-gated endpoint silently logs the user out of the whole app.
    const url: string = err?.config?.url ?? "";
    const isAuthCheck = url.includes("/auth/");
    if (err?.response?.status === 401 && isAuthCheck && typeof window !== "undefined") {
      window.sessionStorage.removeItem("radonaix_token");
    }
    return Promise.reject(err);
  },
);
