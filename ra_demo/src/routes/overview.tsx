import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  Building2,
  Database,
  Handshake,
  Layers,
  Settings2,
  ShieldCheck,
  ShoppingCart,
  Sparkles,
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
// Layout: a contained hero panel (copy left, orbital right) above the chooser
// grid. Every card in the grid shares the same anatomy — accent rail, header,
// app buttons, tagline footer — so the five columns read as equals even though
// they hold different numbers of apps.
//
// Every colour is a semantic token, so light/dark both follow.
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

/**
 * Accent per workspace, from the chart palette. Purely decorative: it gives the
 * five categories a stable identity across the orbit and the chooser cards
 * without inventing colours outside the theme.
 */
const WORKSPACE_ACCENT: Record<string, string> = {
  commercial: "var(--chart-1)",
  customer: "var(--chart-2)",
  revenue: "var(--chart-3)",
  operations: "var(--chart-4)",
  financial: "var(--chart-5)",
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
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <Hero />
      <AssuranceChooser />
    </div>
  );
}

/**
 * The hero, contained in a bordered panel so the copy and the orbital diagram
 * hang together instead of floating in page whitespace. The gradient wash and
 * dot grid live inside the panel and are decorative only.
 */
function Hero() {
  const workspaces = ASSURANCE_WORKSPACE_GROUPS.length;
  const assurances = ASSURANCE_WORKSPACE_GROUPS.reduce((n, g) => n + g.apps.length, 0);

  return (
    <section className="relative isolate overflow-hidden rounded-2xl border border-border bg-card shadow-sm animate-in fade-in slide-in-from-bottom-2 duration-500">
      {/* Decorative layers, clipped by the panel. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-info/5" />
        <div className="absolute -left-20 -top-24 size-80 rounded-full bg-primary/15 blur-3xl" />
        <div className="absolute -right-10 -bottom-28 size-80 rounded-full bg-info/10 blur-3xl" />
        <div
          className="absolute inset-0 opacity-40 dark:opacity-20"
          style={{
            backgroundImage:
              "radial-gradient(circle at center, var(--border) 1px, transparent 1px)",
            backgroundSize: "24px 24px",
            maskImage: "linear-gradient(105deg, transparent 35%, #000 70%)",
            WebkitMaskImage: "linear-gradient(105deg, transparent 35%, #000 70%)",
          }}
        />
      </div>

      <div className="grid items-center gap-8 p-8 sm:p-10 lg:grid-cols-[1.2fr_auto]">
        <div className="min-w-0">
          <span className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-[11px] font-medium uppercase tracking-widest text-foreground/70">
            <Layers className="h-3.5 w-3.5 text-primary" />
            Enterprise Assurance
          </span>

          <h1 className="mt-5 text-3xl font-semibold leading-[1.15] tracking-tight text-foreground sm:text-4xl">
            Assurance that{" "}
            <span className="relative inline-block text-primary">
              protects value.
              <span
                aria-hidden
                className="absolute inset-x-0 -bottom-0.5 h-[3px] rounded-full bg-gradient-to-r from-primary via-primary/60 to-transparent"
              />
            </span>
            <br />
            Everyday.
          </h1>

          <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted-foreground">
            RADONaix unifies data, intelligence and automation to continuously assure every
            transaction, process and experience across your enterprise.
          </p>

          <dl className="mt-7 flex flex-wrap items-center gap-2.5">
            <HeroStat icon={Layers} value={workspaces} label="assurance domains" />
            <HeroStat icon={ShieldCheck} value={assurances} label="assurance apps" />
            <HeroStat icon={Sparkles} value="24×7" label="continuous coverage" />
          </dl>
        </div>

        <OrbitalDiagram />
      </div>
    </section>
  );
}

function HeroStat({
  icon: Icon,
  value,
  label,
}: {
  icon: LucideIcon;
  value: string | number;
  label: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-border bg-background/80 px-3.5 py-2 shadow-sm backdrop-blur">
      <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
      <dt className="sr-only">{label}</dt>
      <dd className="text-[11px] leading-none text-muted-foreground">
        <span className="text-sm font-semibold text-foreground">{value}</span>{" "}
        <span className="align-middle">{label}</span>
      </dd>
    </div>
  );
}

/**
 * The five assurance workspaces around a core. Hidden below lg, where the
 * chooser carries the same information in a form that actually fits.
 *
 * The outer rings drift in opposite directions and dashed spokes tie the core
 * to each node, so the diagram reads as one connected system rather than five
 * loose chips. Motion is suppressed under prefers-reduced-motion.
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
    <div className="relative hidden size-[360px] shrink-0 place-self-center lg:mr-10 lg:block">
      {/* Rings — the outer two drift in opposite directions. */}
      {[
        { size: 100, spin: "50s", dir: "normal", dashed: true },
        { size: 74, spin: "75s", dir: "reverse", dashed: false },
        { size: 48, spin: "0s", dir: "normal", dashed: false },
      ].map((ring) => (
        <div
          key={ring.size}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-border/70 motion-reduce:animate-none"
          style={{
            width: `${ring.size}%`,
            height: `${ring.size}%`,
            borderStyle: ring.dashed ? "dashed" : "solid",
            animation: ring.spin === "0s" ? undefined : `spin ${ring.spin} linear infinite`,
            animationDirection: ring.dir,
          }}
        />
      ))}

      {/* A comet riding the outer ring — a small primary dot with a trail,
          carried around by a slowly spinning wrapper. */}
      <div
        aria-hidden
        className="absolute inset-0 motion-reduce:hidden"
        style={{ animation: "spin 18s linear infinite" }}
      >
        <span className="absolute left-1/2 top-0 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary shadow-[0_0_12px_2px_var(--primary)]" />
      </div>

      {/* Spokes from the core out to each node. */}
      <svg aria-hidden className="absolute inset-0 size-full" viewBox="0 0 100 100">
        {nodes.map(({ group, left, top }) => (
          <line
            key={group.id}
            x1="50"
            y1="50"
            x2={left}
            y2={top}
            stroke="var(--border)"
            strokeWidth="0.4"
            strokeDasharray="1.5 2"
          />
        ))}
      </svg>

      {/* Core. */}
      <div className="absolute left-1/2 top-1/2 grid size-[26%] -translate-x-1/2 -translate-y-1/2 place-items-center">
        <div className="absolute inset-0 animate-pulse rounded-full bg-primary/30 blur-2xl motion-reduce:animate-none" />
        <div className="absolute inset-[6%] rounded-full bg-gradient-to-br from-primary/45 to-primary/10" />
        <div className="absolute inset-[18%] rounded-full border border-primary/30" />
        <span className="relative grid size-[52%] place-items-center rounded-2xl border border-primary/40 bg-card/90 text-2xl font-bold text-primary shadow-[0_0_36px_-4px_var(--primary)] backdrop-blur">
          R
        </span>
      </div>

      {nodes.map(({ group, left, top }, i) => {
        const Icon = WORKSPACE_ICON[group.id] ?? Handshake;
        const accent = WORKSPACE_ACCENT[group.id] ?? "var(--primary)";
        return (
          <div
            key={group.id}
            className="absolute -translate-x-1/2 -translate-y-1/2 animate-in fade-in zoom-in-90"
            style={{
              left: `${left}%`,
              top: `${top}%`,
              animationDelay: `${150 + i * 90}ms`,
              animationFillMode: "both",
            }}
          >
            <div
              className="flex items-center gap-2 rounded-xl border bg-card/95 px-3 py-2 shadow-md backdrop-blur transition duration-300 hover:-translate-y-0.5 hover:shadow-lg"
              style={{
                borderColor: `color-mix(in oklab, ${accent} 35%, var(--border))`,
                boxShadow: `0 4px 14px -6px color-mix(in oklab, ${accent} 45%, transparent)`,
              }}
            >
              <span
                className="grid size-6 shrink-0 place-items-center rounded-md"
                style={{
                  backgroundColor: `color-mix(in oklab, ${accent} 15%, transparent)`,
                  color: accent,
                }}
              >
                <Icon className="h-3.5 w-3.5" />
              </span>
              <span className="whitespace-nowrap text-xs font-semibold text-foreground">
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
 * boxes inside it. Cards share a fixed anatomy — accent rail, header, apps,
 * tagline footer pinned to the bottom — so the row stays visually level even
 * though categories hold one to three apps each.
 */
function AssuranceChooser() {
  const { scope, setScope } = useAssuranceScope();
  const navigate = useNavigate();

  const choose = (appId: string) => {
    setScope(appId);
    // Land on the chosen assurance's executive dashboard — the "how much
    // revenue is at risk" read-out is what picking an assurance is asking for,
    // and it is the entry point to every other module for that scope.
    navigate({
      to: "/assurance/$appId/$section",
      params: { appId, section: "dashboard" },
    });
  };

  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground">
          <span className="h-4 w-1 rounded-full bg-primary" />
          Choose an assurance to continue
        </h2>
        <p className="text-xs text-muted-foreground">
          Sets the scope for this session — changeable any time from the header switcher.
        </p>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {ASSURANCE_WORKSPACE_GROUPS.map((group, i) => {
          const Icon = WORKSPACE_ICON[group.id] ?? Handshake;
          const accent = WORKSPACE_ACCENT[group.id] ?? "var(--primary)";
          const holdsScope = group.apps.some((a) => a.id === scope);
          return (
            <div
              key={group.id}
              className={`group/card relative flex animate-in flex-col overflow-hidden rounded-xl border bg-card transition duration-300 fade-in slide-in-from-bottom-2 hover:-translate-y-1 hover:shadow-lg ${
                holdsScope ? "border-primary/40 shadow-sm" : "border-border hover:border-primary/30"
              }`}
              style={{ animationDelay: `${i * 70}ms`, animationFillMode: "both" }}
            >
              {/* Category accent rail — solid once this card holds the scope,
                  and revealed on hover otherwise. */}
              <span
                aria-hidden
                className={`absolute inset-x-0 top-0 h-[3px] transition-opacity duration-300 ${
                  holdsScope ? "opacity-100" : "opacity-40 group-hover/card:opacity-100"
                }`}
                style={{ background: `linear-gradient(90deg, ${accent}, transparent)` }}
              />

              <div className="flex items-center gap-2.5 p-3 pb-2.5">
                <span
                  className="grid size-9 shrink-0 place-items-center rounded-lg transition duration-300 group-hover/card:scale-105"
                  style={{
                    backgroundColor: `color-mix(in oklab, ${accent} 14%, transparent)`,
                    color: accent,
                  }}
                >
                  <Icon className="h-4.5 w-4.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold leading-tight text-foreground">
                    {WORKSPACE_LABEL[group.id]}
                  </span>
                  <span className="block text-[11px] leading-tight text-muted-foreground">
                    Assurance
                  </span>
                </span>
                <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted-foreground">
                  {group.apps.length}
                </span>
              </div>

              <div className="flex flex-col gap-1.5 px-3">
                {group.apps.map((app) => {
                  const current = scope === app.id;
                  return (
                    <button
                      key={app.id}
                      onClick={() => choose(app.id)}
                      aria-current={current ? "true" : undefined}
                      title={app.summary}
                      className={`group/app flex items-center justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition duration-200 ${
                        current
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-transparent bg-muted/50 text-muted-foreground hover:border-primary/40 hover:bg-muted hover:text-foreground"
                      }`}
                    >
                      <span className="min-w-0 truncate text-[13px] font-medium">
                        {app.name.replace(" Assurance", "")}
                      </span>
                      <span className="flex shrink-0 items-center gap-1">
                        <span
                          className={`font-mono text-[10px] ${
                            current ? "text-primary" : "text-muted-foreground/70"
                          }`}
                        >
                          {app.prefix}
                        </span>
                        <ArrowRight
                          aria-hidden
                          className={`h-3 w-3 transition-all duration-200 ${
                            current
                              ? "text-primary"
                              : "-ml-1 opacity-0 group-hover/app:ml-0 group-hover/app:opacity-100"
                          }`}
                        />
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* Tagline footer, pinned to the bottom so every card ends the
                  same way regardless of how many apps sit above it. */}
              <p className="mt-auto border-t border-border/60 px-3 py-2.5 pt-2.5 text-[11px] leading-snug text-muted-foreground">
                {group.tagline}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
