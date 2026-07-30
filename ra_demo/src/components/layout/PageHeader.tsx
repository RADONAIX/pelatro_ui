import type { ReactNode } from "react";
import { InfoHint } from "@/components/ui-kit/InfoHint";

export function PageHeader({
  title,
  description,
  info,
  actions,
}: {
  title: string;
  description?: string;
  info?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between mb-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl md:text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
          {info && <InfoHint text={info} iconClassName="h-4 w-4" />}
        </div>
        {description && <p className="text-sm text-muted-foreground mt-1 max-w-3xl leading-relaxed">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
