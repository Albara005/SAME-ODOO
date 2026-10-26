import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, CreditCard, FileMinus, FilePlus, Pencil, Plus, Printer, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Journey, journeySteps, LinesEditor, newLine, StatusBadge, toLineIn, TotalsBox, useCustomers, useIdempotencyKey,
  type LineDraft,
} from "../components/business";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select, Textarea, useToast,
} from "../components/ui";
import { CertificateBanner, EInvoiceStatus } from "../components/EInvoicing";
import { PaymentLinksCard } from "../components/OnlinePayments";
import { ZatcaQr } from "../components/ZatcaQr";
import { RecordHistory } from "../components/RecordHistory";
import { useI18n } from "../i18n/I18n";
import { DataTable, type Column } from "../components/DataTable";
import { ListToolbar } from "../components/ListToolbar";
import { digitsFor, isPositive, subtractDec, sumDec } from "../lib/decimal";
import { useDebounced } from "../lib/useDebounced";
import { today } from "../lib/format";

type Invoice = Schemas["InvoiceOut"];
const STATUSES = ["draft", "posted", "partially_paid", "paid", "credited", "cancelled"] as const;
const CURRENCIES = ["SAR", "AED", "QAR", "KWD", "BHD", "OMR", "EGP", "JOD", "IQD", "MAD", "TND", "LYD", "TRY", "USD", "EUR", "GBP", "JPY"];
const CREDITABLE = ["posted", "partially_paid", "paid", "credited"];

function useInvoice(id: string) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["invoice", org.id, id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/invoices/{invoice_id}", { params: { path: { org_id: org.id, invoice_id: id } } })),
  });
}

// ---------------------------------------------------------------- list

type Row = Schemas["InvoiceSummary"];
type Filter = "" | (typeof STATUSES)[number] | "overdue";

/** Every invoice, newest first: quick filters by state (with counts), search by number or customer, sortable columns,
 *  and the totals of what is shown when it is all in one currency. */
