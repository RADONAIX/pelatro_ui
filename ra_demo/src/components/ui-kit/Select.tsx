import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useT } from "@/lib/i18n";

// Themed single-select dropdown — a drop-in replacement for a native <select>
// so filter controls match the app theme (and the report section's dropdowns)
// instead of falling back to the OS-rendered option list.

export interface SelectOption {
  value: string;
  label: string;
}

export function Select({
  value,
  onChange,
  options,
  placeholder = "Select…",
  minWidth = 140,
  className,
  ariaLabel,
  size = "md",
  dropUp = false,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  minWidth?: number;
  className?: string;
  ariaLabel?: string;
  size?: "sm" | "md";
  /** Open the menu upward — useful when the control sits near the page bottom. */
  dropUp?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // When the menu opens, center the selected option in view (so a long list like
  // years doesn't always start at the top, far from the current value).
  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    const item = selectedRef.current;
    if (list && item) {
      list.scrollTop = item.offsetTop - list.clientHeight / 2 + item.clientHeight / 2;
    }
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const sizeCls = size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3 text-sm";

  return (
    <div className="relative" ref={ref} style={{ minWidth }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={ariaLabel}
        className={`w-full ${sizeCls} inline-flex items-center justify-between gap-2 rounded-lg border bg-background transition ${
          open ? "border-primary" : "border-border hover:bg-muted"
        } ${className ?? ""}`}
      >
        <span className={`truncate ${selected ? "text-foreground" : "text-muted-foreground"}`}>
          {selected ? selected.label : t(placeholder)}
        </span>
        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform shrink-0 ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div ref={listRef} className={`absolute z-50 w-full min-w-[120px] rounded-lg border border-border bg-popover shadow-lg p-1 max-h-72 overflow-y-auto ${dropUp ? "bottom-full mb-1.5" : "mt-1.5"}`}>
          {options.map((o) => {
            const on = o.value === value;
            return (
              <button
                key={o.value}
                type="button"
                ref={on ? selectedRef : undefined}
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
                className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 text-sm rounded-md text-left hover:bg-muted ${
                  on ? "text-primary font-medium" : "text-foreground"
                }`}
              >
                <span className="truncate">{o.label}</span>
                {on && <Check className="h-4 w-4 shrink-0 text-primary" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
