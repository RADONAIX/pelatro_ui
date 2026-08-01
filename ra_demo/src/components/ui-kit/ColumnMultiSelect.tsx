import { useMemo, useState } from "react";
import { Check, ChevronsUpDown, Lock } from "lucide-react";
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
// A searchable multi-select for picking report columns.
//
// Same shell as SearchableSelect — trigger, popover, cmdk search — so the two
// read as one control. The differences: selection toggles instead of closing,
// and an option can be LOCKED — shown selected and refusing to toggle off.
//
// Locked options exist because some choices are consequences of others. A
// rule's own key, metric and checked columns are always in its report; showing
// them selected-but-fixed says so, where hiding them would leave the author
// wondering why the report has columns they never picked.
//
// Named apart from the existing MultiSelect, which is a Set-based filter
// control with no search and no locking — a different job.
// ---------------------------------------------------------------------------

export interface ColumnOption {
  value: string;
  label: string;
  group?: string;
  /** Always selected; cannot be toggled off. */
  locked?: boolean;
  /** Short reason shown beside a locked row, e.g. "key". */
  lockedReason?: string;
}

const VIRTUALISE_ABOVE = 200;
const WINDOW_SIZE = 100;

export function ColumnMultiSelect({
  options,
  value,
  onChange,
  placeholder = "Add columns…",
  searchPlaceholder = "Search columns…",
  emptyLabel = "No columns found",
  disabled = false,
  className,
}: {
  options: ColumnOption[];
  /** Selected values EXCLUDING the locked ones — those are implicit. */
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const lockedValues = useMemo(
    () => options.filter((o) => o.locked).map((o) => o.value),
    [options],
  );
  const selected = useMemo(
    () => new Set([...lockedValues, ...value]),
    [lockedValues, value],
  );

  const large = options.length > VIRTUALISE_ABOVE;
  const visible = useMemo(() => {
    if (!large) return options;
    const q = query.trim().toLowerCase();
    const matches = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
    return matches.slice(0, WINDOW_SIZE);
  }, [large, options, query]);
  const hiddenCount = large ? Math.max(0, options.length - visible.length) : 0;

  const grouped = useMemo(() => {
    const groups = new Map<string, ColumnOption[]>();
    for (const option of visible) {
      const key = option.group ?? "";
      const bucket = groups.get(key);
      if (bucket) bucket.push(option);
      else groups.set(key, [option]);
    }
    return [...groups.entries()];
  }, [visible]);

  const toggle = (option: ColumnOption) => {
    if (option.locked) return;
    onChange(
      value.includes(option.value)
        ? value.filter((v) => v !== option.value)
        : [...value, option.value],
    );
  };

  const summary =
    selected.size === 0
      ? placeholder
      : `${selected.size} column${selected.size === 1 ? "" : "s"}`;

  return (
    <Popover
      // Modal, or the list will not scroll inside a Dialog — see SearchableSelect.
      modal
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
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
            selected.size === 0 && "text-muted-foreground",
            className,
          )}
        >
          <span className="truncate">{summary}</span>
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <Command shouldFilter={!large}>
          <CommandInput
            placeholder={searchPlaceholder}
            value={query}
            onValueChange={setQuery}
          />
          <CommandList className="max-h-64">
            <CommandEmpty>{emptyLabel}</CommandEmpty>
            {grouped.map(([group, items]) => (
              <CommandGroup key={group || "_"} heading={group || undefined}>
                {items.map((option) => {
                  const isSelected = selected.has(option.value);
                  return (
                    <CommandItem
                      key={option.value}
                      value={option.label}
                      // Toggling keeps the popover open, so several columns can
                      // be picked in one visit.
                      onSelect={() => toggle(option)}
                      className={cn(option.locked && "cursor-default")}
                    >
                      <span
                        className={cn(
                          "mr-2 flex size-4 shrink-0 items-center justify-center rounded-[4px] border",
                          isSelected
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-input",
                          option.locked && "opacity-70",
                        )}
                      >
                        {isSelected && <Check className="size-3" />}
                      </span>
                      <span className={cn("flex-1 truncate", option.locked && "opacity-70")}>
                        {option.label}
                      </span>
                      {option.locked && (
                        <span
                          className="ml-2 flex shrink-0 items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground"
                          title="Used by the rule, so always in the report"
                        >
                          <Lock className="size-3" />
                          {option.lockedReason ?? "always"}
                        </span>
                      )}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            ))}
            {hiddenCount > 0 && (
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
