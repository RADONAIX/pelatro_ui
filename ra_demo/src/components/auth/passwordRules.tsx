import { CheckCircle2, Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import type { useT } from "@/lib/i18n";
import { InfoHint } from "../ui-kit/InfoHint";

// Shared password length bounds — enforced across login, profile, user
// management and the reset/force-change flows.
export const PASSWORD_MIN_LEN = 8;
export const PASSWORD_MAX_LEN = 20;

// Shared password policy — mirrors the User Management / Profile modules:
// 8–20 characters with upper + lower case letters and a number.
export function passwordRules(t: ReturnType<typeof useT>, value: string) {
  return [
    { label: t("At least 8 characters"), met: value.length >= PASSWORD_MIN_LEN && value.length <= PASSWORD_MAX_LEN },
    { label: t("One uppercase letter"), met: /[A-Z]/.test(value) },
    { label: t("One lowercase letter"), met: /[a-z]/.test(value) },
    { label: t("One number"), met: /[0-9]/.test(value) },
  ];
}

// Returns a specific validation message for a new password, or null when valid.
export function validateNewPassword(
  t: ReturnType<typeof useT>,
  value: string,
  currentPassword?: string,
): string | null {
  if (!value) return t("New password is required.");
  if (value.length < PASSWORD_MIN_LEN) return t("New password must be at least 8 characters.");
  if (value.length > PASSWORD_MAX_LEN) return t("Password must be at most 20 characters.");
  if (!passwordRules(t, value).every((r) => r.met)) return t("New password doesn't meet all requirements.");
  if (currentPassword !== undefined && value === currentPassword)
    return t("New password must be different from the current one.");
  return null;
}

export function RuleRow({ met, label }: { met: boolean; label: string }) {
  return (
    <div className={`flex items-center gap-2 text-sm ${met ? "text-foreground" : "text-muted-foreground"}`}>
      <CheckCircle2 className={`h-4 w-4 shrink-0 ${met ? "text-success" : "text-muted-foreground/50"}`} />
      <span>{label}</span>
    </div>
  );
}

// Labelled password input with a show/hide toggle and an inline error slot.
export function PasswordField({
  label,
  value,
  onChange,
  error,
  autoComplete = "new-password",
  placeholder,
  hint='',
  disablePaste = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  autoComplete?: string;
  placeholder?: string;
  hint?: string;
  /** Block paste/copy/cut/drop — used on the new & confirm fields so the user
   *  must type them out (and can't copy the new value into confirm). */
  disablePaste?: boolean;
}) {
  const [show, setShow] = useState(false);
  const blockClipboard = disablePaste
    ? (e: React.ClipboardEvent<HTMLInputElement>) => e.preventDefault()
    : undefined;
  return (
    <div>
       <label className="flex items-center gap-1  font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
        <span className=" text-sm font-semibold text-foreground mb-2">{label} <span className="text-destructive ml-0.5">*</span></span>
        {hint && <InfoHint text={hint} side="top" />}
      </label>
      <div className="relative">
        <input
          type={show ? "text" : "password"}
          value={value}
          maxLength={PASSWORD_MAX_LEN}
          autoComplete={autoComplete}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onPaste={blockClipboard}
          onCopy={blockClipboard}
          onCut={blockClipboard}
          onDrop={disablePaste ? (e) => e.preventDefault() : undefined}
          aria-invalid={!!error}
          className={`w-full h-12 px-4 pr-11 rounded-xl bg-card border text-sm focus:outline-none focus:ring-2 transition ${
            error
              ? "border-destructive focus:border-destructive focus:ring-destructive/20"
              : "border-border focus:border-primary focus:ring-primary/20"
          }`}
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide password" : "Show password"}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          {show ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
        </button>
      </div>
      {error && <p className="mt-1.5 text-xs font-medium text-destructive">{error}</p>}
    </div>
  );
}
