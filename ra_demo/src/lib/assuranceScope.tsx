import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  APPS,
  WORKSPACES,
  getApp,
  type AppMetadata,
} from "@/lib/assurance/platform-metadata";

// ---------------------------------------------------------------------------
// The Assurance Scope the header switcher selects.
//
// The scope used to be a flat list of hand-written strings. It is now the
// Enterprise Assurance app itself, grouped by workspace — Commercial, Customer,
// Revenue, Operations and Financial — because that hierarchy already exists in
// platform-metadata and every app declares which workspace it belongs to.
//
// The scope no longer swaps the sidebar. The sidebar is fixed; this value only
// decides WHICH app the Enterprise Assurance section of it points at, so
// switching scope re-targets those six links and the entity list without
// disturbing anything above them.
// ---------------------------------------------------------------------------

/**
 * All eight assurance apps are selectable, Rating included.
 *
 * Rating was excluded while the scope still swapped the whole sidebar — back
 * then "Rating Assurance" was ambiguous between the live /rating/* module and
 * ASSURA's app of the same name. The sidebar is fixed now: the rating nav is
 * always the spine, and this switcher only re-targets Controls and
 * Administration. Selecting Rating here means "scope those two to the rating
 * domain", which is exactly what the other seven entries mean, so there is
 * nothing left to disambiguate.
 */
export const ASSURANCE_APPS: AppMetadata[] = APPS;

export interface AssuranceWorkspaceGroup {
  id: string;
  name: string;
  tagline: string;
  apps: AppMetadata[];
}

/**
 * The switcher's render order. Workspaces left empty by the exclusion above are
 * dropped rather than rendered as a bare heading.
 */
export const ASSURANCE_WORKSPACE_GROUPS: AssuranceWorkspaceGroup[] = WORKSPACES.map(
  (ws) => ({
    id: ws.id,
    name: ws.name,
    tagline: ws.tagline,
    apps: ASSURANCE_APPS.filter((a) => a.workspace === ws.id),
  }),
).filter((g) => g.apps.length > 0);

export type AssuranceScope = string;

/**
 * There is deliberately no default assurance.
 *
 * Picking the first app of the first workspace meant a session silently opened
 * on whichever assurance happened to sort first — the user saw its dashboard,
 * controls and reports without ever having chosen it. A session now starts with
 * nothing selected and Home is where the choice is made.
 */

// sessionStorage, not localStorage: a refresh mid-session must not lose the
// assurance you are working in, but a new sign-in has to start unselected. The
// auth token lives in sessionStorage for the same reason, so the two expire
// together.
const STORAGE_KEY = "radonaix_scope";

interface ScopeCtx {
  /** The selected app's id, or null when the user has not chosen one yet. */
  scope: AssuranceScope | null;
  setScope: (scope: AssuranceScope) => void;
  /** The selected app's metadata, or null while nothing is selected. */
  app: AppMetadata | null;
}

const Ctx = createContext<ScopeCtx | null>(null);

function isKnownScope(value: string | null): value is AssuranceScope {
  return !!value && ASSURANCE_APPS.some((a) => a.id === value);
}

export function AssuranceScopeProvider({ children }: { children: ReactNode }) {
  // Always start unselected so the server-rendered markup matches the first
  // client render (no hydration mismatch); a choice made earlier in this
  // session is applied in an effect right after mount, exactly like dark mode
  // and i18n.
  const [scope, setScopeState] = useState<AssuranceScope | null>(null);

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(STORAGE_KEY);
      if (isKnownScope(saved)) setScopeState(saved);
    } catch {
      /* ignore unavailable storage */
    }
  }, []);

  const setScope = useCallback((next: AssuranceScope) => {
    if (!isKnownScope(next)) return;
    setScopeState(next);
    try {
      sessionStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(
    () => ({ scope, setScope, app: scope ? (getApp(scope) ?? null) : null }),
    [scope, setScope],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssuranceScope(): ScopeCtx {
  const ctx = useContext(Ctx);
  // Falling back rather than throwing keeps any component that renders outside
  // the provider (tests, storybook-style previews) working — unselected, which
  // is now the same state a fresh session starts in.
  return ctx ?? { scope: null, setScope: () => {}, app: null };
}
