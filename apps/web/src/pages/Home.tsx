import { useQuery } from "@tanstack/react-query";
import { Bell, ClipboardCheck, FilePlus2, LayoutGrid, ListChecks, ShoppingCart, Sparkles, TrendingUp, Wallet, type LucideIcon } from "lucide-react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useAuth } from "../auth/Auth";
import { useOrg } from "../auth/Org";
import { StarLattice } from "../components/Art";
import { NAV } from "../components/nav";
import { useI18n } from "../i18n/I18n";
import { formatDate, sumAmounts, today } from "../lib/format";

function Kpi({ label, value, sub, to, icon: Icon, hue }: { label: string; value: string; sub?: string; to: string; icon: LucideIcon; hue: string }) {
  return (
    <Link to={to} className={`card card-tight kpi hue-${hue}`}>
      <span className="kpi-head">
        <Icon size={16} aria-hidden /> {label}
      </span>
      <span className="value num">{value}</span>
      {sub && <span className="muted small">{sub}</span>}
    </Link>
  );
}

export function HomePage() {
  const { t, money, lang } = useI18n();
  const { user } = useAuth();
  const { org, can, path } = useOrg();
  const id = { path: { org_id: org.id } };
  const monthStart = `${today().slice(0, 8)}01`;

  const receivables = useQuery({
    queryKey: ["home", org.id, "receivables"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/reports/receivables", { params: id })),
    enabled: can("invoices.read"),
  });
  const sales = useQuery({
    queryKey: ["home", org.id, "sales", monthStart],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/reports/sales", { params: { ...id, query: { date_from: monthStart } } })),
    enabled: can("invoices.read"),
  });
  const tasks = useQuery({
    queryKey: ["tasks", org.id, "open"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/tasks", { params: { ...id, query: { status_filter: "open" } } })),
  });
  const unread = useQuery({
    queryKey: ["notifications", org.id, "unread"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/notifications", { params: { ...id, query: { unread_only: true } } })),
  });
  const approvals = useQuery({
    queryKey: ["approvals", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/approvals", { params: id })),
  });

  const overdue = receivables.data
    ? sumAmounts(Object.entries(receivables.data.totals).filter(([k]) => k !== "current").map(([, v]) => v))
    : null;
  const firstName = (user?.full_name || "").split(/\s+/)[0] || user?.email || "";
  const greeting = new Date().getHours() < 12 ? "home.greetMorning" : "home.greetEvening";
  const apps = NAV.flatMap((g) => g.items).filter((i) => i.desc && (!i.anyOf || i.anyOf.some(can)));

  return (
    <div className="stack" style={{ gap: "var(--space-5)" }}>
      <section className="hero">
        <StarLattice />
        <span className="hero-sub small">
          {org.name} · {formatDate(today(), lang)}
        </span>
        <h1>{t(greeting, { name: firstName })}</h1>
        <div className="hero-actions">
          {can("sales.write") && (
            <Link className="btn btn-gold" to={path("sales/new")}>
              <ShoppingCart size={16} aria-hidden /> {t("home.newQuote")}
            </Link>
          )}
          {can("invoices.write") && (
            <Link className="btn btn-glass" to={path("invoices/new")}>
              <FilePlus2 size={16} aria-hidden /> {t("home.newInvoice")}
            </Link>
          )}
          {can("ai.use") && (
            <Link className="btn btn-glass" to={path("assistant")}>
              <Sparkles size={16} aria-hidden /> {t("home.askAssistant")}
            </Link>
          )}
        </div>
      </section>

      <section className="stack-sm" aria-labelledby="glance">
        <h2 id="glance" className="section-title">
          <TrendingUp size={18} aria-hidden /> {t("home.glance")}
        </h2>
        <div className="kpis">
          {can("invoices.read") && (
            <Kpi
              icon={Wallet}
              hue="emerald"
              label={t("home.receivables")}
              value={receivables.data ? money(receivables.data.total_due, receivables.data.currency) : "…"}
              sub={overdue !== null ? `${t("home.overdue")}: ${money(overdue, org.default_currency)}` : undefined}
              to={path("reports")}
            />
          )}
          {can("invoices.read") && (
            <Kpi
              icon={TrendingUp}
              hue="lapis"
              label={t("home.salesThisMonth")}
              value={sales.data ? money(sales.data.net_sales, sales.data.currency) : "…"}
              sub={sales.data ? t("home.invoicesThisMonth", { count: sales.data.invoices }) : undefined}
              to={path("reports")}
            />
          )}
          <Kpi icon={ListChecks} hue="teal" label={t("home.openTasks")} value={tasks.data ? String(tasks.data.length) : "…"} to={path("tasks")} />
          <Kpi icon={Bell} hue="amber" label={t("home.unread")} value={unread.data ? String(unread.data.length) : "…"} to={path("notifications")} />
          <Kpi icon={ClipboardCheck} hue="rose" label={t("home.approvals")} value={approvals.data ? String(approvals.data.length) : "…"} to={path("approvals")} />
        </div>
      </section>

      {apps.length > 0 && (
        <section className="stack-sm" aria-labelledby="apps">
          <h2 id="apps" className="section-title">
            <LayoutGrid size={18} aria-hidden /> {t("home.apps")}
          </h2>
          <div className="apps">
            {apps.map((a) => (
              <Link key={a.to} to={path(a.to)} className={`app-tile hue-${a.hue}`}>
                <span className="app-icon">
                  <a.icon size={22} aria-hidden />
                </span>
                <span className="stack-sm" style={{ gap: 2 }}>
                  <strong>{t(a.label)}</strong>
                  <span className="muted small">{t(a.desc!)}</span>
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
