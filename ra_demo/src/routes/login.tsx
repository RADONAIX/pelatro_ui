import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { validateEmail as validateEmailRule } from "@/lib/email";
import { authService } from "@/services";
import { toast } from "sonner";

type SsoProvider = "google" | "microsoft";
import { AlertCircle, ArrowRight, Eye, EyeOff, Loader2, Lock, Mail } from "lucide-react";
import loginHero from "@/assets/login-hero.jpg";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { InfoHint } from "@/components/ui-kit/InfoHint";

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

// Floating validation callout shown anchored under a field (browser-bubble style):
// a red-bordered popover with an alert icon and a small pointer arrow.
function FieldError({ id, message }: { id: string; message: string }) {
  return (
    <div
      id={id}
      role="alert"
      className="absolute left-0 top-full mt-2 z-20 flex max-w-md items-start gap-2 rounded-lg border border-destructive bg-card px-3 py-2 shadow-lg"
    >
      <span aria-hidden className="absolute -top-1.5 left-5 h-3 w-3 rotate-45 border-l border-t border-destructive bg-card" />
      <AlertCircle className="relative h-4 w-4 shrink-0 text-destructive mt-0.5" />
      <span className="relative text-xs font-medium leading-snug text-destructive">{message}</span>
    </div>
  );
}

// Field length caps so a single field can't be pasted into unbounded. 254 is the
// RFC 5321 maximum length of an email address; 20 matches the app's password
// policy (8–20 characters).
const EMAIL_MAX = 254;
const PASSWORD_MAX = 20;

