import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { useState } from "react";
import { AlertCircle, KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { authService } from "@/services";
import { useT } from "@/lib/i18n";
import { PasswordField, RuleRow, passwordRules, validateNewPassword } from "@/components/auth/passwordRules";

export const Route = createFileRoute("/reset-password")({
  component: ResetPasswordPage,
  // Read the emailed ?token=… from the URL.
  validateSearch: (search: Record<string, unknown>): { token?: string } => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
});

function ResetPasswordPage() {
  const t = useT();
  const navigate = useNavigate();
  const { token } = Route.useSearch();
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const rules = passwordRules(t, next);
  const missingToken = !token;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (missingToken) return;
    const errs: Record<string, string> = {};
    const nextErr = validateNewPassword(t, next);
    if (nextErr) errs.next = nextErr;
    if (!confirm) errs.confirm = t("Please confirm your new password.");
    else if (next && next !== confirm) errs.confirm = t("New passwords do not match.");
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;

    setFormError("");
    setSaving(true);
    try {
      await authService.resetPassword(token!, next);
      toast.success(t("Password reset"), { description: t("You can now sign in with your new password.") });
      navigate({ to: "/login", replace: true });
    } catch (err: any) {
      setFormError(err?.message || t("This reset link is invalid or has expired. Please request a new one."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md bg-card border border-border rounded-2xl shadow-sm overflow-hidden">
        <div className="h-1.5 w-full bg-primary" />
        <div className="px-6 py-8">
          <div className="mx-auto h-14 w-14 rounded-2xl flex items-center justify-center bg-primary/10 text-primary ring-8 ring-primary/10">
            <ShieldCheck className="h-7 w-7" />
          </div>
          <h1 className="mt-4 text-center text-xl font-bold tracking-tight text-foreground">{t("Reset your password")}</h1>
          <p className="mt-2 text-center text-sm text-muted-foreground leading-relaxed">
            {t("Choose a new password for your account.")}
          </p>

          {missingToken ? (
            <div className="mt-6 text-center">
              <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm font-medium text-destructive text-left">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {t("This reset link is missing its token. Please use the link from your email or request a new one.")}
              </div>
              <Link
                to="/forgot-password"
                className="mt-4 inline-flex items-center justify-center gap-2 h-11 w-full rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90"
              >
                {t("Request a new link")}
              </Link>
            </div>
          ) : (
            <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
              <PasswordField
                label={t("New password")}
                value={next}
                onChange={(v) => { setNext(v); setErrors((p) => ({ ...p, next: "" })); setFormError(""); }}
                error={errors.next}
                disablePaste
              />
              <PasswordField
                label={t("Confirm new password")}
                value={confirm}
                onChange={(v) => { setConfirm(v); setErrors((p) => ({ ...p, confirm: "" })); }}
                error={errors.confirm}
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
                {t("Reset password")}
              </button>

              <Link to="/login" className="block text-center text-sm font-medium text-muted-foreground hover:text-foreground transition">
                {t("Back to sign in")}
              </Link>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
