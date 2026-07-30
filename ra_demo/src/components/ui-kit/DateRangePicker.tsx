import { useState } from "react";
import { Calendar as CalendarIcon } from "lucide-react";
import { format } from "date-fns";
import { type DateRange as RdpRange } from "react-day-picker";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { InfoHint } from "@/components/ui-kit/InfoHint";

function combine(date: Date, time: string): Date {
  const [h, m] = time.split(":").map(Number);
  const d = new Date(date);
  d.setHours(h || 0, m || 0, 0, 0);
  return d;
}

function toTimeStr(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const monthStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1);
const addMonths = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth() + n, 1);

// The two calendars are independent: the left shows the From month, the right
// shows the To month — so a range spanning years (e.g. 2023 → 2026) shows each
// end on its own side instead of two consecutive months. When both fall in the
// same month, the left backs up one month so two distinct months stay visible.
function twoMonths(s: Date, e: Date): [Date, Date] {
  const l = monthStart(s);
  const r = monthStart(e);
  return [l.getTime() < r.getTime() ? l : addMonths(r, -1), r];
}

/**
 * Themed absolute date-range picker (Popover + range Calendar).
 * Replaces the unstyleable native <input type="datetime-local"> picker.
 *
 * - `start`/`end` may be null → the trigger shows `placeholder` (no active range).
 * - `showTime` adds From/To time inputs (datetime precision); off = day-level.
 * - Selections are committed to `onChange` only when "Apply" is clicked.
 */
