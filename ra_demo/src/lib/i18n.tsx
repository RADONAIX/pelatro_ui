import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { FR } from "./translations";

// ---------------------------------------------------------------------------
// Lightweight application i18n.
//
// We use the *English source string as the translation key* (gettext-style):
//   t("User Profile")  → "Profil utilisateur" in French, "User Profile" in English.
// This keeps the call sites readable and means a missing French entry simply
// falls back to the (already meaningful) English text rather than a raw key.
//
// Only the application chrome is translated — never the user's/backend data.
// ---------------------------------------------------------------------------

export type Language = "en" | "fr";

const STORAGE_KEY = "radonaix_lang";

export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "English" },
  { code: "fr", label: "Français" },
];

const DICTS: Record<Language, Record<string, string>> = {
  en: {}, // identity — the source strings are already English
  fr: FR,
};

interface I18nCtx {
  lang: Language;
  setLang: (lang: Language) => void;
  t: (key: string) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  // Always start from "en" so the server-rendered markup matches the first
  // client render (no hydration mismatch). The persisted choice is applied in
  // an effect right after mount, mirroring how the app handles dark mode.
  const [lang, setLangState] = useState<Language>("en");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY) as Language | null;
      if (saved === "en" || saved === "fr") setLangState(saved);
    } catch {
      /* ignore */
    }
  }, []);

  // Keep <html lang> in sync for accessibility / correct hyphenation.
  useEffect(() => {
    if (typeof document !== "undefined") document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Language) => {
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const t = useCallback(
    (key: string) => DICTS[lang][key] ?? key,
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLanguage() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLanguage must be used within LanguageProvider");
  return ctx;
}

/** Convenience hook returning just the translate function. */
export function useT() {
  return useLanguage().t;
}
