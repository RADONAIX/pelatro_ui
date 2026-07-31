import { useCallback, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Side = "top" | "bottom" | "left" | "right";

// The tooltip is rendered into document.body (a portal) so it is never clipped
// by an ancestor's `overflow-hidden` (e.g. a modal shell) and always paints on
// top. Positioning is done with position:fixed + viewport coords, so the class
// carries the highest possible z-index.
const TRANSFORM: Record<Side, string> = {
  top: "translate(-50%, -100%)",
  bottom: "translate(-50%, 0)",
  left: "translate(-100%, -50%)",
  right: "translate(0, -50%)",
};

/**
 * Lightweight tooltip. Wrap any element (typically an icon-only button) to
 * surface a short label on hover/focus. Renders via a portal so it escapes any
 * clipping/stacking context around the trigger.
 */
export function Tooltip({
  label,
  children,
  side = "bottom",
  className = "",
  instant = false,
  multiline = false,
}: {
  label: string;
  children: ReactNode;
  side?: Side;
  className?: string;
  /** Show the tooltip immediately on hover/focus instead of after a short delay. */
  instant?: boolean;
  /** Allow the label to wrap onto multiple lines (for descriptive hints). */
  multiline?: boolean;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);

  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 8;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const anchor: Record<Side, { top: number; left: number }> = {
      top: { top: r.top - gap, left: cx },
      bottom: { top: r.bottom + gap, left: cx },
      left: { top: cy, left: r.left - gap },
      right: { top: cy, left: r.right + gap },
    };
    setCoords(anchor[side]);
  }, [side]);

  const show = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(place, instant ? 0 : 150);
  }, [place, instant]);

  const hide = useCallback(() => {
    window.clearTimeout(timer.current);
    setCoords(null);
  }, []);

  return (
    <span
      ref={ref}
      className={`inline-flex ${className}`}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {coords &&
        createPortal(
          <span
            role="tooltip"
            style={{ position: "fixed", top: coords.top, left: coords.left, transform: TRANSFORM[side] }}
            className={`pointer-events-none z-[2147483647] rounded-md bg-foreground text-background text-[11px] font-medium px-2 py-1 shadow-lg animate-fade-in ${
              multiline ? "w-max max-w-[220px] whitespace-normal text-left leading-snug" : "whitespace-nowrap"
            }`}
          >
            {label}
          </span>,
          document.body,
        )}
    </span>
  );
}
