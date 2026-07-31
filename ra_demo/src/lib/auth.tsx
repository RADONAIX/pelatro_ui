import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";

export type Role = "admin" | "ra_lead" | "analyst" | "viewer";

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrator",
  ra_lead: "RA Manager",
  analyst: "RA Analyst",
  viewer: "Report Viewer",
};

export type PermKey =
  | "dashboard"
  | "reports"
  | "pipelines"
  | "userManagement"
  | "roleManagement"
  | "settings";

export interface Permission {
  view: boolean;
  edit: boolean;
}

export type PermissionMap = Record<PermKey, Permission>;

export const PERMISSION_KEYS: { key: PermKey; label: string; path: string }[] = [
  { key: "dashboard", label: "Dashboard & KPIs", path: "/" },
  { key: "reports", label: "Reports & Certified Exports", path: "/reports" },
  { key: "pipelines", label: "Pipelines & Job Monitor", path: "/pipelines" },
  { key: "userManagement", label: "User Management", path: "/users" },
  { key: "roleManagement", label: "Role Management", path: "/roles" },
  { key: "settings", label: "Settings", path: "/system-config" },
];

// View-only allowed pages
export const VIEW_ONLY_ALLOWED: PermKey[] = ["dashboard", "reports", "pipelines"];

// Path → permission key mapping
export const PATH_TO_PERM: Record<string, PermKey> = {
  "/": "dashboard",
  "/reports": "reports",
  "/pipelines": "pipelines",
  "/users": "userManagement",
  "/roles": "roleManagement",
  "/system-config": "settings",
  "/audit-logs": "settings",
  "/monitoring": "settings",
  // Admin-only demo module. Reuses the existing `settings` permission rather
  // than adding a PermKey the backend doesn't know about.
  "/database-connections": "settings",
};

const ALL_TRUE: PermissionMap = {
  dashboard: { view: true, edit: true },
  reports: { view: true, edit: true },
  pipelines: { view: true, edit: true },
  userManagement: { view: true, edit: true },
  roleManagement: { view: true, edit: true },
  settings: { view: true, edit: true },
};

export const DEFAULT_ROLE_PERMISSIONS: Record<Role, PermissionMap> = {
  admin: ALL_TRUE,
  ra_lead: {
    dashboard: { view: true, edit: true },
    reports: { view: true, edit: true },
    pipelines: { view: true, edit: true },
    userManagement: { view: false, edit: false },
    roleManagement: { view: false, edit: false },
    settings: { view: true, edit: false },
  },
  analyst: {
    dashboard: { view: true, edit: false },
    reports: { view: true, edit: false },
    pipelines: { view: true, edit: false },
    userManagement: { view: false, edit: false },
    roleManagement: { view: false, edit: false },
    settings: { view: false, edit: false },
  },
  viewer: {
    dashboard: { view: true, edit: false },
    reports: { view: true, edit: false },
    pipelines: { view: true, edit: false },
    userManagement: { view: false, edit: false },
    roleManagement: { view: false, edit: false },
    settings: { view: false, edit: false },
  },
};

const PERMS_STORAGE_KEY = "radonaix_role_perms";

export function loadRolePermissions(): Record<Role, PermissionMap> {
  if (typeof window === "undefined") return DEFAULT_ROLE_PERMISSIONS;
  try {
    const raw = localStorage.getItem(PERMS_STORAGE_KEY);
    if (!raw) return DEFAULT_ROLE_PERMISSIONS;
    return { ...DEFAULT_ROLE_PERMISSIONS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_ROLE_PERMISSIONS;
  }
}

export function saveRolePermissions(perms: Record<Role, PermissionMap>) {
  localStorage.setItem(PERMS_STORAGE_KEY, JSON.stringify(perms));
  window.dispatchEvent(new Event("radonaix:perms-updated"));
}

// Normalise a (possibly partial) backend permission map over the role's
// defaults so every PermKey always has a {view, edit} entry.
function normalizePerms(role: Role | undefined, backend: Partial<PermissionMap> | null): PermissionMap {
  const base = (role && DEFAULT_ROLE_PERMISSIONS[role]) || ALL_TRUE;
  if (!backend) return base;
  const out = {} as PermissionMap;
  (Object.keys(ALL_TRUE) as PermKey[]).forEach((k) => {
    out[k] = (backend[k] as Permission) ?? base[k];
  });
  return out;
}

// The current user's effective permissions, straight from the backend — i.e.
// whatever Role Management has configured for their role (administration.roles).
// Auth-only endpoint, so it works for every role (admin and non-admin alike).
export async function fetchMyPermissions(): Promise<PermissionMap | null> {
  try {
    const { data } = await api.get<Partial<PermissionMap>>("/auth/my-permissions");
    return data && Object.keys(data).length ? (data as PermissionMap) : null;
  } catch {
    return null; // unreachable / unauthorized → caller keeps the default map
  }
}

interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  roleLabel?: string;
  department?: string;
  lastLogin?: string;
  status?: string;
  avatar?: string;
  // True when the account was created with a temporary password (or an admin
  // reset it): the user must set a new password before using the app. Cleared
  // by the backend once the password is changed (POST /auth/change-password).
  mustResetPassword?: boolean;
}

