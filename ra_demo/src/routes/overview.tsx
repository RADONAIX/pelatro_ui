import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import {
  ArrowRight,
  Building2,
  Database,
  Handshake,
  Layers,
  Radar,
  Settings2,
  ShieldCheck,
  ShoppingCart,
  Target,
  Users,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { ASSURANCE_WORKSPACE_GROUPS, useAssuranceScope } from "@/lib/assuranceScope";

// ---------------------------------------------------------------------------
// The landing page — where a session starts.
//
// Its job is to get the user to pick an assurance before they go anywhere else:
// that choice is the Assurance Scope, and it decides which app the Controls
// screen and the header switcher point at for the rest of the session. Picking
// one here sets the scope and continues into the platform, so the scope is never
// an unnoticed default.
//
// Built on this app's own tokens rather than the mockup's indigo, so it sits
// inside the amber shell instead of fighting it. Every colour below is a
// semantic token, so light/dark both follow automatically.
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

const PILLARS: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Radar,
    title: "360° Visibility",
    body: "Real-time visibility across processes, data and systems in one unified view.",
  },
  {
    icon: ShieldCheck,
    title: "Proactive Intelligence",
    body: "Insights identify risks, anomalies and opportunities before they impact your business.",
  },
  {
    icon: Zap,
    title: "Automation at Scale",
    body: "Automate controls, investigations and resolutions with built-in orchestration.",
  },
  {
    icon: Target,
    title: "Business Impact",
    body: "Focus on what matters most — protect revenue, reduce leakage and improve customer trust.",
  },
];

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
    <div className="mx-auto max-w-7xl">
      <Hero />
      <AssuranceChooser />
      <Pillars />
    </div>
  );
}

function Hero() {
  return (
    <section className="grid items-center gap-10 lg:grid-cols-2">
      <div className="min-w-0">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
          <Layers className="h-3.5 w-3.5 text-primary" />
          Enterprise Assurance
        </span>

        <h1 className="mt-5 text-4xl font-semibold leading-[1.1] tracking-tight text-foreground sm:text-5xl">
          Assurance that
          <br />
          <span className="text-primary">protects value.</span>
          <br />
          Everyday.
        </h1>

        <p className="mt-5 max-w-xl text-base leading-relaxed text-muted-foreground">
          RADONaix unifies data, intelligence and automation to continuously assure every
          transaction, process and experience across your enterprise.
        </p>

        <a
          href="#choose-assurance"
          className="mt-8 inline-flex items-center gap-2 rounded-lg bg-foreground px-5 py-3 text-sm font-medium text-background transition hover:opacity-90"
        >
          Explore Platform
          <ArrowRight className="h-4 w-4" />
        </a>
      </div>

      <OrbitalDiagram />
    </section>
  );
}

/**
 * The five assurance workspaces arranged around a core. Decorative on small
 * screens' terms — it is hidden below lg, where the chooser below carries the
 * same information in a form that actually fits.
 */
function OrbitalDiagram() {
  const nodes = ASSURANCE_WORKSPACE_GROUPS.map((group, i) => {
    // Evenly spaced from the top, clockwise.
    const angle = ((-90 + (360 / ASSURANCE_WORKSPACE_GROUPS.length) * i) * Math.PI) / 180;
    return {
      group,
      left: 50 + 40 * Math.cos(angle),
      top: 50 + 40 * Math.sin(angle),
    };
  });

  return (
    <div className="relative hidden aspect-square w-full lg:block">
      {/* Concentric rings */}
      {[100, 78, 56].map((size) => (
        <div
          key={size}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-border/60"
          style={{ width: `${size}%`, height: `${size}%` }}
        />
      ))}

      {/* Core */}
      <div className="absolute left-1/2 top-1/2 grid size-[26%] -translate-x-1/2 -translate-y-1/2 place-items-center">
        <div className="absolute inset-0 rounded-full bg-primary/25 blur-2xl" />
        <div className="absolute inset-[12%] rounded-full bg-gradient-to-br from-primary/40 to-primary/10" />
        <span className="relative grid size-[52%] place-items-center rounded-2xl border border-primary/40 bg-card/80 text-2xl font-bold text-primary backdrop-blur">
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
            <div className="flex items-center gap-2.5 rounded-xl border border-border bg-card px-3 py-2.5 shadow-sm">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              <span className="whitespace-nowrap text-xs font-medium leading-tight text-foreground">
                {group.name.replace(" Assurance", "")}
                <span className="block text-muted-foreground">Assurance</span>
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The actual point of this page: pick an assurance, which sets the scope and
 * moves on. Grouped by workspace so the list matches the header switcher
 * exactly — same source, same order.
 */
function AssuranceChooser() {
  const { scope, setScope } = useAssuranceScope();
  const navigate = useNavigate();
  const [active, setActive] = useState<string | null>(null);

  const choose = (appId: string) => {
    setActive(appId);
    setScope(appId);
    // Continue into the platform on the first module in the sidebar.
    navigate({ to: "/rating" });
  };

  return (
    <section id="choose-assurance" className="mt-16 scroll-mt-6">
      <div className="flex flex-col gap-1 border-t border-border pt-10">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">
          Choose an assurance to continue
        </h2>
        <p className="text-sm text-muted-foreground">
          Your selection sets the assurance scope for this session. You can change it any time from
          the switcher in the header.
        </p>
      </div>

      <div className="mt-6 space-y-6">
        {ASSURANCE_WORKSPACE_GROUPS.map((group) => {
          const Icon = WORKSPACE_ICON[group.id] ?? Handshake;
          return (
            <div key={group.id}>
              <div className="flex items-center gap-2">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                <h3 className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
                  {group.name}
                </h3>
                <span className="text-[11px] text-muted-foreground/70">· {group.tagline}</span>
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {group.apps.map((app) => {
                  const current = scope === app.id;
                  const busy = active === app.id;
                  return (
                    <button
                      key={app.id}
                      onClick={() => choose(app.id)}
                      aria-current={current ? "true" : undefined}
                      className={`group flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition ${
                        current
                          ? "border-primary bg-primary/5"
                          : "border-border bg-card hover:border-primary/50 hover:bg-muted/40"
                      } ${busy ? "opacity-70" : ""}`}
                    >
                      <div className="flex w-full items-center justify-between gap-2">
                        <span className="text-sm font-medium text-foreground">{app.name}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {app.prefix}
                        </span>
                      </div>
                      <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                        {app.summary}
                      </p>
                      <span className="mt-1 inline-flex items-center gap-1.5 text-xs font-medium text-primary opacity-0 transition group-hover:opacity-100">
                        {current ? "Continue" : "Select"}
                        <ArrowRight className="h-3.5 w-3.5" />
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

function Pillars() {
  return (
    <section className="mt-16 grid gap-4 border-t border-border pt-10 sm:grid-cols-2 lg:grid-cols-4">
      {PILLARS.map(({ icon: Icon, title, body }) => (
        <div key={title} className="rounded-xl border border-border bg-card p-5">
          <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
            <Icon className="h-4 w-4" />
          </span>
          <h3 className="mt-4 text-sm font-semibold text-foreground">{title}</h3>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{body}</p>
        </div>
      ))}
    </section>
  );
}