export function InvoicesPage() {
  const { t, money, date } = useI18n();
  const { org, can, path } = useOrg();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState<Filter>((params.get("status") as Filter | null) ?? "");
  const [q, setQ] = useState(params.get("q") ?? "");
  const term = useDebounced(q.trim(), 250);
  const invoices = useQuery({
    queryKey: ["invoices", org.id, "list", term],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/invoices", { params: { path: { org_id: org.id }, query: { q: term || undefined, limit: 500 } } })),
    placeholderData: keepPreviousData,
  });
  useEffect(() => {
    const next = new URLSearchParams();
    if (filter) next.set("status", filter);
    if (term) next.set("q", term);
    setParams(next, { replace: true });
  }, [filter, term, setParams]);
  const now = today();
  const overdue = (i: Row) => isPositive(i.amount_due) && !!i.due_date && i.due_date < now && i.status !== "draft" && i.status !== "cancelled";
  const all = invoices.data ?? [];
  const count = (f: Filter) => all.filter((i) => (f === "" ? true : f === "overdue" ? overdue(i) : i.status === f)).length;
  const shown = all.filter((i) => (filter === "" ? true : filter === "overdue" ? overdue(i) : i.status === filter));
  const currencies = new Set(shown.map((i) => i.currency));
  const one = currencies.size === 1 ? [...currencies][0]! : null;
  const columns: Column<Row>[] = [
    {
      key: "number", header: t("invoices.number"), sort: (i) => i.number ?? "",
      cell: (i) => (
        <>
          <Link to={path(`invoices/${i.id}`)} className="cell-strong num">{i.number ?? t("status.draft")}</Link>
          {i.kind === "debit_note" && <> <Badge tone="info">{t("debit.badge")}</Badge></>}
        </>
      ),
    },
    { key: "customer", header: t("invoices.customer"), sort: (i) => i.customer_name, cell: (i) => <span dir="auto">{i.customer_name}</span> },
    { key: "issued", header: t("invoices.issueDate"), sort: (i) => i.issue_date, cell: (i) => <span className="num small">{date(i.issue_date)}</span> },
    {
      key: "due", header: t("invoices.dueDate"), sort: (i) => i.due_date ?? "",
      cell: (i) => <span className={`num small ${overdue(i) ? "text-danger" : ""}`}>{date(i.due_date)}</span>,
    },
    {
      key: "total", header: t("invoices.total"), align: "end", sort: (i) => Number(i.total),
      cell: (i) => <span className="num">{money(i.total, i.currency)}</span>,
      total: one ? <span className="num">{money(sumDec(shown.map((i) => i.total), digitsFor(one)), one)}</span> : undefined,
    },
    {
      key: "amount_due", header: t("invoices.due"), align: "end", sort: (i) => Number(i.amount_due),
      cell: (i) => <span className={`num ${isPositive(i.amount_due) ? "cell-strong" : "muted"}`}>{money(i.amount_due, i.currency)}</span>,
      total: one ? <span className="num">{money(sumDec(shown.map((i) => i.amount_due), digitsFor(one)), one)}</span> : undefined,
    },
    { key: "status", header: t("common.status"), sort: (i) => i.status, cell: (i) => <StatusBadge status={i.status} /> },
  ];
  const filters: { key: Filter; label: string; count?: number }[] = [
    { key: "", label: t("common.all"), count: count("") },
    { key: "draft", label: t("status.draft"), count: count("draft") },
    { key: "posted", label: t("status.posted"), count: count("posted") },
    { key: "partially_paid", label: t("status.partially_paid"), count: count("partially_paid") },
    { key: "overdue", label: t("invoices.overdue"), count: count("overdue") },
    { key: "paid", label: t("status.paid"), count: count("paid") },
    { key: "credited", label: t("status.credited"), count: count("credited") },
    { key: "cancelled", label: t("status.cancelled"), count: count("cancelled") },
  ];
  return (
    <div className="stack">
      <PageHeader
        title={t("invoices.title")}
        actions={
          can("invoices.write") && (
            <Link className="btn btn-primary" to={path("invoices/new")}>
              <Plus size={16} aria-hidden /> {t("invoices.new")}
            </Link>
          )
        }
      />
      <CertificateBanner />
      <ListToolbar
        search={q} onSearch={setQ} placeholder={t("invoices.searchHint")}
        filters={filters.filter((f) => f.key === "" || f.key === filter || (f.count ?? 0) > 0)} filter={filter} onFilter={setFilter}
        shown={invoices.data ? shown.length : undefined}
      />
      <QueryView query={invoices}>
        {() =>
          shown.length === 0 ? (
            <Empty>{term || filter ? t("common.noMatches") : undefined}</Empty>
          ) : (
            <DataTable
              rows={shown} columns={columns} rowKey={(i) => i.id} label={t("invoices.title")}
              onRowClick={(i) => navigate(path(`invoices/${i.id}`))} rowClassName={(i) => (overdue(i) ? "row-overdue" : "")}
            />
          )
        }
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- editor

/** The organization's latest rate for a currency on or before a day (Accounting > Currencies), to suggest on a form. */
function useSuggestedRate(currency: string, day: string, enabled: boolean) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["exchange-rate", org.id, currency, day],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/exchange-rates/latest", {
      params: { path: { org_id: org.id }, query: { currency, on: day } } })),
    enabled: enabled && currency !== org.default_currency && Boolean(day),
    staleTime: 60_000,
  }).data ?? null;
}

function toDraft(inv: Invoice): LineDraft[] {
  return inv.lines.map((l) => ({
    key: l.id,
    product_id: l.product_id ?? "",
    description: l.description,
    quantity: l.quantity,
    unit_price: l.unit_price,
    discount_percent: l.discount_percent,
    tax: l.tax_id ?? "",
  }));
}

export function InvoiceEditorPage() {
  const { invoiceId } = useParams();
  const existing = useInvoice(invoiceId ?? "");
  if (invoiceId && existing.isPending) return <QueryView query={existing}>{() => null}</QueryView>;
  if (invoiceId && existing.isError) return <ErrorNotice error={existing.error} />;
  return <InvoiceEditor invoice={invoiceId ? existing.data : undefined} />;
}

