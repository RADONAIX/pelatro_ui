import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------------------
// The Assurance Scope the header switcher selects.
//
// The value already lived in localStorage under `radonaix_scope`; this provider
// lifts it out of the Header's local state so other surfaces (currently the
// Sidebar) can react to it. Same storage key, same default, same option list —
// nothing about the switcher's appearance or persistence changes.
// ---------------------------------------------------------------------------

// Order here IS the order the header switcher renders. Everything that reads a
// scope compares it by value, never by position, so this list can be reordered
// or extended without touching another file.
export const ASSURANCE_SCOPES = [
  "Mediation Assurance",
  "Rating Assurance",
  "Billing Assurance",
  "Balance Assurance",
  "Usage Assurance",
  "Roaming Assurance",
  "Subscription Assurance",
  "Summary (all assurances)",
] as const;

export type AssuranceScope = (typeof ASSURANCE_SCOPES)[number];

export const RATING_SCOPE: AssuranceScope = "Rating Assurance";
export const DEFAULT_SCOPE: AssuranceScope = "Mediation Assurance";

const STORAGE_KEY = "radonaix_scope";

interface ScopeCtx {
  scope: AssuranceScope;
  setScope: (scope: AssuranceScope) => void;
  /** True when the Rating Assurance module owns the navigation. */
  isRating: boolean;
}

const Ctx = createContext<ScopeCtx | null>(null);

function isKnownScope(value: string | null): value is AssuranceScope {
  return !!value && (ASSURANCE_SCOPES as readonly string[]).includes(value);
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
    setScopeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(
    () => ({ scope, setScope, isRating: scope === RATING_SCOPE }),
    [scope, setScope],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssuranceScope(): ScopeCtx {
  const ctx = useContext(Ctx);
  // Falling back rather than throwing keeps any component that renders outside
  // the provider (tests, storybook-style previews) on the default scope, which
  // is the pre-existing behaviour.
  return ctx ?? { scope: DEFAULT_SCOPE, setScope: () => {}, isRating: false };
}
