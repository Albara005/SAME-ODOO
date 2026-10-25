import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, CreditCard, FileText, PackageCheck, Pencil, Plus, Trash2, Undo2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Journey, journeySteps, LinesEditor, newLine, StatusBadge, Tabs, toPurchaseLineIn, TotalsBox, useIdempotencyKey, useVendors,
  useWarehouses, type LineDraft,
} from "../components/business";
import {
  Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select, Textarea, useToast,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { isPositive, subtractDec } from "../lib/decimal";
import { today } from "../lib/format";
import { VendorCreditDialog } from "./VendorCredits";

type Order = Schemas["PurchaseOrderOut"];
type Bill = Schemas["BillOut"];
type Vendor = Schemas["VendorOut"];
type Tab = "orders" | "bills" | "vendors";

function useOrder(id: string) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["purchase-order", org.id, id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/purchase-orders/{order_id}", { params: { path: { org_id: org.id, order_id: id } } })),
  });
}

function useBill(id: string) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["bill", org.id, id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bills/{bill_id}", { params: { path: { org_id: org.id, bill_id: id } } })),
  });
}

const errorToast = (toast: (m: string, tone?: "info" | "error") => void, fallback: string) => (e: Error) =>
  toast(e instanceof ApiError ? e.message : fallback, "error");

// ---------------------------------------------------------------- lists

export function PurchasesPage() {
  const { t } = useI18n();
  const { can, path } = useOrg();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab | null) ?? "orders";
  const [addingVendor, setAddingVendor] = useState(false);
  const write = can("purchases.write");
  return (
    <div className="stack">
      <PageHeader
        title={t("purchases.title")}
        actions={
          write && (
            <>
              {tab === "orders" && (
                <Link className="btn btn-primary" to={path("purchases/orders/new")}>
                  <Plus size={16} aria-hidden /> {t("purchases.newOrder")}
                </Link>
              )}
              {tab === "bills" && (
                <Link className="btn btn-primary" to={path("purchases/bills/new")}>
                  <Plus size={16} aria-hidden /> {t("bills.new")}
                </Link>
              )}
              {tab === "vendors" && (
                <Button variant="primary" icon={<Plus size={16} />} onClick={() => setAddingVendor(true)}>
                  {t("vendors.new")}
                </Button>
              )}
            </>
          )
        }
      />
      <Tabs<Tab>
        tabs={[
          { key: "orders", label: t("purchases.orders") },
          { key: "bills", label: t("bills.title") },
          { key: "vendors", label: t("vendors.title") },
        ]}
        value={tab}
        onChange={(k) => setParams(k === "orders" ? {} : { tab: k }, { replace: true })}
      />
      {tab === "orders" && <OrdersList />}
      {tab === "bills" && <BillsList />}
      {tab === "vendors" && <VendorsList />}
      {addingVendor && <VendorDialog onClose={() => setAddingVendor(false)} />}
    </div>
  );
}

function OrdersList() {
  const { t, money, date } = useI18n();
  const { org, path } = useOrg();
  const orders = useQuery({
    queryKey: ["purchase-orders", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/purchase-orders", { params: { path: { org_id: org.id } } })),
    placeholderData: keepPreviousData,
  });
  return (
    <QueryView query={orders}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("purchases.empty")}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("invoices.number")}</th>
                  <th>{t("purchases.vendor")}</th>
                  <th>{t("sales.orderDate")}</th>
                  <th>{t("purchases.expected")}</th>
                  <th className="end">{t("invoices.total")}</th>
                  <th>{t("common.status")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <Link to={path(`purchases/orders/${o.id}`)} className="mono">{o.number}</Link>
                    </td>
                    <td>{o.vendor_name}</td>
                    <td className="num small">{date(o.order_date)}</td>
                    <td className="num small">{date(o.expected_date)}</td>
                    <td className="end num">{money(o.total, org.default_currency)}</td>
                    <td><StatusBadge status={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }
    </QueryView>
  );
}

