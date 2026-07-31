// ---------------------------------------------------------------------------
// Shared email validation for the auth + user-management forms.
//
// The local part and domain are restricted to a safe character set, so
// addresses containing unsupported special characters (e.g. `name!#$@x.com`,
// `a b@x.com`, `foo@bar`) are rejected during input validation — not just
// missing-`@` cases. Kept deliberately practical (not full RFC 5322): letters,
// digits and `. _ % + -` in the local part; a dotted domain with a 2+ letter TLD.
// ---------------------------------------------------------------------------

/** Local part (before `@`): letters, digits and . _ % + - only. */
export const EMAIL_LOCAL_RE = /^[A-Za-z0-9._%+-]+$/;
/** Domain (after `@`): letters/digits/hyphens/dots ending in a 2+ letter TLD. */
export const EMAIL_DOMAIN_RE = /^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;
/** Whole-address shorthand (used where a single boolean/regex test is enough). */
export const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;

type T = (s: string) => string;

/**
 * Validate an email and return a specific, human-readable error — or null when
 * valid. `t` is the i18n translate function (pass `(s) => s` for a raw check).
 */
export function validateEmail(t: T, raw: string): string | null {
  const value = raw.trim();
  if (!value) return t("Please enter your email address.");
  if (/\s/.test(raw)) return t("Email address must not contain spaces.");
  if (!value.includes("@")) return t("Email address must include an '@' symbol.");
  const parts = value.split("@");
  if (parts.length > 2) return t("Email address must contain only one '@' symbol.");
  const [local, domain] = parts;
  if (!local) return t("Enter the part before the '@' (e.g. name@example.com).");
  if (!domain) return t("Enter the domain after the '@' (e.g. name@example.com).");
  if (!EMAIL_LOCAL_RE.test(local))
    return t("Email address contains unsupported special characters.");
  if (!EMAIL_DOMAIN_RE.test(domain))
    return t("Add a valid domain ending such as .com or .in (e.g. name@example.com).");
  return null;
}

/** Boolean convenience wrapper around {@link validateEmail}. */
export function isValidEmail(raw: string): boolean {
  return validateEmail((s) => s, raw) === null;
}
