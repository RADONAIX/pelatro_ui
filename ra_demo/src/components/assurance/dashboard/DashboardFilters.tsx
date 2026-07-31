import { useState } from "react";
import { CalendarDays, Download, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

// Filters are identical across all eight assurances — same four selects, same
// two actions, same order. Demo-only: they present state, they don't refetch.

const DATE_RANGES = [
  "Last 7 days",
  "Last 30 days",
  "Last 90 days",
  "Current cycle",
  "Year to date",
];
const SERVICES = ["All Services", "Voice", "SMS", "Data", "Roaming", "VAS"];
const TECHNOLOGIES = [
  "All Technologies",
  "2G",
  "3G",
  "4G / LTE",
  "5G SA",
  "IMS",
];
const REGIONS = [
  "All Regions",
  "North",
  "South",
  "East",
  "West",
  "International",
];

function Select({
  label,
  options,
  value,
  onChange,
  icon,
}: {
  label: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
  icon?: React.ReactNode;
}) {
  return (
    <label className="group relative flex h-9 items-center gap-2 rounded-lg border border-border/70 bg-card pl-3 pr-2 transition-colors hover:border-border focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/15">
      {icon}
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="cursor-pointer appearance-none bg-transparent pr-4 text-[13px] text-foreground outline-none"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      <svg
        className="pointer-events-none absolute right-2.5 size-3 text-muted-foreground"
        viewBox="0 0 12 12"
        fill="none"
        aria-hidden
      >
        <path
          d="M3 4.5 6 7.5 9 4.5"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
    </label>
  );
}

export function DashboardFilters({ onRefresh }: { onRefresh?: () => void }) {
  const [range, setRange] = useState(DATE_RANGES[1]);
  const [service, setService] = useState(SERVICES[0]);
  const [tech, setTech] = useState(TECHNOLOGIES[0]);
  const [region, setRegion] = useState(REGIONS[0]);
  const [spinning, setSpinning] = useState(false);

  const refresh = () => {
    setSpinning(true);
    onRefresh?.();
    window.setTimeout(() => setSpinning(false), 700);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        label="Date range"
        options={DATE_RANGES}
        value={range}
        onChange={setRange}
        icon={
          <CalendarDays className="size-3.5 shrink-0 text-muted-foreground" />
        }
      />
      <Select
        label="Service"
        options={SERVICES}
        value={service}
        onChange={setService}
      />
      <Select
        label="Technology"
        options={TECHNOLOGIES}
        value={tech}
        onChange={setTech}
      />
      <Select
        label="Region"
        options={REGIONS}
        value={region}
        onChange={setRegion}
      />

      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          className="flex h-9 items-center gap-2 rounded-lg border border-border/70 bg-card px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-muted/60"
        >
          <Download className="size-3.5 text-muted-foreground" />
          Export
        </button>
        <button
          type="button"
          onClick={refresh}
          className="flex h-9 items-center gap-2 rounded-lg bg-primary px-3 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          <RefreshCw className={cn("size-3.5", spinning && "animate-spin")} />
          Refresh
        </button>
      </div>
    </div>
  );
}
