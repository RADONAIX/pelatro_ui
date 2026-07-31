import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { useT } from "@/lib/i18n";
import { InfoHint } from "@/components/ui-kit/InfoHint";

type Option<T extends string> = { value: T; label?: string };

function setsEqual<T extends string>(a: Set<T>, b: Set<T>) {
  if (a.size !== b.size) return false;
  for (const item of a) {
    if (!b.has(item)) return false;
  }
  return true;
}

export function MultiSelect<T extends string>({
  label,
  info,
  options,
  selected,
  onChange,
  placeholder = "Select…",
  minWidth = 180,
  allowEmpty = false,
}: {
  label?: string;
  info?: string;
  options: (T | Option<T>)[];
  selected: Set<T>;
  onChange: (next: Set<T>) => void;
  placeholder?: string;
  minWidth?: number;
  allowEmpty?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [pendingSelected, setPendingSelected] = useState<Set<T>>(new Set(selected));
  const ref = useRef<HTMLDivElement>(null);

  // Keep the latest props/state in refs so the effects below depend only on
  // `open`. Parents often pass inline `onChange` handlers and re-render on a
  // background timer (e.g. 2s auto-refresh); without this, every such re-render
  // re-ran the effect and reverted in-flight selections / disturbed the open
  // dropdown. Reading through refs means a refresh can't touch the dropdown.
  const selectedRef = useRef(selected);
  const onChangeRef = useRef(onChange);
  const pendingRef = useRef(pendingSelected);
  selectedRef.current = selected;
  onChangeRef.current = onChange;
  pendingRef.current = pendingSelected;

  // Seed the pending selection from the committed value only when the dropdown
  // opens — never on an unrelated re-render.
  useEffect(() => {
    if (open) setPendingSelected(new Set(selectedRef.current));
  }, [open]);

  // Outside-click commits the pending selection and closes. Subscribes only
  // while open and reads live values via refs, so it never re-subscribes.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) {
        if (!setsEqual(pendingRef.current, selectedRef.current)) {
          onChangeRef.current(new Set(pendingRef.current));
        }
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const norm: Option<T>[] = options.map((o) =>
    typeof o === "string" ? { value: o as T, label: o as string } : (o as Option<T>),
  );

  // Apply a new selection: update the in-dropdown pending set AND commit it to
  // the parent right away, so each pick takes effect immediately (the dropdown
  // stays open for more picks; closing it is not required to apply filters).
  const apply = (next: Set<T>) => {
    setPendingSelected(next);
    if (!setsEqual(next, selected)) onChange(next);
  };

  const toggle = (v: T) => {
    const next = new Set(pendingSelected);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    if (!allowEmpty && next.size === 0) return;
    apply(next);
  };

  const commit = () => {
    if (!setsEqual(pendingSelected, selected)) {
      onChange(new Set(pendingSelected));
    }
  };

  const summarySelected = open ? pendingSelected : selected;
  const summary = (() => {
    if (summarySelected.size === 0) return t(placeholder);
    if (summarySelected.size === norm.length) return t("All");
    if (summarySelected.size <= 2) return norm.filter((o) => summarySelected.has(o.value)).map((o) => t(o.label ?? o.value)).join(", ");
    return `${summarySelected.size} ${t("selected")}`;
  })();

  return (
    <div className="relative" ref={ref} style={{ minWidth }}>
      {label && (
        <div className="flex items-center gap-1 mb-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
          {info && <InfoHint text={info} />}
        </div>
      )}
      <button
        type="button"
        onClick={() => {
          if (open) {
            commit();
            setOpen(false);
          } else {
            setPendingSelected(new Set(selected));
            setOpen(true);
          }
        }}
        className={`w-full h-9 px-3 inline-flex items-center justify-between gap-2 rounded-lg border bg-background text-sm transition ${
          open ? "border-primary" : "border-border hover:bg-muted"
        }`}
      >
        <span className={`truncate ${summarySelected.size === 0 ? "text-muted-foreground" : "text-foreground"}`}>{summary}</span>
        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute z-50 mt-1.5 w-full min-w-[200px] rounded-lg border border-border bg-popover shadow-lg p-1 max-h-72 overflow-y-auto">
          <div className="flex items-center justify-between px-2 py-1.5">
            <button
              onClick={() => apply(new Set(norm.map((o) => o.value)))}
              className="text-[11px] text-primary hover:underline"
            >
              {t("Select all")}
            </button>
            <button
              onClick={() => allowEmpty && apply(new Set())}
              disabled={!allowEmpty}
              className="text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40"
            >
              <X className="h-3 w-3 inline mr-1" />{t("Clear")}
            </button>
          </div>
          <div className="h-px bg-border my-1" />
          {norm.map((o) => {
            const on = pendingSelected.has(o.value);
            return (
              <button
                key={o.value}
                onClick={() => toggle(o.value)}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-sm rounded-md hover:bg-muted text-left"
              >
                <span
                  className={`h-4 w-4 rounded border flex items-center justify-center shrink-0 ${
                    on ? "bg-primary border-primary text-primary-foreground" : "border-border bg-background"
                  }`}
                >
                  {on && <Check className="h-3 w-3" />}
                </span>
                <span className="truncate text-foreground">{t(o.label ?? o.value)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