function BillsList() {
  const { t, money, date } = useI18n();
  const { org, path } = useOrg();
  const bills = useQuery({
    queryKey: ["bills", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bills", { params: { path: { org_id: org.id } } })),
    placeholderData: keepPreviousData,
  });
  return (
    <QueryView query={bills}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("bills.empty")}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("invoices.number")}</th>
                  <th>{t("bills.vendorReference")}</th>
                  <th>{t("purchases.vendor")}</th>
                  <th>{t("bills.billDate")}</th>
                  <th>{t("invoices.dueDate")}</th>
                  <th className="end">{t("invoices.total")}</th>
                  <th className="end">{t("invoices.due")}</th>
                  <th>{t("common.status")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <Link to={path(`purchases/bills/${b.id}`)} className="mono">{b.number ?? t("invoices.draftNoNumber")}</Link>
                    </td>
                    <td className="mono small">{b.vendor_reference || "—"}</td>
                    <td>{b.vendor_name}</td>
                    <td className="num small">{date(b.bill_date)}</td>
                    <td className="num small">{date(b.due_date)}</td>
                    <td className="end num">{money(b.total, org.default_currency)}</td>
                    <td className="end num">{money(b.amount_due, org.default_currency)}</td>
                    <td><StatusBadge status={b.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }
    </QueryView>
  );
}

