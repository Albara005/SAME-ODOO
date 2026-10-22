import { Languages, LogOut, Menu, Moon } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/Auth";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import { useTheme } from "../lib/theme";
import { visibleNav } from "./nav";
import { IconButton } from "./ui";

export function BrandMark() {
  const { t } = useI18n();
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden>
        N
      </span>
      <span>{t("app.name")}</span>
    </div>
  );
}

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
  const { org, can, path } = useOrg();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label={t("nav.menu")}>
        <BrandMark />
        <NavLink to="/orgs" className="org-switch" title={t("orgs.switch")}>
          <strong>{org.name}</strong>
          <span className="muted small">{org.role}</span>
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
          <span className="muted small">{user?.full_name || user?.email}</span>
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