function LoginPage() {
  const { setSession } = useAuth();
  const t = useT();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(false);
  // Per-field validation messages. Set on submit (and cleared as the user types)
  // so each field gets a *specific* message instead of the browser's generic,
  // input-echoing bubble.
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  // Guard against React StrictMode double-invoking the callback effect.
  const ssoHandled = useRef(false);

  // Specific, human-readable email validation (shared rule; rejects unsupported
  // special characters). Returns null when valid.
  const validateEmail = (raw: string): string | null => validateEmailRule(t, raw);

  const validatePassword = (value: string): string | null => {
    if (!value) return t("Please enter your password.");
    return null;
  };

  // SSO callback: the BACKEND completes the OAuth code exchange server-side and
  // redirects back to /login?token=<jwt> (or ?error=…). We just resolve the
  // user from that token and start the session — no token exchange in browser.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const err = params.get("error");
    if (!token && !err) return;
    if (ssoHandled.current) return;
    ssoHandled.current = true;

    if (err) {
      toast.error(decodeURIComponent(err));
      window.history.replaceState({}, "", "/login");
      return;
    }
    setSsoLoading(true);
    (async () => {
      try {
        const user = await authService.me(token!);
        setSession(token!, user);
        toast.success(t("Welcome back"));
        // Replace the /login?token=… entry with the dashboard — this both leaves
        // the login screen and drops the token from browser history. Do NOT also
        // call history.replaceState here: it would override this navigation and
        // keep the user stuck on /login.
        navigate({ to: "/", replace: true });
      } catch (e: any) {
        setSsoLoading(false);
        toast.error(e?.message || t("Login failed"));
        window.history.replaceState({}, "", "/login");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Hand off to the backend OAuth login endpoint — the whole exchange (and the
  // client secret) stays server-side.
  const onSso = (provider: SsoProvider) => {
    setSsoLoading(true);
    window.location.href = authService.ssoLoginUrl(provider);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Validate both fields up front so an empty form highlights *both* the email
    // and password fields (not just the first one) with specific messages.
    const emailErr = validateEmail(email);
    const passwordErr = validatePassword(password);
    if (emailErr || passwordErr) {
      setErrors({ email: emailErr ?? undefined, password: passwordErr ?? undefined });
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      const res = await authService.login(email, password);
      setSession(res.token, res.user);
      if (res.user?.mustResetPassword) {
        // Temporary password: the SessionGate will present the forced
        // password-change screen. Nudge the user rather than say "Welcome back".
        toast.message(t("Set a new password to continue"));
      } else {
        toast.success(t("Welcome back"));
      }
      navigate({ to: "/" });
    } catch (err: any) {
      // Surface the backend's message (e.g. "Your account has been disabled.",
      // "Invalid email or password.", lockout) instead of the generic axios
      // "Request failed with status code 401".
      toast.error(err?.response?.data?.error?.message || err?.message || t("Login failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-background">
      {/* Left: hero illustration */}
      <div className="hidden lg:block relative overflow-hidden bg-gradient-to-br from-amber-50 via-white to-yellow-50">
        <img
          src={loginHero}
          alt={t("Secure revenue assurance")}
          className="absolute inset-0 h-full w-full object-cover"
          width={1280}
          height={1280}
        />
      </div>

      {/* Right: form */}
      <div className="flex items-center justify-center p-6 sm:p-12 bg-card">
        <div className="w-full max-w-md">
          {/* Product mark */}
          <div className="mb-10 flex items-center gap-3">
            <div className="h-14 w-14 rounded-xl bg-primary flex items-center justify-center shadow-sm">
              <span className="font-extrabold text-xl tracking-tight text-primary-foreground" aria-label="RADONaix">
                RA
              </span>
            </div>
            <div className="min-w-0">
              <div className="font-semibold tracking-tight text-base leading-none text-foreground">RADONaix</div>
              <div className="text-xs text-muted-foreground mt-1">{t("Revenue Assurance")}</div>
            </div>
          </div>

          <h1 className="text-4xl font-bold tracking-tight text-foreground">{t("Welcome back")}</h1>
          <p className="mt-2 text-base text-muted-foreground">{t("Sign in to your account")}</p>

          <form onSubmit={onSubmit} noValidate className="mt-10 space-y-6">
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <label htmlFor="login-email" className="text-sm font-semibold text-foreground">{t("Email")}</label>
                <InfoHint text={t("Enter the email address registered with your account.")} />
              </div>
              <div className="relative">
                <Mail className={`absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 ${errors.email ? "text-destructive" : "text-muted-foreground"}`} />
                <input
                  id="login-email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  maxLength={EMAIL_MAX}
                  placeholder={t("Enter your Email")}
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    if (errors.email) setErrors((p) => ({ ...p, email: undefined }));
                  }}
                  aria-invalid={!!errors.email}
                  aria-describedby={errors.email ? "login-email-error" : undefined}
                  className={`w-full h-14 pl-12 pr-4 rounded-2xl bg-card border focus:outline-none focus:ring-2 text-sm transition ${
                    errors.email
                      ? "border-destructive focus:border-destructive focus:ring-destructive/20"
                      : "border-border focus:border-primary focus:ring-primary/20"
                  }`}
                />
                {errors.email && <FieldError id="login-email-error" message={errors.email} />}
              </div>
            </div>

            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <label htmlFor="login-password" className="text-sm font-semibold text-foreground">{t("Password")}</label>
                <InfoHint text={t("Enter your account password. Use the eye icon to show or hide it.")} />
              </div>
              <div className="relative">
                <Lock className={`absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 ${errors.password ? "text-destructive" : "text-muted-foreground"}`} />
                <input
                  id="login-password"
                  type={showPwd ? "text" : "password"}
                  autoComplete="current-password"
                  maxLength={PASSWORD_MAX}
                  placeholder={t("Enter your password")}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (errors.password) setErrors((p) => ({ ...p, password: undefined }));
                  }}
                  aria-invalid={!!errors.password}
                  aria-describedby={errors.password ? "login-password-error" : undefined}
                  className={`w-full h-14 pl-12 pr-12 rounded-2xl bg-card border focus:outline-none focus:ring-2 text-sm transition ${
                    errors.password
                      ? "border-destructive focus:border-destructive focus:ring-destructive/20"
                      : "border-border focus:border-primary focus:ring-primary/20"
                  }`}
                />
                <Tooltip label={showPwd ? t("Hide password") : t("Show password")} side="left" instant className="!absolute right-4 top-1/2 -translate-y-1/2">
                  <button
                    type="button"
                    onClick={() => setShowPwd((s) => !s)}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label={showPwd ? t("Hide password") : t("Show password")}
                  >
                    {showPwd ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                  </button>
                </Tooltip>
                {errors.password && <FieldError id="login-password-error" message={errors.password} />}
              </div>
              <div className="mt-2 flex justify-end">
                <Link to="/forgot-password" className="text-sm font-medium text-primary hover:underline">
                  {t("Forgot password?")}
                </Link>
              </div>
            </div>

            <Tooltip label={t("Sign in to your account")} side="bottom" className="w-full">
              <button
                type="submit"
                disabled={loading}
                className="relative group w-full h-14 rounded-2xl bg-primary text-primary-foreground font-semibold text-base hover:opacity-95 transition flex items-center justify-center gap-2 shadow-sm disabled:opacity-60"
              >
                {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : (
                  <>
                    <span>{t("Sign in")}</span>
                    <ArrowRight className="h-5 w-5 absolute right-8 transition-transform group-hover:translate-x-1" />
                  </>
                )}
              </button>
            </Tooltip>
          </form>

          {/* Single sign-on */}
          <div className="mt-8 flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">{t("or")}</span>
            <div className="h-px flex-1 bg-border" />
          </div>

          <div className="mt-6 space-y-3">
            <button
              type="button"
              onClick={() => onSso("google")}
              disabled={ssoLoading || loading}
              className="w-full h-12 rounded-2xl border border-border bg-card flex items-center justify-center gap-3 text-sm font-medium text-foreground hover:bg-muted transition disabled:opacity-60"
            >
              {ssoLoading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <svg viewBox="0 0 48 48" className="h-4 w-4" aria-hidden="true">
                  <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z" />
                  <path fill="#FF3D00" d="m6.306 14.691 6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z" />
                  <path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z" />
                  <path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l.003-.002 6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z" />
                </svg>
              )}
              {t("Continue with Google")}
            </button>
            <button
              type="button"
              onClick={() => onSso("microsoft")}
              disabled={ssoLoading || loading}
              className="w-full h-12 rounded-2xl border border-border bg-card flex items-center justify-center gap-3 text-sm font-medium text-foreground hover:bg-muted transition disabled:opacity-60"
            >
              {ssoLoading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <svg viewBox="0 0 23 23" className="h-4 w-4" aria-hidden="true">
                  <path fill="#F25022" d="M1 1h10v10H1z" />
                  <path fill="#7FBA00" d="M12 1h10v10H12z" />
                  <path fill="#00A4EF" d="M1 12h10v10H1z" />
                  <path fill="#FFB900" d="M12 12h10v10H12z" />
                </svg>
              )}
              {t("Continue with Microsoft")}
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}
