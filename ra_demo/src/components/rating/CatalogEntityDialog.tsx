import { useEffect, useMemo, useState } from "react";
import { Loader2, Save, X } from "lucide-react";
import { toast } from "sonner";
import { Select } from "@/components/ui-kit/Select";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import { useReferenceOptions, useSaveCatalogEntity } from "@/lib/rating/hooks";
import type {
  CatalogEntity,
  EntitySchema,
  FieldSchema,
} from "@/lib/rating/types";

// ---------------------------------------------------------------------------
// Create/edit form for any canonical metadata entity.
//
// The form is GENERATED from `GET /catalog/schema`, which the backend derives
// from the entity's own Pydantic model — the same principle as the rule
// builder. Adding a column to a catalogue makes the field appear here with no
// change to this file, and the form can never offer a field the API rejects.
// ---------------------------------------------------------------------------

type FormValue = string | number | boolean | string[] | null;

function initialValue(field: FieldSchema, existing?: CatalogEntity): FormValue {
  if (existing) {
    const current = existing[field.key];
    if (current === null || current === undefined)
      return field.multiple ? [] : "";
    if (field.multiple) return (current as unknown[]).map(String);
    if (field.data_type === "BOOLEAN") return Boolean(current);
    return String(current);
  }
  if (field.multiple)
    return Array.isArray(field.default) ? field.default.map(String) : [];
  if (field.data_type === "BOOLEAN") return Boolean(field.default);
  return field.default === null || field.default === undefined
    ? ""
    : String(field.default);
}

function FieldControl({
  field,
  value,
  onChange,
}: {
  field: FieldSchema;
  value: FormValue;
  onChange: (v: FormValue) => void;
}) {
  const t = useT();
  const { options: refOptions, isLoading } = useReferenceOptions(
    field.data_type === "REFERENCE" ? field.reference : null,
  );
  const inputCls =
    "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary";

  if (field.multiple) {
    const options =
      field.data_type === "REFERENCE"
        ? refOptions
        : field.values.map((v) => ({ value: v, label: v }));
    return (
      <MultiSelect
        options={options}
        selected={new Set((value as string[]) ?? [])}
        onChange={(next) => onChange([...next])}
        placeholder={isLoading ? "Loading…" : "Select…"}
        minWidth={0}
        allowEmpty
      />
    );
  }

  if (field.data_type === "ENUM" || field.data_type === "REFERENCE") {
    const options =
      field.data_type === "REFERENCE"
        ? refOptions
        : field.values.map((v) => ({ value: v, label: v }));
    return (
      <Select
        value={String(value ?? "")}
        onChange={onChange}
        options={
          field.required
            ? options
            : [{ value: "", label: t("Not set") }, ...options]
        }
        placeholder={isLoading ? "Loading…" : "Select…"}
        minWidth={0}
        className="w-full"
      />
    );
  }

  if (field.data_type === "BOOLEAN") {
    return (
      <Select
        value={value ? "true" : "false"}
        onChange={(v) => onChange(v === "true")}
        options={[
          { value: "true", label: t("Yes") },
          { value: "false", label: t("No") },
        ]}
        minWidth={0}
        className="w-full"
      />
    );
  }

  const inputType =
    field.data_type === "NUMBER"
      ? "number"
      : field.data_type === "DATE"
        ? "date"
        : field.data_type === "TIME"
          ? "time"
          : "text";

  return (
    <input
      type={inputType}
      step={field.data_type === "NUMBER" ? "any" : undefined}
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      className={inputCls}
    />
  );
}

export function CatalogEntityDialog({
  schema,
  entity,
  onClose,
}: {
  schema: EntitySchema;
  /** Omit to create; pass a row to edit it. */
  entity?: CatalogEntity;
  onClose: () => void;
}) {
  const t = useT();
  const save = useSaveCatalogEntity(schema.slug);
  const editing = !!entity;

  const [values, setValues] = useState<Record<string, FormValue>>({});

  useEffect(() => {
    const seeded: Record<string, FormValue> = {};
    schema.fields.forEach((f) => {
      seeded[f.key] = initialValue(f, entity);
    });
    setValues(seeded);
  }, [schema, entity]);

  const missing = useMemo(
    () =>
      schema.fields
        .filter((f) => f.required)
        .filter((f) => {
          const v = values[f.key];
          return (
            v === "" ||
            v === null ||
            v === undefined ||
            (Array.isArray(v) && !v.length)
          );
        })
        .map((f) => f.label),
    [schema.fields, values],
  );

  const submit = async () => {
    if (missing.length) return;
    // Drop blanks so an untouched optional field is absent from the body rather
    // than sent as "", which the API would reject on a typed column.
    const body: Record<string, unknown> = {};
    schema.fields.forEach((f) => {
      const v = values[f.key];
      if (v === "" || v === null || v === undefined) return;
      if (Array.isArray(v) && v.length === 0) return;
      body[f.key] = f.data_type === "NUMBER" ? Number(v) : v;
    });

    try {
      await save.mutateAsync({ id: entity?.id, body });
      toast.success(editing ? t("Saved") : t("Created"), {
        description: `${schema.label} ${String(body.code ?? entity?.code ?? "")}`,
      });
      onClose();
    } catch (err) {
      toast.error(t("Couldn't save"), { description: ratingError(err) });
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-label={`${editing ? t("Edit") : t("New")} ${schema.label}`}
    >
      <div className="w-full max-w-3xl my-8 rounded-xl border border-border bg-card shadow-2xl">
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-border">
          <div>
            <h2 className="text-sm font-semibold text-foreground">
              {editing
                ? `${t("Edit")} ${schema.label.toLowerCase()}`
                : `${t("New")} ${schema.label.toLowerCase()}`}
            </h2>
            {editing && (
              <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                {entity?.code}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label={t("Close")}
            className="h-8 w-8 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted transition"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-5 grid grid-cols-1 sm:grid-cols-2 gap-4">
          {schema.fields.map((field) => (
            <div
              key={field.key}
              className={
                field.key === "description" ? "sm:col-span-2" : undefined
              }
            >
              <div className="flex items-center gap-1 mb-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  {t(field.label)}
                  {field.required && (
                    <span className="text-destructive ml-0.5">*</span>
                  )}
                </label>
                {field.help && <InfoHint text={field.help} />}
              </div>
              {/* The code identifies the entity for every rule that references
                  it, so it is fixed once created. */}
              {editing && field.key === "code" ? (
                <div className="h-9 flex items-center px-3 rounded-lg border border-border bg-muted/40 text-sm font-mono text-muted-foreground">
                  {entity?.code}
                </div>
              ) : (
                <FieldControl
                  field={field}
                  value={values[field.key] ?? ""}
                  onChange={(v) =>
                    setValues((prev) => ({ ...prev, [field.key]: v }))
                  }
                />
              )}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-border">
          <span className="text-[11px] text-muted-foreground">
            {missing.length > 0 && `${t("Required")}: ${missing.join(", ")}`}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
            >
              {t("Cancel")}
            </button>
            <button
              onClick={submit}
              disabled={missing.length > 0 || save.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {save.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {editing ? t("Save changes") : t("Create")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
