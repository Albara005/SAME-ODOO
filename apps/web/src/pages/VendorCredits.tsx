import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, Printer } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { TotalsBox, useIdempotencyKey } from "../components/business";
import { Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select } from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { isPositive, subtractDec } from "../lib/decimal";
import { today } from "../lib/format";

type Bill = Schemas["BillOut"];
type Credit = Schemas["VendorCreditOut"];

/** The vendor's credit note against a posted bill: units credited, and those sent back out of stock. */
export function VendorCreditDialog({ bill, onClose, onDone }: { bill: Bill; onClose: () => void; onDone: (creditId: string) => void }) {
  const { t, amount } = useI18n();
  const { org, can } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const open = bill.lines
    .map((l) => ({ ...l, open: subtractDec(l.quantity, l.qty_credited ?? "0") }))
    .filter((l) => isPositive(l.open));
  // Only goods received against a purchase order can go back; moving stock is warehouse work.
  const canReturn = can("inventory.write") && open.some((l) => !l.account_id && l.purchase_order_line_id);
  const [form, setForm] = useState({ issue_date: today(), reason: "", vendor_reference: "" });
  const [qty, setQty] = useState<Record<string, string>>({});
  const [back, setBack] = useState<Record<string, string>>({});
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/bills/{bill_id}/credits", {
        params: { path: { org_id: org.id, bill_id: bill.id }, header: { "idempotency-key": idempotencyKey } },
        body: {
          ...form,
          lines: open.filter((l) => isPositive(qty[l.id] ?? "")).map((l) => ({
            bill_line_id: l.id, quantity: qty[l.id] ?? "", returned_quantity: isPositive(back[l.id] ?? "") ? back[l.id]! : "0",
          })),
        },
      })),
    onSuccess: (c) => onDone(c.id),
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("vcredit.new")} wide>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <p className="muted">{t("vcredit.hint")}</p>
        {create.isError && !err["reason"] && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("credit.date")}>
            <Input type="date" value={form.issue_date} min={bill.bill_date} onChange={(e) => setForm({ ...form, issue_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("vcredit.reference")} optional>
            <Input value={form.vendor_reference} maxLength={60} onChange={(e) => setForm({ ...form, vendor_reference: e.target.value })} ltr />
          </Field>
        </div>
        <Field label={t("credit.reason")} error={err["reason"]}>
          <Input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} required />
        </Field>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("invoices.description")}</th>
                <th className="end">{t("credit.openQty")}</th>
                <th style={{ width: 120 }}>{t("credit.qty")}</th>
                {canReturn && <th style={{ width: 120 }}>{t("vcredit.returned")}</th>}
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
                      {!l.account_id && l.purchase_order_line_id && (
                        <Input aria-label={`${t("vcredit.returned")} ${l.description}`} inputMode="decimal" value={back[l.id] ?? ""}
                          placeholder="0" onChange={(e) => setBack({ ...back, [l.id]: e.target.value })} ltr />
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canReturn && <p className="small muted">{t("vcredit.returnHint")}</p>}
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("vcredit.record")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function VendorCreditPage() {
  const { creditId = "" } = useParams();
  const { org } = useOrg();
  const credit = useQuery({
    queryKey: ["vendor-credit", org.id, creditId],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/vendor-credits/{credit_id}", { params: { path: { org_id: org.id, credit_id: creditId } } })),
  });
  return <QueryView query={credit}>{(c) => <VendorCreditDetail credit={c} />}</QueryView>;
}

function VendorCreditDetail({ credit: c }: { credit: Credit }) {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const [receiving, setReceiving] = useState(false);
  const ids = { org_id: org.id, credit_id: c.id };
  const refunds = useQuery({
    queryKey: ["vendor-refunds", org.id, c.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/vendor-credits/{credit_id}/refunds", { params: { path: ids } })),
  });
  const owed = isPositive(c.refundable);
  return (
    <div className="stack">
      <PageHeader
        title={c.number}
        subtitle={
          <span className="row">
            <Badge tone="info">{t("vcredit.badge")}</Badge>
            <span>{c.vendor_name}</span>
          </span>
        }
        actions={
          <div className="row no-print">
            {owed && can("payments.write") && (
              <Button variant="primary" icon={<Banknote size={16} />} onClick={() => setReceiving(true)}>{t("vcredit.receive")}</Button>
            )}
            <Button icon={<Printer size={16} />} onClick={() => window.print()}>{t("invoices.print")}</Button>
          </div>
        }
      />
      <Card>
        <div className="grid-2">
          <dl className="kv">
            <dt>{t("purchases.vendor")}</dt>
            <dd>{c.vendor_name}</dd>
            <dt>{t("vcredit.bill")}</dt>
            <dd><Link className="mono" to={path(`purchases/bills/${c.bill_id}`)}>{c.bill_number}</Link></dd>
            <dt>{t("vcredit.reference")}</dt>
            <dd className="mono">{c.vendor_reference || "—"}</dd>
            <dt>{t("credit.date")}</dt>
            <dd className="num">{date(c.issue_date)}</dd>
            <dt>{t("credit.reason")}</dt>
            <dd>{c.reason}</dd>
            {c.stock_reference && (
              <>
                <dt>{t("vcredit.stockOut")}</dt>
                <dd className="mono">{c.stock_reference}</dd>
              </>
            )}
          </dl>
          <TotalsBox
            subtotal={money(c.subtotal, c.currency)}
            tax={money(c.tax, c.currency)}
            total={money(c.total, c.currency)}
            extra={
              <>
                <dt>{t("vcredit.applied")}</dt>
                <dd className="num" style={{ textAlign: "end" }}>{money(c.applied, c.currency)}</dd>
                <dt>{t("vcredit.refunded")}</dt>
                <dd className="num" style={{ textAlign: "end" }}>{money(c.refunded, c.currency)}</dd>
                <dt>{t("vcredit.owed")}</dt>
                <dd className="num" style={{ textAlign: "end" }}><strong>{money(c.refundable, c.currency)}</strong></dd>
              </>
            }
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
              <th className="end">{t("vcredit.returned")}</th>
              <th className="end">{t("invoices.net")}</th>
            </tr>
          </thead>
          <tbody>
            {c.lines.map((l) => (
              <tr key={l.bill_line_id}>
                <td>{l.description}</td>
                <td className="end num">{amount(l.quantity)}</td>
                <td className="end num">{money(l.unit_cost, c.currency)}</td>
                <td className="end num">{l.tax_rate === "0" ? "—" : `${l.tax_rate}%`}</td>
                <td className="end num">{isPositive(l.returned_quantity) ? amount(l.returned_quantity) : "—"}</td>
                <td className="end num">{money(l.net_amount, c.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Card title={t("vcredit.refunds")}>
        <QueryView query={refunds}>
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
                {list.map((r) => (
                  <tr key={r.id}>
                    <td className="num">{date(r.refund_date)}</td>
                    <td>{r.method === "bank" ? t("invoices.bank") : t("invoices.cash")}</td>
                    <td>{r.reference}</td>
                    <td className="end num">{money(r.amount, c.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryView>
      </Card>
      {receiving && (
        <ReceiveRefundDialog credit={c} onClose={() => setReceiving(false)} onDone={() => {
          setReceiving(false);
          void qc.invalidateQueries({ queryKey: ["vendor-credit", org.id, c.id] });
          void qc.invalidateQueries({ queryKey: ["vendor-refunds", org.id, c.id] });
          void qc.invalidateQueries({ queryKey: ["vendor-credits", org.id] });
        }} />
      )}
    </div>
  );
}

function ReceiveRefundDialog({ credit, onClose, onDone }: { credit: Credit; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const [form, setForm] = useState({ amount: credit.refundable, refund_date: today(), method: "bank" as "bank" | "cash", reference: "" });
  const receive = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/vendor-credits/{credit_id}/refunds", {
        params: { path: { org_id: org.id, credit_id: credit.id }, header: { "idempotency-key": idempotencyKey } },
        body: form,
      })),
    onSuccess: onDone,
  });
  const err = receive.error instanceof ApiError ? receive.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("vcredit.receive")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); receive.mutate(); }}>
        {receive.isError && !Object.keys(err).length && <ErrorNotice error={receive.error} />}
        <div className="grid-2">
          <Field label={`${t("invoices.amount")} (${credit.currency})`} error={err["amount"]}>
            <Input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} required ltr />
          </Field>
          <Field label={t("vcredit.receivedOn")}>
            <Input type="date" value={form.refund_date} min={credit.issue_date} onChange={(e) => setForm({ ...form, refund_date: e.target.value })} required ltr />
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
          <Button type="submit" variant="primary" loading={receive.isPending}>{t("vcredit.receive")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