/** A new debit note on a posted invoice: the invoice editor with the invoice's customer and currency. */
export function DebitNoteEditorPage() {
  const { invoiceId = "" } = useParams();
  const original = useInvoice(invoiceId);
  return <QueryView query={original}>{(inv) => <InvoiceEditor debitOf={inv} />}</QueryView>;
}

function InvoiceEditor({ invoice, debitOf }: { invoice?: Invoice; debitOf?: Invoice }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const customers = useCustomers();
  const base = debitOf ?? invoice;
  const debitNote = Boolean(debitOf || invoice?.debit_note_of_id);
  const [header, setHeader] = useState({
    customer_id: base?.customer_id ?? "",
    issue_date: invoice?.issue_date ?? today(),
    due_date: invoice?.due_date ?? "",
    notes: invoice?.notes ?? "",
    currency: base?.currency ?? org.default_currency,
    exchange_rate: base && base.currency !== org.default_currency ? base.exchange_rate : "",
  });
  const [reason, setReason] = useState(invoice?.reason ?? "");
  const foreign = header.currency !== org.default_currency;
  const [rateTouched, setRateTouched] = useState(Boolean(header.exchange_rate));
  const suggested = useSuggestedRate(header.currency, header.issue_date, foreign && !rateTouched);
  useEffect(() => {
    if (suggested && !rateTouched) setHeader((h) => ({ ...h, exchange_rate: suggested.rate }));
  }, [suggested, rateTouched]);
  const [lines, setLines] = useState<LineDraft[]>(invoice ? toDraft(invoice) : [newLine()]);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...header, due_date: header.due_date || null, lines: lines.map(toLineIn),
        exchange_rate: foreign ? header.exchange_rate || null : null,
      };
      if (debitOf) {
        return unwrap(api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/debit-notes", {
          params: { path: { org_id: org.id, invoice_id: debitOf.id } },
          body: { issue_date: header.issue_date, due_date: header.due_date || null, notes: header.notes, reason,
                  lines: body.lines, exchange_rate: body.exchange_rate },
        }));
      }
      return invoice
        ? unwrap(api.PUT("/api/v1/orgs/{org_id}/invoices/{invoice_id}", {
          params: { path: { org_id: org.id, invoice_id: invoice.id } }, body: debitNote ? { ...body, reason } : body }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/invoices", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
      if (saved.debit_note_of_id) void qc.invalidateQueries({ queryKey: ["invoice", org.id, saved.debit_note_of_id] });
      qc.setQueryData(["invoice", org.id, saved.id], saved);
      navigate(path(`invoices/${saved.id}`));
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <form className="stack" onSubmit={submit}>
      <PageHeader title={debitOf ? t("debit.of", { number: debitOf.number ?? "" })
        : invoice?.debit_note_of_id ? t("debit.of", { number: invoice.debit_note_of_number ?? "" })
          : invoice ? t("invoices.edit") : t("invoices.new")} />
      {save.isError && <ErrorNotice error={save.error} />}
      {debitNote && <p className="notice notice-info small">{t("debit.hint")}</p>}
      <Card>
        <div className="grid-2">
          <Field label={t("invoices.customer")} error={err["customer_id"]}>
            <Select value={header.customer_id} disabled={debitNote} onChange={(e) => setHeader({ ...header, customer_id: e.target.value })} required>
              <option value="">{t("invoices.chooseCustomer")}</option>
              {(customers.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("invoices.issueDate")} error={err["issue_date"]}>
            <Input type="date" value={header.issue_date} onChange={(e) => setHeader({ ...header, issue_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.dueDate")} error={err["due_date"]} optional>
            <Input type="date" value={header.due_date} min={header.issue_date} onChange={(e) => setHeader({ ...header, due_date: e.target.value })} ltr />
          </Field>
          <Field label={t("invoices.currency")} hint={foreign ? t("invoices.foreignHint", { currency: header.currency }) : undefined}>
            <Select value={header.currency} disabled={debitNote} onChange={(e) => setHeader({ ...header, currency: e.target.value })}>
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </Select>
          </Field>
          {debitNote && (
            <Field label={t("debit.reason")} hint={t("debit.reasonHint")} error={err["reason"]}>
              <Input value={reason} maxLength={1000} required onChange={(e) => setReason(e.target.value)} />
            </Field>
          )}
          {foreign && (
            <Field label={t("invoices.exchangeRate", { currency: header.currency, base: org.default_currency })} error={err["exchange_rate"]}
              hint={suggested && !rateTouched ? t("fx.rateSuggested", { day: suggested.day }) : undefined}>
              <Input value={header.exchange_rate} inputMode="decimal" required ltr
                onChange={(e) => { setRateTouched(true); setHeader({ ...header, exchange_rate: e.target.value }); }} />
            </Field>
          )}
        </div>
      </Card>
      <Card title={t("invoices.lines")}>
        <LinesEditor lines={lines} onChange={setLines} currency={header.currency} />
      </Card>
      <Field label={t("invoices.notes")} optional>
        <Textarea value={header.notes} onChange={(e) => setHeader({ ...header, notes: e.target.value })} />
      </Field>
      <div className="form-actions">
        <Button onClick={() => navigate(-1)}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" loading={save.isPending}>
          {t("invoices.saveDraft")}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- detail

export function InvoiceDetailPage() {
  const { invoiceId = "" } = useParams();
  const invoice = useInvoice(invoiceId);
  return <QueryView query={invoice}>{(inv) => <InvoiceDetail invoice={inv} />}</QueryView>;
}

function InvoiceDetail({ invoice: inv }: { invoice: Invoice }) {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [paying, setPaying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [crediting, setCrediting] = useState(false);
  const ids = { org_id: org.id, invoice_id: inv.id };
  const refresh = (updated?: Invoice) => {
    if (updated) qc.setQueryData(["invoice", org.id, inv.id], updated);
    void qc.invalidateQueries({ queryKey: ["invoice", org.id, inv.id] });
    void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
    void qc.invalidateQueries({ queryKey: ["home", org.id] });
    if (inv.debit_note_of_id) void qc.invalidateQueries({ queryKey: ["invoice", org.id, inv.debit_note_of_id] });
  };
  const onError = (e: Error) => toast(e instanceof ApiError ? e.message : t("common.error"), "error");
  const onlinePaid = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["invoice", org.id, inv.id] });
    void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
    void qc.invalidateQueries({ queryKey: ["payments", org.id, inv.id] });
  }, [qc, org.id, inv.id]);

  const post = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/post", { params: { path: ids } })),
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/orgs/{org_id}/invoices/{invoice_id}", { params: { path: ids } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
      navigate(path("invoices"));
    },
    onError,
  });
  const payments = useQuery({
    queryKey: ["payments", org.id, inv.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/invoices/{invoice_id}/payments", { params: { path: ids } })),
    enabled: inv.status !== "draft",
  });

  const isDraft = inv.status === "draft";
  const canPay = can("payments.write") && (inv.status === "posted" || inv.status === "partially_paid");
  const credited = isPositive(inv.amount_credited);
  const canDebit = can("invoices.write") && !inv.debit_note_of_id && ["posted", "partially_paid", "paid", "credited"].includes(inv.status);
  const canCredit = can("invoices.credit") && CREDITABLE.includes(inv.status)
    && inv.lines.some((l) => isPositive(subtractDec(l.quantity, l.qty_credited)));

  return (
    <div className="stack">
      <PageHeader
        title={inv.number ?? t("invoices.draftNoNumber")}
        subtitle={
          <span className="row">
            <StatusBadge status={inv.status} />
            {inv.kind === "debit_note" && <Badge tone="info">{t("debit.badge")}</Badge>}
            <span>{inv.customer_name}</span>
          </span>
        }
        actions={
          <div className="row no-print">
            {isDraft && can("invoices.write") && (
              <>
                {!inv.sales_order_id && (
                  <Link className="btn" to={path(`invoices/${inv.id}/edit`)}>
                    <Pencil size={16} aria-hidden /> {t("common.edit")}
                  </Link>
                )}
                <Button variant="danger" icon={<Trash2 size={16} />} loading={remove.isPending}
                  onClick={() => window.confirm(t("invoices.deleteConfirm")) && remove.mutate()}>
                  {t("common.delete")}
                </Button>
              </>
            )}
            {isDraft && can("invoices.post") && (
              <Button variant="primary" icon={<CheckCircle2 size={16} />} loading={post.isPending}
                onClick={() => window.confirm(t("invoices.postConfirm")) && post.mutate()}>
                {t("invoices.post")}
              </Button>
            )}
            {canPay && (
              <Button variant="primary" icon={<CreditCard size={16} />} onClick={() => setPaying(true)}>
                {t("invoices.pay")}
              </Button>
            )}
            {canCredit && (
              <Button icon={<FileMinus size={16} />} onClick={() => setCrediting(true)}>
                {t("credit.new")}
              </Button>
            )}
            {canDebit && (
              <Link className="btn" to={path(`invoices/${inv.id}/debit-note`)}>
                <FilePlus size={16} aria-hidden /> {t("debit.new")}
              </Link>
            )}
            {inv.status === "posted" && !credited && can("invoices.cancel") && (
              <Button variant="danger" icon={<Ban size={16} />} onClick={() => setCancelling(true)}>
                {t("invoices.cancel")}
              </Button>
            )}
            <Button icon={<Printer size={16} />} onClick={() => window.print()}>
              {t("invoices.print")}
            </Button>
          </div>
        }
      />
      {isDraft && <div className="no-print"><CertificateBanner /></div>}
      {inv.status !== "cancelled" && (
        <div className="no-print">
          <Journey
            label={t("common.progress")}
            steps={journeySteps(
              [t("invoices.stepDraft"), t("invoices.stepPost"), t("invoices.stepPaid")],
              isDraft ? 0 : 2,
              inv.status === "paid" || inv.status === "credited",
            )}
            />
        </div>
      )}
      {isDraft && inv.sales_order_id && <div className="notice notice-info no-print">{t("invoices.fromOrder")}</div>}
      <Card>
        <div className="grid-2">
          <dl className="kv">
            <dt>{t("invoices.customer")}</dt>
            <dd>{inv.customer_name}</dd>
            <dt>{t("invoices.issueDate")}</dt>
            <dd className="num">{date(inv.issue_date)}</dd>
            <dt>{t("invoices.dueDate")}</dt>
            <dd className="num">{date(inv.due_date)}</dd>
            {inv.debit_note_of_id && (
              <>
                <dt>{t("debit.adjusts")}</dt>
                <dd><Link className="mono" to={path(`invoices/${inv.debit_note_of_id}`)}>{inv.debit_note_of_number}</Link></dd>
                <dt>{t("debit.reason")}</dt>
                <dd dir="auto">{inv.reason}</dd>
              </>
            )}
            {inv.sales_order_id && (
              <>
                <dt>{t("invoices.salesOrder")}</dt>
                <dd>
                  <Link className="mono" to={path(`sales/${inv.sales_order_id}`)}>{inv.sales_order_number}</Link>
                </dd>
              </>
            )}
          </dl>
          <TotalsBox
            subtotal={money(inv.subtotal, inv.currency)}
            tax={money(inv.tax, inv.currency)}
            total={money(inv.total, inv.currency)}
            extra={
              !isDraft && (
                <>
                  <dt>{t("invoices.paid")}</dt>
                  <dd className="num" style={{ textAlign: "end" }}>{money(inv.amount_paid, inv.currency)}</dd>
                  {credited && (
                    <>
                      <dt>{t("credit.credited")}</dt>
                      <dd className="num" style={{ textAlign: "end" }}>{money(inv.amount_credited, inv.currency)}</dd>
                    </>
                  )}
                  <dt>{t("invoices.due")}</dt>
                  <dd className="num" style={{ textAlign: "end" }}><strong>{money(inv.amount_due, inv.currency)}</strong></dd>
                  {inv.base_total && (
                    <>
                      <dt>{t("invoices.rateBooked", { currency: inv.currency, base: inv.base_currency })}</dt>
                      <dd className="num" style={{ textAlign: "end" }}>{inv.exchange_rate}</dd>
                      <dt>{t("invoices.inBase", { base: inv.base_currency })}</dt>
                      <dd className="num" style={{ textAlign: "end" }}>{money(inv.base_total, inv.base_currency)}</dd>
                      <dt>{t("invoices.dueInBase", { base: inv.base_currency })}</dt>
                      <dd className="num" style={{ textAlign: "end" }}>{money(inv.base_due ?? "0", inv.base_currency)}</dd>
                    </>
                  )}
                </>
              )
            }
          />
        </div>
        {inv.zatca_qr && <ZatcaQr payload={inv.zatca_qr} />}
      </Card>
      {inv.einvoice && <div className="no-print"><EInvoiceStatus doc={inv.einvoice} onChange={() => refresh()} /></div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("invoices.description")}</th>
              <th className="end">{t("invoices.qty")}</th>
              <th className="end">{t("invoices.unitPrice")}</th>
              <th className="end">{t("invoices.discount")}</th>
              <th className="end">{t("invoices.taxCol")}</th>
              <th className="end">{t("invoices.net")}</th>
            </tr>
          </thead>
          <tbody>
            {inv.lines.map((l) => (
              <tr key={l.id}>
                <td>{l.description}</td>
                <td className="end num">{amount(l.quantity)}</td>
                <td className="end num">{money(l.unit_price, inv.currency)}</td>
                <td className="end num">{l.discount_percent === "0" ? "—" : `${l.discount_percent}%`}</td>
                <td className="end num">{l.tax_rate === "0" ? "—" : `${l.tax_rate}%`}</td>
                <td className="end num">{money(l.net_amount, inv.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {inv.taxes.length > 0 && (
        <Card title={t("invoices.taxes")} tight>
          <dl className="kv">
            {inv.taxes.map((x) => (
              <div key={x.tax_id} style={{ display: "contents" }}>
                <dt>
                  <bdi>{x.name}</bdi> ({x.rate}%) · {money(x.base, inv.currency)}
                </dt>
                <dd className="num">{money(x.amount, inv.currency)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      )}
      {inv.notes && (
        <Card tight>
          <p style={{ whiteSpace: "pre-wrap" }}>{inv.notes}</p>
        </Card>
      )}
      {inv.debit_notes.length > 0 && (
        <Card title={t("debit.title")} tight>
          <ul className="stack-sm" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {inv.debit_notes.map((n) => (
              <li key={n.id} className="row-between">
                <Link className="mono" to={path(`invoices/${n.id}`)}>{n.number ?? t("status.draft")}</Link>
                <StatusBadge status={n.status} />
                <span className="small muted num">{date(n.issue_date)}</span>
                <span className="num">{money(n.total, inv.currency)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {inv.credit_notes.length > 0 && (
        <Card title={t("credit.title")} tight>
          <ul className="stack-sm" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {inv.credit_notes.map((n) => (
              <li key={n.id} className="row-between">
                <Link className="mono" to={path(`credit-notes/${n.id}`)}>{n.number}</Link>
                <span className="small muted num">{date(n.issue_date)}</span>
                <span className="num">{money(n.total, inv.currency)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {!isDraft && (
        <Card title={t("invoices.payments")}>
          <QueryView query={payments}>
            {(list) =>
              list.length === 0 ? (
                <Empty />
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>{t("invoices.paymentDate")}</th>
                      <th>{t("invoices.method")}</th>
                      <th>{t("invoices.reference")}</th>
                      <th className="end">{t("invoices.amount")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((p) => (
                      <tr key={p.id}>
                        <td className="num">{date(p.payment_date)}</td>
                        <td>{p.method === "bank" ? t("invoices.bank") : p.method === "online" ? t("invoices.online") : t("invoices.cash")}</td>
                        <td>
                          {p.reference}
                          {p.base_amount && (
                            <div className="small muted num">
                              {t("invoices.paidInBase", { amount: money(p.base_amount, inv.base_currency), rate: p.exchange_rate })}
                              {p.exchange_difference && p.exchange_difference !== "0.00" &&
                                ` · ${t("invoices.exchangeDifference")} ${money(p.exchange_difference, inv.base_currency)}`}
                            </div>
                          )}
                        </td>
                        <td className="end num">{money(p.amount, inv.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            }
          </QueryView>
        </Card>
      )}
      {!isDraft && <div className="no-print"><PaymentLinksCard invoice={inv} onPaid={onlinePaid} /></div>}
      {paying && (
        <PaymentDialog invoice={inv} onClose={() => setPaying(false)} onDone={() => {
          setPaying(false);
          refresh();
          void qc.invalidateQueries({ queryKey: ["payments", org.id, inv.id] });
        }} />
      )}
      {cancelling && <CancelDialog invoice={inv} onClose={() => setCancelling(false)} onDone={(u) => { setCancelling(false); refresh(u); }} />}
      {crediting && (
        <CreditNoteDialog invoice={inv} onClose={() => setCrediting(false)} onDone={(noteId) => {
          refresh();
          void qc.invalidateQueries({ queryKey: ["stock", org.id] });
          navigate(path(`credit-notes/${noteId}`));
        }} />
      )}
      <RecordHistory kind="invoice" id={inv.id} version={`${inv.status}:${inv.amount_due}:${inv.amount_credited}`} />
    </div>
  );
}

/** Credit some units of some lines; goods that came from a sales order can come back into stock. */
function CreditNoteDialog({ invoice, onClose, onDone }: { invoice: Invoice; onClose: () => void; onDone: (noteId: string) => void }) {
  const { t, amount } = useI18n();
  const { org, can } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const open = invoice.lines
    .map((l) => ({ ...l, open: subtractDec(l.quantity, l.qty_credited) }))
    .filter((l) => isPositive(l.open));
  const canReturn = Boolean(invoice.sales_order_id) && can("inventory.write");
  const [form, setForm] = useState({ issue_date: today(), reason: "" });
  const [qty, setQty] = useState<Record<string, string>>({});
  const [back, setBack] = useState<Record<string, string>>({});
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/credit-notes", {
        params: { path: { org_id: org.id, invoice_id: invoice.id }, header: { "idempotency-key": idempotencyKey } },
        body: {
          ...form,
          lines: open.filter((l) => isPositive(qty[l.id] ?? "")).map((l) => ({
            invoice_line_id: l.id, quantity: qty[l.id] ?? "", returned_quantity: isPositive(back[l.id] ?? "") ? back[l.id]! : "0",
          })),
        },
      })),
    onSuccess: (n) => onDone(n.id),
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("credit.new")} wide>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <p className="muted">{t("credit.hint")}</p>
        {create.isError && !err["reason"] && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("credit.date")}>
            <Input type="date" value={form.issue_date} min={invoice.issue_date} onChange={(e) => setForm({ ...form, issue_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("credit.reason")} error={err["reason"]}>
            <Input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} required />
          </Field>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("invoices.description")}</th>
                <th className="end">{t("credit.openQty")}</th>
                <th style={{ width: 120 }}>{t("credit.qty")}</th>
                {canReturn && <th style={{ width: 120 }}>{t("credit.returned")}</th>}
              </tr>
            </thead>
            <tbody>
              {open.map((l) => (
                <tr key={l.id}>
                  <td>{l.description}</td>
                  <td className="end num">{amount(l.open)}</td>
                  <td>
                    <Input aria-label={`${t("credit.qty")} ${l.description}`} inputMode="decimal" value={qty[l.id] ?? ""}
                      placeholder="0" onChange={(e) => setQty({ ...qty, [l.id]: e.target.value })} ltr />
                  </td>
                  {canReturn && (
                    <td>
                      {l.product_id && (
                        <Input aria-label={`${t("credit.returned")} ${l.description}`} inputMode="decimal" value={back[l.id] ?? ""}
                          placeholder="0" onChange={(e) => setBack({ ...back, [l.id]: e.target.value })} ltr />
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canReturn && <p className="small muted">{t("credit.returnHint")}</p>}
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("credit.issue")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function PaymentDialog({ invoice, onClose, onDone }: { invoice: Invoice; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const foreign = invoice.currency !== invoice.base_currency;
  const [form, setForm] = useState({ amount: invoice.amount_due, payment_date: today(), method: "bank" as "bank" | "cash", reference: "",
    exchange_rate: foreign ? invoice.exchange_rate : "" });
  const [rateTouched, setRateTouched] = useState(false);
  const suggested = useSuggestedRate(invoice.currency, form.payment_date, foreign && !rateTouched);
  useEffect(() => {
    if (suggested && !rateTouched) setForm((f) => ({ ...f, exchange_rate: suggested.rate }));
  }, [suggested, rateTouched]);
  const pay = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/payments", {
          params: { path: { org_id: org.id, invoice_id: invoice.id }, header: { "idempotency-key": idempotencyKey } },
          body: { ...form, exchange_rate: foreign ? form.exchange_rate : null },
        }),
      ),
    onSuccess: onDone,
  });
  const err = pay.error instanceof ApiError ? pay.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("invoices.pay")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); pay.mutate(); }}>
        {pay.isError && !Object.keys(err).length && <ErrorNotice error={pay.error} />}
        <div className="grid-2">
          <Field label={`${t("invoices.amount")} (${invoice.currency})`} error={err["amount"]}>
            <Input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.paymentDate")} error={err["payment_date"]}>
            <Input type="date" value={form.payment_date} onChange={(e) => setForm({ ...form, payment_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.method")}>
            <Select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value as "bank" | "cash" })}>
              <option value="bank">{t("invoices.bank")}</option>
              <option value="cash">{t("invoices.cash")}</option>
            </Select>
          </Field>
          <Field label={t("invoices.reference")} optional>
            <Input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />
          </Field>
          {foreign && (
            <Field label={t("invoices.paymentRate", { currency: invoice.currency, base: invoice.base_currency })}
              hint={suggested && !rateTouched ? `${t("fx.rateSuggested", { day: suggested.day })} · ${t("invoices.paymentRateHint", { rate: invoice.exchange_rate })}`
                : t("invoices.paymentRateHint", { rate: invoice.exchange_rate })} error={err["exchange_rate"]}>
              <Input value={form.exchange_rate} inputMode="decimal" required ltr
                onChange={(e) => { setRateTouched(true); setForm({ ...form, exchange_rate: e.target.value }); }} />
            </Field>
          )}
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={pay.isPending}>
            {t("invoices.pay")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function CancelDialog({ invoice, onClose, onDone }: { invoice: Invoice; onClose: () => void; onDone: (u: Invoice) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [cancelDate, setCancelDate] = useState(today());
  const cancel = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/cancel", {
        params: { path: { org_id: org.id, invoice_id: invoice.id } }, body: { cancel_date: cancelDate },
      })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("invoices.cancel")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); cancel.mutate(); }}>
        <p className="muted">{t("invoices.cancelHint")}</p>
        {cancel.isError && <ErrorNotice error={cancel.error} />}
        <Field label={t("invoices.cancelDate")}>
          <Input type="date" value={cancelDate} min={invoice.issue_date} onChange={(e) => setCancelDate(e.target.value)} required ltr />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.close")}</Button>
          <Button type="submit" variant="danger" loading={cancel.isPending}>
            {t("invoices.cancel")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
