import { Languages, LogOut, Menu, Moon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useAuth } from "../auth/Auth";
import { useOrg } from "../auth/Org";
import { useI18n, useRoleLabel } from "../i18n/I18n";
import { useLiveCounts } from "../lib/live";
import { useTheme } from "../lib/theme";
import { StarLattice, StarMark } from "./Art";
import { visibleNav } from "./nav";
import { IconButton, Loading, useToast } from "./ui";

export function BrandMark() {
  const { t } = useI18n();
  return (
    <div className="brand">
      <StarMark />
      <span>{t("app.name")}</span>
    </div>
  );
}

const initials = (name: string) =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w.replace(/^ال/, "")[0] ?? "").join("").toUpperCase() || "•";

export function TopActions() {
  const { t, lang, setLang } = useI18n();
  const { toggle } = useTheme();
  return (
    <>
      <IconButton label={t("lang.switch")} onClick={() => setLang(lang === "ar" ? "en" : "ar")}>
        <Languages size={18} />
      </IconButton>
      <IconButton label={t("theme.toggle")} onClick={toggle}>
        <Moon size={18} />
      </IconButton>
    </>
  );
}

/** Counts for the menu: pushed live by the server; the first values come from the lists themselves. */
function useCounts(): Record<string, number | undefined> {
  const { org } = useOrg();
  const { t } = useI18n();
  const toast = useToast();
  const location = useLocation();
  const onNotificationsPage = location.pathname.endsWith("/notifications");
  // On the notifications page the list itself shows the new item, so no toast there.
  const live = useLiveCounts(org.id, (title) => !onNotificationsPage && toast(t("notifications.new", { title })));
  const id = { path: { org_id: org.id } };
  const unread = useQuery({
    queryKey: ["notifications", org.id, "unread"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/notifications", { params: { ...id, query: { unread_only: true } } })),
  });
  const approvals = useQuery({
    queryKey: ["approvals", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/approvals", { params: id })),
  });
  const tasks = useQuery({
    queryKey: ["tasks", org.id, "open"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/tasks", { params: { ...id, query: { status_filter: "open" } } })),
  });
  return {
    notifications: live?.notifications ?? unread.data?.length,
    approvals: live?.approvals ?? approvals.data?.length,
    tasks: live?.tasks ?? tasks.data?.length,
  };
}

export function Layout() {
  const { t } = useI18n();
  const roleLabel = useRoleLabel();
  const counts = useCounts();
  const { org, can, path } = useOrg();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label={t("nav.menu")}>
        <StarLattice />
        <BrandMark />
        <NavLink to="/orgs" className="org-switch" title={t("orgs.switch")}>
          <span className="org-avatar" aria-hidden>{initials(org.name)}</span>
          <span className="org-text">
            <strong>{org.name}</strong>
            <span className="muted small">{roleLabel(org.role)}</span>
          </span>
        </NavLink>
        <nav className="stack-sm">
          {visibleNav(can).map((group, i) => (
            <div className="nav-group" key={group.title ?? i}>
              {group.title && <div className="nav-title">{t(group.title)}</div>}
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={path(item.to)}
                  end={item.to === ""}
                  className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
                >
                  <item.icon size={18} aria-hidden />
                  {t(item.label)}
                  {Boolean(counts[item.to]) && (
                    <span className="nav-badge" aria-label={t("nav.count", { n: counts[item.to]! })}>{counts[item.to]}</span>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <IconButton label={t("nav.menu")} className="btn btn-ghost icon-btn menu-btn" onClick={() => setOpen(true)}>
            <Menu size={20} />
          </IconButton>
          <span className="grow" />
          <span className="user-chip">
            <span className="user-avatar" aria-hidden>{initials(user?.full_name || user?.email || "")}</span>
            <span className="small">{user?.full_name || user?.email}</span>
          </span>
          <TopActions />
          <IconButton label={t("auth.logout")} onClick={() => void logout()}>
            <LogOut size={18} />
          </IconButton>
        </header>
        <main className="content" id="main">
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
