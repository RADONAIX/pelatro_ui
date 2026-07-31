import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { AlertCircle, KeyRound, Loader2, LogOut, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { authService } from "@/services";
import { PasswordField, RuleRow, passwordRules, validateNewPassword } from "./passwordRules";

// Full-screen gate shown when the signed-in user must set a new password before
// using the app (temporary password on first login, or an admin reset). Backed
// by POST /auth/change-password, which requires the current (temporary) password
// and clears the mustResetPassword flag server-side on success.
export function ForcePasswordChange() {
  const t = useT();
  const navigate = useNavigate();
  const { user, signOut, clearMustResetPassword } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const rules = passwordRules(t, next);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!current) errs.current = t("Enter your current (temporary) password.");
    const nextErr = validateNewPassword(t, next, current);
    if (nextErr) errs.next = nextErr;
    if (!confirm) errs.confirm = t("Please confirm your new password.");
    else if (next && next !== confirm) errs.confirm = t("New passwords do not match.");
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;

    setFormError("");
    setSaving(true);
    try {
      await authService.changePassword(current, next);
      clearMustResetPassword();
      toast.success(t("Password updated"), { description: t("You can now use the application.") });
      navigate({ to: "/", replace: true });
    } catch (err: any) {
      setFormError(err?.response?.data?.error?.message || err?.message || t("Could not change password"));
    } finally {
      setSaving(false);
    }
  };

  const onSignOut = () => {
    signOut();
    navigate({ to: "/login", replace: true });
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md bg-card border border-border rounded-2xl shadow-2xl overflow-hidden">
        <div className="h-1.5 w-full bg-gradient-to-r from-[#F8C800] via-primary to-primary" />
        <div className="px-6 pt-6 pb-5">
          <div className="mx-auto h-14 w-14 rounded-2xl flex items-center justify-center bg-primary/10 text-primary ring-8 ring-primary/10">
            <ShieldAlert className="h-7 w-7" />
          </div>
          <h1 className="mt-4 text-center text-xl font-bold tracking-tight text-foreground">{t("Set a new password")}</h1>
          <p className="mt-2 text-center text-sm text-muted-foreground leading-relaxed">
            {t("Your account uses a temporary password. Please choose a new password to continue.")}
            {user?.email && (
              <span className="block mt-1 font-medium text-foreground/80">{user.email}</span>
            )}
          </p>

          <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
            <PasswordField
              label={t("Current (temporary) password")}
              value={current}
              onChange={(v) => { setCurrent(v); setErrors((p) => ({ ...p, current: "" })); setFormError(""); }}
              error={errors.current}
              autoComplete="current-password"
              placeholder={t("Enter the password you just used to sign in")}
              hint="Your existing password, needed to authorize this change."
            />
            <PasswordField
              label={t("New password")}
              value={next}
              onChange={(v) => { setNext(v); setErrors((p) => ({ ...p, next: "" })); setFormError(""); }}
              error={errors.next}
              hint="Must be 8–20 characters with upper and lower case letters and a number. Must differ from your current password."
              disablePaste
            />
            <PasswordField
              label={t("Confirm new password")}
              value={confirm}
              onChange={(v) => { setConfirm(v); setErrors((p) => ({ ...p, confirm: "" })); }}
              error={errors.confirm}
              hint="Re-enter the new password exactly to confirm there are no typos."
              disablePaste
            />

            <div className="rounded-xl bg-muted/40 p-4 space-y-2">
              {rules.map((r) => <RuleRow key={r.label} met={r.met} label={r.label} />)}
            </div>

            {formError && (
              <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm font-medium text-destructive">
                <AlertCircle className="h-4 w-4 shrink-0" /> {formError}
              </div>
            )}

            <button
              type="submit"
              disabled={saving}
              className="w-full h-12 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 disabled:opacity-60 inline-flex items-center justify-center gap-2"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
              {t("Set password & continue")}
            </button>
          </form>
        </div>

        <div className="px-6 pb-6">
          <button
            type="button"
            onClick={onSignOut}
            className="w-full h-10 rounded-xl border border-border text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition inline-flex items-center justify-center gap-2"
          >
            <LogOut className="h-4 w-4" /> {t("Sign out")}
          </button>
        </div>
      </div>
    </div>
  );
}
