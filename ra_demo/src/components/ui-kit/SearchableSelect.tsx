import { useMemo, useRef, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";

// ---------------------------------------------------------------------------
// A Select that can be typed into.
//
// Deliberately mirrors the Select it replaces — same trigger height, same
// border, same placeholder behaviour, same disabled states — so a form built
// from Selects doesn't visibly change when one becomes searchable. The search
// box lives INSIDE the popover, as the first row, rather than beside the
// trigger.
//
// Keyboard handling comes from cmdk: type to filter, up/down to move, Enter to
// choose, Escape to close. The trigger is a real button, so it is reachable by
// Tab like the Select was.
// ---------------------------------------------------------------------------

export interface SearchableOption {
  value: string;
  label: string;
  /** Optional heading this option sits under. */
  group?: string;
  /** Searched in addition to the label — e.g. a table's schema. */
  keywords?: string;
}

/**
 * Above this many options the list renders a windowed slice instead of every
 * row.
 *
 * cmdk filters and scores every child it is given, and mounting several
 * thousand DOM nodes to then hide most of them is what makes a wide schema feel
 * broken. Past the cap this component filters the data itself and hands cmdk
 * only what is on screen.
 */
const VIRTUALISE_ABOVE = 200;

/** How many rows to render when windowing. Comfortably more than fills the popover. */
const WINDOW_SIZE = 100;

export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Select…",
  searchPlaceholder = "Search…",
  emptyLabel = "No results found",
  disabled = false,
  className,
}: {
  options: SearchableOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(
    () => options.find((o) => o.value === value),
    [options, value],
  );

  const large = options.length > VIRTUALISE_ABOVE;

  // Own the filtering only when the list is large enough to be worth it; below
  // the cap cmdk's own matching (which scores substrings better than a plain
  // includes) is left alone.
  const visible = useMemo(() => {
    if (!large) return options;
    const q = query.trim().toLowerCase();
    const matches = q
      ? options.filter(
          (o) =>
            o.label.toLowerCase().includes(q) ||
            o.keywords?.toLowerCase().includes(q),
        )
      : options;
    return matches.slice(0, WINDOW_SIZE);
  }, [large, options, query]);

  const hiddenCount = large ? Math.max(0, options.length - visible.length) : 0;

  const grouped = useMemo(() => {
    const groups = new Map<string, SearchableOption[]>();
    for (const option of visible) {
      const key = option.group ?? "";
      const bucket = groups.get(key);
      if (bucket) bucket.push(option);
      else groups.set(key, [option]);
    }
    return [...groups.entries()];
  }, [visible]);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Clearing on close means reopening starts from the full list rather
        // than the last search, which is what a Select would have done.
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            "h-9 w-full justify-between border-input bg-background px-3 font-normal",
            !selected && "text-muted-foreground",
            className,
          )}
        >
          <span className="truncate">{selected?.label ?? placeholder}</span>
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent
        className="w-[--radix-popover-trigger-width] p-0"
        align="start"
      >
        {/* shouldFilter=false when we filter ourselves, or cmdk would filter the
            already-windowed slice a second time. */}
        <Command shouldFilter={!large}>
          <CommandInput
            placeholder={searchPlaceholder}
            value={query}
            onValueChange={(next) => {
              setQuery(next);
              // A new query invalidates the scroll position; without this the
              // list can open part-way down after a wide filter.
              listRef.current?.scrollTo({ top: 0 });
            }}
          />
          <CommandList ref={listRef} className="max-h-64">
            <CommandEmpty>{emptyLabel}</CommandEmpty>
            {grouped.map(([group, items]) => (
              <CommandGroup key={group || "_"} heading={group || undefined}>
                {items.map((option) => (
                  <CommandItem
                    key={option.value}
                    value={
                      // cmdk matches on this string, so the keywords have to be
                      // part of it for a schema-qualified search to hit.
                      option.keywords
                        ? `${option.label} ${option.keywords}`
                        : option.label
                    }
                    onSelect={() => {
                      onChange(option.value);
                      setOpen(false);
                      setQuery("");
                    }}
                  >
                    <Check
                      className={cn(
                        "mr-2 size-4",
                        option.value === value ? "opacity-100" : "opacity-0",
                      )}
                    />
                    <span className="truncate">{option.label}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}

            {hiddenCount > 0 && (
              // Truthful rather than silent: the list IS cut, and an author
              // searching a 1,200-column table should know the rest are behind
              // a narrower query.
              <div className="px-3 py-2 text-[11px] text-muted-foreground">
                Showing {visible.length} of {options.length}. Keep typing to narrow.
              </div>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
