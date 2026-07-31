import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { ExecutiveDashboard } from "../dashboard/ExecutiveDashboard";

// The dashboard section is the executive dashboard — one template, eight
// assurances. Everything app-specific lives in lib/assurance/dashboard-config.
export function DashboardSection({ app }: { app: AppMetadata }) {
  return <ExecutiveDashboard app={app} />;
}
