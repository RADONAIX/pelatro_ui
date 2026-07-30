import { useMemo, useState } from "react";
import { Plus } from "lucide-react";
import type { AppMetadata, RuleCategory } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES } from "@/lib/assurance/platform-metadata";
import { CATEGORY_PARAMS, type CustomRule } from "@/lib/assurance/rule-authoring";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Draft = Omit<CustomRule, "id" | "appId" | "createdAt">;

const SEVERITIES: CustomRule["severity"][] = ["critical", "high", "medium"];
const FREQUENCIES: CustomRule["frequency"][] = ["Real-time", "Hourly", "Daily", "Cycle"];

export function RuleBuilder({
  app,
  onCreate,
}: {
  app: AppMetadata;
  onCreate: (rule: Draft) => void;
}) {
  const scoped = useMemo(
    () =>
      RULE_CATEGORIES.filter(
        (c) => app.ruleTypes.includes(c) || app.ruleLibrary.some((r) => r.category === c),
      ),
    [app],
  );

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<RuleCategory>(scoped[0] ?? RULE_CATEGORIES[0]);
  const [entity, setEntity] = useState(app.entities[0]);
  const [severity, setSeverity] = useState<CustomRule["severity"]>("high");
  const [frequency, setFrequency] = useState<CustomRule["frequency"]>("Daily");
  const [state, setState] = useState<CustomRule["state"]>("Draft");
  const [params, setParams] = useState<Record<string, string>>({});

  const fields = CATEGORY_PARAMS[category];
  const valid = name.trim().length > 1;

  function reset() {
    setName("");
    setDescription("");
    setCategory(scoped[0] ?? RULE_CATEGORIES[0]);
    setEntity(app.entities[0]);
    setSeverity("high");
    setFrequency("Daily");
    setState("Draft");
    setParams({});
  }

  function submit() {
    if (!valid) return;
    onCreate({ name: name.trim(), description: description.trim(), category, entity, severity, frequency, state, params });
    reset();
    setOpen(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="gap-1.5">
          <Plus className="size-4" />
          New rule
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create control rule</DialogTitle>
          <DialogDescription>
            Rules are metadata. Pick a primitive category and the universal rule engine compiles
            the parameters into an executable control for {app.name}.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="rule-name">Rule name</Label>
              <Input
                id="rule-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Roaming CDR completeness"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Primitive category</Label>
              <Select
                value={category}
                onValueChange={(v) => {
                  setCategory(v as RuleCategory);
                  setParams({});
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RULE_CATEGORIES.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                      {!scoped.includes(c) && " · out of scope"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="rule-desc">Intent</Label>
            <Textarea
              id="rule-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What leakage or integrity risk does this control detect?"
              rows={2}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>Entity scope</Label>
              <Select value={entity} onValueChange={setEntity}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {app.entities.map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Severity</Label>
              <Select value={severity} onValueChange={(v) => setSeverity(v as CustomRule["severity"])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITIES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Execution frequency</Label>
              <Select
                value={frequency}
                onValueChange={(v) => setFrequency(v as CustomRule["frequency"])}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FREQUENCIES.map((f) => (
                    <SelectItem key={f} value={f}>
                      {f}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="rounded-md border border-border p-3">
            <p className="mb-3 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {category} parameters
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map((f) => (
                <div key={f.key} className="space-y-1.5">
                  <Label htmlFor={`p-${f.key}`}>{f.label}</Label>
                  <Input
                    id={`p-${f.key}`}
                    value={params[f.key] ?? ""}
                    onChange={(e) => setParams((p) => ({ ...p, [f.key]: e.target.value }))}
                    placeholder={f.placeholder}
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Lifecycle state</Label>
            <Select value={state} onValueChange={(v) => setState(v as CustomRule["state"])}>
              <SelectTrigger className="sm:w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Draft">Draft — not scheduled</SelectItem>
                <SelectItem value="Active">Active — scheduled for execution</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!valid} onClick={submit}>
            Create rule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}