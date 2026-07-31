import { useState, useRef } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";

const TOOLTIP_W = 288; // matches w-72
const MARGIN = 8;

/**
 * Small "ⓘ" info icon that reveals a short, wrapping description on hover or
 * keyboard focus. The tooltip is rendered in a portal with fixed positioning,
 * so it is never clipped by an ancestor's `overflow-hidden` / scroll container
 * and always paints above other components. It clamps itself to the viewport so
 * it can't run off the left/right edge. `align` only sets the preferred side.
 */
export function InfoHint({
  text,
  side = "bottom",
  align = "left",
  iconClassName = "h-3.5 w-3.5",
  className = "",
}: {
  text: string;
  side?: "bottom" | "top";
  align?: "left" | "right";
  iconClassName?: string;
  className?: string;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; side: "bottom" | "top" } | null>(null);

  const show = () => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Preferred horizontal anchor, then clamp so it stays fully on-screen.
    let left = align === "right" ? r.right - TOOLTIP_W : r.left;
    left = Math.max(MARGIN, Math.min(left, window.innerWidth - TOOLTIP_W - MARGIN));
    const top = side === "top" ? r.top - 6 : r.bottom + 6;
    setPos({ left, top, side });
  };
  const hide = () => setPos(null);

  return (
    <span className={`relative inline-flex align-middle ${className}`}>
      <button
        ref={ref}
        type="button"
        aria-label={text}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        className="text-muted-foreground/50 hover:text-muted-foreground focus:outline-none focus-visible:text-muted-foreground transition-colors"
      >
        <Info className={iconClassName} />
      </button>
      {pos &&
        typeof document !== "undefined" &&
        createPortal(
          <span
            role="tooltip"
            style={{
              position: "fixed",
              left: pos.left,
              top: pos.top,
              transform: pos.side === "top" ? "translateY(-100%)" : undefined,
            }}
            className="pointer-events-none w-72 max-w-[80vw] whitespace-normal text-left normal-case tracking-normal rounded-md bg-foreground text-background text-[11px] font-normal leading-snug px-2.5 py-1.5 shadow-lg z-[100]"
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  );
}
