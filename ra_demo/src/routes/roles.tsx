import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  DEFAULT_ROLE_PERMISSIONS,
  loadRolePermissions,
  PERMISSION_KEYS,
  VIEW_ONLY_ALLOWED,
  type PermissionMap,
  type Role,
  useAuth,
} from "@/lib/auth";
import { roleService } from "@/services";
import { Pencil, X, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/roles")({ component: RolesPage });

interface RoleRow {
  id: Role;
  name: string;
  description: string;
  status: "Active" | "Inactive";
  createdAt: string;
  updatedAt: string;
}

function RolesPage() {
  const { canEdit } = useAuth();
  const t = useT();
  const editable = canEdit("/roles");
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [perms, setPerms] = useState<Record<Role, PermissionMap>>(DEFAULT_ROLE_PERMISSIONS);
  const [selected, setSelected] = useState<Role>("ra_lead");
  const [editing, setEditing] = useState<RoleRow | null>(null);

  useEffect(() => {
    roleService.list().then((r) => {
      const list = r as RoleRow[];
      setRoles(list);
      // Seed the permission matrices from the backend roles (fall back to local).
      const merged: any = { ...loadRolePermissions() };
      list.forEach((role) => { if ((role as any).permissions) merged[role.id] = (role as any).permissions; });
      setPerms(merged);
    });
  }, []);

  const selectedRole = useMemo(() => roles.find((r) => r.id === selected), [roles, selected]);

  const upsertRole = async (r: RoleRow) => {
    try {
      await roleService.upsert({ id: r.id, name: r.name, description: r.description, status: r.status });
      setRoles((await roleService.list()) as RoleRow[]);
      toast.success(t("Role saved"));
    } catch (e: any) {
      toast.error(e?.response?.data?.error?.message ?? t("Failed to save role"));
    }
    setEditing(null);
  };

  return (
    <AppShell>
      <PageHeader
        title={t("Role Management")}
        description={t("Create roles, configure page-level access, and control which modules each role can view or edit.")}
        info={t("Define roles and control each module's access permissions.")}
      />

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Roles list */}
        <div className="lg:col-span-1 bg-card border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-border text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            {t("Roles")}
          </div>
          <ul>
            {roles.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => setSelected(r.id)}
                  className={`w-full text-left px-4 py-3 border-b border-border flex items-start gap-3 hover:bg-muted/40 ${
                    selected === r.id ? "bg-primary/5" : ""
                  }`}
                >
                  <div className={`mt-0.5 h-8 w-8 rounded-lg flex items-center justify-center ${selected === r.id ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
                    <ShieldCheck className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-medium text-sm text-foreground truncate">{r.name}</div>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${r.status === "Active" ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}>
                        {t(r.status)}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground line-clamp-1">{r.description}</div>
                    <div className="text-[10px] text-muted-foreground/70 mt-1">
                      {t("Updated")} {new Date(r.updatedAt).toLocaleDateString()}
                    </div>
                  </div>
                  {editable && (
                    <Tooltip label={t("Edit this role")} side="bottom">
                      <span
                        onClick={(e) => { e.stopPropagation(); setEditing(r); }}
                        className="text-muted-foreground hover:text-primary cursor-pointer"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </span>
                    </Tooltip>
                  )}
                </button>
              </li>
            ))}
            {roles.length === 0 && (
              <li className="px-4 py-12 text-center text-muted-foreground text-sm">{t("No data found")}</li>
            )}
          </ul>
        </div>

        {/* Permission matrix */}
        <div className="lg:col-span-2 bg-card border border-border rounded-xl shadow-sm">
          {roles.length === 0 ? (
          <div className="px-5 py-16 text-center text-sm text-muted-foreground">{t("No data found")}</div>
          ) : (
          <>
          <div className="px-5 py-4 border-b border-border flex items-center justify-between">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">{t("Permission matrix")}</div>
              <div className="font-semibold text-foreground">{selectedRole?.name ?? selected}</div>
            </div>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-5 py-3">{t("Module / Page")}</th>
                <th className="font-medium px-4 py-3 w-24 text-center">{t("View")}</th>
                <th className="font-medium px-4 py-3 w-24 text-center">{t("Edit")}</th>
              </tr>
            </thead>
            <tbody>
              {PERMISSION_KEYS.map(({ key, label }) => {
                const p = perms[selected][key];
                const viewOnlyOnly = VIEW_ONLY_ALLOWED.includes(key);
                return (
                  <tr key={key} className="border-t border-border">
                    <td className="px-5 py-3 text-foreground">
                      {t(label)}
                      {viewOnlyOnly && (
                        <span className="ml-2 text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">{t("view-only supported")}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <input
                        type="checkbox"
                        readOnly
                        checked={p.view}
                        onChange={() => {}}
                        className="h-4 w-4 accent-primary pointer-events-none cursor-default"
                      />
                    </td>
                    <td className="px-4 py-3 text-center">
                      <input
                        type="checkbox"
                        readOnly
                        checked={p.edit}
                        onChange={() => {}}
                        className="h-4 w-4 accent-primary pointer-events-none cursor-default"
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </>
          )}
        </div>
      </div>

      {editing && (
        <RoleModal
          role={editing}
          onClose={() => setEditing(null)}
          onSave={upsertRole}
        />
      )}
    </AppShell>
  );
}

function RoleModal({ role, onClose, onSave }: { role: RoleRow | null; onClose: () => void; onSave: (r: RoleRow) => void }) {
  const t = useT();
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [status, setStatus] = useState<"Active" | "Inactive">(role?.status ?? "Active");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const clearErr = (key: string) => setErrors((prev) => (prev[key] ? (() => { const { [key]: _drop, ...rest } = prev; return rest; })() : prev));

  const validate = (): Record<string, string> => {
    const e: Record<string, string> = {};
    const nm = name.trim();
    if (!nm) e.name = t("Role name is required.");
    else if (nm.length < 2) e.name = t("Role name must be at least 2 characters.");
    else if (nm.length > 50) e.name = t("Role name must be at most 50 characters.");
    else if (!/^[\p{L}][\p{L}\d\s&/_-]*$/u.test(nm)) e.name = t("Role name can only contain letters, numbers, spaces and & / _ -");

    if (description.trim().length > 200) e.description = t("Description must be at most 200 characters.");
    return e;
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    const now = new Date().toISOString();
    const id = (role?.id ?? (name.trim().toLowerCase().replace(/\s+/g, "_") as Role));
    onSave({
      id: id as Role,
      name: name.trim(),
      description: description.trim(),
      status,
      createdAt: role?.createdAt ?? now,
      updatedAt: now,
    });
  };

  const errCls = (key: string) => `form-input${errors[key] ? " border-destructive focus:border-destructive" : ""}`;

  return (
    <Modal
      title={role ? t("Edit role") : t("New role")}
      subtitle={role ? t("Update role details and status") : t("Create a new role")}
      icon={<ShieldCheck className="h-5 w-5" />}
      onClose={onClose}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label={t("Role name")} required error={errors.name} hint={t("A short, recognizable name for the role (e.g. RA Analyst). 2–50 characters.")}>
          <input value={name} maxLength={50} onChange={(e) => { setName(e.target.value); clearErr("name"); }} className={errCls("name")} />
        </Field>
        <Field label={t("Description")} error={errors.description} hint={t("Optional summary of what this role is for and who should have it (up to 200 characters).")}>
          <textarea value={description} maxLength={200} onChange={(e) => { setDescription(e.target.value); clearErr("description"); }} rows={3} className={errCls("description")} />
        </Field>
        <Field label={t("Status")} required hint={t("Active roles can be assigned to users. Inactive roles are kept but cannot be assigned.")}>
          <Select
            value={status}
            onChange={(v) => setStatus(v as "Active" | "Inactive")}
            minWidth={0}
            className="w-full"
            ariaLabel={t("Status")}
            options={[{ value: "Active", label: t("Active") }, { value: "Inactive", label: t("Inactive") }]}
          />
        </Field>
        <div className="-mx-5 -mb-5 mt-2 px-5 pt-4 pb-5 border-t border-border grid grid-cols-2 gap-3">
          <button type="submit" className="h-11 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 inline-flex items-center justify-center gap-2">
            <ShieldCheck className="h-4 w-4" /> {role ? t("Update role") : t("Save role")}
          </button>
          <button type="button" onClick={onClose} className="h-11 rounded-xl border border-border text-sm font-semibold hover:bg-muted">{t("Cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}

export function Modal({ title, subtitle, icon, children, onClose }: { title: string; subtitle?: string; icon?: React.ReactNode; children: React.ReactNode; onClose: () => void }) {
  const t = useT();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 backdrop-blur-sm p-4 animate-fade-in">
      <div className="bg-card border border-border rounded-2xl shadow-xl w-full max-w-lg animate-scale-in">
        <div className="flex items-start justify-between px-5 py-4 border-b border-border">
          <div className="flex items-center gap-3 min-w-0">
            {icon && (
              <div className="h-11 w-11 shrink-0 rounded-xl bg-primary/15 text-primary flex items-center justify-center">
                {icon}
              </div>
            )}
            <div className="min-w-0">
              <h3 className="font-semibold text-foreground truncate">{title}</h3>
              {subtitle && <p className="text-sm text-muted-foreground truncate">{subtitle}</p>}
            </div>
          </div>
          <Tooltip label={t("Close this dialog")} side="bottom">
            <button onClick={onClose} aria-label={t("Close")} className="h-8 w-8 shrink-0 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </Tooltip>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, required, error, hint, children }: { label: string; required?: boolean; error?: string; hint?: string; children: React.ReactNode }) {
    return (
    <label className="block">
      <span className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
        <span>{label}{required && <span className="text-destructive ml-0.5">*</span>}</span>
        {hint && <InfoHint text={hint} side="top" />}
      </span>
      {children}
      {error && <span className="mt-1 block text-[11px] font-medium text-destructive">{error}</span>}
    </label>
  );
}
