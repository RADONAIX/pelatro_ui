import { createFileRoute, Link } from "@tanstack/react-router";
import { ShieldAlert, ArrowLeft } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/access-denied")({ component: AccessDeniedPage });

function AccessDeniedPage() {
  const t = useT();
  return (
    <AppShell requirePath="/access-denied">
      <div className="max-w-lg mx-auto mt-16 bg-card border border-border rounded-2xl p-10 text-center shadow-sm">
        <div className="h-14 w-14 mx-auto rounded-full bg-destructive/10 flex items-center justify-center mb-4">
          <ShieldAlert className="h-7 w-7 text-destructive" />
        </div>
        <h1 className="text-2xl font-semibold text-foreground">{t("Access Denied")}</h1>
        <p className="text-sm text-muted-foreground mt-2">
          {t("Your role doesn’t have permission to view this module. Contact your administrator if you need access.")}
        </p>
        <Link
          to="/"
          className="mt-6 inline-flex items-center gap-2 h-10 px-4 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90"
        >
          <ArrowLeft className="h-4 w-4" /> {t("Back to dashboard")}
        </Link>
      </div>
    </AppShell>
  );
}