function VendorsList() {
  const { t } = useI18n();
  const { can } = useOrg();
  const vendors = useVendors();
  const [editing, setEditing] = useState<Vendor | null>(null);
  return (
    <QueryView query={vendors}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("vendors.empty")}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("common.email")}</th>
                  <th>{t("common.phone")}</th>
                  <th>{t("customers.taxNumber")}</th>
                  {can("purchases.write") && <th className="end">{t("common.actions")}</th>}
                </tr>
              </thead>
              <tbody>
                {list.map((v) => (
                  <tr key={v.id}>
                    <td>{v.name}</td>
                    <td className="mono small">{v.email || "—"}</td>
                    <td className="mono small">{v.phone || "—"}</td>
                    <td className="mono small">{v.tax_number || "—"}</td>
                    {can("purchases.write") && (
                      <td className="end">
                        <Button size="sm" icon={<Pencil size={14} />} onClick={() => setEditing(v)}>{t("common.edit")}</Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {editing && <VendorDialog vendor={editing} onClose={() => setEditing(null)} />}
          </div>
        )
      }
    </QueryView>
  );
}

function VendorDialog({ vendor, onClose }: { vendor?: Vendor; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: vendor?.name ?? "", email: vendor?.email ?? "", phone: vendor?.phone ?? "", tax_number: vendor?.tax_number ?? "",
    city: vendor?.city ?? "",
  });
  const save = useMutation({
    mutationFn: () => {
      const fields = { ...form, email: form.email || null };
      // Editing sends only these fields, so details entered elsewhere (address, notes) stay as they are.
      return vendor
        ? unwrap(api.PATCH("/api/v1/orgs/{org_id}/vendors/{vendor_id}", { params: { path: { org_id: org.id, vendor_id: vendor.id } }, body: fields }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/vendors", {
            params: { path: { org_id: org.id } },
            body: { kind: "company", address_line: "", building_number: "", district: "", commercial_registration: "", country: "",
              notes: "", postal_code: "", ...fields },
          }));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["vendors", org.id] });
      onClose();
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <Dialog open onClose={onClose} title={vendor ? vendor.name : t("vendors.new")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <Field label={t("common.name")} error={err["name"]}>
          <Input value={form.name} onChange={set("name")} required />
        </Field>
        <div className="grid-2">
          <Field label={t("common.email")} error={err["email"]} optional>
            <Input type="email" value={form.email} onChange={set("email")} ltr />
          </Field>
          <Field label={t("common.phone")} optional>
            <Input value={form.phone} onChange={set("phone")} ltr />
          </Field>
          <Field label={t("customers.taxNumber")} optional>
            <Input value={form.tax_number} onChange={set("tax_number")} ltr />
          </Field>
          <Field label={t("customers.city")} optional>
            <Input value={form.city} onChange={set("city")} />
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- purchase order editor

function orderLinesToDraft(lines: { id: string; product_id: string | null; description: string; quantity: string; unit_cost: string; tax_id: string | null }[]): LineDraft[] {
  return lines.map((l) => ({
    key: l.id, product_id: l.product_id ?? "", description: l.description, quantity: l.quantity, unit_price: l.unit_cost,
    discount_percent: "0", tax: l.tax_id ?? "",
  }));
}

export function PurchaseOrderEditorPage() {
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
  const vendors = useVendors();
  const warehouses = useWarehouses();
  const [header, setHeader] = useState({
    vendor_id: order?.vendor_id ?? "", order_date: order?.order_date ?? today(), expected_date: order?.expected_date ?? "",
    warehouse_id: order?.warehouse_id ?? "", notes: order?.notes ?? "",
  });
  const [lines, setLines] = useState<LineDraft[]>(order ? orderLinesToDraft(order.lines) : [{ ...newLine(), unit_price: "" }]);
  const save = useMutation({
    mutationFn: () => {
      const body = { ...header, expected_date: header.expected_date || null, warehouse_id: header.warehouse_id || null,
                     lines: lines.map(toPurchaseLineIn) };
      return order
        ? unwrap(api.PUT("/api/v1/orgs/{org_id}/purchase-orders/{order_id}", { params: { path: { org_id: org.id, order_id: order.id } }, body }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/purchase-orders", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["purchase-orders", org.id] });
      qc.setQueryData(["purchase-order", org.id, saved.id], saved);
      navigate(path(`purchases/orders/${saved.id}`));
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }
  return (
    <form className="stack" onSubmit={submit}>
      <PageHeader title={order ? order.number : t("purchases.newOrder")} />
      {save.isError && <ErrorNotice error={save.error} />}
      <Card>
        <div className="grid-2">
          <Field label={t("purchases.vendor")} error={err["vendor_id"]}>
            <Select value={header.vendor_id} onChange={(e) => setHeader({ ...header, vendor_id: e.target.value })} required>
              <option value="">{t("purchases.chooseVendor")}</option>
              {(vendors.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </Select>
          </Field>
          <Field label={t("sales.orderDate")} error={err["order_date"]}>
            <Input type="date" value={header.order_date} onChange={(e) => setHeader({ ...header, order_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("purchases.expected")} error={err["expected_date"]} optional>
            <Input type="date" value={header.expected_date} min={header.order_date} onChange={(e) => setHeader({ ...header, expected_date: e.target.value })} ltr />
          </Field>
          {warehouses.data && warehouses.data.length > 1 && (
            <Field label={t("inventory.warehouse")} optional>
              <Select value={header.warehouse_id} onChange={(e) => setHeader({ ...header, warehouse_id: e.target.value })}>
                <option value="">{t("sales.defaultWarehouse")}</option>
                {warehouses.data.filter((w) => w.active).map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
              </Select>
            </Field>
          )}
        </div>
      </Card>
      <Card title={t("invoices.lines")}>
        <LinesEditor lines={lines} onChange={setLines} currency={org.default_currency} purchase />
      </Card>
      <Field label={t("invoices.notes")} optional>
        <Textarea value={header.notes} onChange={(e) => setHeader({ ...header, notes: e.target.value })} />
      </Field>
      <div className="form-actions">
        <Button onClick={() => navigate(-1)}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" loading={save.isPending}>{t("purchases.save")}</Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- purchase order detail

export function PurchaseOrderDetailPage() {
  const { orderId = "" } = useParams();
  const order = useOrder(orderId);
  return <QueryView query={order}>{(o) => <OrderDetail order={o} />}</QueryView>;
}

function orderJourney(o: Order, t: (k: MessageKey) => string) {
  const labels = [t("purchases.stepDraft"), t("purchases.stepConfirm"), t("purchases.stepReceive"), t("purchases.stepBill")];
  if (o.status === "draft") return journeySteps(labels, 0);
  if (o.bill_status === "billed") return journeySteps(labels, 3, true);
  const received = o.receipt_status === "received" || o.receipt_status === "not_applicable";
  return journeySteps(labels, received ? 3 : 2);
}

function OrderDetail({ order: o }: { order: Order }) {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [receiving, setReceiving] = useState(false);
  const [billing, setBilling] = useState(false);
  const ids = { org_id: org.id, order_id: o.id };
  const refresh = (updated?: Order) => {
    if (updated) qc.setQueryData(["purchase-order", org.id, o.id], updated);
    void qc.invalidateQueries({ queryKey: ["purchase-order", org.id, o.id] });
    void qc.invalidateQueries({ queryKey: ["purchase-orders", org.id] });
  };
  const onError = errorToast(toast, t("common.error"));
  const confirm = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/purchase-orders/{order_id}/confirm", { params: { path: ids } })),
    onSuccess: refresh,
    onError,
  });
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/purchase-orders/{order_id}/cancel", { params: { path: ids } })),
    onSuccess: refresh,
    onError,
  });
  const confirmed = o.status === "confirmed";
  const hasProgress = o.lines.some((l) => isPositive(l.qty_received) || isPositive(l.qty_billed));
  const canReceive = confirmed && can("inventory.write") && o.lines.some((l) => isPositive(l.qty_to_receive));
  const canBill = confirmed && can("purchases.write") && o.lines.some((l) => isPositive(l.qty_to_bill));
  return (
    <div className="stack">
      <PageHeader
        title={o.number}
        subtitle={<span className="row"><StatusBadge status={o.status} /><span>{o.vendor_name}</span></span>}
        actions={
          <div className="row no-print">
            {o.status === "draft" && can("purchases.write") && (
              <>
                <Link className="btn" to={path(`purchases/orders/${o.id}/edit`)}>
                  <Pencil size={16} aria-hidden /> {t("common.edit")}
                </Link>
                <Button variant="primary" icon={<CheckCircle2 size={16} />} loading={confirm.isPending} onClick={() => confirm.mutate()}>
                  {t("purchases.confirm")}
                </Button>
              </>
            )}
            {canReceive && (
              <Button variant="primary" icon={<PackageCheck size={16} />} onClick={() => setReceiving(true)}>
                {t("purchases.receive")}
              </Button>
            )}
            {canBill && (
              <Button variant="primary" icon={<FileText size={16} />} onClick={() => setBilling(true)}>
                {t("purchases.bill")}
              </Button>
            )}
            {o.status !== "cancelled" && !hasProgress && can("purchases.write") && (
              <Button variant="danger" icon={<Ban size={16} />} loading={cancel.isPending}
                onClick={() => window.confirm(t("purchases.cancelConfirm")) && cancel.mutate()}>
                {t("purchases.cancel")}
              </Button>
            )}
          </div>
        }
      />
      {o.status !== "cancelled" && (
        <Card tight>
          <Journey label={t("common.progress")} steps={orderJourney(o, t)} />
        </Card>
      )}
      <Card>
        <div className="grid-2">
          <dl className="kv">
            <dt>{t("purchases.vendor")}</dt>
            <dd>{o.vendor_name}</dd>
            <dt>{t("sales.orderDate")}</dt>
            <dd className="num">{date(o.order_date)}</dd>
            <dt>{t("purchases.expected")}</dt>
            <dd className="num">{date(o.expected_date)}</dd>
            {confirmed && (
              <>
                <dt>{t("purchases.receipt")}</dt>
                <dd><StatusBadge status={o.receipt_status} /></dd>
                <dt>{t("purchases.billing")}</dt>
                <dd><StatusBadge status={o.bill_status} /></dd>
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
              <th className="end">{t("purchases.unitCost")}</th>
              <th className="end">{t("invoices.net")}</th>
              {confirmed && (
                <>
                  <th className="end">{t("purchases.received")}</th>
                  <th className="end">{t("purchases.billed")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {o.lines.map((l) => (
              <tr key={l.id}>
                <td>{l.description}</td>
                <td className="end num">{amount(l.quantity)}</td>
                <td className="end num">{money(l.unit_cost, o.currency)}</td>
                <td className="end num">{money(l.net_amount, o.currency)}</td>
                {confirmed && (
                  <>
                    <td className="end num">{amount(l.qty_received)}</td>
                    <td className="end num">{amount(l.qty_billed)}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {o.notes && <Card tight><p style={{ whiteSpace: "pre-wrap" }}>{o.notes}</p></Card>}
      {receiving && (
        <ReceiveDialog order={o} onClose={() => setReceiving(false)} onDone={(ref) => {
          setReceiving(false);
          toast(t("purchases.received_ok", { ref }));
          refresh();
          void qc.invalidateQueries({ queryKey: ["stock", org.id] });
        }} />
      )}
      {billing && (
        <BillFromOrderDialog order={o} onClose={() => setBilling(false)} onDone={(billId) => {
          refresh();
          void qc.invalidateQueries({ queryKey: ["bills", org.id] });
          navigate(path(`purchases/bills/${billId}`));
        }} />
      )}
    </div>
  );
}

function ReceiveDialog({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (reference: string) => void }) {
  const { t, amount } = useI18n();
  const { org } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const open = order.lines.filter((l) => isPositive(l.qty_to_receive));
  const [all, setAll] = useState(true);
  const [moveDate, setMoveDate] = useState(today());
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(open.map((l) => [l.id, l.qty_to_receive])));
  const receive = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/purchase-orders/{order_id}/receipts", {
        params: { path: { org_id: org.id, order_id: order.id }, header: { "idempotency-key": idempotencyKey } },
        body: {
          move_date: moveDate,
          lines: all ? null : open.filter((l) => isPositive(qty[l.id] ?? "")).map((l) => ({ line_id: l.id, quantity: qty[l.id] ?? "" })),
        },
      })),
    onSuccess: (r) => onDone(r.reference),
  });
  return (
    <Dialog open onClose={onClose} title={t("purchases.receive")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); receive.mutate(); }}>
        <p className="muted">{t("purchases.receiveHint")}</p>
        {receive.isError && <ErrorNotice error={receive.error} />}
        <Field label={t("purchases.receiptDate")}>
          <Input type="date" value={moveDate} min={order.order_date} onChange={(e) => setMoveDate(e.target.value)} required ltr />
        </Field>
        <Checkbox label={t("purchases.receiveAll")} checked={all} onChange={(e) => setAll(e.target.checked)} />
        {!all && (
          <table>
            <thead>
              <tr>
                <th>{t("invoices.description")}</th>
                <th className="end">{t("purchases.toReceive")}</th>
                <th style={{ width: 120 }}>{t("invoices.qty")}</th>
              </tr>
            </thead>
            <tbody>
              {open.map((l) => (
                <tr key={l.id}>
                  <td>{l.description}</td>
                  <td className="end num">{amount(l.qty_to_receive)}</td>
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
          <Button type="submit" variant="primary" loading={receive.isPending}>{t("purchases.receive")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function BillFromOrderDialog({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (billId: string) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [form, setForm] = useState({ bill_date: today(), due_date: "", vendor_reference: "" });
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/purchase-orders/{order_id}/bill", {
        params: { path: { org_id: org.id, order_id: order.id } },
        body: { ...form, due_date: form.due_date || null },
      })),
    onSuccess: (b) => onDone(b.id),
  });
  return (
    <Dialog open onClose={onClose} title={t("purchases.bill")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <p className="muted">{t("purchases.billHint")}</p>
        {create.isError && <ErrorNotice error={create.error} />}
        <Field label={t("bills.vendorReference")} hint={t("bills.vendorReferenceHint")} optional>
          <Input value={form.vendor_reference} onChange={(e) => setForm({ ...form, vendor_reference: e.target.value })} ltr />
        </Field>
        <div className="grid-2">
          <Field label={t("bills.billDate")}>
            <Input type="date" value={form.bill_date} min={order.order_date} onChange={(e) => setForm({ ...form, bill_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.dueDate")} optional>
            <Input type="date" value={form.due_date} min={form.bill_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} ltr />
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("purchases.bill")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- vendor bills

export function BillEditorPage() {
  const { billId } = useParams();
  const existing = useBill(billId ?? "");
  if (billId && existing.isPending) return <QueryView query={existing}>{() => null}</QueryView>;
  if (billId && existing.isError) return <ErrorNotice error={existing.error} />;
  return <BillEditor bill={billId ? existing.data : undefined} />;
}

function BillEditor({ bill }: { bill?: Bill }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const vendors = useVendors();
  const [header, setHeader] = useState({
    vendor_id: bill?.vendor_id ?? "", vendor_reference: bill?.vendor_reference ?? "", bill_date: bill?.bill_date ?? today(),
    due_date: bill?.due_date ?? "", notes: bill?.notes ?? "",
  });
  const [lines, setLines] = useState<LineDraft[]>(bill ? orderLinesToDraft(bill.lines) : [{ ...newLine(), unit_price: "" }]);
  const save = useMutation({
    mutationFn: () => {
      const body = { ...header, due_date: header.due_date || null, lines: lines.map(toPurchaseLineIn) };
      return bill
        ? unwrap(api.PUT("/api/v1/orgs/{org_id}/bills/{bill_id}", { params: { path: { org_id: org.id, bill_id: bill.id } }, body }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/bills", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["bills", org.id] });
      qc.setQueryData(["bill", org.id, saved.id], saved);
      navigate(path(`purchases/bills/${saved.id}`));
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <PageHeader title={bill ? t("bills.edit") : t("bills.new")} subtitle={t("bills.directHint")} />
      {save.isError && <ErrorNotice error={save.error} />}
      <Card>
        <div className="grid-2">
          <Field label={t("purchases.vendor")} error={err["vendor_id"]}>
            <Select value={header.vendor_id} onChange={(e) => setHeader({ ...header, vendor_id: e.target.value })} required>
              <option value="">{t("purchases.chooseVendor")}</option>
              {(vendors.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </Select>
          </Field>
          <Field label={t("bills.vendorReference")} hint={t("bills.vendorReferenceHint")} optional>
            <Input value={header.vendor_reference} onChange={(e) => setHeader({ ...header, vendor_reference: e.target.value })} ltr />
          </Field>
          <Field label={t("bills.billDate")} error={err["bill_date"]}>
            <Input type="date" value={header.bill_date} onChange={(e) => setHeader({ ...header, bill_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.dueDate")} error={err["due_date"]} optional>
            <Input type="date" value={header.due_date} min={header.bill_date} onChange={(e) => setHeader({ ...header, due_date: e.target.value })} ltr />
          </Field>
        </div>
      </Card>
      <Card title={t("invoices.lines")}>
        <LinesEditor lines={lines} onChange={setLines} currency={org.default_currency} purchase services />
      </Card>
      <Field label={t("invoices.notes")} optional>
        <Textarea value={header.notes} onChange={(e) => setHeader({ ...header, notes: e.target.value })} />
      </Field>
      <div className="form-actions">
        <Button onClick={() => navigate(-1)}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" loading={save.isPending}>{t("bills.save")}</Button>
      </div>
    </form>
  );
}

export function BillDetailPage() {
  const { billId = "" } = useParams();
  const bill = useBill(billId);
  return <QueryView query={bill}>{(b) => <BillDetail bill={b} />}</QueryView>;
}

function BillDetail({ bill: b }: { bill: Bill }) {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [paying, setPaying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [crediting, setCrediting] = useState(false);
  const [pricing, setPricing] = useState<Bill["lines"][number] | null>(null);
  const ids = { org_id: org.id, bill_id: b.id };
  const refresh = (updated?: Bill) => {
    if (updated) qc.setQueryData(["bill", org.id, b.id], updated);
    void qc.invalidateQueries({ queryKey: ["bill", org.id, b.id] });
    void qc.invalidateQueries({ queryKey: ["bills", org.id] });
    if (b.purchase_order_id) void qc.invalidateQueries({ queryKey: ["purchase-order", org.id, b.purchase_order_id] });
  };
  const onError = errorToast(toast, t("common.error"));
  const post = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/bills/{bill_id}/post", { params: { path: ids } })),
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/orgs/{org_id}/bills/{bill_id}", { params: { path: ids } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bills", org.id] });
      navigate(path("purchases?tab=bills"));
    },
    onError,
  });
  const payments = useQuery({
    queryKey: ["bill-payments", org.id, b.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bills/{bill_id}/payments", { params: { path: ids } })),
    enabled: b.status !== "draft",
  });
  const credits = useQuery({
    queryKey: ["vendor-credits", org.id, b.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/vendor-credits", { params: { path: { org_id: org.id }, query: { bill_id: b.id } } })),
    enabled: b.status !== "draft",
  });
  const isDraft = b.status === "draft";
  const canPay = can("payments.write") && (b.status === "posted" || b.status === "partially_paid");
  const canCredit = can("bills.post") && ["posted", "partially_paid", "paid", "credited"].includes(b.status)
    && b.lines.some((l) => isPositive(subtractDec(l.quantity, l.qty_credited ?? "0")));
  return (
    <div className="stack">
      <PageHeader
        title={b.number ?? t("invoices.draftNoNumber")}
        subtitle={<span className="row"><StatusBadge status={b.status} /><span>{b.vendor_name}</span></span>}
        actions={
          <div className="row no-print">
            {isDraft && can("purchases.write") && (
              <>
                {!b.purchase_order_id && (
                  <Link className="btn" to={path(`purchases/bills/${b.id}/edit`)}>
                    <Pencil size={16} aria-hidden /> {t("common.edit")}
                  </Link>
                )}
                <Button variant="danger" icon={<Trash2 size={16} />} loading={remove.isPending}
                  onClick={() => window.confirm(t("bills.deleteConfirm")) && remove.mutate()}>
                  {t("common.delete")}
                </Button>
              </>
            )}
            {isDraft && can("bills.post") && (
              <Button variant="primary" icon={<CheckCircle2 size={16} />} loading={post.isPending}
                onClick={() => window.confirm(t("bills.postConfirm")) && post.mutate()}>
                {t("bills.post")}
              </Button>
            )}
            {canPay && (
              <Button variant="primary" icon={<CreditCard size={16} />} onClick={() => setPaying(true)}>{t("bills.pay")}</Button>
            )}
            {canCredit && (
              <Button icon={<Undo2 size={16} />} onClick={() => setCrediting(true)}>{t("vcredit.new")}</Button>
            )}
            {b.status === "posted" && can("bills.post") && !isPositive(b.amount_credited ?? "0") && (
              <Button variant="danger" icon={<Ban size={16} />} onClick={() => setCancelling(true)}>{t("bills.cancel")}</Button>
            )}
          </div>
        }
      />
      {isDraft && b.purchase_order_id && <div className="notice notice-info">{t("bills.fromOrder")}</div>}
      <Card>
        <div className="grid-2">
          <dl className="kv">
            <dt>{t("purchases.vendor")}</dt>
            <dd>{b.vendor_name}</dd>
            <dt>{t("bills.vendorReference")}</dt>
            <dd className="mono">{b.vendor_reference || "—"}</dd>
            <dt>{t("bills.billDate")}</dt>
            <dd className="num">{date(b.bill_date)}</dd>
            <dt>{t("invoices.dueDate")}</dt>
            <dd className="num">{date(b.due_date)}</dd>
            {b.purchase_order_id && (
              <>
                <dt>{t("purchases.order")}</dt>
                <dd><Link className="mono" to={path(`purchases/orders/${b.purchase_order_id}`)}>{b.purchase_order_number}</Link></dd>
              </>
            )}
          </dl>
          <TotalsBox
            subtotal={money(b.subtotal, b.currency)}
            tax={money(b.tax, b.currency)}
            total={money(b.total, b.currency)}
            extra={!isDraft && (
              <>
                <dt>{t("invoices.paid")}</dt>
                <dd className="num" style={{ textAlign: "end" }}>{money(b.amount_paid, b.currency)}</dd>
                {isPositive(b.amount_credited ?? "0") && (
                  <>
                    <dt>{t("vcredit.credited")}</dt>
                    <dd className="num" style={{ textAlign: "end" }}>{money(b.amount_credited ?? "0", b.currency)}</dd>
                  </>
                )}
                <dt>{t("invoices.due")}</dt>
                <dd className="num" style={{ textAlign: "end" }}><strong>{money(b.amount_due, b.currency)}</strong></dd>
              </>
            )}
          />
        </div>
      </Card>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("invoices.description")}</th>
              <th className="end">{t("invoices.qty")}</th>
              <th className="end">{t("purchases.unitCost")}</th>
              <th className="end">{t("invoices.taxCol")}</th>
              <th className="end">{t("invoices.net")}</th>
              {isDraft && b.purchase_order_id && can("purchases.write") && <th className="no-print" />}
            </tr>
          </thead>
          <tbody>
            {b.lines.map((l) => (
              <tr key={l.id}>
                <td>{l.description}</td>
                <td className="end num">{amount(l.quantity)}</td>
                <td className="end num">
                  {money(l.unit_cost, b.currency)}
                  {l.ordered_unit_cost && l.ordered_unit_cost !== l.unit_cost && (
                    <span className="muted small" style={{ display: "block" }}>
                      {t("bills.orderedAt", { price: money(l.ordered_unit_cost, b.currency) })}
                    </span>
                  )}
                </td>
                <td className="end num">{l.tax_rate === "0" ? "—" : `${l.tax_rate}%`}</td>
                <td className="end num">{money(l.net_amount, b.currency)}</td>
                {isDraft && b.purchase_order_id && can("purchases.write") && (
                  <td className="end no-print">
                    {l.purchase_order_line_id && (
                      <Button size="sm" variant="ghost" onClick={() => setPricing(l)}>{t("bills.vendorPrice")}</Button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {b.notes && <Card tight><p style={{ whiteSpace: "pre-wrap" }}>{b.notes}</p></Card>}
      {!isDraft && (
        <Card title={t("invoices.payments")}>
          <QueryView query={payments}>
            {(list) => list.length === 0 ? <Empty /> : (
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
                      <td>{p.method === "bank" ? t("invoices.bank") : t("invoices.cash")}</td>
                      <td>{p.reference}</td>
                      <td className="end num">{money(p.amount, b.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryView>
        </Card>
      )}
      {(credits.data?.length ?? 0) > 0 && (
        <Card title={t("vcredit.title")}>
          <table>
            <thead>
              <tr>
                <th>{t("invoices.number")}</th>
                <th>{t("credit.date")}</th>
                <th className="end">{t("invoices.total")}</th>
                <th className="end">{t("vcredit.owed")}</th>
              </tr>
            </thead>
            <tbody>
              {credits.data!.map((c) => (
                <tr key={c.id}>
                  <td><Link className="mono" to={path(`purchases/credits/${c.id}`)}>{c.number}</Link></td>
                  <td className="num">{date(c.issue_date)}</td>
                  <td className="end num">{money(c.total, b.currency)}</td>
                  <td className="end num">{isPositive(c.refundable) ? money(c.refundable, b.currency) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {pricing && <VendorPriceDialog bill={b} line={pricing} onClose={() => setPricing(null)} onDone={(updated) => { setPricing(null); refresh(updated); }} />}
      {crediting && (
        <VendorCreditDialog bill={b} onClose={() => setCrediting(false)} onDone={(creditId) => {
          setCrediting(false);
          refresh();
          void qc.invalidateQueries({ queryKey: ["vendor-credits", org.id, b.id] });
          navigate(path(`purchases/credits/${creditId}`));
        }} />
      )}
      {paying && (
        <BillPaymentDialog bill={b} onClose={() => setPaying(false)} onDone={() => {
          setPaying(false);
          refresh();
          void qc.invalidateQueries({ queryKey: ["bill-payments", org.id, b.id] });
        }} />
      )}
      {cancelling && <BillCancelDialog bill={b} onClose={() => setCancelling(false)} onDone={(u) => { setCancelling(false); refresh(u); }} />}
    </div>
  );
}

function VendorPriceDialog({ bill, line, onClose, onDone }: {
  bill: Bill; line: Bill["lines"][number]; onClose: () => void; onDone: (updated: Bill) => void;
}) {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const [price, setPrice] = useState(line.unit_cost);
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/bills/{bill_id}/lines/{line_id}/price", {
      params: { path: { org_id: org.id, bill_id: bill.id, line_id: line.id } }, body: { unit_cost: price },
    })),
    onSuccess: onDone,
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("bills.vendorPriceTitle", { line: line.description })}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="muted">{t("bills.vendorPriceHint", { price: money(line.ordered_unit_cost ?? line.unit_cost, bill.currency) })}</p>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <Field label={t("bills.vendorUnitPrice")} error={err["unit_cost"]}>
          <Input inputMode="decimal" value={price} required onChange={(e) => setPrice(e.target.value)} ltr />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function BillPaymentDialog({ bill, onClose, onDone }: { bill: Bill; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const [form, setForm] = useState({ amount: bill.amount_due, payment_date: today(), method: "bank" as "bank" | "cash", reference: "" });
  const pay = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/bills/{bill_id}/payments", {
        params: { path: { org_id: org.id, bill_id: bill.id }, header: { "idempotency-key": idempotencyKey } },
        body: form,
      })),
    onSuccess: onDone,
  });
  const err = pay.error instanceof ApiError ? pay.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("bills.pay")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); pay.mutate(); }}>
        {pay.isError && !Object.keys(err).length && <ErrorNotice error={pay.error} />}
        <div className="grid-2">
          <Field label={`${t("invoices.amount")} (${bill.currency})`} error={err["amount"]}>
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
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={pay.isPending}>{t("bills.pay")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function BillCancelDialog({ bill, onClose, onDone }: { bill: Bill; onClose: () => void; onDone: (u: Bill) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [cancelDate, setCancelDate] = useState(today());
  const cancel = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/bills/{bill_id}/cancel", {
        params: { path: { org_id: org.id, bill_id: bill.id } }, body: { cancel_date: cancelDate },
      })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("bills.cancel")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); cancel.mutate(); }}>
        <p className="muted">{t("bills.cancelHint")}</p>
        {cancel.isError && <ErrorNotice error={cancel.error} />}
        <Field label={t("invoices.cancelDate")}>
          <Input type="date" value={cancelDate} min={bill.bill_date} onChange={(e) => setCancelDate(e.target.value)} required ltr />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.close")}</Button>
          <Button type="submit" variant="danger" loading={cancel.isPending}>{t("bills.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
