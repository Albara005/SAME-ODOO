import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Lock } from "lucide-react";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import { ColumnChart } from "../components/ColumnChart";
import { download, Tabs } from "../components/business";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, useToast, errorText,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { isZero } from "../lib/decimal";
import { addDays, fiscalYear, locale, today } from "../lib/format";

type Tab = "receivables" | "payables" | "sales" | "pnl" | "balance" | "trial";

/** The default chart's accounts in the reader's language; accounts an organization adds keep their own name. */
const DEFAULT_ACCOUNTS = ["1100", "1200", "1300", "1400", "1500", "2100", "2150", "2200", "3000", "3100", "4000", "5000", "5100", "6000"];

export function useAccountName() {
  const { t } = useI18n();
  return (code: string, name: string) => (DEFAULT_ACCOUNTS.includes(code) ? t(`acct.${code}` as MessageKey) : name);
}

const BUCKETS: [string, MessageKey][] = [
  ["current", "reports.current"], ["1_30", "reports.b1_30"], ["31_60", "reports.b31_60"],
  ["61_90", "reports.b61_90"], ["over_90", "reports.over90"],
];

export function ReportsPage() {
  const { t } = useI18n();
  const { can } = useOrg();
  const tabs = [
    can("accounting.read") && { key: "pnl" as const, label: t("reports.pnl") },
    can("accounting.read") && { key: "balance" as const, label: t("reports.balanceSheet") },
    can("invoices.read") && { key: "receivables" as const, label: t("reports.receivables") },
    can("purchases.read") && { key: "payables" as const, label: t("reports.payables") },
    can("invoices.read") && { key: "sales" as const, label: t("reports.sales") },
    can("accounting.read") && { key: "trial" as const, label: t("reports.trialBalance") },
  ].filter((x): x is { key: Tab; label: string } => Boolean(x));
  const [tab, setTab] = useState<Tab>(tabs[0]?.key ?? "receivables");
  return (
    <div className="stack">
      <PageHeader title={t("reports.title")} actions={can("accounting.read") && <BooksLock />} />
      <Tabs<Tab> tabs={tabs} value={tab} onChange={setTab} />
      {tab === "pnl" && <ProfitAndLoss />}
      {tab === "balance" && <BalanceSheet />}
      {tab === "receivables" && <Receivables />}
      {tab === "payables" && <Payables />}
      {tab === "sales" && <Sales />}
      {tab === "trial" && <TrialBalance />}
    </div>
  );
}

/** Closed books: nothing can be dated on or before the lock date. Shown to accountants, changed by accounting.close. */
export function BooksLock() {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const params = { path: { org_id: org.id } };
  const lock = useQuery({
    queryKey: ["books-lock", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/lock", { params })),
  });
  const [value, setValue] = useState("");
  const save = useMutation({
    mutationFn: (locked_until: string | null) => unwrap(api.PUT("/api/v1/orgs/{org_id}/accounting/lock", { params, body: { locked_until } })),
    onSuccess: (r) => {
      qc.setQueryData(["books-lock", org.id], r);
      void qc.invalidateQueries({ queryKey: ["org", org.id] });
      setEditing(false);
      toast(r.locked_until ? t("lock.closed", { date: date(r.locked_until) }) : t("lock.reopened"));
    },
  });
  const until = lock.data?.locked_until ?? null;
  return (
    <div className="books-lock">
      <Lock size={16} aria-hidden />
      <span className="small">{until ? t("lock.until", { date: date(until) }) : t("lock.open")}</span>
      {can("accounting.close") && (
        <Button size="sm" onClick={() => { setValue(until ?? ""); setEditing(true); }}>{t("lock.change")}</Button>
      )}
      {editing && (
        <Dialog open onClose={() => setEditing(false)} title={t("lock.title")}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(value || null); }}>
            <p className="muted">{t("lock.body")}</p>
            {save.isError && <ErrorNotice error={save.error} />}
            <Field label={t("lock.date")} hint={t("lock.hint")} optional>
              <Input type="date" value={value} max={yesterday()} onChange={(e) => setValue(e.target.value)} ltr />
            </Field>
            <div className="form-actions">
              {until && (
                <Button variant="danger" onClick={() => save.mutate(null)} loading={save.isPending && !value}>{t("lock.reopen")}</Button>
              )}
              <Button onClick={() => setEditing(false)}>{t("common.cancel")}</Button>
              <Button type="submit" variant="primary" loading={save.isPending && Boolean(value)} disabled={!value}>{t("lock.save")}</Button>
            </div>
          </form>
        </Dialog>
      )}
    </div>
  );
}