export function DateRangePicker({
  start,
  end,
  onChange,
  min,
  max,
  showTime = true,
  showApply = true,
  placeholder = "Select range",
  className,
  fromInfo,
  toInfo,
}: {
  start: Date | null;
  end: Date | null;
  onChange: (range: { start: Date; end: Date }) => void;
  min?: Date;
  max?: Date;
  showTime?: boolean;
  /** When false, selections commit immediately (no in-popover Apply button). */
  showApply?: boolean;
  placeholder?: string;
  className?: string;
  /** Optional info hints shown beside the From / To time inputs in the popover. */
  fromInfo?: string;
  toInfo?: string;
}) {
  const [open, setOpen] = useState(false);

  // Defaults used to seed the calendar when no range is applied yet.
  const defStart = start ?? new Date(Date.now() - 7 * 24 * 3600 * 1000);
  const defEnd = end ?? new Date();
  const [draft, setDraft] = useState<{ start: Date; end: Date }>({ start: defStart, end: defEnd });
  // The first endpoint of an in-progress selection. `null` means the visible
  // range is complete, so the next click starts a brand-new range instead of
  // moving an endpoint of the existing one.
  const [anchor, setAnchor] = useState<Date | null>(null);
  // Whether to render a highlighted selection in the calendar. When no range is
  // applied ("All dates"), we show NONE until the user actually clicks a day —
  // otherwise the default seed range (last 7 days) would look pre-selected.
  const [hasRange, setHasRange] = useState<boolean>(start != null && end != null);
  // Independently-navigable months for the left (From) and right (To) calendars.
  const [leftMonth, setLeftMonth] = useState<Date>(() => twoMonths(defStart, defEnd)[0]);
  const [rightMonth, setRightMonth] = useState<Date>(() => twoMonths(defStart, defEnd)[1]);

  // Reset the draft to the currently-applied range whenever the popover opens,
  // and treat that range as complete so the first click restarts selection.
  const onOpenChange = (o: boolean) => {
    if (o) {
      const s = start ?? defStart;
      const e = end ?? defEnd;
      setDraft({ start: s, end: e });
      setAnchor(null);
      setHasRange(start != null && end != null);
      const [l, r] = twoMonths(s, e);
      setLeftMonth(l);
      setRightMonth(r);
    }
    setOpen(o);
  };

  // A range is invalid when its end lands before its start (e.g. same day with
  // From 19:32 / To 17:32). We block committing such a range.
  const isInvalid = (r: { start: Date; end: Date }) => r.start.getTime() > r.end.getTime();

  // Update the draft; when there's no Apply button, commit to the parent live —
  // but never commit an inverted (end-before-start) range.
  const update = (next: { start: Date; end: Date }) => {
    setDraft(next);
    if (!showApply && !isInvalid(next)) onChange(next);
  };

  // We drive range selection off the clicked day (`triggerDate`) rather than the
  // range react-day-picker computes: with a complete range showing, RDP would
  // just slide the nearest endpoint. Instead, the first click starts a fresh
  // single-day range and the second click closes it (endpoints auto-ordered).
  const handleSelect = (_range: RdpRange | undefined, triggerDate?: Date) => {
    const day = triggerDate;
    if (!day) return;
    setHasRange(true); // a click means a real selection now exists
    if (anchor == null) {
      setAnchor(day);
      update({ start: combine(day, toTimeStr(draft.start)), end: combine(day, toTimeStr(draft.end)) });
    } else {
      const [from, to] = day < anchor ? [day, anchor] : [anchor, day];
      setAnchor(null);
      update({ start: combine(from, toTimeStr(draft.start)), end: combine(to, toTimeStr(draft.end)) });
    }
  };

  const invalid = isInvalid(draft);

  const apply = () => {
    if (invalid) return;
    onChange(draft);
    setOpen(false);
  };

  const fmt = showTime ? "dd MMM yyyy, HH:mm" : "dd MMM yyyy";
  const label = start && end ? `${format(start, fmt)} – ${format(end, fmt)}` : placeholder;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={
            className ??
            "inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-card text-xs text-foreground hover:bg-muted transition"
          }
        >
          <CalendarIcon className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className={start && end ? "" : "text-muted-foreground"}>{label}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="z-[90] w-auto p-0">
        {/* Two independent calendars styled to read as one unit: the left keeps
            only its ‹ (prev) arrow, the right only its › (next) arrow, and each
            month/year dropdown jumps that side independently (e.g. 2023 ↔ 2026). */}
        <div className="flex flex-col sm:flex-row">
          <Calendar
            mode="range"
            numberOfMonths={1}
            showOutsideDays={false}
            captionLayout="dropdown"
            startMonth={min ?? new Date(2000, 0)}
            endMonth={max ?? new Date(new Date().getFullYear() + 5, 11)}
            month={leftMonth}
            onMonthChange={setLeftMonth}
            selected={hasRange ? { from: draft.start, to: draft.end } : undefined}
            onSelect={handleSelect}
            disabled={[...(min ? [{ before: min }] : []), ...(max ? [{ after: max }] : [])]}
            className="sm:pr-1.5"
            classNames={{ button_next: "invisible pointer-events-none" }}
          />
          <Calendar
            mode="range"
            numberOfMonths={1}
            showOutsideDays={false}
            captionLayout="dropdown"
            startMonth={min ?? new Date(2000, 0)}
            endMonth={max ?? new Date(new Date().getFullYear() + 5, 11)}
            month={rightMonth}
            onMonthChange={setRightMonth}
            selected={hasRange ? { from: draft.start, to: draft.end } : undefined}
            onSelect={handleSelect}
            disabled={[...(min ? [{ before: min }] : []), ...(max ? [{ after: max }] : [])]}
            className="sm:pl-1.5"
            classNames={{ button_previous: "invisible pointer-events-none" }}
          />
        </div>
        {(showTime || showApply) && (
          <div className="border-t border-border p-3">
          <div className="flex items-center justify-between gap-4">
            {/* From / To on the left */}
            {showTime ? (
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  {"From"}
                  {fromInfo && <InfoHint text={fromInfo} />}
                  <input
                    type="time"
                    value={toTimeStr(draft.start)}
                    onChange={(e) => update({ ...draft, start: combine(draft.start, e.target.value) })}
                    className="h-8 px-2 rounded-md border border-border bg-card text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20"
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  {"To"}
                  {toInfo && <InfoHint text={toInfo} />}
                  <input
                    type="time"
                    value={toTimeStr(draft.end)}
                    onChange={(e) => update({ ...draft, end: combine(draft.end, e.target.value) })}
                    className="h-8 px-2 rounded-md border border-border bg-card text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20"
                  />
                </label>
              </div>
            ) : (
              <span className="text-xs text-muted-foreground">
                {format(draft.start, "dd MMM yyyy")} – {format(draft.end, "dd MMM yyyy")}
              </span>
            )}
            {/* Apply on the right */}
            {showApply && (
              <button
                type="button"
                onClick={apply}
                disabled={invalid}
                className="h-8 px-4 rounded-md bg-primary text-primary-foreground text-xs font-semibold hover:opacity-90 transition disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:opacity-50"
              >
                Apply
              </button>
            )}
          </div>
          {invalid && (
            <p className="mt-2 text-xs text-destructive">
              {"End date/time must be after the start."}
            </p>
          )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