interface AuthCtx {
  user: User | null;
  token: string | null;
  permissions: PermissionMap;
  /** True while the initial session bootstrap (on refresh) is in flight. */
  loading: boolean;
  setSession: (token: string, user: User) => void;
  signOut: () => void;
  hasRole: (...roles: Role[]) => boolean;
  canAccess: (path: string) => boolean;
  canEdit: (path: string) => boolean;
  refreshPermissions: () => void;
  /** Clear the forced-password-change flag once the user has set a new password. */
  clearMustResetPassword: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [permissions, setPermissions] = useState<PermissionMap>(ALL_TRUE);
  // Start true so the first paint (server + client) is a neutral shell until the
  // bootstrap resolves — no flash of content or a premature login redirect.
  const [loading, setLoading] = useState(true);

  // Pull the user's effective permissions from the backend (the authoritative
  // Role Management config) and normalise them over the role defaults.
  const loadPerms = useCallback(async (role: Role) => {
    const backend = await fetchMyPermissions();
    setPermissions(normalizePerms(role, backend));
  }, []);

  // Session bootstrap — the single place that hydrates the session from the
  // backend using the stored bearer token: the user (/auth/me) AND permissions
  // (/auth/my-permissions), fetched together. Context is the ONLY source of
  // truth (no sessionStorage user/perms copy). Runs on every refresh; a failure
  // means the token is invalid/expired, so we clear the session.
  const bootstrap = useCallback(async () => {
    try {
      const [meRes, backendPerms] = await Promise.all([
        api.get<User>("/auth/me"),
        fetchMyPermissions(),
      ]);
      const me = meRes.data;
      setPermissions(normalizePerms(me.role, backendPerms));
      setUser(me);
    } catch {
      if (typeof window !== "undefined") sessionStorage.removeItem("radonaix_token");
      setToken(null);
      setUser(null);
      setPermissions(ALL_TRUE);
    } finally {
      setLoading(false);
    }
  }, []);

  // On load (refresh): if a token survived, restore it and bootstrap the session
  // from the backend. Otherwise there is no session to restore.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const t = sessionStorage.getItem("radonaix_token");
    if (t) {
      setToken(t);
      bootstrap();
    } else {
      setLoading(false);
    }
  }, [bootstrap]);

  // Role Management dispatches this after saving — re-pull the live session's
  // permissions so changes to the current user's own role take effect at once.
  useEffect(() => {
    const handler = () => { if (user) loadPerms(user.role); };
    window.addEventListener("radonaix:perms-updated", handler);
    return () => window.removeEventListener("radonaix:perms-updated", handler);
  }, [loadPerms, user]);

  // Login: persist ONLY the bearer token; user + permissions live in context.
  const setSession = (t: string, u: User) => {
    if (typeof window !== "undefined") sessionStorage.setItem("radonaix_token", t);
    setToken(t);
    setUser(u);
    setPermissions(normalizePerms(u.role, null)); // immediate role default…
    setLoading(false);
    loadPerms(u.role);                             // …then the authoritative map
  };

  const signOut = () => {
    if (typeof window !== "undefined") sessionStorage.removeItem("radonaix_token");
    setToken(null);
    setUser(null);
    setPermissions(ALL_TRUE);
    setLoading(false);
  };

  const hasRole = (...roles: Role[]) => !!user && roles.includes(user.role);

  const canAccess = (path: string) => {
    if (!user) return false;
    if (path === "/profile" || path === "/access-denied" || path === "/login") return true;
    const key = PATH_TO_PERM[path];
    if (!key) return true;
    return !!permissions[key]?.view;
  };

  const canEdit = (path: string) => {
    if (!user) return false;
    const key = PATH_TO_PERM[path];
    if (!key) return true;
    return !!permissions[key]?.edit;
  };

  const refreshPermissions = () => { if (user) loadPerms(user.role); };

  // After a forced password change succeeds, drop the flag locally so the gate
  // releases the user into the app without needing a full re-login.
  const clearMustResetPassword = () => {
    setUser((prev) => (prev ? { ...prev, mustResetPassword: false } : prev));
  };

  return (
    <Ctx.Provider value={{ user, token, permissions, loading, setSession, signOut, hasRole, canAccess, canEdit, refreshPermissions, clearMustResetPassword }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
