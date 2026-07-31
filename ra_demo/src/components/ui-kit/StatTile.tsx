import type { LucideIcon } from "lucide-react";

// Summary tile used above list screens. Renders as a plain <div> by default; pass
// onClick to make it an interactive filter toggle, which reports its state via
// aria-pressed rather than colour alone.
export function StatTile({
  icon: Icon,
  label,
  value,
  onClick,
  active = false,
}: Readonly<{
  icon: LucideIcon;
  label: string;
  value: string;
  onClick?: () => void;
  active?: boolean;
}>) {
  const body = (
    <>
      <span
        className={`h-10 w-10 shrink-0 rounded-lg flex items-center justify-center transition-colors ${
          active ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary"
        }`}
      >
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0 text-left">
        <div className="text-xs text-muted-foreground truncate">{label}</div>
        <div className="text-lg font-semibold text-foreground tabular-nums">{value}</div>
      </div>
    </>
  );

  if (!onClick) {
    return <div className="bg-card border border-border rounded-xl p-4 flex items-center gap-3">{body}</div>;
  }

  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`bg-card border rounded-xl p-4 flex items-center gap-3 w-full text-left transition ${
        active ? "border-primary ring-1 ring-primary/30" : "border-border hover:bg-muted/40"
      }`}
    >
      {body}
    </button>
  );
}
