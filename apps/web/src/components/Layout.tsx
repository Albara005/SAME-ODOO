import { Languages, LogOut, Menu, Moon } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/Auth";
import { useOrg } from "../auth/Org";
import { useI18n, useRoleLabel } from "../i18n/I18n";
import { useTheme } from "../lib/theme";
import { StarLattice, StarMark } from "./Art";
import { visibleNav } from "./nav";
import { IconButton } from "./ui";

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

export function Layout() {
  const { t } = useI18n();
  const roleLabel = useRoleLabel();
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
          <Outlet />
        </main>
      </div>
    </div>
  );
}
