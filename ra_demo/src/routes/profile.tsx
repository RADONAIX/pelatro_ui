import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import {
  Pencil, KeyRound, Languages, Mail, Shield, Building2, Clock, BadgeCheck,
  X, Eye, EyeOff, Loader2, User as UserIcon, Phone, Save, AlertCircle, CheckCircle2,
} from "lucide-react";
import { useT, useLanguage, LANGUAGES, type Language } from "@/lib/i18n";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { authService, userService } from "@/services";
import { toast } from "sonner";

export const Route = createFileRoute("/profile")({ component: ProfilePage });

function backendMessage(err: any, fallback: string): string {
  return err?.response?.data?.error?.message || err?.message || fallback;
}

// --- Info tile -------------------------------------------------------------
function InfoTile({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-border bg-background/50 p-4">
      <div className="h-9 w-9 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="text-sm font-medium text-foreground truncate">{value}</div>
      </div>
    </div>
  );
}

// --- Modal shell -----------------------------------------------------------
function Modal({
  title,
  subtitle,
  icon: Icon,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  icon?: any;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-foreground/40 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto animate-scale-in"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {/* Accent bar */}
        <div className="h-1.5 bg-primary" />
        <div className="flex items-start justify-between px-6 py-4 border-b border-border">
          <div className="flex items-center gap-3">
            {Icon && (
              <div className="h-10 w-10 shrink-0 rounded-xl bg-primary/15 text-primary flex items-center justify-center">
                <Icon className="h-5 w-5" />
              </div>
            )}
            <div>
              <h3 className="text-base font-semibold text-foreground">{title}</h3>
              {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// Labelled input with a leading icon and a muted fill — the field style used in
// the profile modals.
function IconField({
  label,
  icon: Icon,
  value,
  onChange,
  type = "text",
  placeholder,
  disabled,
  autoFocus,
  required,
  error,
  maxLength,
  hint,
}: {
  label: string;
  icon: any;
  value: string;
  onChange?: (v: string) => void;
  type?: string;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  required?: boolean;
  error?: string;
  maxLength?: number;
  hint?: string;
}) {
  return (
    <div>
      <label className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
        <span>{label}{required && <span className="text-destructive ml-0.5">*</span>}</span>
        {hint && <InfoHint text={hint} side="top" />}
      </label>
      <div className="relative">
        <Icon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type={type}
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          autoFocus={autoFocus}
          maxLength={maxLength}
          aria-invalid={!!error}
          className={`w-full h-11 pl-10 pr-3 rounded-xl border bg-muted/40 text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:bg-background focus:ring-2 transition disabled:opacity-60 ${error ? "border-destructive focus:border-destructive focus:ring-destructive/20" : "border-border focus:border-primary focus:ring-primary/20"}`}
        />
      </div>
      {error && <p className="mt-1 text-[11px] font-medium text-destructive">{error}</p>}
    </div>
  );
}

// --- Edit profile ----------------------------------------------------------
function EditProfileModal({ onClose }: { onClose: () => void }) {
  const t = useT();
  const { user, token, setSession } = useAuth();
  // The backend stores a single full name — edit it as one field.
  const [fullName, setFullName] = useState((user?.name ?? "").trim());
  const [phone, setPhone] = useState((user as any)?.phone ?? "");
  const [department, setDepartment] = useState(user?.department ?? "");
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const initials = user?.avatar || user?.name?.slice(0, 2).toUpperCase() || "RA";

  const clearErr = (key: string) => setErrors((prev) => (prev[key] ? (() => { const { [key]: _drop, ...rest } = prev; return rest; })() : prev));

  const validate = (): Record<string, string> => {
    const e: Record<string, string> = {};
    // Name rules mirror the User Management module: letters (any language) plus
    // spaces, . ' - and no digits. Full name allows up to 80 chars.
    const nameRe = /^[\p{L}][\p{L}\s.'-]*$/u;
    const fn = fullName.trim();
    if (!fn) e.fullName = t("Full name is required.");
    else if (fn.length < 2) e.fullName = t("Full name must be at least 2 characters.");
    else if (fn.length > 80) e.fullName = t("Full name must be at most 80 characters.");
    else if (!nameRe.test(fn)) e.fullName = t("Name can only contain letters, spaces, hyphens and apostrophes.");

    // Phone: optional, but must be a plausible number (7–15 digits) when provided.
    const ph = phone.trim();
    if (ph) {
      if (!/^[+]?[\d\s()-]{7,20}$/.test(ph)) e.phone = t("Enter a valid phone number.");
      else {
        const digits = ph.replace(/\D/g, "");
        if (digits.length < 7 || digits.length > 15) e.phone = t("Phone number must have 7 to 15 digits.");
      }
    }
    return e;
  };

  const onSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    const name = fullName.trim();
    setSaving(true);
    try {
      const res = await userService.update(user.id, {
        fullName: name,
        department: department.trim(),
        phone: phone.trim(),
      });
      // Reflect the change in the current session immediately.
      if (token) setSession(token, { ...user, name: res?.fullName ?? name, department: res?.department ?? department.trim() });
      toast.success(t("Profile updated"));
      onClose();
    } catch (err: any) {
      toast.error(backendMessage(err, t("Could not update profile")));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={t("Edit Profile")} subtitle={t("Update your personal information")} icon={Pencil} onClose={onClose}>
      <form onSubmit={onSave} noValidate className="p-6 space-y-5">

        <IconField label={t("Full Name")} icon={UserIcon} value={fullName} onChange={(v) => { setFullName(v); clearErr("fullName"); }} autoFocus required error={errors.fullName} maxLength={80} hint={t("Your full name as shown across the app. Letters, spaces, hyphens and apostrophes only (2–80 characters).")} />

        <div>
          <IconField label={t("Email Address")} icon={Mail} value={user?.email ?? ""} disabled hint={t("Your sign-in email. This is managed by an administrator and cannot be changed here.")} />
          <p className="mt-1 text-[11px] text-muted-foreground">{t("Email and role are managed by an administrator.")}</p>
        </div>

        <IconField label={t("Phone")} icon={Phone} value={phone} onChange={(v) => { setPhone(v); clearErr("phone"); }} type="tel" placeholder={t("+1 (000) 000-0000")} error={errors.phone} maxLength={20} hint={t("Optional contact number. Include the country code if relevant (7–15 digits).")} />
        <IconField label={t("Department")} icon={Building2} value={department} onChange={setDepartment} placeholder={t("e.g. Finance Operations")} maxLength={60} hint={t("Optional team or business unit you belong to, used for grouping and reporting.")} />

        <div className="flex gap-3 pt-1">
          <button type="submit" disabled={saving} className="flex-1 h-11 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 disabled:opacity-60 inline-flex items-center justify-center gap-2">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} {t("Save Changes")}
          </button>
          <button type="button" onClick={onClose} className="flex-1 h-11 rounded-xl bg-muted text-foreground text-sm font-semibold hover:bg-muted/70 transition">{t("Cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}

// --- Change password -------------------------------------------------------
function PwdField({ label, value, onChange, required, error, hint, disablePaste = false }: { label: string; value: string; onChange: (v: string) => void; required?: boolean; error?: string; hint?: string; disablePaste?: boolean }) {
  const [show, setShow] = useState(false);
  const blockClipboard = disablePaste ? (e: React.ClipboardEvent<HTMLInputElement>) => e.preventDefault() : undefined;
  return (
    <div>
      <label className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
        <span>{label}{required && <span className="text-destructive ml-0.5">*</span>}</span>
        {hint && <InfoHint text={hint} side="top" />}
      </label>
      <div className="relative">
        <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type={show ? "text" : "password"}
          maxLength={20}
          aria-invalid={!!error}
          className={`w-full h-11 pl-10 pr-11 rounded-xl border bg-muted/40 text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:bg-background focus:ring-2 transition ${error ? "border-destructive focus:border-destructive focus:ring-destructive/20" : "border-border focus:border-primary focus:ring-primary/20"}`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onPaste={blockClipboard}
          onCopy={blockClipboard}
          onCut={blockClipboard}
          onDrop={disablePaste ? (e) => e.preventDefault() : undefined}
          autoComplete="off"
        />
        <button type="button" onClick={() => setShow((s) => !s)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground" aria-label={show ? "Hide" : "Show"}>
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {error && <p className="mt-1 text-[11px] font-medium text-destructive">{error}</p>}
    </div>
  );
}

// One live password-strength rule row.
function Requirement({ met, label }: { met: boolean; label: string }) {
  return (
    <div className={`flex items-center gap-2 text-sm ${met ? "text-success" : "text-muted-foreground"}`}>
      <CheckCircle2 className={`h-4 w-4 shrink-0 ${met ? "text-success" : "text-muted-foreground/50"}`} />
      <span>{label}</span>
    </div>
  );
}

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const t = useT();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const clearErr = (key: string) => { setError(""); setErrors((prev) => (prev[key] ? (() => { const { [key]: _drop, ...rest } = prev; return rest; })() : prev)); };

  // Live password rules — mirror the User Management module (8–20 chars with
  // upper + lower case letters and a number).
  const rules = [
    { label: t("At least 8 characters"), met: next.length >= 8 && next.length <= 20 },
    { label: t("One uppercase letter"), met: /[A-Z]/.test(next) },
    { label: t("One lowercase letter"), met: /[a-z]/.test(next) },
    { label: t("One number"), met: /[0-9]/.test(next) },
  ];

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!current) errs.current = t("Enter your current password.");
    if (!next) errs.next = t("New password is required.");
    else if (next.length < 8) errs.next = t("New password must be at least 8 characters.");
    else if (next.length > 20) errs.next = t("Password must be at most 20 characters.");
    else if (!rules.every((r) => r.met)) errs.next = t("New password doesn't meet all requirements.");
    else if (next === current) errs.next = t("New password must be different from the current one.");
    if (!confirm) errs.confirm = t("Please confirm your new password.");
    else if (next && next !== confirm) errs.confirm = t("New passwords do not match.");
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    setError("");
    setSaving(true);
    try {
      await authService.changePassword(current, next);
      toast.success(t("Password changed"));
      onClose();
    } catch (err: any) {
      setError(backendMessage(err, t("Could not change password")));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={t("Change Password")} subtitle={t("Keep your account secure")} icon={KeyRound} onClose={onClose}>
      <form onSubmit={onSubmit} noValidate className="p-6 space-y-5">
        <PwdField label={t("Current Password")} value={current} onChange={(v) => { setCurrent(v); clearErr("current"); }} required error={errors.current} hint={t("Your existing password, needed to authorize this change.")} />
        <PwdField label={t("New Password")} value={next} onChange={(v) => { setNext(v); clearErr("next"); }} required error={errors.next} hint={t("Must be 8–20 characters with upper and lower case letters and a number. Must differ from your current password.")} disablePaste />
        <PwdField label={t("Confirm New Password")} value={confirm} onChange={(v) => { setConfirm(v); clearErr("confirm"); }} required error={errors.confirm} hint={t("Re-enter the new password exactly to confirm there are no typos.")} disablePaste />

        <div className="rounded-xl bg-muted/40 p-4 space-y-2">
          {rules.map((r) => <Requirement key={r.label} met={r.met} label={r.label} />)}
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm font-medium text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" /> {error}
          </div>
        )}

        <div className="flex gap-3 pt-1">
          <button type="submit" disabled={saving} className="flex-1 h-11 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 disabled:opacity-60 inline-flex items-center justify-center gap-2">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} {t("Update Password")}
          </button>
          <button type="button" onClick={onClose} className="flex-1 h-11 rounded-xl bg-muted text-foreground text-sm font-semibold hover:bg-muted/70 transition">{t("Cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}

// --- Language --------------------------------------------------------------
function LanguageCard() {
  const t = useT();
  const { lang, setLang } = useLanguage();
  return (
    <div className="bg-card border border-border rounded-2xl p-6 shadow-sm">
      <div className="flex items-center gap-3 pb-4 border-b border-border">
        <div className="h-10 w-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Languages className="h-5 w-5" />
        </div>
        <div>
          <h2 className="text-base font-semibold text-foreground">{t("Language")}</h2>
          <p className="text-sm text-muted-foreground">{t("Choose the language for the application interface.")}</p>
        </div>
      </div>
      <div className="pt-4 max-w-xs">
        <Select
          value={lang}
          onChange={(v) => setLang(v as Language)}
          options={LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
          ariaLabel={t("Language")}
          className="w-full"
        />
      </div>
    </div>
  );
}

function ProfilePage() {
  const { user, canEdit, token, setSession } = useAuth();
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [changingPwd, setChangingPwd] = useState(false);

  // Re-fetch the signed-in user from the backend whenever the page opens (or is
  // refreshed) so profile fields reflect the current DB — the cached session
  // copy from login can otherwise go stale (e.g. an admin edits this user).
  // Keyed on `token` only (setSession isn't memoised) to avoid a refetch loop.
  useEffect(() => {
    if (!token) return;
    let alive = true;
    authService
      .me(token)
      .then((fresh) => { if (alive && fresh) setSession(token, fresh); })
      .catch(() => { /* backend unreachable → keep the cached copy */ });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!user) return <AppShell><div /></AppShell>;

  return (
    <AppShell>
      <PageHeader title={t("User Profile")} description={t("Your account information and access.")} info={t("View your account details and switch the application language.")} />

      <div className=" space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
          {/* Left: identity + actions */}
          <div className="lg:col-span-1 bg-card border border-border rounded-2xl shadow-sm overflow-hidden">
            <div className="relative bg-gradient-to-br from-primary/15 via-primary/5 to-transparent px-6 pt-8 pb-6 flex flex-col items-center text-center">
              <div className="h-24 w-24 shrink-0 rounded-2xl bg-primary text-primary-foreground text-3xl font-semibold flex items-center justify-center shadow-sm ring-4 ring-card">
                {user.avatar || user.name.slice(0, 2).toUpperCase()}
              </div>
              <h2 className="mt-4 text-lg font-bold tracking-tight text-foreground break-words">{user.name}</h2>
              <p className="text-sm text-muted-foreground">{user.roleLabel || user.role}</p>
              <span className="mt-3"><StatusBadge value={user.status || "Active"} /></span>
            </div>
            <div className="p-5 pt-4 space-y-2.5 border-t border-border">
              {canEdit("/users") && (
                <button onClick={() => setEditing(true)} className="w-full inline-flex items-center justify-center gap-2 h-10 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition">
                  <Pencil className="h-4 w-4" /> {t("Edit Profile")}
                </button>
              )}
              <button onClick={() => setChangingPwd(true)} className="w-full inline-flex items-center justify-center gap-2 h-10 rounded-lg border border-border bg-card text-sm hover:bg-muted transition">
                <KeyRound className="h-4 w-4" /> {t("Change Password")}
              </button>
            </div>
          </div>

          {/* Right: account fields */}
          <div className="lg:col-span-2 bg-card border border-border rounded-2xl shadow-sm overflow-hidden pb-4">
            <div className="px-6 py-4 border-b border-border">
              <h3 className="text-base font-semibold text-foreground">{t("Account Information")}</h3>
              <p className="text-sm text-muted-foreground mt-0.5">{t("Your account details and access.")}</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-6">
              <InfoTile icon={Mail} label={t("Email")} value={user.email} />
              <InfoTile icon={Shield} label={t("Role")} value={user.roleLabel || user.role} />
              <InfoTile icon={Building2} label={t("Department")} value={user.department || "—"} />
              <InfoTile icon={Clock} label={t("Last Login")} value={user.lastLogin ? new Date(user.lastLogin).toLocaleString() : "—"} />
              <InfoTile icon={BadgeCheck} label={t("Account Status")} value={user.status || "Active"} />
            </div>
          </div>
        </div>

        {/* Preferences */}
        <LanguageCard />
      </div>

      {editing && <EditProfileModal onClose={() => setEditing(false)} />}
      {changingPwd && <ChangePasswordModal onClose={() => setChangingPwd(false)} />}
    </AppShell>
  );
}
