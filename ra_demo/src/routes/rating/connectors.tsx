import { createFileRoute } from "@tanstack/react-router";
import { useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  HelpCircle,
  Loader2,
  Plug,
  Plus,
  Trash2,
  Upload,
  Wifi,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useCanEditCatalog,
  useConnectorCatalog,
  useConnectorImports,
  useCreateSourceSystem,
  useDeleteSourceSystem,
  useRunConnectorImport,
  useSourceSystems,
  useTestConnection,
} from "@/lib/rating/hooks";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import type { SourceSystem } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/connectors")({
  component: ConnectorsPage,
});

const HEALTH: Record<string, { tone: string; icon: typeof CheckCircle2 }> = {
  HEALTHY: {
    tone: "bg-success/10 text-success border-success/20",
    icon: CheckCircle2,
  },
  DEGRADED: {
    tone: "bg-warning/15 text-warning-foreground border-warning/30",
    icon: AlertTriangle,
  },
  UNREACHABLE: {
    tone: "bg-destructive/10 text-destructive border-destructive/20",
    icon: XCircle,
  },
  UNKNOWN: {
    tone: "bg-muted text-muted-foreground border-border",
    icon: HelpCircle,
  },
};

const EMPTY = {
  code: "",
  name: "",
  vendor: "ERICSSON_CS",
  category: "RULES",
  source_type: "SFTP",
  import_mode: "FULL",
  schedule: "",
  host: "",
  port: "",
  path: "",
  database: "",
  base_url: "",
  username: "",
  password: "",
  token: "",
};

