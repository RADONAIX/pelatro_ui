// Client-side single sign-on for a static SPA, using OAuth 2.0
// Authorization Code flow with PKCE. No client secret is involved (and must
// not be) — the provider app registration's redirect URI must be of type
// "Single-page application", which enables PKCE and CORS on the token endpoint.
//
// Flow: startLogin() redirects to the provider; the provider redirects back to
// our /login with `?code=…`; completeLoginFromUrl() exchanges the code for an
// id_token directly from the browser and derives the app user from its claims.

import type { Role } from "@/lib/auth";

export type SsoProvider = "google" | "microsoft";

const env = (import.meta as any).env ?? {};

interface ProviderConfig {
  clientId?: string;
  authorizeUrl: string;
  tokenUrl: string;
  scope: string;
}

function configFor(provider: SsoProvider): ProviderConfig {
  if (provider === "microsoft") {
    const tenant = env.VITE_MS_TENANT || "common";
    return {
      clientId: env.VITE_MS_CLIENT_ID,
      authorizeUrl: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize`,
      tokenUrl: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`,
      scope: "openid profile email",
    };
  }
  return {
    clientId: env.VITE_GOOGLE_CLIENT_ID,
    authorizeUrl: "https://accounts.google.com/o/oauth2/v2/auth",
    tokenUrl: "https://oauth2.googleapis.com/token",
    scope: "openid email profile",
  };
}

const redirectUri = () => env.VITE_OAUTH_REDIRECT_URI || `${window.location.origin}/login`;

/** Whether the given provider has a client ID configured (button is usable). */
export function ssoConfigured(provider: SsoProvider): boolean {
  return !!configFor(provider).clientId;
}

// --- PKCE helpers -----------------------------------------------------------
function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(byteLength = 48): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

function decodeJwtPayload(jwt: string): any {
  const b64 = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const json = decodeURIComponent(
    Array.prototype.map
      .call(atob(b64), (c: string) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
      .join(""),
  );
  return JSON.parse(json);
}

const VERIFIER_KEY = "radonaix_pkce_verifier";
const STATE_KEY = "radonaix_oauth_state";
const PROVIDER_KEY = "radonaix_oauth_provider";

/** Begin the OAuth dance — redirects the browser to the provider. */
export async function startLogin(provider: SsoProvider): Promise<void> {
  const cfg = configFor(provider);
  if (!cfg.clientId) throw new Error(`${provider} sign-in is not configured`);

  const verifier = randomString();
  const state = randomString(16);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  sessionStorage.setItem(PROVIDER_KEY, provider);

  const params = new URLSearchParams({
    client_id: cfg.clientId,
    response_type: "code",
    redirect_uri: redirectUri(),
    scope: cfg.scope,
    state,
    code_challenge: await pkceChallenge(verifier),
    code_challenge_method: "S256",
  });
  if (provider === "microsoft") {
    params.set("response_mode", "query");
  } else {
    params.set("access_type", "online");
    params.set("include_granted_scopes", "true");
  }

  window.location.href = `${cfg.authorizeUrl}?${params.toString()}`;
}

export interface SsoResult {
  token: string;
  user: { id: string; name: string; email: string; role: Role; roleLabel?: string; avatar?: string };
}

// Map a verified SSO identity to an app user. NOTE: unknown SSO users are
// granted the "admin" role for now so they can use the app immediately during
// rollout. Replace this with real provisioning (a backend lookup of the email
// → role) once the directory side is wired up.
function claimsToUser(claims: any): SsoResult["user"] {
  const email: string = claims.email || claims.preferred_username || claims.upn || "";
  const name: string = claims.name || email || "User";
  const avatar = name
    .split(/\s+/)
    .map((s: string) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return {
    id: claims.oid || claims.sub || email,
    name,
    email,
    role: "admin",
    roleLabel: "Administrator",
    avatar,
  };
}

/**
 * If the current URL is an OAuth callback (`?code=…`), exchange the code for an
 * id_token and return the session. Returns null when there's nothing to do.
 * Throws (with the provider's message) on failure.
 */
export async function completeLoginFromUrl(): Promise<SsoResult | null> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const error = params.get("error_description") || params.get("error");
  if (error) throw new Error(decodeURIComponent(error));
  if (!code) return null;

  const provider = (sessionStorage.getItem(PROVIDER_KEY) as SsoProvider) || "microsoft";
  const verifier = sessionStorage.getItem(VERIFIER_KEY) || "";
  const savedState = sessionStorage.getItem(STATE_KEY) || "";
  const state = params.get("state") || "";
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  sessionStorage.removeItem(PROVIDER_KEY);

  if (!verifier) throw new Error("Missing PKCE verifier — please retry sign-in.");
  if (savedState && state && savedState !== state) throw new Error("State mismatch — please retry sign-in.");

  const cfg = configFor(provider);
  const body = new URLSearchParams({
    client_id: cfg.clientId!,
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri(),
    code_verifier: verifier,
    scope: cfg.scope,
  });

  const res = await fetch(cfg.tokenUrl, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!res.ok) {
    let message = `Token exchange failed (${res.status})`;
    try {
      const j = await res.json();
      message = j.error_description || j.error || message;
    } catch {
      /* non-JSON */
    }
    throw new Error(message);
  }

  const tokens = await res.json();
  const idToken: string | undefined = tokens.id_token;
  if (!idToken) throw new Error("No id_token returned by the provider.");
  return { token: idToken, user: claimsToUser(decodeJwtPayload(idToken)) };
}
