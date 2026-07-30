import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { userService, roleService } from "@/services";
import { Plus, Pencil, Search, Power, PowerOff, Trash2, ChevronDown, Check, X, UserPlus, Eye, EyeOff, AlertTriangle } from "lucide-react";
import { Modal, Field } from "./roles";
import { useAuth, ROLE_LABELS, type Role } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { validateEmail } from "@/lib/email";
import { toast } from "sonner";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { Select } from "@/components/ui-kit/Select";
import { TablePagination } from "@/components/reports/ReportFilters";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";
import { ConfirmDialog } from "@/components/ui-kit/ConfirmDialog";

export const Route = createFileRoute("/users")({ component: UsersPage });

interface UserRow {
  id: string;
  fullName: string;
  email: string;
  phone: string;
  department: string;
  role: Role;
  status: "Active" | "Disabled";
  lastLogin: string;
  createdAt: string;
}

function UsersPage() {
  const { canEdit } = useAuth();
  const t = useT();
  const editable = canEdit("/users");
  const [users, setUsers] = useState<UserRow[]>([]);
  const [roles, setRoles] = useState<{ id: Role; name: string }[]>([]);
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [confirmToggle, setConfirmToggle] = useState<UserRow | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<UserRow | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  useEffect(() => {
    userService.listFull().then((u) => setUsers(u as UserRow[]));
    roleService.list().then((r: any) => setRoles(r));
  }, []);

  const filtered = useMemo(() => {
    return users.filter((u) => {
      if (roleFilter !== "all" && u.role !== roleFilter) return false;
      if (statusFilter !== "all" && u.status !== statusFilter) return false;
      const q = query.trim().toLowerCase();
      if (q) {
        // Match against every visible column (contains), like the reports search.
        const haystack = [
          u.fullName, u.email, u.phone, u.department,
          ROLE_LABELS[u.role] ?? u.role, u.status,
          u.lastLogin ? new Date(u.lastLogin).toLocaleString() : "",
        ].map((v) => String(v ?? "").toLowerCase());
        if (!haystack.some((v) => v.includes(q))) return false;
      }
      return true;
    });
  }, [users, query, roleFilter, statusFilter]);

  // Column sorting.
  const { sortKey, sortDir, onSort } = useSort();
  const sorted = useMemo(
    () => sortRows(filtered, sortKey, sortDir, (u, key) => {
      switch (key) {
        case "fullName": return u.fullName;
        case "email": return u.email;
        case "phone": return u.phone;
        case "department": return u.department;
        case "role": return ROLE_LABELS[u.role] ?? u.role;
        case "status": return u.status;
        case "lastLogin": return u.lastLogin ? new Date(u.lastLogin).getTime() : 0;
        default: return "";
      }
    }),
    [filtered, sortKey, sortDir],
  );

  // Back to page 1 whenever the result set changes (search / filters).
  useEffect(() => { setPage(1); }, [query, roleFilter, statusFilter, pageSize]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageItems = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);

  const reload = () => userService.listFull().then((u) => setUsers(u as UserRow[]));

  const upsert = async (u: UserRow & { password?: string }) => {
    const exists = users.some((x) => x.id === u.id);
    try {
      if (exists) {
        await userService.update(u.id, {
          fullName: u.fullName, email: u.email, phone: u.phone,
          department: u.department, role: u.role, status: u.status,
        });
      } else {
        await userService.create({
          fullName: u.fullName, email: u.email,
          password: u.password || "ChangeMe!123", role: u.role,
          phone: u.phone, department: u.department, status: u.status,
          mustResetPassword: true,
        });
      }
      await reload();
      toast.success(exists ? t("User updated") : t("User created"));
    } catch (e: any) {
      toast.error(e?.response?.data?.error?.message ?? t("Failed to save user"));
    }
    setEditing(null);
    setCreating(false);
  };

  const toggleStatus = async (u: UserRow) => {
    const next = (u.status === "Active" ? "Disabled" : "Active") as "Active" | "Disabled";
    try {
      await userService.update(u.id, { status: next });
      await reload();
      toast.success(next === "Active" ? t("Account activated") : t("Account deactivated"), {
        description: `${u.fullName} ${t("is now")} ${next === "Active" ? t("active") : t("disabled")}.`,
      });
    } catch {
      toast.error(t("Failed to update status"));
    }
    setConfirmToggle(null);
  };

  const removeUser = async (u: UserRow) => {
    try {
      await userService.remove(u.id);
      await reload();
      toast.success(t("User deleted"), { description: `${u.fullName} ${t("has been removed.")}` });
    } catch (e: any) {
      toast.error(e?.response?.data?.error?.message ?? t("Failed to delete user"));
    }
    setConfirmDelete(null);
  };

  return (
    <AppShell>
      <PageHeader
        title={t("User Management")}
        description={t("Manage operators, analysts and auditors who have access to RADONaix. Assign roles to control permissions.")}
        info={t("Create and manage users and assign their roles.")}
        actions={
          editable && (
            <Tooltip label={t("Add a new user")} side="bottom">
              <button
                onClick={() => setCreating(true)}
                className="inline-flex items-center gap-2 h-9 px-3 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90"
              >
                <Plus className="h-4 w-4" /> {t("Add user")}
              </button>
            </Tooltip>
          )
        }
      />

      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        <div className="p-4 border-b border-border flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("Search all columns…")}
              maxLength={100}
              autoComplete="off"
              className={`w-full h-9 pl-9 ${query ? "pr-9" : "pr-3"} rounded-lg border border-border bg-background text-sm`}
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("Clear search")}
                title={t("Clear search")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 h-5 w-5 inline-flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <Select
            value={roleFilter}
            onChange={setRoleFilter}
            minWidth={150}
            ariaLabel={t("All roles")}
            options={[{ value: "all", label: t("All roles") }, ...roles.map((r) => ({ value: r.id, label: r.name }))]}
          />
          <Select
            value={statusFilter}
            onChange={setStatusFilter}
            minWidth={140}
            ariaLabel={t("All status")}
            options={[
              { value: "all", label: t("All status") },
              { value: "Active", label: t("Active") },
              { value: "Disabled", label: t("Deactivated") },
            ]}
          />
          <button
            type="button"
            onClick={() => { setQuery(""); setRoleFilter("all"); setStatusFilter("all"); }}
            disabled={!query && roleFilter === "all" && statusFilter === "all"}
            className="ml-auto shrink-0 inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-border bg-card text-xs font-medium hover:bg-muted transition disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
          >
            <X className="h-3.5 w-3.5" /> {t("Clear")}
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[900px]">
            <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                {[
                  { key: "fullName", label: "Full Name" },
                  { key: "email", label: "Email" },
                  { key: "phone", label: "Phone" },
                  { key: "department", label: "Department" },
                  { key: "role", label: "Role" },
                  { key: "status", label: "Status" },
                  { key: "lastLogin", label: "Last Login" },
                ].map((c) => (
                  <SortHeader
                    key={c.key}
                    label={t(c.label)}
                    colKey={c.key}
                    activeKey={sortKey}
                    dir={sortDir}
                    onSort={onSort}
                    thClassName="text-left font-medium px-4 py-3"
                  />
                ))}
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {pageItems.map((u) => (
                <tr key={u.id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-4 py-3 font-medium text-foreground">{u.fullName}</td>
                  <td className="px-4 py-3 text-foreground/80">{u.email}</td>
                  <td className="px-4 py-3 text-foreground/80">{u.phone}</td>
                  <td className="px-4 py-3 text-foreground/80">{u.department}</td>
                  <td className="px-4 py-3 text-foreground/80">{ROLE_LABELS[u.role] ?? u.role}</td>
                  <td className="px-4 py-3">
                    <StatusCell
                      status={u.status}
                      editable={editable}
                      onSelect={(next) => next !== u.status && setConfirmToggle(u)}
                    />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{new Date(u.lastLogin).toLocaleString()}</td>
                  <td className="px-4 py-3 text-right">
                    {editable && (
                      <div className="flex items-center justify-end gap-1">
                        <Tooltip label={t("Edit this user")} side="bottom">
                          <button onClick={() => setEditing(u)} aria-label={t("Edit user")} className="h-8 w-8 rounded-md hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                        </Tooltip>
                        <Tooltip label={t("Delete this user")} side="bottom">
                          <button onClick={() => setConfirmDelete(u)} aria-label={t("Delete user")} className="h-8 w-8 rounded-md hover:bg-destructive/10 flex items-center justify-center text-muted-foreground hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>
                        </Tooltip>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-12 text-center text-muted-foreground text-sm">{users.length === 0 ? t("No data found") : t("No users match the current filters.")}</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <TablePagination
          page={safePage}
          pageSize={pageSize}
          total={filtered.length}
          onPage={setPage}
          onPageSize={setPageSize}
        />
      </div>

      {(editing || creating) && (
        <UserModal
          user={editing}
          roles={roles}
          onClose={() => { setEditing(null); setCreating(false); }}
          onSave={upsert}
        />
      )}

      <ConfirmDialog
        open={!!confirmToggle}
        title={confirmToggle?.status === "Active" ? t("Confirm Deactivation") : t("Confirm Activation")}
        subject={confirmToggle ? { name: confirmToggle.fullName, email: confirmToggle.email, role: ROLE_LABELS[confirmToggle.role] ?? confirmToggle.role } : undefined}
        message={
          confirmToggle?.status === "Active"
            ? t("This action will deactivate this user account.")
            : t("This action will activate this user account.")
        }
        confirmLabel={confirmToggle?.status === "Active" ? "Confirm Deactivate" : "Confirm Activate"}
        tone={confirmToggle?.status === "Active" ? "danger" : "success"}
        icon={confirmToggle?.status === "Active" ? <PowerOff className="h-5 w-5" /> : <Power className="h-5 w-5" />}
        heroIcon={confirmToggle?.status === "Active" ? <PowerOff className="h-9 w-9" /> : <Power className="h-9 w-9" />}
        confirmIcon={confirmToggle?.status === "Active" ? <PowerOff className="h-4 w-4" /> : <Power className="h-4 w-4" />}
        onCancel={() => setConfirmToggle(null)}
        onConfirm={() => confirmToggle && toggleStatus(confirmToggle)}
      />

      <ConfirmDialog
        open={!!confirmDelete}
        title={t("Confirm Deletion")}
        subject={confirmDelete ? { name: confirmDelete.fullName, email: confirmDelete.email, role: ROLE_LABELS[confirmDelete.role] ?? confirmDelete.role } : undefined}
        message={
          <>
            {t("This action will")}{" "}
            <span className="font-semibold text-foreground">{t("permanently delete")}</span>{" "}
            {t("this user account.")}
          </>
        }
        note={t("This cannot be undone.")}
      
        confirmLabel="Confirm Delete"
        tone="danger"
        icon={<Trash2 className="h-5 w-5" />}
        heroIcon={<AlertTriangle className="h-9 w-9" />}
        confirmIcon={<Trash2 className="h-4 w-4" />}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && removeUser(confirmDelete)}
      />
    </AppShell>
  );
}

// Status shown as a colored pill that doubles as a dropdown (Active / Disabled).
// Selecting a different value bubbles up so the parent can confirm before saving.
// Falls back to a static pill when the user lacks edit rights.
function StatusCell({ status, editable, onSelect }: { status: "Active" | "Disabled"; editable: boolean; onSelect: (next: "Active" | "Disabled") => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const pill = (s: string) => (s === "Active" ? "bg-success/10 text-success" : "bg-[#EF4444]/10 text-[#EF4444]");
  // "Disabled" is the stored value; it is shown to users as "Deactivated".
  const label = (s: string) => (s === "Disabled" ? t("Deactivated") : t(s));

  if (!editable) {
    return <span className={`text-[11px] px-2 py-0.5 rounded-full ${pill(status)}`}>{label(status)}</span>;
  }

  const options: ("Active" | "Disabled")[] = ["Active", "Disabled"];
  return (
    <div className="relative inline-block" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={t("Change status")}
        className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full transition hover:opacity-80 ${pill(status)}`}
      >
        {label(status)}
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 min-w-[140px] rounded-lg border border-border bg-popover shadow-lg p-1">
          {options.map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => { setOpen(false); if (o !== status) onSelect(o); }}
              className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 text-sm rounded-md text-left hover:bg-muted ${o === status ? "text-primary font-medium" : "text-foreground"}`}
            >
              <span className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${o === "Active" ? "bg-success" : "bg-[#EF4444]"}`} />
                {label(o)}
              </span>
              {o === status && <Check className="h-3.5 w-3.5 shrink-0 text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function UserModal({ user, roles, onClose, onSave }: { user: UserRow | null; roles: { id: Role; name: string }[]; onClose: () => void; onSave: (u: UserRow & { password?: string }) => void }) {
  const t = useT();
  const [fullName, setFullName] = useState(user?.fullName ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [department, setDepartment] = useState(user?.department ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? (roles[0]?.id as Role) ?? "analyst");
  const [status, setStatus] = useState<"Active" | "Disabled">(user?.status ?? "Active");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Clear a single field's error as the user edits it.
  const clearErr = (key: string) => setErrors((prev) => (prev[key] ? (() => { const { [key]: _drop, ...rest } = prev; return rest; })() : prev));

  const validate = (): Record<string, string> => {
    const e: Record<string, string> = {};

    // Full name: 2–80 chars, letters (any language) plus spaces, . ' - — no digits.
    const name = fullName.trim();
    if (!name) e.fullName = t("Full name is required.");
    else if (name.length < 2) e.fullName = t("Full name must be at least 2 characters.");
    else if (name.length > 80) e.fullName = t("Full name must be at most 80 characters.");
    else if (!/^[\p{L}][\p{L}\s.'-]*$/u.test(name)) e.fullName = t("Full name can only contain letters, spaces, hyphens and apostrophes.");

    // Email: required, well-formed, with a real TLD and no unsupported special
    // characters (see lib/email — rejects e.g. name!#$@x.com).
    const mail = email.trim();
    if (!mail) e.email = t("Email is required.");
    else if (mail.length > 120) e.email = t("Email must be at most 120 characters.");
    else { const err = validateEmail(t, mail); if (err) e.email = err; }

    // Phone: optional, but must be a plausible number (7–15 digits) when provided.
    const ph = phone.trim();
    if (ph) {
      if (!/^[+]?[\d\s()-]{7,20}$/.test(ph)) e.phone = t("Enter a valid phone number.");
      else {
        const digits = ph.replace(/\D/g, "");
        if (digits.length < 7 || digits.length > 15) e.phone = t("Phone number must have 7 to 15 digits.");
      }
    }

    // Password only applies when creating a user (not shown on edit).
    if (!user) {
      if (!password) e.password = t("Temporary password is required.");
      else if (password.length < 8) e.password = t("Password must be at least 8 characters.");
      else if (password.length > 20) e.password = t("Password must be at most 20 characters.");
      else if (!/[a-z]/.test(password) || !/[A-Z]/.test(password) || !/\d/.test(password))
        e.password = t("Password must include upper and lower case letters and a number.");
    }
    return e;
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    const now = new Date().toISOString();
    onSave({
      id: user?.id ?? `u-${Date.now()}`,
      fullName: fullName.trim(),
      email: email.trim(),
      phone: phone.trim(),
      department: department.trim(),
      role,
      status,
      lastLogin: user?.lastLogin ?? now,
      createdAt: user?.createdAt ?? now,
      password: password || undefined,
    });
  };

  const errCls = (key: string) => `form-input${errors[key] ? " border-destructive focus:border-destructive" : ""}`;

  return (
    <Modal
      title={user ? t("Edit user") : t("Add user")}
      subtitle={user ? t("Update user details and permissions") : t("Create a new user account")}
      icon={<UserPlus className="h-5 w-5" />}
      onClose={onClose}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("Full name")} required error={errors.fullName} hint={t("The user's full name as it will appear across the app. Letters, spaces, hyphens and apostrophes only (2–80 characters).")}>
            <input value={fullName} maxLength={80} onChange={(e) => { setFullName(e.target.value); clearErr("fullName"); }} className={errCls("fullName")} />
          </Field>
          <Field label={t("Email")} required error={errors.email} hint={t("Used to sign in and to receive account notifications. Must be a unique, valid email address.")}>
            <input type="email" value={email} maxLength={120} onChange={(e) => { setEmail(e.target.value); clearErr("email"); }} className={errCls("email")} />
          </Field>
          <Field label={t("Phone")} error={errors.phone} hint={t("Optional contact number. Include the country code if relevant (7–15 digits).")}>
            <input value={phone} maxLength={20} onChange={(e) => { setPhone(e.target.value); clearErr("phone"); }} className={errCls("phone")} placeholder={t("+1 (000) 000-0000")} />
          </Field>
          <Field label={t("Department")} hint={t("Optional team or business unit the user belongs to, used for grouping and reporting.")}><input value={department} maxLength={60} onChange={(e) => setDepartment(e.target.value)} className="form-input" /></Field>
          <Field label={t("Assigned role")} required hint={t("Determines what the user can view and edit. Permissions come from the role's configuration in Role Management.")}>
            <Select
              value={role}
              onChange={(v) => setRole(v as Role)}
              className="w-full"
              ariaLabel={t("Assigned role")}
              options={roles.map((r) => ({ value: r.id, label: r.name }))}
            />
          </Field>
          <Field label={t("Status")} required hint={t("Active users can sign in and use the app. Deactivated users are blocked from access until re-activated.")}>
            <Select
              value={status}
              onChange={(v) => setStatus(v as "Active" | "Disabled")}
              className="w-full"
              ariaLabel={t("Status")}
              options={[
                { value: "Active", label: t("Active") },
                { value: "Disabled", label: t("Deactivated") },
              ]}
            />
          </Field>
          {!user && (
            <div className="col-span-2">
              <Field label={t("Temporary password")} required error={errors.password} hint={t("An initial password shared with the user. Must be 8–20 characters with upper and lower case letters and a number. They'll be asked to change it on first sign-in.")}>
                <div className="relative">
                  <input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    maxLength={20}
                    minLength={8}
                    onChange={(e) => { setPassword(e.target.value); clearErr("password"); }}
                    placeholder={t("min 8 chars")}
                    className={`${errCls("password")} pr-10`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    aria-label={showPassword ? t("Hide password") : t("Show password")}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 h-6 w-6 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </Field>
            </div>
          )}
        </div>
        <div className="-mx-5 -mb-5 mt-2 px-5 pt-4 pb-5 border-t border-border grid grid-cols-2 gap-3">
          <button type="submit" className="h-11 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 inline-flex items-center justify-center gap-2">
            <UserPlus className="h-4 w-4" /> {user ? t("Update user") : t("Save user")}
          </button>
          <button type="button" onClick={onClose} className="h-11 rounded-xl border border-border text-sm font-semibold hover:bg-muted">{t("Cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}