function yesterday(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

export function ExportButton({ path, filename }: { path: string; filename: string }) {
  const { t } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      icon={<Download size={16} />}
      loading={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await download(path, filename);
        } catch (e) {
          toast(errorText(e, t("common.error")), "error");
        } finally {
          setBusy(false);
        }
      }}
    >
      {t("reports.export")}
    </Button>
  );
}

export const qs = (params: Record<string, string>) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  return s ? `?${s}` : "";
};

// ---------------------------------------------------------------- receivables aging

function Receivables() {
  const { t, money, date } = useI18n();
  const { org } = useOrg();
  const [asOf, setAsOf] = useState(today());
  const report = useQuery({
    queryKey: ["report", org.id, "receivables", asOf],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/reports/receivables", { params: { path: { org_id: org.id }, query: { as_of: asOf || undefined } } })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.asOf")}>
          <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} ltr />
        </Field>
      </div>
      <QueryView query={report}>
        {(r) =>
          r.customers.length === 0 ? (
            <Empty>{t("reports.noReceivables")}</Empty>
          ) : (
            <div className={`table-wrap ${report.isPlaceholderData ? "refetching" : ""}`}>
              <table>
                <caption className="sr-only">{t("reports.receivables")} · {date(r.as_of)}</caption>
                <thead>
                  <tr>
                    <th>{t("invoices.customer")}</th>
                    {BUCKETS.map(([k, label]) => (
                      <th key={k} className="end">{t(label)}</th>
                    ))}
                    <th className="end">{t("reports.totalDue")}</th>
                  </tr>
                </thead>
                <tbody>
                  {r.customers.map((c) => (
                    <tr key={c.customer_id}>
                      <td>
                        {c.customer_name}
                        <span className="muted small"> · {t("reports.invoiceCount", { n: c.invoices })}</span>
                      </td>
                      {BUCKETS.map(([k]) => (
                        <td key={k} className="end num">{isZero(c.buckets[k] ?? "0") ? "—" : money(c.buckets[k]!, r.currency)}</td>
                      ))}
                      <td className="end num"><strong>{money(c.total_due, r.currency)}</strong></td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <th>{t("invoices.total")}</th>
                    {BUCKETS.map(([k]) => (
                      <th key={k} className="end num">{money(r.totals[k] ?? "0", r.currency)}</th>
                    ))}
                    <th className="end num">{money(r.total_due, r.currency)}</th>
                  </tr>
                </tfoot>
              </table>
            </div>
          )
        }
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- sales

function monthLabel(key: string, lang: "ar" | "en") {
  const [y, m] = key.split("-").map(Number);
  if (!y || !m) return key;
  return new Intl.DateTimeFormat(locale(lang), { month: "short", year: "2-digit", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, 1)));
}

function Sales() {
  const { t, money, lang } = useI18n();
  const { org } = useOrg();
  const fiscal = fiscalYear(org.fiscal_year_start_month ?? 1, today());
  const [range, setRange] = useState({ date_from: fiscal.start, date_to: today() });
  const report = useQuery({
    queryKey: ["report", org.id, "sales", range],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/reports/sales", {
        params: { path: { org_id: org.id }, query: { date_from: range.date_from || undefined, date_to: range.date_to || undefined } },
      })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.from")}>
          <Input type="date" value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} ltr />
        </Field>
        <Field label={t("reports.to")}>
          <Input type="date" value={range.date_to} min={range.date_from} onChange={(e) => setRange({ ...range, date_to: e.target.value })} ltr />
        </Field>
        <div className="grow" />
        <ExportButton path={`/api/v1/orgs/${org.id}/invoices/export.csv${qs(range)}`} filename="invoices.csv" />
      </div>
      <QueryView query={report}>
        {(r) => (
          <div className={`stack ${report.isPlaceholderData ? "refetching" : ""}`}>
            <div className="grid-3">
              <Card tight>
                <div className="stat">
                  <span className="muted small">{t("reports.netSales")}</span>
                  <span className="value">{money(r.net_sales, r.currency)}</span>
                </div>
              </Card>
              <Card tight>
                <div className="stat">
                  <span className="muted small">{t("reports.gross")}</span>
                  <span className="value">{money(r.gross_sales, r.currency)}</span>
                </div>
              </Card>
              <Card tight>
                <div className="stat">
                  <span className="muted small">{t("nav.invoices")}</span>
                  <span className="value">{r.invoices}</span>
                </div>
              </Card>
            </div>
            <Card title={t("reports.byMonth")}>
              {r.by_month.length === 0 ? (
                <Empty />
              ) : (
                <div className="stack">
                  <p className="muted small">{t("reports.netSalesOf", { currency: r.currency })}</p>
                  <ColumnChart
                    title={t("reports.byMonth")}
                    locale={locale(lang)}
                    columns={r.by_month.map((b) => ({
                      key: b.key,
                      label: monthLabel(b.key, lang),
                      value: b.net,
                      display: money(b.net, r.currency),
                      detail: t("reports.invoiceCount", { n: b.invoices }),
                    }))}
                  />
                  <details>
                    <summary className="small">{t("reports.showTable")}</summary>
                    <Buckets rows={r.by_month.map((b) => ({ ...b, label: monthLabel(b.key, lang) }))} currency={r.currency} />
                  </details>
                </div>
              )}
            </Card>
            <div className="grid-2">
              <Card title={t("reports.topCustomers")}>
                {r.top_customers.length ? <Buckets rows={r.top_customers} currency={r.currency} /> : <Empty />}
              </Card>
              <Card title={t("reports.topProducts")}>
                {r.top_products.length ? <Buckets rows={r.top_products} currency={r.currency} /> : <Empty />}
              </Card>
            </div>
          </div>
        )}
      </QueryView>
    </div>
  );
}

