import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { useState } from "react";
import { AlertCircle, ArrowLeft, Loader2, Mail, MailCheck, Send } from "lucide-react";
import { authService } from "@/services";
import { useT } from "@/lib/i18n";
import { validateEmail } from "@/lib/email";
import { InfoHint } from "@/components/ui-kit/InfoHint";

export const Route = createFileRoute("/forgot-password")({
  component: ForgotPasswordPage,
});

function ForgotPasswordPage() {
  const t = useT();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const emailErr = validateEmail(t, email);
    if (emailErr) {
      setError(emailErr);
      return;
    }
    setError("");
    setLoading(true);
    try {
      await authService.requestPasswordReset(email.trim());
      // The backend replies 200 regardless of whether the email exists (to avoid
      // account enumeration), so we always show the same confirmation.
      setSent(true);
    } catch (err: any) {
      setError(err?.message || t("Could not send the reset email. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md bg-card border border-border rounded-2xl shadow-sm overflow-hidden">
        <div className="h-1.5 w-full bg-primary" />
        <div className="px-6 py-8">
          {sent ? (
            <div className="text-center">
              <div className="mx-auto h-14 w-14 rounded-2xl flex items-center justify-center bg-success/10 text-success ring-8 ring-success/10">
                <MailCheck className="h-7 w-7" />
              </div>
              <h1 className="mt-4 text-xl font-bold tracking-tight text-foreground">{t("Check your email")}</h1>
              <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                {t("If an account exists for")} <span className="font-medium text-foreground">{email.trim()}</span>,{" "}
                {t("we've sent a link to reset your password. The link expires shortly for your security.")}
              </p>
              <Link
                to="/login"
                className="mt-6 inline-flex items-center justify-center gap-2 h-11 w-full rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90"
              >
                {t("Back to sign in")}
              </Link>
            </div>
          ) : (
            <>
              <div className="mx-auto h-14 w-14 rounded-2xl flex items-center justify-center bg-primary/10 text-primary ring-8 ring-primary/10">
                <Mail className="h-7 w-7" />
              </div>
              <h1 className="mt-4 text-center text-xl font-bold tracking-tight text-foreground">{t("Forgot your password?")}</h1>
              <p className="mt-2 text-center text-sm text-muted-foreground leading-relaxed">
                {t("Enter your account email and we'll send you a link to reset your password.")}
              </p>

              <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
                <div>
                  <label htmlFor="fp-email" className="flex items-center gap-1.5 text-sm font-semibold text-foreground mb-2">
                    {t("Email")}
                    <InfoHint text={t("Enter the email linked to your account. If it exists, we'll send a password reset link that expires shortly for your security.")} />
                  </label>
                  <div className="relative">
                    <Mail className={`absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 ${error ? "text-destructive" : "text-muted-foreground"}`} />
                    <input
                      id="fp-email"
                      type="email"
                      inputMode="email"
                      autoComplete="email"
                      maxLength={254}
                      value={email}
                      onChange={(e) => { setEmail(e.target.value); if (error) setError(""); }}
                      placeholder={t("Enter your Email")}
                      aria-invalid={!!error}
                      className={`w-full h-12 pl-12 pr-4 rounded-xl bg-card border text-sm focus:outline-none focus:ring-2 transition ${
                        error
                          ? "border-destructive focus:border-destructive focus:ring-destructive/20"
                          : "border-border focus:border-primary focus:ring-primary/20"
                      }`}
                    />
                  </div>
                  {error && (
                    <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive">
                      <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {error}
                    </p>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={loading}
                  className="w-full h-12 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 disabled:opacity-60 inline-flex items-center justify-center gap-2"
                >
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  {t("Send reset link")}
                </button>
              </form>

              <button
                type="button"
                onClick={() => navigate({ to: "/login" })}
                className="mt-4 w-full inline-flex items-center justify-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition"
              >
                <ArrowLeft className="h-4 w-4" /> {t("Back to sign in")}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