function ConnectorsPage() {
  const t = useT();
  const canEdit = useCanEditCatalog();
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: catalog } = useConnectorCatalog();
  const { data: sources = [], isLoading, error, refetch } = useSourceSystems();
  const create = useCreateSourceSystem();
  const test = useTestConnection();
  const runImport = useRunConnectorImport();
  const remove = useDeleteSourceSystem();

  const [form, setForm] = useState({ ...EMPTY });
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | undefined>();
  const [importTarget, setImportTarget] = useState<string | undefined>();
  const { data: imports = [] } = useConnectorImports(openId);

  const vendors = catalog?.vendors ?? [];
  const selectedVendor = vendors.find((v) => v.code === form.vendor);
  const healthy = sources.filter((s) => s.health_status === "HEALTHY").length;
  const withAdapters = vendors.filter((v) => v.adapter_available).length;

  const patch = (p: Partial<typeof EMPTY>) => setForm((f) => ({ ...f, ...p }));

  const submit = async () => {
    const connection: Record<string, unknown> = {};
    if (form.host) connection.host = form.host;
    if (form.port) connection.port = Number(form.port);
    if (form.path) connection.remote_path = form.path;
    if (form.database) connection.database = form.database;
    if (form.base_url) connection.base_url = form.base_url;

    const credentials: Record<string, unknown> = {};
    if (form.username) credentials.username = form.username;
    if (form.password) credentials.password = form.password;
    if (form.token) credentials.token = form.token;

    try {
      await create.mutateAsync({
        code: form.code.toUpperCase(),
        name: form.name,
        vendor: form.vendor,
        category: form.category,
        source_type: form.source_type,
        import_mode: form.import_mode,
        schedule: form.schedule || null,
        connection,
        credentials,
      });
      toast.success(t("Connector registered"), {
        description: form.code.toUpperCase(),
      });
      setForm({ ...EMPTY });
      setCreating(false);
    } catch (err) {
      toast.error(t("Could not register the connector"), {
        description: ratingError(err),
      });
    }
  };

  const onImportFile = async (file: File | null) => {
    if (!file || !importTarget) return;
    try {
      const result = await runImport.mutateAsync({ id: importTarget, file });
      toast.success(t("Import complete"), {
        description: `${result.rules_created} ${t("created")}, ${result.rules_updated} ${t("updated")}, ${result.rules_unchanged} ${t("unchanged")}, ${result.records_rejected} ${t("rejected")}`,
      });
      setOpenId(importTarget);
    } catch (err) {
      toast.error(t("Import failed"), { description: ratingError(err) });
    }
    setImportTarget(undefined);
    if (fileInput.current) fileInput.current.value = "";
  };

  return (
    <AppShell>
      <PageHeader
        title={t("Source Systems & Connectors")}
        description={t(
          "Every upstream system that supplies tariff configuration. A vendor adapter maps its native export onto the canonical rule model, so everything downstream sees one format.",
        )}
        info={t(
          "Imports detect change: a nightly full dump that changed nothing reports zero updates rather than versioning every rule.",
        )}
        actions={
          canEdit && (
            <button
              onClick={() => setCreating(true)}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <Plus className="h-4 w-4" /> {t("Add connector")}
            </button>
          )
        }
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatTile
          icon={Plug}
          label={t("Connectors")}
          value={String(sources.length)}
        />
        <StatTile
          icon={Wifi}
          label={t("Healthy")}
          value={`${healthy} / ${sources.length}`}
        />
        <StatTile
          icon={Database}
          label={t("Vendor adapters")}
          value={String(withAdapters)}
        />
      </div>

      <input
        ref={fileInput}
        type="file"
        accept=".json,.csv,.xml"
        onChange={(e) => onImportFile(e.target.files?.[0] ?? null)}
        className="hidden"
      />

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {sources.length === 0 && !isLoading && (
        <RatingEmpty
          icon={Plug}
          title="No connectors configured"
          description="Register a source system to import tariff rules from a charging system, product catalogue or numbering plan."
          action={
            canEdit && (
              <button
                onClick={() => setCreating(true)}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
              >
                <Plus className="h-4 w-4" /> {t("Add connector")}
              </button>
            )
          }
        />
      )}

      {sources.length > 0 && (
        <div className="space-y-2 mb-6">
          {sources.map((s) => {
            const health = HEALTH[s.health_status] ?? HEALTH.UNKNOWN;
            const HealthIcon = health.icon;
            return (
              <div
                key={s.id}
                className="bg-card border border-border rounded-xl p-4"
              >
                <div className="flex flex-wrap items-start gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-foreground">
                        {s.name}
                      </span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {s.code}
                      </span>
                      <span
                        className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded border ${health.tone}`}
                      >
                        <HealthIcon className="h-3 w-3" />
                        {s.health_status}
                      </span>
                    </div>
                    <div className="text-[12px] text-muted-foreground mt-0.5">
                      {s.vendor} · {s.source_type} · {s.import_mode}
                      {s.schedule && (
                        <>
                          {" · "}
                          <Clock className="h-3 w-3 inline -mt-0.5" />{" "}
                          {s.schedule}
                        </>
                      )}
                    </div>
                    {s.health_detail && (
                      <div className="text-[11px] text-muted-foreground mt-1">
                        {s.health_detail}
                      </div>
                    )}
                  </div>

                  <div className="text-right text-[11px] text-muted-foreground shrink-0">
                    <div>
                      {s.total_imports} {t("imports")}
                      {s.failed_imports > 0 &&
                        `, ${s.failed_imports} ${t("failed")}`}
                    </div>
                    <div>
                      {s.total_records_imported} {t("rules imported")}
                    </div>
                    {s.last_import_at && (
                      <div>
                        {t("last")}{" "}
                        {new Date(s.last_import_at).toLocaleString()}
                      </div>
                    )}
                  </div>

                  {canEdit && (
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() =>
                          test.mutateAsync(s.id).then(
                            (r) =>
                              toast.success(
                                `${t("Connection")} ${r.health_status}`,
                                {
                                  description: r.health_detail,
                                },
                              ),
                            (e) =>
                              toast.error(t("Test failed"), {
                                description: ratingError(e),
                              }),
                          )
                        }
                        disabled={test.isPending}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition disabled:opacity-50"
                      >
                        <Wifi className="h-3.5 w-3.5" /> {t("Test")}
                      </button>
                      <button
                        onClick={() => {
                          setImportTarget(s.id);
                          fileInput.current?.click();
                        }}
                        disabled={runImport.isPending}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition disabled:opacity-50"
                      >
                        {runImport.isPending && importTarget === s.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Upload className="h-3.5 w-3.5" />
                        )}
                        {t("Import")}
                      </button>
                      <button
                        onClick={() =>
                          setOpenId(openId === s.id ? undefined : s.id)
                        }
                        className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition"
                      >
                        {t("History")}
                      </button>
                      <button
                        onClick={() =>
                          remove.mutateAsync(s.id).then(
                            () => toast.success(t("Connector removed")),
                            (e) =>
                              toast.error(t("Could not remove"), {
                                description: ratingError(e),
                              }),
                          )
                        }
                        aria-label={t("Remove")}
                        className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                </div>

                {openId === s.id && (
                  <div className="mt-4 pt-4 border-t border-border">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                      {t("Import history")}
                    </div>
                    {imports.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {t("No imports yet.")}
                      </p>
                    ) : (
                      <table className="w-full text-[12px]">
                        <tbody className="divide-y divide-border">
                          {imports.map((imp) => (
                            <tr key={imp.id}>
                              <td className="py-2 text-muted-foreground whitespace-nowrap">
                                {new Date(imp.created_at).toLocaleString()}
                              </td>
                              <td className="py-2 text-muted-foreground">
                                {imp.trigger}
                              </td>
                              <td className="py-2 text-foreground">
                                {imp.rules_created} {t("created")} ·{" "}
                                {imp.rules_updated} {t("updated")} ·{" "}
                                {imp.rules_unchanged} {t("unchanged")}
                                {imp.records_rejected > 0 && (
                                  <span className="text-destructive">
                                    {" "}
                                    · {imp.records_rejected} {t("rejected")}
                                  </span>
                                )}
                              </td>
                              <td className="py-2 text-right text-muted-foreground">
                                {imp.status}
                                {imp.duration_ms
                                  ? ` · ${imp.duration_ms}ms`
                                  : ""}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* --- Supported vendors --------------------------------------------- */}
      <section className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground">
            {t("Vendor adapters")}
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            {t(
              "A vendor with an adapter can be imported natively. The rest can still be onboarded by exporting to CSV or the canonical format.",
            )}
          </p>
        </div>
        <ul className="divide-y divide-border">
          {vendors.map((v) => (
            <li key={v.code} className="px-5 py-3 flex items-start gap-3">
              <span
                className={`text-[10px] font-medium px-1.5 py-0.5 rounded border shrink-0 mt-0.5 ${
                  v.adapter_available
                    ? "bg-success/10 text-success border-success/20"
                    : "bg-muted text-muted-foreground border-border"
                }`}
              >
                {v.adapter_available ? t("adapter") : t("planned")}
              </span>
              <div className="min-w-0">
                <div className="text-sm text-foreground">{v.label}</div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  {v.description}
                </p>
                {v.expects.length > 0 && (
                  <div className="text-[11px] text-muted-foreground/70 font-mono mt-1 truncate">
                    {v.record_path} · {v.expects.slice(0, 8).join(", ")}
                    {v.expects.length > 8 ? "…" : ""}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* --- Create dialog --------------------------------------------------- */}
      {creating && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-2xl my-8 rounded-xl border border-border bg-card shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-sm font-semibold text-foreground">
                {t("New connector")}
              </h2>
              <button
                onClick={() => setCreating(false)}
                aria-label={t("Close")}
                className="h-8 w-8 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted transition"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="px-5 py-5 grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Code" required>
                <input
                  value={form.code}
                  onChange={(e) =>
                    patch({ code: e.target.value.toUpperCase() })
                  }
                  placeholder="ERIC_CS_PROD"
                  className={inputCls}
                />
              </Field>
              <Field label="Name" required>
                <input
                  value={form.name}
                  onChange={(e) => patch({ name: e.target.value })}
                  placeholder={t("Ericsson CS — production")}
                  className={inputCls}
                />
              </Field>
              <Field label="Vendor" hint={selectedVendor?.description}>
                <Select
                  value={form.vendor}
                  onChange={(v) => patch({ vendor: v })}
                  options={vendors.map((v) => ({
                    value: v.code,
                    label: v.adapter_available
                      ? v.label
                      : `${v.label} (${t("no adapter")})`,
                  }))}
                  minWidth={0}
                  className="w-full"
                />
              </Field>
              <Field label="Supplies">
                <Select
                  value={form.category}
                  onChange={(v) => patch({ category: v })}
                  options={(catalog?.categories ?? []).map((c) => ({
                    value: c,
                    label: c,
                  }))}
                  minWidth={0}
                  className="w-full"
                />
              </Field>
              <Field label="Source type">
                <Select
                  value={form.source_type}
                  onChange={(v) => patch({ source_type: v })}
                  options={(catalog?.source_types ?? []).map((c) => ({
                    value: c,
                    label: c,
                  }))}
                  minWidth={0}
                  className="w-full"
                />
              </Field>
              <Field
                label="Import mode"
                hint="FULL re-reads everything and detects change; INCREMENTAL trusts the source to send only deltas."
              >
                <Select
                  value={form.import_mode}
                  onChange={(v) => patch({ import_mode: v })}
                  options={(catalog?.import_modes ?? []).map((c) => ({
                    value: c,
                    label: c,
                  }))}
                  minWidth={0}
                  className="w-full"
                />
              </Field>
              <Field
                label="Schedule"
                hint="Cron expression. Leave blank for manual only."
              >
                <input
                  value={form.schedule}
                  onChange={(e) => patch({ schedule: e.target.value })}
                  placeholder="0 2 * * *"
                  className={inputCls}
                />
              </Field>

              <div className="sm:col-span-2 pt-2 border-t border-border">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-3">
                  {t("Connection")}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {form.source_type === "API" ? (
                    <Field label="Base URL">
                      <input
                        value={form.base_url}
                        onChange={(e) => patch({ base_url: e.target.value })}
                        placeholder="https://brm.telco.internal/api"
                        className={inputCls}
                      />
                    </Field>
                  ) : (
                    <>
                      <Field label="Host">
                        <input
                          value={form.host}
                          onChange={(e) => patch({ host: e.target.value })}
                          className={inputCls}
                        />
                      </Field>
                      <Field label="Port">
                        <input
                          value={form.port}
                          onChange={(e) => patch({ port: e.target.value })}
                          className={inputCls}
                        />
                      </Field>
                      {form.source_type === "DATABASE" ? (
                        <Field label="Database">
                          <input
                            value={form.database}
                            onChange={(e) =>
                              patch({ database: e.target.value })
                            }
                            className={inputCls}
                          />
                        </Field>
                      ) : (
                        <Field label="Remote path">
                          <input
                            value={form.path}
                            onChange={(e) => patch({ path: e.target.value })}
                            placeholder="/exports/tariffs"
                            className={inputCls}
                          />
                        </Field>
                      )}
                    </>
                  )}
                </div>
              </div>

              <div className="sm:col-span-2 pt-2 border-t border-border">
                <div className="flex items-center gap-1 mb-3">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {t("Credentials")}
                  </span>
                  <InfoHint
                    text={t(
                      "Stored separately from the connection settings and never returned by the API — reads show only whether a secret is set.",
                    )}
                  />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <Field label="Username">
                    <input
                      value={form.username}
                      onChange={(e) => patch({ username: e.target.value })}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="Password">
                    <input
                      type="password"
                      value={form.password}
                      onChange={(e) => patch({ password: e.target.value })}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="API token">
                    <input
                      type="password"
                      value={form.token}
                      onChange={(e) => patch({ token: e.target.value })}
                      className={inputCls}
                    />
                  </Field>
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border">
              <button
                onClick={() => setCreating(false)}
                className="rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
              >
                {t("Cancel")}
              </button>
              <button
                onClick={submit}
                disabled={
                  !form.code.trim() || !form.name.trim() || create.isPending
                }
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
              >
                {create.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="h-4 w-4" />
                )}
                {t("Register")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

const inputCls =
  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary";

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <div>
      <div className="flex items-center gap-1 mb-1.5">
        <label className="text-xs font-medium text-muted-foreground">
          {t(label)}
          {required && <span className="text-destructive ml-0.5">*</span>}
        </label>
        {hint && <InfoHint text={t(hint)} />}
      </div>
      {children}
    </div>
  );
}

export type { SourceSystem };
