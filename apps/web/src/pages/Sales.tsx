import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, FileText, Globe, Pencil, Plus, Truck, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  CurrencyField, Journey, journeySteps, LinesEditor, newLine, RateField, StatusBadge, toLineIn, TotalsBox, useCustomers,
  useIdempotencyKey, useRate, useWarehouses, type LineDraft,
} from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select, Textarea, errorText,
  useToast,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { RecordHistory } from "../components/RecordHistory";
import { useI18n } from "../i18n/I18n";
import { DataTable, type Column } from "../components/DataTable";
import { ListToolbar } from "../components/ListToolbar";
import { digitsFor, isPositive, sumDec } from "../lib/decimal";
import { useDebounced } from "../lib/useDebounced";
import { today } from "../lib/format";

type Order = Schemas["OrderOut"];
type WebOrder = Schemas["WebOrderOut"];
const STATUSES = ["quotation", "confirmed", "cancelled"] as const;

function useOrder(id: string) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["order", org.id, id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/sales-orders/{order_id}", { params: { path: { org_id: org.id, order_id: id } } })),
    enabled: Boolean(id),   // a new one has nothing to load
  });
}

// ---------------------------------------------------------------- list

type OrderRow = Schemas["OrderSummary"];
type OrderFilter = "" | (typeof STATUSES)[number];

export function SalesOrdersPage() {
  const { t, money, date } = useI18n();
  const { org, can, path } = useOrg();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState<OrderFilter>((params.get("status") as OrderFilter | null) ?? "");
  const [q, setQ] = useState(params.get("q") ?? "");
  const term = useDebounced(q.trim(), 250);
  const orders = useQuery({
    queryKey: ["orders", org.id, "list", term],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/sales-orders", { params: { path: { org_id: org.id }, query: { q: term || undefined, limit: 500 } } })),
    placeholderData: keepPreviousData,
  });
  useEffect(() => {
    const next = new URLSearchParams();
    if (filter) next.set("status", filter);
    if (term) next.set("q", term);
    setParams(next, { replace: true });
  }, [filter, term, setParams]);
  const all = orders.data ?? [];
  const shown = filter ? all.filter((o) => o.status === filter) : all;
  const currencies = new Set(shown.map((o) => o.currency));
  const one = currencies.size === 1 ? [...currencies][0]! : null;   // amounts in two currencies don't add up
  const columns: Column<OrderRow>[] = [
    { key: "number", header: t("invoices.number"), sort: (o) => o.number,
      cell: (o) => <Link to={path(`sales/${o.id}`)} className="cell-strong num">{o.number}</Link> },
    { key: "customer", header: t("invoices.customer"), sort: (o) => o.customer_name, cell: (o) => <span dir="auto">{o.customer_name}</span> },
    { key: "date", header: t("sales.orderDate"), sort: (o) => o.order_date, cell: (o) => <span className="num small">{date(o.order_date)}</span> },
    { key: "total", header: t("invoices.total"), align: "end", sort: (o) => Number(o.total),
      cell: (o) => <span className="num">{money(o.total, o.currency)}</span>,
      total: one ? <span className="num">{money(sumDec(shown.map((o) => o.total), digitsFor(one)), one)}</span> : undefined },
    { key: "status", header: t("common.status"), sort: (o) => o.status, cell: (o) => <StatusBadge status={o.status} /> },
  ];
  return (
    <div className="stack">
      <PageHeader
        title={t("sales.title")}
        actions={
          can("sales.write") && (
            <Link className="btn btn-primary" to={path("sales/new")}>
              <Plus size={16} aria-hidden /> {t("sales.new")}
            </Link>
          )
        }
      />
      <WebOrders />
      <ListToolbar
        search={q} onSearch={setQ} placeholder={t("invoices.searchHint")}
        filters={[{ key: "" as OrderFilter, label: t("common.all"), count: all.length },
          ...STATUSES.map((st) => ({ key: st as OrderFilter, label: t(`status.${st}`), count: all.filter((o) => o.status === st).length }))]}
        filter={filter} onFilter={setFilter} shown={orders.data ? shown.length : undefined}
      />
      <QueryView query={orders}>
        {() =>
          shown.length === 0 ? (
            <Empty>{term || filter ? t("common.noMatches") : undefined}</Empty>
          ) : (
            <DataTable rows={shown} columns={columns} rowKey={(o) => o.id} label={t("sales.title")}
              onRowClick={(o) => navigate(path(`sales/${o.id}`))} />
          )
        }
      </QueryView>
    </div>
  );
}

