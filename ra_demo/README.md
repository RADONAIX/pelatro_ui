# RADONaix — Revenue Assurance UI

Front-end for the RADONaix revenue assurance platform: reconciliation reporting,
pipeline monitoring, certified exports, case management and platform
administration.

This repository is the **demo build** of the UI. Most screens talk to the real
RADONaix API; a handful are self-contained client-side modules used to show
intended functionality that the backend does not implement yet. Which is which is
documented below — it matters, and it isn't guessable from the screens.

---

## Stack

| | |
|---|---|
| Framework | React 19 + [TanStack Start](https://tanstack.com/start) (file-based routing) |
| Build | Vite 7 |
| Styling | Tailwind CSS 4, hand-rolled design system in `src/components/ui-kit` |
| Data | TanStack Query, Axios |
| Charts / BI | Recharts, embedded Apache Superset |
| Language | TypeScript (strict) |

---

## Getting started

```bash
npm install
npm run dev
```

The dev server needs to know where the API lives. Without it, requests fall back
to a relative `/api`, which 404s unless something is proxying:

```bash
VITE_API_BASE_URL=http://localhost:8000/api npm run dev
```

**Port matters.** The backend's CORS allow-list is `localhost:3000`, `5173` and
`8080`. Vite may pick a different port if those are taken, and the app will then
fail every API call with a CORS error rather than an obvious one — pin it:

```bash
VITE_API_BASE_URL=http://localhost:8000/api npm run dev -- --port 5173 --strictPort
```

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `VITE_API_BASE_URL` | RADONaix API base | `/api` (relative) |
| `VITE_PIPELINES_API_BASE` | Pipelines API host | falls back to the API base |
| `VITE_AUTH_API_BASE` | Host used for the Superset guest-token call | `""` (same origin) |
| `VITE_SUPERSET_DOMAIN` | Superset instance for the embedded dashboard | — |
| `VITE_GRAFANA_URL` | Grafana instance for System Monitoring panels | `/grafana` |

### Scripts

| Command | Does |
|---|---|
| `npm run dev` | Dev server with HMR |
| `npm run build` | Production build (SPA mode — static output for nginx) |
| `npm run preview` | Serve the production build |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |

---

## Modules

### Backend-connected

These call the RADONaix API and reflect real data.

| Module | Route | API |
|---|---|---|
| Dashboard & KPIs | `/` | Embedded Superset via `/superset/guest-token` |
| Reports & Certified Exports | `/reports` | `/reports/{key}`, `/exports` |
| Pipelines & Job Monitor | `/pipelines` | `/pipelines/*` |
| Download Centre | `/downloads` | `/exports` |
| User Management | `/users` | `/users` |
| Role Management | `/roles` | `/roles` |
| Audit Logs | `/audit-logs` | `/audit-logs` |
| System Configuration | `/system-config` | `/system/config` |
| Profile & auth | `/profile`, `/login`, … | `/auth/*` |

**System Monitoring** (`/monitoring`) is a special case: it embeds Grafana
dashboards, but its server inventory is currently a static list in the route file.
Its **Report Servers → Server Operations** tab embeds a separate **ServerOps**
app (a Next.js service that SSHes into the hosts for live systemd control). That
app is *not* part of this repo — it runs on its own port (`:3003`) and is proxied
same-origin at `/serverops/`; the tab iframes `VITE_SERVEROPS_URL` (default
`/serverops/`). See `deploy/nginx/ra_demo.conf` for the proxy block.

### Client-side modules

These persist to `localStorage` and make **no** backend calls. They exist because
the API does not cover them — in some cases the endpoints are absent entirely, in
others they are implemented but disabled server-side.

| Module | Route | Why it isn't wired |
|---|---|---|
| Data Sources | `/data-sources` | No CRUD API; stream/table config lives in server-side YAML + `DATA_STREAMS` env |
| Recon Workflows | `/recon-workflows` | No recon-config surface at all. `/api/recon` only *reads* results computed elsewhere; it never defines the rules |
| Database Connections | `/database-connections` | No connection CRUD API |
| Case Management | `/cases` | `/api/cases` exists in the backend but its router is commented out |
| Assurance Workbench | `/workbench` | `/api/workbench` likewise disabled. **Hidden from navigation** — reachable by direct URL only |

Each of these files opens with a `DEMO-ONLY` comment explaining its specific
situation. Where the client shape overlaps the real API (case `severity`,
`status`, `owner`, `linkedTxnId`, comments) it deliberately uses the backend's
vocabulary, so wiring them later is a small step rather than a rewrite.

#### `localStorage` keys

Versioned. **Bump the suffix whenever seed values or the stored shape change** —
the loaders prefer any non-empty stored list over the seed, so without a bump an
existing session keeps stale data and the change looks broken.

```
radonaix_cases_v4             Case Management
radonaix_db_connections_v1    Database Connections
radonaix_data_sources_v3      Data Sources
radonaix_recon_workflows_v1   Recon Workflows
radonaix_workbench_v1         Assurance Workbench
radonaix_theme                Light/dark preference
radonaix_sidebar_collapsed    Sidebar state
radonaix_scope                Assurance scope selector
```

Clear them in DevTools to reset a module to its seed.

---

## Navigation

```
Dashboard & KPIs
Reports & Certified Exports   › report catalogue
Pipelines & Job Monitor
Case Management               › Cases | Self Assigned
Operations                    › Data Sources
                                Recon Workflows
System Monitoring             › Applications | Databases | Report Servers
```

Administration — Users, Roles, Audit Logs, Database Connections — lives in the
gear menu in the header, not the sidebar.

**System Monitoring is deliberately top-level rather than inside Operations.** It
already has its own sub-items, so nesting it would create a third level the
sidebar doesn't support; it also reports platform health rather than
assurance-domain state, and is gated on a different permission.

## Access control

Route access is driven by `PATH_TO_PERM` in `src/lib/auth.tsx`, checked against
the permission map the backend returns from `/auth/my-permissions`. Roles:
`admin`, `ra_lead`, `analyst`, `viewer`.

The client-side modules deliberately carry **no** permission key, so they stay
visible to any signed-in user, except Database Connections which reuses the
existing `settings` permission (admin + RA Manager only, read-only for the
latter). No new permission keys were invented — the backend doesn't know about
them.

## Layout

```
src/
├─ routes/            file-based routes (one file per screen)
├─ components/
│  ├─ layout/         AppShell, Sidebar, Header, PageHeader
│  ├─ ui-kit/         the design system actually in use
│  ├─ ui/             shadcn primitives — almost entirely unused, see note
│  ├─ cases/          case investigation modal + assistant panel
│  ├─ pipelines/  reports/  downloads/  auth/
├─ lib/               api client, auth, i18n, per-module data modules
└─ services/          typed API service layer
```

> **Note on `components/ui/`** — a shadcn tree is present but effectively dead:
> only `calendar` and `popover` are imported, by `DateRangePicker`. Every screen
> is hand-written Tailwind against `components/ui-kit/`. Match the ui-kit house
> style, not shadcn.

## Conventions

- **Persist from handlers, never a reactive effect.** A `useEffect([state])`
  writer races the mount-time load and clobbers storage with the empty initial
  state — reliably, under StrictMode's double-invoked effects in dev. Every
  client-side module loads in a mount-only effect and writes via an explicit
  `persist()`.
- **Guard `typeof window`** on every storage read and write; the build prerenders.
- **Plain strings in the client-side modules**, `useT()` everywhere else. The
  demo modules are consistently untranslated; mixing the two reads as an accident.
- **Widen literal unions for form state.** `as const` arrays infer literal types,
  but a native `<select>` yields `string` — form interfaces widen those fields and
  narrow once on save.
- **Native `<select>` inside modals.** The ui-kit `Select` renders its menu as an
  absolutely-positioned element that clips inside a scrolling dialog.

## Deploying alongside the production app

The production RADONaix UI owns `/var/www/radonaix` and deploys with
`rsync -a --delete`. **Do not deploy this build there** — it would replace the
live app, and the next production deploy would delete it again.

`deploy/nginx/ra_demo.conf` serves this UI on its own port and web root, sharing
the same backend and Grafana:

```bash
cd /opt/ra_demo
git clone https://github.com/RADONAIX/ra_demo.git .
node --version                 # must be >= 20.19 (Vite 7)
npm ci
npm run build                  # no VITE_ vars — the relative defaults are correct

sudo mkdir -p /var/www/ra_demo
sudo rsync -a --delete dist/client/ /var/www/ra_demo/
sudo cp deploy/nginx/ra_demo.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

Production stays on `https://<host>/`; the demo answers on `https://<host>:8443/`.

**The demo talks to the production backend.** Reports, Pipelines, Users, Roles,
Audit Logs and System Configuration are live — a write in the demo is a write in
production. Demo with a `viewer` or `analyst` account so the platform's own RBAC
blocks the write paths.

## Assistant panel

Case Management includes an assurance assistant in the investigation view. There
is **no LLM integration in this project** — no dependency, no `/chat` endpoint.
Replies are keyword-matched against the open case and generated client-side in
`src/components/cases/AssistantPanel.tsx`. Wiring it to a real model means a new
backend integration.
