import { useEffect, useState, type ReactNode } from "react";
import { Navigate, useRouterState } from "@tanstack/react-router";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { useAuth } from "@/lib/auth";

const COLLAPSE_KEY = "radonaix_sidebar_collapsed";

export function AppShell({
  children,
  requireAuth = true,
  requirePath,
}: {
  children: ReactNode;
  requireAuth?: boolean;
  requirePath?: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const { canAccess, user, token, loading } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  useEffect(() => {
    setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  };

  // Still bootstrapping the session from the backend — hold a neutral screen.
  if (requireAuth && loading) {
    return <div className="min-h-screen bg-background" />;
  }
  // No session (or the token was rejected during bootstrap) — go to login.
  if (requireAuth && !token) {
    return <Navigate to="/login" />;
  }
  // Token present but the user hasn't resolved yet — hold the neutral shell
  // rather than flashing ungated nav/content.
  if (requireAuth && !user) {
    return <div className="min-h-screen bg-background" />;
  }

  const checkPath = requirePath ?? pathname;
  const allowed = !user || canAccess(checkPath);

  return (
    <div className="min-h-screen flex bg-background">
      <Sidebar collapsed={collapsed} onToggle={toggleCollapsed} />
      <div className="flex-1 flex flex-col min-w-0">
        <Header onToggleSidebar={toggleCollapsed} />
        <main className="flex-1 px-6 py-6 overflow-x-auto">
          {allowed ? children : <Navigate to="/access-denied" />}
        </main>
      </div>
    </div>
  );
}
