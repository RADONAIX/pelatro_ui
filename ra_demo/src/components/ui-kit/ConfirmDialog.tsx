import { X } from "lucide-react";
import type { ReactNode } from "react";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";

export type ConfirmTone = "primary" | "success" | "danger";

// Per-tone theming: the confirm button, icon halos, subject-card avatar, the
// consequence callout and the top accent bar all read from the same palette so
// the dialog feels cohesive with the app theme.
const TONE: Record<
  ConfirmTone,
  { btn: string; iconWrap: string; ring: string; bar: string; avatar: string; softBg: string; dot: string; listText: string }
> = {
  primary: {
    btn: "bg-primary text-primary-foreground hover:bg-primary/90",
    iconWrap: "bg-primary/10 text-primary",
    ring: "ring-primary/15",
    bar: "bg-primary",
    avatar: "bg-gradient-to-br from-primary/80 to-primary",
    softBg: "bg-primary/5 border-primary/15",
    dot: "bg-primary",
    listText: "text-primary",
  },
  success: {
    btn: "bg-success text-success-foreground hover:bg-success/90",
    iconWrap: "bg-success/10 text-success",
    ring: "ring-success/15",
    bar: "bg-success",
    avatar: "bg-gradient-to-br from-success/80 to-success",
    softBg: "bg-success/5 border-success/20",
    dot: "bg-success",
    listText: "text-success",
  },
  danger: {
    btn: "bg-gradient-to-r from-[#EF4444] to-[#DC2626] text-white hover:from-[#DC2626] hover:to-[#B91C1C]",
    iconWrap: "bg-[#EF4444]/10 text-[#EF4444]",
    ring: "ring-[#EF4444]/15",
    bar: "bg-gradient-to-r from-[#F472B6] via-[#EF4444] to-[#DC2626]",
    avatar: "bg-gradient-to-br from-[#F472B6] to-[#EF4444]",
    softBg: "bg-[#EF4444]/5 border-[#EF4444]/15",
    dot: "bg-[#EF4444]",
    listText: "text-[#B91C1C]",
  },
};

// Two-letter avatar initials from a display name (e.g. "Mohith123" → "MO").
function initials(name: string): string {
  const cleaned = name.replace(/[^a-zA-Z0-9]/g, "");
  return cleaned.slice(0, 2).toUpperCase() || "?";
}

export function ConfirmDialog({
  open,
  title,
  message,
  note,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "primary",
  onCancel,
  onConfirm,
  icon,
  heroIcon,
  confirmIcon,
  subject,
  bullets,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  /** Optional emphasised sentence below the message (e.g. an irreversible-action warning). */
  note?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  onCancel: () => void;
  onConfirm: () => void;
  /** Small badge icon shown in the header beside the title. */
  icon?: ReactNode;
  /** Large centred icon (in a ringed circle) shown above the body. */
  heroIcon?: ReactNode;
  /** Icon rendered inside the confirm button. */
  confirmIcon?: ReactNode;
  /** Renders a subject card (avatar + name + email + role badge). */
  subject?: { name: string; email?: string; role?: string };
  /** Bulleted list of consequences shown in a tone-tinted callout. */
  bullets?: ReactNode[];
}) {
  const t = useT();
  if (!open) return null;
  const tw = TONE[tone];
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4 animate-fade-in"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        className="relative bg-card border border-border rounded-2xl shadow-2xl w-full max-w-md animate-scale-in overflow-hidden"
      >
        {/* Tone accent strip */}
        <div className={`h-1.5 w-full ${tw.bar}`} />

        {/* Header: badge + title + close */}
        <div className="flex items-center gap-3 px-5 pt-5 pb-4 border-b border-border">
          {icon && (
            <div className={`h-10 w-10 rounded-xl flex items-center justify-center shrink-0 ${tw.iconWrap}`}>
              {icon}
            </div>
          )}
          <h3 className="flex-1 text-md font-semibold text-foreground">{title}</h3>
          <Tooltip label={t("Close this dialog")} side="left">
            <button
              onClick={onCancel}
              aria-label={t("Close")}
              className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors shrink-0"
            >
              <X className="h-4 w-4" />
            </button>
          </Tooltip>
        </div>

        <div className="px-6 pt-5 pb-5">
          {/* Hero icon */}
          {heroIcon && (
            <div className={`mx-auto h-20 w-20 rounded-full flex items-center justify-center ring-8 ${tw.iconWrap} ${tw.ring}`}>
              {heroIcon}
            </div>
          )}

          {/* Subject card */}
          {subject && (
            <div className="mt-5 flex items-center gap-3 rounded-2xl border border-border bg-muted/30 px-4 py-3">
              <div className={`h-11 w-11 rounded-full flex items-center justify-center text-sm font-semibold text-white shrink-0 ${tw.avatar}`}>
                {initials(subject.name)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-base font-semibold text-foreground truncate">{subject.name}</div>
                {subject.email && <div className="text-xs text-muted-foreground truncate">{subject.email}</div>}
              </div>
              {subject.role && (
                <span className="shrink-0 text-xs font-semibold px-2.5 py-1 rounded-full bg-primary/10 text-primary">
                  {subject.role}
                </span>
              )}
            </div>
          )}

          {/* Message + optional emphasised note */}
          <div className="mt-4 text-center text-sm text-muted-foreground leading-relaxed">
            {message}
            {note && <span className="block mt-1 font-medium text-foreground/80">{note}</span>}
          </div>

          {/* Consequence bullets */}
          {bullets && bullets.length > 0 && (
            <ul className={`mt-4 rounded-2xl border px-4 py-3 space-y-2 ${tw.softBg}`}>
              {bullets.map((b, i) => (
                <li key={i} className={`flex items-start gap-2.5 text-sm ${tw.listText}`}>
                  <span className={`mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 ${tw.dot}`} />
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="px-6 pb-6 flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 h-11 rounded-xl border border-border text-sm font-semibold text-foreground hover:bg-muted transition-colors"
          >
            {t(cancelLabel)}
          </button>
          <button
            onClick={onConfirm}
            className={`flex-1 h-11 rounded-xl text-sm font-semibold shadow-sm transition-colors inline-flex items-center justify-center gap-2 ${tw.btn}`}
          >
            {confirmIcon}
            {t(confirmLabel)}
          </button>
        </div>
      </div>
    </div>
  );
}
