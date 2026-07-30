import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  Building2,
  Database,
  Handshake,
  Layers,
  Settings2,
  ShoppingCart,
  Users,
  type LucideIcon,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { ASSURANCE_WORKSPACE_GROUPS, useAssuranceScope } from "@/lib/assuranceScope";

// ---------------------------------------------------------------------------
// The landing page — where a session starts.
//
// Its job is to get the user to pick an assurance before they go anywhere else:
// that choice is the Assurance Scope, and it decides which app the Controls
// screen and the header switcher point at for the rest of the session.
//
// Sized to fit one viewport without scrolling, so the choice is never below the
// fold. Every colour is a semantic token, so light/dark both follow.
// ---------------------------------------------------------------------------

export const Route = createFileRoute("/overview")({
  component: OverviewPage,
});

/** Icon per workspace — mirrors the orbital diagram in the product mockup. */
const WORKSPACE_ICON: Record<string, LucideIcon> = {
  commercial: ShoppingCart,
  customer: Users,
  revenue: Database,
  operations: Settings2,
  financial: Building2,
};

/** Workspace short name, for the micro-label on each assurance card. */
const WORKSPACE_LABEL: Record<string, string> = Object.fromEntries(
  ASSURANCE_WORKSPACE_GROUPS.map((g) => [g.id, g.name.replace(" Assurance", "")]),
);

function OverviewPage() {
  return (
    <AppShell>
      <OverviewContent />
    </AppShell>
  );
}

/**
 * The page body, separate from the AppShell wrapper so it can be rendered
 * without the auth gate.
 */
export function OverviewContent() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8">
      <Hero />
      <AssuranceChooser />
    </div>
  );
}

function Hero() {
  return (
    <section className="grid items-center gap-8 lg:grid-cols-[1fr_auto]">
      <div className="min-w-0">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
          <Layers className="h-3.5 w-3.5 text-primary" />
          Enterprise Assurance
        </span>

        <h1 className="mt-4 text-3xl font-semibold leading-[1.15] tracking-tight text-foreground sm:text-4xl">
          Assurance that <span className="text-primary">protects value.</span>
          <br />
          Everyday.
        </h1>

        <p className="mt-3 max-w-lg text-sm leading-relaxed text-muted-foreground">
          RADONaix unifies data, intelligence and automation to continuously assure every
          transaction, process and experience across your enterprise.
        </p>
      </div>

      <OrbitalDiagram />
    </section>
  );
}

/**
 * The five assurance workspaces around a core. Hidden below lg, where the
 * chooser carries the same information in a form that actually fits.
 */
function OrbitalDiagram() {
  const nodes = ASSURANCE_WORKSPACE_GROUPS.map((group, i) => {
    // Evenly spaced from the top, clockwise.
    const angle = ((-90 + (360 / ASSURANCE_WORKSPACE_GROUPS.length) * i) * Math.PI) / 180;
    return {
      group,
      left: 50 + 38 * Math.cos(angle),
      top: 50 + 38 * Math.sin(angle),
    };
  });

  return (
    <div className="relative hidden size-[300px] shrink-0 lg:block">
      {[100, 74, 48].map((size) => (
        <div
          key={size}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-border/60"
          style={{ width: `${size}%`, height: `${size}%` }}
        />
      ))}

      <div className="absolute left-1/2 top-1/2 grid size-[24%] -translate-x-1/2 -translate-y-1/2 place-items-center">
        <div className="absolute inset-0 rounded-full bg-primary/25 blur-2xl" />
        <div className="absolute inset-[10%] rounded-full bg-gradient-to-br from-primary/40 to-primary/10" />
        <span className="relative grid size-[56%] place-items-center rounded-xl border border-primary/40 bg-card/80 text-lg font-bold text-primary backdrop-blur">
          R
        </span>
      </div>

      {nodes.map(({ group, left, top }) => {
        const Icon = WORKSPACE_ICON[group.id] ?? Handshake;
        return (
          <div
            key={group.id}
            className="absolute -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${left}%`, top: `${top}%` }}
          >
            <div className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-2 py-1.5 shadow-sm">
              <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
              <span className="whitespace-nowrap text-[11px] font-medium text-foreground">
                {WORKSPACE_LABEL[group.id]}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The point of this page: pick an assurance, which sets the scope and moves on.
 *
 * One card per assurance category, with that category's assurances as small
 * boxes inside it. The grouping carries the visual weight — the categories are
 * the map of the platform, the boxes inside are the destinations.
 */
function AssuranceChooser() {
  const { scope, setScope } = useAssuranceScope();
  const navigate = useNavigate();

  const choose = (appId: string) => {
    setScope(appId);
    // Continue into the platform on the first module in the sidebar.
    navigate({ to: "/rating" });
  };

  return (
    <section className="border-t border-border pt-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">
          Choose an assurance to continue
        </h2>
        <p className="text-xs text-muted-foreground">
          Sets the scope for this session — changeable any time from the header switcher.
        </p>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {ASSURANCE_WORKSPACE_GROUPS.map((group) => {
          const Icon = WORKSPACE_ICON[group.id] ?? Handshake;
          const holdsScope = group.apps.some((a) => a.id === scope);
          return (
            <div
              key={group.id}
              className={`flex flex-col rounded-xl border bg-card p-3 transition ${
                holdsScope ? "border-primary/40 shadow-sm" : "border-border"
              }`}
            >
              <div className="flex items-center gap-2.5 px-0.5 pb-3">
                <span
                  className={`grid size-9 shrink-0 place-items-center rounded-lg ${
                    holdsScope ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"
                  }`}
                >
                  <Icon className="h-4.5 w-4.5" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold leading-tight text-foreground">
                    {WORKSPACE_LABEL[group.id]}
                  </span>
                  <span className="block text-[11px] leading-tight text-muted-foreground">
                    Assurance
                  </span>
                </span>
              </div>

              <div className="flex flex-1 flex-col gap-1.5">
                {group.apps.map((app) => {
                  const current = scope === app.id;
                  return (
                    <button
                      key={app.id}
                      onClick={() => choose(app.id)}
                      aria-current={current ? "true" : undefined}
                      title={app.summary}
                      className={`flex items-center justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition ${
                        current
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-transparent bg-muted/50 text-muted-foreground hover:border-primary/40 hover:bg-muted hover:text-foreground"
                      }`}
                    >
                      <span className="min-w-0 truncate text-[13px] font-medium">
                        {app.name.replace(" Assurance", "")}
                      </span>
                      <span
                        className={`shrink-0 font-mono text-[10px] ${
                          current ? "text-primary" : "text-muted-foreground/70"
                        }`}
                      >
                        {app.prefix}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
