import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { systemConfigService } from "@/services";
import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";

export const Route = createFileRoute("/system-config")({ component: SystemConfigPage });

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">{label}</div>
      {children}
    </div>
  );
}

function SystemConfigPage() {
  const { canEdit } = useAuth();
  const t = useT();
  const editable = canEdit("/system-config");
  const [cfg, setCfg] = useState<any>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { systemConfigService.get().then(setCfg); }, []);
  if (!cfg) return <AppShell><div /></AppShell>;

  const set = (k: string, v: any) => setCfg((c: any) => ({ ...c, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const updated = await systemConfigService.update({
        environment: cfg.environment,
        retentionDays: Number(cfg.retentionDays),
        slaMinutes: Number(cfg.slaMinutes),
        alertEmail: cfg.alertEmail,
        maintenanceMode: cfg.maintenanceMode,
      });
      setCfg(updated);
      toast.success(t("Configuration saved"));
    } catch (e: any) {
      toast.error(e?.response?.data?.error?.message ?? t("Failed to save configuration"));
    }
    setSaving(false);
  };

  return (
    <AppShell>
      <PageHeader
        title={t("System Configuration")}
        description={t("Global runtime parameters for the assurance platform.")}
        info={t("Global runtime settings for the assurance platform.")}
        actions={
          editable && (
            <Tooltip label={t("Save the configuration changes")} side="bottom">
              <button
                onClick={save}
                disabled={saving}
                className="h-9 px-4 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
              >
                {saving ? t("Saving…") : t("Save changes")}
              </button>
            </Tooltip>
          )
        }
      />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 max-w-5xl">
        <Field label={t("Environment")}>
          <select value={cfg.environment} disabled={!editable} onChange={(e) => set("environment", e.target.value)} className="form-input">
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </select>
        </Field>
        <Field label={t("Retention (days)")}>
          <input type="number" min={1} value={cfg.retentionDays} disabled={!editable} onChange={(e) => set("retentionDays", e.target.value)} className="form-input" />
        </Field>
        <Field label={t("SLA Window (minutes)")}>
          <input type="number" min={1} value={cfg.slaMinutes} disabled={!editable} onChange={(e) => set("slaMinutes", e.target.value)} className="form-input" />
        </Field>
        <Field label={t("Alert Email")}>
          <input type="email" value={cfg.alertEmail ?? ""} disabled={!editable} onChange={(e) => set("alertEmail", e.target.value)} className="form-input" />
        </Field>
        <Field label={t("Maintenance Mode")}>
          <select value={cfg.maintenanceMode ? "on" : "off"} disabled={!editable} onChange={(e) => set("maintenanceMode", e.target.value === "on")} className="form-input">
            <option value="off">{t("Off")}</option>
            <option value="on">{t("On")}</option>
          </select>
        </Field>
      </div>
    </AppShell>
  );
}