function Buckets({ rows, currency }: { rows: { key: string; label: string; net: string; invoices: number }[]; currency: string }) {
  const { t, money } = useI18n();
  return (
    <table>
      <thead>
        <tr>
          <th>{t("common.name")}</th>
          <th className="end">{t("nav.invoices")}</th>
          <th className="end">{t("reports.netSales")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b) => (
          <tr key={b.key}>
            <td>{b.label}</td>
            <td className="end num">{b.invoices}</td>
            <td className="end num">{money(b.net, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- trial balance

function TrialBalance() {
  const { t, money, amount } = useI18n();
  const accountName = useAccountName();
  const { org } = useOrg();
  const [asOf, setAsOf] = useState("");
  const report = useQuery({
    queryKey: ["report", org.id, "trial", asOf],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/trial-balance", { params: { path: { org_id: org.id }, query: { as_of: asOf || undefined } } })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.asOf")} optional>
          <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} ltr />
        </Field>
      </div>
      <QueryView query={report}>
        {(r) => (
          <div className={`stack ${report.isPlaceholderData ? "refetching" : ""}`}>
            <div>
              <Badge tone={r.balanced ? "success" : "danger"}>{r.balanced ? t("reports.balanced") : t("reports.unbalanced")}</Badge>
            </div>
            {r.lines.length === 0 ? (
              <Empty />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("reports.account")}</th>
                      <th className="end">{t("reports.debit")}</th>
                      <th className="end">{t("reports.credit")}</th>
                      <th className="end">{t("reports.balance")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.lines.map((l) => (
                      <tr key={l.account_id}>
                        <td>
                          <span className="mono">{l.code}</span> {accountName(l.code, l.name)}
                        </td>
                        <td className="end num">{amount(l.debit)}</td>
                        <td className="end num">{amount(l.credit)}</td>
                        <td className="end num">{amount(l.balance)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th>{t("invoices.total")}</th>
                      <th className="end num">{money(r.total_debit, r.currency)}</th>
                      <th className="end num">{money(r.total_credit, r.currency)}</th>
                      <th />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </div>
        )}
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- statements

type StatementLine = { account_id: string; code: string; name: string; amount: string };

function StatementSection({ title, lines, total, currency, extra }: {
  title: string; lines: StatementLine[]; total: string; currency: string; extra?: { label: string; amount: string };
}) {
  const { t, money } = useI18n();
  const accountName = useAccountName();
  return (
    <table className="statement">
      <caption>{title}</caption>
      <tbody>
        {lines.length === 0 && !extra && (
          <tr><td colSpan={2} className="muted small">{t("reports.nothing")}</td></tr>
        )}
        {lines.map((l) => (
          <tr key={l.account_id}>
            <td><span className="mono muted">{l.code}</span> {accountName(l.code, l.name)}</td>
            <td className="end num">{money(l.amount, currency)}</td>
          </tr>
        ))}
        {extra && (
          <tr>
            <td><em>{extra.label}</em></td>
            <td className="end num">{money(extra.amount, currency)}</td>
          </tr>
        )}
      </tbody>
      <tfoot>
        <tr>
          <th>{t("reports.totalOf", { what: title })}</th>
          <th className="end num">{money(total, currency)}</th>
        </tr>
      </tfoot>
    </table>
  );
}

function ProfitAndLoss() {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const startMonth = org.fiscal_year_start_month ?? 1;
  const thisYear = fiscalYear(startMonth, today());
  const lastYear = fiscalYear(startMonth, addDays(thisYear.start, -1));
  const [range, setRange] = useState({ from: thisYear.start, to: today() });
  const report = useQuery({
    queryKey: ["report", org.id, "pnl", range],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/profit-and-loss", {
      params: { path: { org_id: org.id }, query: { date_from: range.from || undefined, date_to: range.to || undefined } },
    })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.from")} optional>
          <Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} ltr />
        </Field>
        <Field label={t("reports.to")} optional>
          <Input type="date" value={range.to} min={range.from} onChange={(e) => setRange({ ...range, to: e.target.value })} ltr />
        </Field>
        <div className="row" style={{ gap: 4, alignSelf: "flex-end" }}>
          <Button size="sm" variant="ghost" aria-pressed={range.from === thisYear.start && range.to === today()}
            onClick={() => setRange({ from: thisYear.start, to: today() })}>{t("reports.thisFiscalYear")}</Button>
          <Button size="sm" variant="ghost" aria-pressed={range.from === lastYear.start && range.to === lastYear.end}
            onClick={() => setRange({ from: lastYear.start, to: lastYear.end })}>{t("reports.lastFiscalYear")}</Button>
        </div>
      </div>
      <QueryView query={report}>
        {(r) => (
          <Card>
            <div className={`statement-sheet ${report.isPlaceholderData ? "refetching" : ""}`}>
              <StatementSection title={t("reports.income")} lines={r.income} total={r.total_income} currency={r.currency} />
              <StatementSection title={t("reports.expenses")} lines={r.expenses} total={r.total_expenses} currency={r.currency} />
              <div className={`statement-result ${r.net_profit.startsWith("-") ? "loss" : "profit"}`}>
                <span>{r.net_profit.startsWith("-") ? t("reports.netLoss") : t("reports.netProfit")}</span>
                <strong className="num">{money(r.net_profit.replace(/^-/, ""), r.currency)}</strong>
              </div>
            </div>
          </Card>
        )}
      </QueryView>
    </div>
  );
}

function BalanceSheet() {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const [asOf, setAsOf] = useState(today());
  const report = useQuery({
    queryKey: ["report", org.id, "balance", asOf],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/balance-sheet", {
      params: { path: { org_id: org.id }, query: { as_of: asOf || undefined } },
    })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.asOf")} optional>
          <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} ltr />
        </Field>
      </div>
      <QueryView query={report}>
        {(r) => (
          <div className={`stack ${report.isPlaceholderData ? "refetching" : ""}`}>
            <div>
              <Badge tone={r.balanced ? "success" : "danger"}>{r.balanced ? t("reports.balanced") : t("reports.unbalanced")}</Badge>
            </div>
            <div className="grid-2 statement-columns">
              <Card>
                <StatementSection title={t("reports.assets")} lines={r.assets} total={r.total_assets} currency={r.currency} />
              </Card>
              <Card>
                <div className="stack">
                  <StatementSection title={t("reports.liabilities")} lines={r.liabilities} total={r.total_liabilities} currency={r.currency} />
                  <StatementSection title={t("reports.equity")} lines={r.equity} total={r.total_equity} currency={r.currency}
                    extra={{ label: t("reports.currentEarnings"), amount: r.current_earnings }} />
                  <div className="statement-result">
                    <span>{t("reports.liabilitiesAndEquity")}</span>
                    <strong className="num">{money(r.total_liabilities_and_equity, r.currency)}</strong>
                  </div>
                </div>
              </Card>
            </div>
          </div>
        )}
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- payables aging

function Payables() {
  const { t, money, date } = useI18n();
  const { org } = useOrg();
  const [asOf, setAsOf] = useState(today());
  const report = useQuery({
    queryKey: ["report", org.id, "payables", asOf],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/reports/payables", { params: { path: { org_id: org.id }, query: { as_of: asOf || undefined } } })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.asOf")}>
          <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} ltr />
        </Field>
      </div>
      <QueryView query={report}>
        {(r) =>
          r.vendors.length === 0 ? (
            <Empty>{t("reports.noPayables")}</Empty>
          ) : (
            <div className={`table-wrap ${report.isPlaceholderData ? "refetching" : ""}`}>
              <table>
                <caption className="sr-only">{t("reports.payables")} · {date(r.as_of)}</caption>
                <thead>
                  <tr>
                    <th>{t("purchases.vendor")}</th>
                    {BUCKETS.map(([k, label]) => <th key={k} className="end">{t(label)}</th>)}
                    <th className="end">{t("reports.totalDue")}</th>
                  </tr>
                </thead>
                <tbody>
                  {r.vendors.map((v) => (
                    <tr key={v.vendor_id}>
                      <td>{v.vendor_name}<span className="muted small"> · {t("reports.billCount", { n: v.bills })}</span></td>
                      {BUCKETS.map(([k]) => (
                        <td key={k} className="end num">{isZero(v.buckets[k] ?? "0") ? "—" : money(v.buckets[k]!, r.currency)}</td>
                      ))}
                      <td className="end num"><strong>{money(v.total_due, r.currency)}</strong></td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <th>{t("invoices.total")}</th>
                    {BUCKETS.map(([k]) => <th key={k} className="end num">{money(r.totals[k] ?? "0", r.currency)}</th>)}
                    <th className="end num">{money(r.total_due, r.currency)}</th>
                  </tr>
                </tfoot>
              </table>
            </div>
          )
        }
      </QueryView>
    </div>
  );
}