/** Order requests sent by a published App Studio site: a person turns each into a quotation, or dismisses it. */
function WebOrders() {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const requests = useQuery({
    queryKey: ["web-orders", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/web-orders", { params: { path: { org_id: org.id }, query: { status: "open" } } })),
  });
  const onError = (e: Error) => toast(errorText(e, t("common.error")), "error");
  const convert = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/orgs/{org_id}/web-orders/{web_order_id}/convert", { params: { path: { org_id: org.id, web_order_id: id } } })),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["web-orders", org.id] });
      void qc.invalidateQueries({ queryKey: ["orders", org.id] });
      navigate(path(`sales/${r.sales_order_id}`));
    },
    onError,
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/orgs/{org_id}/web-orders/{web_order_id}/dismiss", { params: { path: { org_id: org.id, web_order_id: id } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["web-orders", org.id] }),
    onError,
  });
  const list = requests.data ?? [];
  if (list.length === 0) return null;
  const act = can("sales.write") && can("customers.write");
  return (
    <Card title={t("webOrders.title", { n: list.length })}>
      <p className="muted small">{t("webOrders.hint")}</p>
      <div className="stack-sm">
        {list.map((w: WebOrder) => (
          <div key={w.id} className="web-order">
            <div className="row-between">
              <span className="row">
                <Globe size={16} aria-hidden className="muted" />
                <strong className="mono">{w.number}</strong>
                <span dir="auto">{w.customer_name}</span>
                <span className="muted small" dir="ltr">{[w.email, w.phone].filter(Boolean).join(" · ")}</span>
                <Badge>{w.project_name}</Badge>
                {w.status === "awaiting_payment" && <Badge tone="warning">{t("webOrders.awaitingPayment")}</Badge>}
              </span>
              <span className="muted small num">{date(w.created_at)}</span>
            </div>
            <ul className="small">
              {w.lines.map((l, i) => <li key={i}><span className="num">{amount(l.quantity)}</span> × <span dir="auto">{l.name}</span></li>)}
            </ul>
            {w.note && <p className="small" dir="auto">{w.note}</p>}
            <div className="row-between">
              <strong className="num">{money(w.total, w.currency)}</strong>
              {act && (
                <span className="row">
                  <Button size="sm" icon={<X size={14} />} loading={dismiss.isPending && dismiss.variables === w.id}
                    onClick={() => window.confirm(t("webOrders.dismissConfirm", { number: w.number })) && dismiss.mutate(w.id)}>
                    {t("webOrders.dismiss")}
                  </Button>
                  <Button size="sm" variant="primary" icon={<FileText size={14} />} loading={convert.isPending && convert.variables === w.id}
                    onClick={() => convert.mutate(w.id)}>
                    {t("webOrders.convert")}
                  </Button>
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- editor

function toDraft(order: Order): LineDraft[] {
  return order.lines.map((l) => ({
    key: l.id,
    product_id: l.product_id ?? "",
    description: l.description,
    quantity: l.quantity,
    unit_price: l.unit_price,
    discount_percent: l.discount_percent,
    tax: l.tax_id ?? "",
  }));
}

export function SalesOrderEditorPage() {
  const { orderId } = useParams();
  const existing = useOrder(orderId ?? "");
  if (orderId && existing.isPending) return <QueryView query={existing}>{() => null}</QueryView>;
  if (orderId && existing.isError) return <ErrorNotice error={existing.error} />;
  return <OrderEditor order={orderId ? existing.data : undefined} />;
}

function OrderEditor({ order }: { order?: Order }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const customers = useCustomers();
  const warehouses = useWarehouses();
  const [header, setHeader] = useState({
    customer_id: order?.customer_id ?? "",
    order_date: order?.order_date ?? today(),
    valid_until: order?.valid_until ?? "",
    warehouse_id: order?.warehouse_id ?? "",
    notes: order?.notes ?? "",
    currency: order?.currency ?? org.default_currency,
  });
  const [lines, setLines] = useState<LineDraft[]>(order ? toDraft(order) : [newLine()]);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...header,
        valid_until: header.valid_until || null,
        warehouse_id: header.warehouse_id || null,
        lines: lines.map(toLineIn),
      };
      return order
        ? unwrap(api.PUT("/api/v1/orgs/{org_id}/sales-orders/{order_id}", { params: { path: { org_id: org.id, order_id: order.id } }, body }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/sales-orders", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["orders", org.id] });
      qc.setQueryData(["order", org.id, saved.id], saved);
      navigate(path(`sales/${saved.id}`));
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <form className="stack" onSubmit={submit}>
      <PageHeader title={order ? t("sales.edit") : t("sales.new")} />
      {save.isError && <ErrorNotice error={save.error} />}
      <Card>
        <div className="grid-2">
          <Field label={t("invoices.customer")} error={err["customer_id"]}>
            <Select value={header.customer_id} onChange={(e) => setHeader({ ...header, customer_id: e.target.value })} required>
              <option value="">{t("invoices.chooseCustomer")}</option>
              {(customers.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("sales.orderDate")} error={err["order_date"]}>
            <Input type="date" value={header.order_date} onChange={(e) => setHeader({ ...header, order_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("sales.validUntil")} error={err["valid_until"]} optional>
            <Input type="date" value={header.valid_until} min={header.order_date} onChange={(e) => setHeader({ ...header, valid_until: e.target.value })} ltr />
          </Field>
          <CurrencyField value={header.currency} onChange={(currency) => setHeader({ ...header, currency })} />
          {warehouses.data && warehouses.data.length > 1 && (
            <Field label={t("inventory.warehouse")} optional>
              <Select value={header.warehouse_id} onChange={(e) => setHeader({ ...header, warehouse_id: e.target.value })}>
                <option value="">{t("sales.defaultWarehouse")}</option>
                {warehouses.data.filter((w) => w.active).map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.code} · {w.name}
                  </option>
                ))}
              </Select>
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
          {t("sales.save")}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- detail

export function SalesOrderDetailPage() {
  const { orderId = "" } = useParams();
  const order = useOrder(orderId);
  return <QueryView query={order}>{(o) => <OrderDetail order={o} />}</QueryView>;
}

function OrderDetail({ order: o }: { order: Order }) {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [delivering, setDelivering] = useState(false);
  const [invoicing, setInvoicing] = useState(false);
  const ids = { org_id: org.id, order_id: o.id };
  const refresh = (updated?: Order) => {
    if (updated) qc.setQueryData(["order", org.id, o.id], updated);
    void qc.invalidateQueries({ queryKey: ["order", org.id, o.id] });
    void qc.invalidateQueries({ queryKey: ["orders", org.id] });
  };
  const onError = (e: Error) => toast(e instanceof ApiError ? e.message : t("common.error"), "error");

  const confirm = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/sales-orders/{order_id}/confirm", { params: { path: ids } })),
    onSuccess: refresh,
    onError,
  });
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/sales-orders/{order_id}/cancel", { params: { path: ids } })),
    onSuccess: refresh,
    onError,
  });

  const confirmed = o.status === "confirmed";
  const hasProgress = o.lines.some((l) => isPositive(l.qty_delivered) || isPositive(l.qty_invoiced));
  const canDeliver = confirmed && can("inventory.write") && o.lines.some((l) => isPositive(l.qty_to_deliver));
  const canInvoice = confirmed && can("invoices.write") && o.lines.some((l) => isPositive(l.qty_to_invoice));

  return (
    <div className="stack">
      <PageHeader
        title={o.number}
        subtitle={
          <span className="row">
            <StatusBadge status={o.status} />
            <span>{o.customer_name}</span>
          </span>
        }
        actions={
          <div className="row no-print">
            {o.status === "quotation" && can("sales.write") && (
              <>
                <Link className="btn" to={path(`sales/${o.id}/edit`)}>
                  <Pencil size={16} aria-hidden /> {t("common.edit")}
                </Link>
                <Button variant="primary" icon={<CheckCircle2 size={16} />} loading={confirm.isPending} onClick={() => confirm.mutate()}>
                  {t("sales.confirm")}
                </Button>
              </>
            )}
            {canDeliver && (
              <Button variant="primary" icon={<Truck size={16} />} onClick={() => setDelivering(true)}>
                {t("sales.deliver")}
              </Button>
            )}
            {canInvoice && (
              <Button variant="primary" icon={<FileText size={16} />} onClick={() => setInvoicing(true)}>
                {t("sales.invoice")}
              </Button>
            )}
            {o.status !== "cancelled" && !hasProgress && can("sales.write") && (
              <Button variant="danger" icon={<Ban size={16} />} loading={cancel.isPending}
                onClick={() => window.confirm(t("sales.cancelConfirm")) && cancel.mutate()}>
                {t("sales.cancel")}
              </Button>
            )}
          </div>
        }
      />
      {o.status !== "cancelled" && (
        <Journey label={t("common.progress")} steps={orderJourney(o, t)} />
      )}
      <Card>
        <div className="grid-2">
          <dl className="kv">
            <dt>{t("invoices.customer")}</dt>
            <dd>{o.customer_name}</dd>
            <dt>{t("sales.orderDate")}</dt>
            <dd className="num">{date(o.order_date)}</dd>
            <dt>{t("sales.validUntil")}</dt>
            <dd className="num">{date(o.valid_until)}</dd>
            {confirmed && (
              <>
                <dt>{t("sales.delivery")}</dt>
                <dd><StatusBadge status={o.delivery_status} /></dd>
                <dt>{t("sales.invoicing")}</dt>
                <dd><StatusBadge status={o.invoice_status} /></dd>
              </>
            )}
          </dl>
          <TotalsBox subtotal={money(o.subtotal, o.currency)} tax={money(o.tax, o.currency)} total={money(o.total, o.currency)} />
        </div>
      </Card>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("invoices.description")}</th>
              <th className="end">{t("invoices.qty")}</th>
              <th className="end">{t("invoices.unitPrice")}</th>
              <th className="end">{t("invoices.net")}</th>
              {confirmed && (
                <>
                  <th className="end">{t("sales.delivered")}</th>
                  <th className="end">{t("sales.reservedQty")}</th>
                  <th className="end">{t("sales.invoicedQty")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {o.lines.map((l) => (
              <tr key={l.id}>
                <td>{l.description}</td>
                <td className="end num">{amount(l.quantity)}</td>
                <td className="end num">{money(l.unit_price, o.currency)}</td>
                <td className="end num">{money(l.net_amount, o.currency)}</td>
                {confirmed && (
                  <>
                    <td className="end num">{amount(l.qty_delivered)}</td>
                    <td className={`end num ${isPositive(l.qty_to_deliver) && l.qty_reserved !== l.qty_to_deliver ? "short" : ""}`}
                      title={isPositive(l.qty_to_deliver) ? t("sales.reservedHint", { n: amount(l.qty_to_deliver) }) : undefined}>
                      {isPositive(l.qty_to_deliver) ? amount(l.qty_reserved) : "—"}
                    </td>
                    <td className="end num">{amount(l.qty_invoiced)}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {o.notes && (
        <Card tight>
          <p style={{ whiteSpace: "pre-wrap" }}>{o.notes}</p>
        </Card>
      )}
      {delivering && (
        <DeliveryDialog order={o} onClose={() => setDelivering(false)} onDone={(ref) => {
          setDelivering(false);
          toast(t("sales.delivered_ok", { ref }));
          refresh();
          void qc.invalidateQueries({ queryKey: ["stock", org.id] });
        }} />
      )}
      {invoicing && (
        <InvoiceFromOrderDialog order={o} onClose={() => setInvoicing(false)} onDone={(invoiceId) => {
          refresh();
          void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
          navigate(path(`invoices/${invoiceId}`));
        }} />
      )}
      <RecordHistory kind="sales_order" id={o.id} version={`${o.status}:${JSON.stringify(o.lines.map((l) => [l.qty_delivered, l.qty_invoiced]))}`} />
    </div>
  );
}

function orderJourney(o: Order, t: (k: MessageKey) => string) {
  const labels = [t("sales.stepQuote"), t("sales.stepConfirm"), t("sales.stepDeliver"), t("sales.stepInvoice")];
  if (o.status === "quotation") return journeySteps(labels, 0);
  if (o.invoice_status === "invoiced") return journeySteps(labels, 3, true);
  const shipped = o.delivery_status === "delivered" || o.delivery_status === "not_applicable";
  return journeySteps(labels, shipped ? 3 : 2);
}

function DeliveryDialog({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (reference: string) => void }) {
  const { t, amount } = useI18n();
  const { org } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const open = order.lines.filter((l) => isPositive(l.qty_to_deliver));
  const [all, setAll] = useState(true);
  const [moveDate, setMoveDate] = useState(today());
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(open.map((l) => [l.id, l.qty_to_deliver])));
  const deliver = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/orgs/{org_id}/sales-orders/{order_id}/deliveries", {
          params: { path: { org_id: org.id, order_id: order.id }, header: { "idempotency-key": idempotencyKey } },
          body: {
            move_date: moveDate,
            lines: all ? null : open.filter((l) => isPositive(qty[l.id] ?? "")).map((l) => ({ line_id: l.id, quantity: qty[l.id] ?? "" })),
          },
        }),
      ),
    onSuccess: (r) => onDone(r.reference),
  });
  return (
    <Dialog open onClose={onClose} title={t("sales.deliver")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); deliver.mutate(); }}>
        {deliver.isError && <ErrorNotice error={deliver.error} />}
        <Field label={t("sales.deliveryDate")}>
          <Input type="date" value={moveDate} min={order.order_date} onChange={(e) => setMoveDate(e.target.value)} required ltr />
        </Field>
        <Checkbox label={t("sales.deliverAll")} checked={all} onChange={(e) => setAll(e.target.checked)} />
        {!all && (
          <table>
            <thead>
              <tr>
                <th>{t("invoices.description")}</th>
                <th className="end">{t("sales.toDeliver")}</th>
                <th style={{ width: 120 }}>{t("invoices.qty")}</th>
              </tr>
            </thead>
            <tbody>
              {open.map((l) => (
                <tr key={l.id}>
                  <td>{l.description}</td>
                  <td className="end num">{amount(l.qty_to_deliver)}</td>
                  <td>
                    <Input aria-label={`${t("invoices.qty")} ${l.description}`} inputMode="decimal" value={qty[l.id] ?? ""}
                      onChange={(e) => setQty({ ...qty, [l.id]: e.target.value })} ltr />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={deliver.isPending}>
            {t("sales.deliver")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function InvoiceFromOrderDialog({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (invoiceId: string) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [form, setForm] = useState({ issue_date: today(), due_date: "" });
  const rate = useRate(order.currency, form.issue_date);
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/sales-orders/{order_id}/invoice", {
        params: { path: { org_id: org.id, order_id: order.id } },
        body: { issue_date: form.issue_date, due_date: form.due_date || null, exchange_rate: rate.rate || null },
      })),
    onSuccess: (inv) => onDone(inv.id),
  });
  return (
    <Dialog open onClose={onClose} title={t("sales.invoice")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <p className="muted">{t("sales.invoiceHint")}</p>
        {create.isError && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("invoices.issueDate")}>
            <Input type="date" value={form.issue_date} min={order.order_date} onChange={(e) => setForm({ ...form, issue_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.dueDate")} optional>
            <Input type="date" value={form.due_date} min={form.issue_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} ltr />
          </Field>
          <RateField state={rate} currency={order.currency} />
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>
            {t("sales.invoice")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
