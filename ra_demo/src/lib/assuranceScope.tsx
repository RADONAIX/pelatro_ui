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

export const DEFAULT_SCOPE: AssuranceScope = ASSURANCE_WORKSPACE_GROUPS[0].apps[0].id;

// Same key as before. Values used to be display names ("Mediation Assurance");
// they are now app ids. isKnownScope rejects the old shape, so a stale entry
// falls back to the default instead of selecting nothing — no migration needed.
const STORAGE_KEY = "radonaix_scope";

interface ScopeCtx {
  /** The selected app's id, e.g. "usage". */
  scope: AssuranceScope;
  setScope: (scope: AssuranceScope) => void;
  /** The selected app's metadata — never null, the scope is always a valid id. */
  app: AppMetadata;
}

const Ctx = createContext<ScopeCtx | null>(null);

function isKnownScope(value: string | null): value is AssuranceScope {
  return !!value && ASSURANCE_APPS.some((a) => a.id === value);
}

export function AssuranceScopeProvider({ children }: { children: ReactNode }) {
  // Always start from the default so the server-rendered markup matches the
  // first client render (no hydration mismatch); the persisted choice is
  // applied in an effect right after mount, exactly like dark mode and i18n.
  const [scope, setScopeState] = useState<AssuranceScope>(DEFAULT_SCOPE);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (isKnownScope(saved)) setScopeState(saved);
    } catch {
      /* ignore unavailable storage */
    }
  }, []);

  const setScope = useCallback((next: AssuranceScope) => {
    if (!isKnownScope(next)) return;
    setScopeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(
    () => ({ scope, setScope, app: getApp(scope) ?? getApp(DEFAULT_SCOPE)! }),
    [scope, setScope],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssuranceScope(): ScopeCtx {
  const ctx = useContext(Ctx);
  // Falling back rather than throwing keeps any component that renders outside
  // the provider (tests, storybook-style previews) on the default scope, which
  // is the pre-existing behaviour.
  return (
    ctx ?? {
      scope: DEFAULT_SCOPE,
      setScope: () => {},
      app: getApp(DEFAULT_SCOPE)!,
    }
  );
}
