import { useQuery } from "@tanstack/react-query";
import { FilePlus2, Sparkles, UserPlus } from "lucide-react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import { Card, PageHeader } from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { sumAmounts, today } from "../lib/format";

function Stat({ label, value, sub, to }: { label: string; value: string; sub?: string; to?: string }) {
  const body = (
    <div className="stat">
      <span className="muted small">{label}</span>
      <span className="value num">{value}</span>
      {sub && <span className="muted small">{sub}</span>}
    </div>
  );
  return <Card tight>{to ? <Link to={to}>{body}</Link> : body}</Card>;
}

export function HomePage() {
  const { t, money } = useI18n();
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

  return (
    <div className="stack">
      <PageHeader title={t("home.title")} subtitle={org.name} />
      <div className="grid-cards">
        {can("invoices.read") && (
          <Stat
            label={t("home.receivables")}
            value={receivables.data ? money(receivables.data.total_due, receivables.data.currency) : "…"}
            sub={overdue !== null ? `${t("home.overdue")}: ${money(overdue, org.default_currency)}` : undefined}
            to={path("reports")}
          />
        )}
        {can("invoices.read") && (
          <Stat
            label={t("home.salesThisMonth")}
            value={sales.data ? money(sales.data.net_sales, sales.data.currency) : "…"}
            sub={sales.data ? t("home.invoicesThisMonth", { count: sales.data.invoices }) : undefined}
            to={path("reports")}
          />
        )}
        <Stat label={t("home.openTasks")} value={tasks.data ? String(tasks.data.length) : "…"} to={path("tasks")} />
        <Stat label={t("home.unread")} value={unread.data ? String(unread.data.length) : "…"} to={path("notifications")} />
        <Stat label={t("home.approvals")} value={approvals.data ? String(approvals.data.length) : "…"} to={path("approvals")} />
      </div>
      <Card title={t("home.quick")}>
        <div className="row">
          {can("invoices.write") && (
            <Link className="btn" to={path("invoices/new")}>
              <FilePlus2 size={16} aria-hidden /> {t("home.newInvoice")}
            </Link>
          )}
          {can("customers.write") && (
            <Link className="btn" to={path("customers?new=1")}>
              <UserPlus size={16} aria-hidden /> {t("home.newCustomer")}
            </Link>
          )}
          {can("ai.use") && (
            <Link className="btn" to={path("assistant")}>
              <Sparkles size={16} aria-hidden /> {t("home.askAssistant")}
            </Link>
          )}
        </div>
      </Card>
    </div>
  );
}
