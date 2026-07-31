import { useState } from "react";
import { ChevronDown, Layers, Sun, Moon } from "lucide-react";

export function TopNav() {
  const [dark, setDark] = useState(false);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
  };

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className="mx-auto flex h-16 max-w-[1600px] items-center gap-3 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
            <span className="text-xs font-black tracking-tight">RA</span>
          </div>
          <div className="min-w-0 leading-tight">
            <p className="truncate text-base font-bold tracking-tight">RADONaix</p>
            <p className="truncate text-[11px] text-muted-foreground">Revenue Assurance</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          <button className="hidden items-center gap-2 rounded-xl border border-primary/60 bg-primary/15 px-3 py-1.5 text-left transition-colors hover:bg-primary/25 md:flex">
            <Layers className="size-4 shrink-0 text-primary-foreground/70" />
            <span className="leading-tight">
              <span className="block text-[9px] font-semibold uppercase tracking-widest text-muted-foreground">
                Assurance Scope
              </span>
              <span className="block text-xs font-semibold">Global View</span>
            </span>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          </button>

          <button
            onClick={toggleTheme}
            aria-label="Toggle theme"
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {dark ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
          </button>

          <div className="flex items-center gap-2 pl-1">
            <div className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">
              AD
            </div>
            <div className="hidden leading-tight sm:block">
              <p className="text-xs font-semibold">Admin</p>
              <p className="text-[10px] text-muted-foreground">Administrator</p>
            </div>
            <ChevronDown className="hidden size-4 text-muted-foreground sm:block" />
          </div>
        </div>
      </div>
    </header>
  );
}
