import {
  ArrowLeftRight, Bell, ChevronLeft, ChevronRight, Languages, LogOut, MailWarning, Menu, Moon, PanelLeftClose, PanelLeftOpen,
  Plus, Search, UserRound, X,
} from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api, ApiError, unwrap } from "../api/client";
import { useAuth } from "../auth/Auth";
import { useOrg } from "../auth/Org";
import { useI18n, useRoleLabel } from "../i18n/I18n";
import { CrumbContext } from "../lib/crumbs";
import { initialsOf } from "../lib/format";
import { useLiveCounts } from "../lib/live";
import { useTheme } from "../lib/theme";
import { StarLattice, StarMark } from "./Art";
import { ACTIONS, CommandPalette } from "./CommandPalette";
import { MenuButton } from "./Menu";
import { NAV, visibleNav, type NavItem } from "./nav";
import { Button, IconButton, Loading, useToast } from "./ui";

export function BrandMark() {
  const { t } = useI18n();
  return (
    <div className="brand">
      <StarMark />
      <span>{t("app.name")}</span>
    </div>
  );
}

const initials = initialsOf;

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

export function SignOutButton() {
  const { t } = useI18n();
  const { logout } = useAuth();
  return (
    <IconButton label={t("auth.logout")} onClick={() => void logout()}>
      <LogOut size={18} />
    </IconButton>
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

const DISMISSED_KEY = "novara.verify-dismissed";

/** Reminds a user who hasn't confirmed their email yet; can be hidden until the next visit. */
function VerifyBanner() {
  const { t } = useI18n();
  const { user } = useAuth();
  const toast = useToast();
  const [hidden, setHidden] = useState(() => {
    try {
      return sessionStorage.getItem(DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const resend = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/me/verify-email")),
    onSuccess: () => toast(t("verify.resent")),
    onError: (e) => toast(e instanceof ApiError && e.status === 429 ? t("verify.wait") : t("common.error"), "error"),
  });
  if (!user || user.email_verified || hidden) return null;
  return (
    <div className="verify-banner">
      <MailWarning size={18} aria-hidden />
      <span className="grow">{t("verify.banner", { email: user.email })}</span>
      <Button size="sm" loading={resend.isPending} onClick={() => resend.mutate()}>
        {t("verify.resend")}
      </Button>
      <IconButton
        label={t("verify.later")}
        onClick={() => {
          setHidden(true);
          try {
            sessionStorage.setItem(DISMISSED_KEY, "1");
          } catch {
            /* storage unavailable: hidden until reload */
          }
        }}
      >
        <X size={16} />
      </IconButton>
    </div>
  );
}

/** The app the page belongs to: the menu item whose path is the longest prefix of the page's. */
function currentApp(pathname: string): NavItem | undefined {
  const rest = pathname.replace(/^\/o\/[^/]+\/?/, "");
  const items = NAV.flatMap((g) => g.items);
  return items
    .filter((i) => (i.to === "" ? rest === "" : rest === i.to || rest.startsWith(`${i.to}/`)))
    .sort((a, b) => b.to.length - a.to.length)[0];
}

function useStoredFlag(key: string): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  const set = useCallback((v: boolean) => {
    setOn(v);
    try {
      localStorage.setItem(key, v ? "1" : "0");
    } catch {
      /* storage unavailable: until reload */
    }
  }, [key]);
  return [on, set];
}

function Crumbs({ title }: { title: string | null }) {
  const { t, dir } = useI18n();
  const { path } = useOrg();
  const location = useLocation();
  const app = currentApp(location.pathname);
  const Sep = dir === "rtl" ? ChevronLeft : ChevronRight;
  const appLabel = app ? t(app.label) : null;
  const deeper = app && location.pathname.replace(/\/$/, "").split("/").length > 3 + app.to.split("/").filter(Boolean).length;
  return (
    <nav className="crumbs" aria-label={t("nav.breadcrumb")}>
      <ol>
        {app && (
          <li>
            {title && title !== appLabel ? (
              <Link to={path(app.to)} className="crumb-app"><app.icon size={16} aria-hidden /> {appLabel}</Link>
            ) : (
              <span className="crumb-app" aria-current="page"><app.icon size={16} aria-hidden /> {appLabel}</span>
            )}
          </li>
        )}
        {title && title !== appLabel && (
          <li aria-current="page" className={deeper ? "crumb-record" : ""}>
            <Sep size={14} aria-hidden className="crumb-sep" />
            <span dir="auto">{title}</span>
          </li>
        )}
      </ol>
    </nav>
  );
}

function QuickCreate() {
  const { t } = useI18n();
  const { can, path } = useOrg();
  const items = ACTIONS.filter((a) => a.create && a.anyOf.some((p) => can(p)));
  if (!items.length) return null;
  return (
    <MenuButton label={t("palette.quickCreate")} button={<Plus size={18} />}
      items={items.map((a) => ({ kind: "link" as const, label: t(a.label), to: path(a.to), icon: <a.icon size={16} aria-hidden /> }))} />
  );
}

function UserMenu() {
  const { t } = useI18n();
  const { user, logout } = useAuth();
  const { org } = useOrg();
  const roleLabel = useRoleLabel();
  const name = user?.full_name || user?.email || "";
  return (
    <MenuButton
      label={t("account.menu", { name })}
      className="user-menu"
      button={<><span className="user-avatar" aria-hidden>{initials(name)}</span><span className="user-name small">{name}</span></>}
      items={[
        { kind: "label", label: `${org.name} · ${roleLabel(org.role)}` },
        { kind: "link", label: t("account.title"), to: "/account", icon: <UserRound size={16} aria-hidden /> },
        { kind: "link", label: t("orgs.switch"), to: "/orgs", icon: <ArrowLeftRight size={16} aria-hidden /> },
        { kind: "separator" },
        { kind: "action", label: t("auth.logout"), onSelect: () => void logout(), icon: <LogOut size={16} aria-hidden />, danger: true },
      ]}
    />
  );
}

export function Layout() {
  const { t } = useI18n();
  const roleLabel = useRoleLabel();
  const counts = useCounts();
  const { org, can, path } = useOrg();
  const [open, setOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const [title, setTitle] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useStoredFlag("novara.nav-collapsed");
  const location = useLocation();
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const app = currentApp(location.pathname);
  useEffect(() => {
    const parts = [title, app && title !== t(app.label) ? t(app.label) : null, org.name, t("app.name")].filter(Boolean);
    document.title = parts.join(" · ");
  }, [title, app, org.name, t]);
  const unread = counts.notifications ?? 0;

  return (
    <CrumbContext.Provider value={setTitle}>
      <div className={`shell ${collapsed ? "collapsed" : ""}`}>
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
          <nav className="stack-sm nav-scroll">
            {visibleNav(can, org.apps).map((group, i) => (
              <div className="nav-group" key={group.title ?? i}>
                {group.title && <div className="nav-title">{t(group.title)}</div>}
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={path(item.to)}
                    end={item.to === ""}
                    title={collapsed ? t(item.label) : undefined}
                    className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
                  >
                    <item.icon size={18} aria-hidden />
                    <span className="nav-label">{t(item.label)}</span>
                    {Boolean(counts[item.to]) && (
                      <span className="nav-badge" aria-label={t("nav.count", { n: counts[item.to]! })}>{counts[item.to]}</span>
                    )}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
          <button type="button" className="nav-collapse" onClick={() => setCollapsed(!collapsed)} aria-pressed={collapsed}
            title={collapsed ? t("nav.expand") : t("nav.collapse")}>
            {collapsed ? <PanelLeftOpen size={18} aria-hidden /> : <PanelLeftClose size={18} aria-hidden />}
            <span className="nav-label">{collapsed ? t("nav.expand") : t("nav.collapse")}</span>
          </button>
        </aside>
        <div className="main">
          <header className="topbar">
            <IconButton label={t("nav.menu")} className="btn btn-ghost icon-btn menu-btn" onClick={() => setOpen(true)}>
              <Menu size={20} />
            </IconButton>
            <Crumbs title={title} />
            <button type="button" className="search-trigger" onClick={() => setPalette(true)} aria-keyshortcuts="Control+K Meta+K">
              <Search size={16} aria-hidden />
              <span className="search-trigger-text">{t("palette.trigger")}</span>
              <kbd className="kbd">Ctrl K</kbd>
            </button>
            <div className="topbar-actions">
              <QuickCreate />
              <Link to={path("notifications")} className="btn btn-ghost icon-btn bell"
                aria-label={unread ? `${t("nav.notifications")} (${t("nav.count", { n: unread })})` : t("nav.notifications")}
                title={t("nav.notifications")}>
                <Bell size={18} aria-hidden />
                {unread > 0 && <span className="bell-count" aria-hidden>{unread > 99 ? "99+" : unread}</span>}
              </Link>
              <TopActions />
              <UserMenu />
            </div>
          </header>
          <main className="content" id="main">
            <VerifyBanner />
            <Suspense fallback={<Loading />}>
              <Outlet />
            </Suspense>
          </main>
        </div>
        {palette && <CommandPalette onClose={() => setPalette(false)} />}
      </div>
    </CrumbContext.Provider>
  );
}
