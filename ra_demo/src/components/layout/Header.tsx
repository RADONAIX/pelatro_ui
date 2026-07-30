import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { Moon, Sun, Settings, ChevronDown, Users, ScrollText, Cog, LogOut, Layers, Check, Menu, ShieldCheck, UserCog, Database } from "lucide-react";
import { useAuth, ROLE_LABELS } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { DownloadCenter } from "@/components/layout/DownloadCenter";
import { ASSURANCE_SCOPES, useAssuranceScope } from "@/lib/assuranceScope";

export function Header({ onToggleSidebar }: { onToggleSidebar?: () => void }) {
  const { user, signOut, canAccess } = useAuth();
  const t = useT();
  const navigate = useNavigate();
  // The scope lives in a context now so the Sidebar can react to it. Storage
  // key, default and behaviour are unchanged (see @/lib/assuranceScope).
  const { scope, setScope } = useAssuranceScope();
  const [dark, setDark] = useState<boolean>(() =>
    typeof window !== "undefined" ? localStorage.getItem("radonaix_theme") === "dark" : false,
  );
  const [scopeOpen, setScopeOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const scopeRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("radonaix_theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (scopeRef.current && !scopeRef.current.contains(e.target as Node)) setScopeOpen(false);
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) setSettingsOpen(false);
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) setProfileOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const settingsItems = [
    { label: "User Management", icon: Users, to: "/users" },
    { label: "Role Management", icon: ShieldCheck, to: "/roles" },
    { label: "Audit Logs", icon: ScrollText, to: "/audit-logs" },
    { label: "Database Connections", icon: Database, to: "/database-connections" },
    // { label: "System Configuration", icon: Cog, to: "/system-config" },
  ].filter((it) => canAccess(it.to));

  return (
    <header className="h-16 shrink-0 border-b border-border bg-card/80 backdrop-blur flex items-center px-4 md:px-6 gap-4 sticky top-0 z-30">
      {onToggleSidebar && (
        <Tooltip label={t("Open or close the navigation menu")} side="bottom">
          <button
            onClick={onToggleSidebar}
            className="md:hidden h-9 w-9 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground"
            aria-label={t("Toggle menu")}
          >
            <Menu className="h-4 w-4" />
          </button>
        </Tooltip>
      )}
      <div className="flex items-center gap-2 ml-auto">
        <div className="relative" ref={scopeRef}>
          <button
            onClick={() => { setScopeOpen((o) => !o); setSettingsOpen(false); setProfileOpen(false); }}
            className="hidden md:flex items-center gap-2 h-10 pl-3 pr-3 rounded-full border border-primary/30 bg-primary/5 hover:bg-primary/10 transition"
          >
            <Layers className="h-4 w-4 text-primary" />
            <div className="text-left leading-tight">
              <div className="text-[10px] tracking-widest text-primary/80 font-semibold">{t("ASSURANCE SCOPE")}</div>
              <div className="text-sm font-semibold text-foreground">{scope}</div>
            </div>
            <ChevronDown className="h-4 w-4 text-primary ml-1" />
          </button>
          {scopeOpen && (
            <div className="absolute right-0 mt-2 w-72 rounded-xl border border-border bg-popover shadow-lg py-2 z-40">
              <div className="px-4 py-2 text-[10px] tracking-widest text-muted-foreground font-semibold">{t("ASSURANCE SCOPE")}</div>
              {ASSURANCE_SCOPES.map((s) => (
                <button key={s} onClick={() => { setScope(s); setScopeOpen(false); }}
                  className={`w-full flex items-center justify-between gap-3 px-4 py-2 text-sm hover:bg-muted ${scope === s ? "text-primary font-semibold" : "text-foreground"}`}>
                  <span className="flex items-center gap-3"><Layers className={`h-4 w-4 ${scope === s ? "text-primary" : "text-muted-foreground"}`} />{s}</span>
                  {scope === s && <Check className="h-4 w-4 text-primary" />}
                </button>
              ))}
            </div>
          )}
        </div>

        <Tooltip label={dark ? t("Switch to light mode") : t("Switch to dark mode")} side="bottom">
          <button onClick={() => setDark((d) => !d)} className="h-9 w-9 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground transition" aria-label={t("Toggle dark mode")}>
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </Tooltip>

        <DownloadCenter />

        {settingsItems.length > 0 && (
          <div className="relative" ref={settingsRef}>
        <Tooltip label={t("Adminstrative")} side="bottom">
            <button onClick={() => { setSettingsOpen((o) => !o); setProfileOpen(false); setScopeOpen(false); }} className="h-9 w-9 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground transition" aria-label={t("Settings")}>
              <UserCog className="h-4 w-4" />
            </button>
        </Tooltip>
            
            {settingsOpen && (
              <div className="absolute right-0 mt-2 w-64 rounded-xl border border-border bg-popover shadow-lg py-2 z-40">
                {settingsItems.map((it) => {
                  const Icon = it.icon;
                  return (
                    <Link key={it.to} to={it.to} onClick={() => setSettingsOpen(false)} className="flex items-center gap-3 px-4 py-2 text-sm hover:bg-muted text-foreground">
                      <Icon className="h-4 w-4 text-muted-foreground" />{t(it.label)}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        )}

        <div className="relative" ref={profileRef}>
            <button onClick={() => { setProfileOpen((o) => !o); setSettingsOpen(false); setScopeOpen(false); }} className="flex items-center gap-2 h-10 pl-1 pr-3 rounded-full hover:bg-muted transition">
              <div className="h-8 w-8 rounded-full bg-primary text-primary-foreground text-xs font-semibold flex items-center justify-center">
                {user?.avatar || user?.name?.slice(0, 2).toUpperCase() || "RA"}
              </div>
              <div className="hidden md:block text-left leading-tight">
                <div className="text-xs font-semibold text-foreground">{user?.name || t("User")}</div>
                <div className="text-[10px] text-muted-foreground">{user?.roleLabel || (user ? ROLE_LABELS[user.role] : t("Member"))}</div>
              </div>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          {profileOpen && (
            <div className="absolute right-0 mt-2 w-56 rounded-xl border border-border bg-popover shadow-lg py-2 z-40">
              <Link to="/profile" onClick={() => setProfileOpen(false)} className="block px-4 py-2 text-sm hover:bg-muted">{t("View profile")}</Link>
              <button onClick={() => { signOut(); setProfileOpen(false); navigate({ to: "/login" }); }} className="w-full text-left px-4 py-2 text-sm hover:bg-muted text-destructive">{t("Sign out")}</button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
