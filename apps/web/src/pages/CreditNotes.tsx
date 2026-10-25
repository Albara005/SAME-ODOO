import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, Printer } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { TotalsBox, useIdempotencyKey } from "../components/business";
import { Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select } from "../components/ui";
import { EInvoiceStatus } from "../components/EInvoicing";
import { ZatcaQr } from "../components/ZatcaQr";
import { useI18n } from "../i18n/I18n";
import { isPositive } from "../lib/decimal";
import { today } from "../lib/format";

type Note = Schemas["CreditNoteOut"];

export function CreditNotePage() {
  const { noteId = "" } = useParams();
  const { org } = useOrg();
  const note = useQuery({
    queryKey: ["credit-note", org.id, noteId],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/credit-notes/{note_id}", { params: { path: { org_id: org.id, note_id: noteId } } })),
  });
  return <QueryView query={note}>{(n) => <CreditNoteDetail note={n} />}</QueryView>;
}

function CreditNoteDetail({ note: n }: { note: Note }) {
  const { t, money, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const [refunding, setRefunding] = useState(false);
  const ids = { org_id: org.id, note_id: n.id };
  const refunds = useQuery({
    queryKey: ["refunds", org.id, n.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/credit-notes/{note_id}/refunds", { params: { path: ids } })),
  });
  const owed = isPositive(n.refundable);
  return (
    <div className="stack">
      <PageHeader
        title={n.number}
        subtitle={
          <span className="row">
            <Badge tone="info">{t("credit.badge")}</Badge>
            <span>{n.customer_name}</span>
          </span>
        }
        actions={
          <div className="row no-print">
            {owed && can("payments.write") && (
              <Button variant="primary" icon={<Banknote size={16} />} onClick={() => setRefunding(true)}>{t("credit.refund")}</Button>
            )}
            <Button icon={<Printer size={16} />} onClick={() => window.print()}>{t("invoices.print")}</Button>
          </div>
        }
      />
      <Card>
        <div className="grid-2">
          <dl className="kv">
            <dt>{t("invoices.customer")}</dt>
            <dd>{n.customer_name}</dd>
            <dt>{t("credit.invoice")}</dt>
            <dd><Link className="mono" to={path(`invoices/${n.invoice_id}`)}>{n.invoice_number}</Link></dd>
            <dt>{t("credit.date")}</dt>
            <dd className="num">{date(n.issue_date)}</dd>
            <dt>{t("credit.reason")}</dt>
            <dd>{n.reason}</dd>
            {n.stock_reference && (
              <>
                <dt>{t("credit.stockBack")}</dt>
                <dd className="mono">{n.stock_reference}</dd>
              </>
            )}
          </dl>
          <TotalsBox
            subtotal={money(n.subtotal, n.currency)}
            tax={money(n.tax, n.currency)}
            total={money(n.total, n.currency)}
            extra={
              <>
                <dt>{t("credit.applied")}</dt>
                <dd className="num" style={{ textAlign: "end" }}>{money(n.applied, n.currency)}</dd>
                <dt>{t("credit.refunded")}</dt>
                <dd className="num" style={{ textAlign: "end" }}>{money(n.refunded, n.currency)}</dd>
                <dt>{t("credit.owed")}</dt>
                <dd className="num" style={{ textAlign: "end" }}><strong>{money(n.refundable, n.currency)}</strong></dd>
              </>
            }
          />
        </div>
        {n.zatca_qr && <ZatcaQr payload={n.zatca_qr} />}
      </Card>
      {n.einvoice && (
        <div className="no-print">
          <EInvoiceStatus doc={n.einvoice} onChange={() => void qc.invalidateQueries({ queryKey: ["credit-note", org.id, n.id] })} />
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("invoices.description")}</th>
              <th className="end">{t("invoices.qty")}</th>
              <th className="end">{t("invoices.unitPrice")}</th>
              <th className="end">{t("invoices.taxCol")}</th>
              <th className="end">{t("credit.returned")}</th>
              <th className="end">{t("invoices.net")}</th>
            </tr>
          </thead>
          <tbody>
            {n.lines.map((l) => (
              <tr key={l.invoice_line_id}>
                <td>{l.description}</td>
                <td className="end num">{amount(l.quantity)}</td>
                <td className="end num">{money(l.unit_price, n.currency)}</td>
                <td className="end num">{l.tax_rate === "0" ? "—" : `${l.tax_rate}%`}</td>
                <td className="end num">{isPositive(l.returned_quantity) ? amount(l.returned_quantity) : "—"}</td>
                <td className="end num">{money(l.net_amount, n.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Card title={t("credit.refunds")}>
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
                    <td className="end num">{money(r.amount, n.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryView>
      </Card>
      {refunding && (
        <RefundDialog note={n} onClose={() => setRefunding(false)} onDone={() => {
          setRefunding(false);
          void qc.invalidateQueries({ queryKey: ["credit-note", org.id, n.id] });
          void qc.invalidateQueries({ queryKey: ["refunds", org.id, n.id] });
        }} />
      )}
    </div>
  );
}

function RefundDialog({ note, onClose, onDone }: { note: Note; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [idempotencyKey] = useIdempotencyKey();
  const [form, setForm] = useState({ amount: note.refundable, refund_date: today(), method: "bank" as "bank" | "cash", reference: "" });
  const refund = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/credit-notes/{note_id}/refunds", {
        params: { path: { org_id: org.id, note_id: note.id }, header: { "idempotency-key": idempotencyKey } },
        body: form,
      })),
    onSuccess: onDone,
  });
  const err = refund.error instanceof ApiError ? refund.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("credit.refund")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); refund.mutate(); }}>
        {refund.isError && !Object.keys(err).length && <ErrorNotice error={refund.error} />}
        <div className="grid-2">
          <Field label={`${t("invoices.amount")} (${note.currency})`} error={err["amount"]}>
            <Input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} required ltr />
          </Field>
          <Field label={t("invoices.paymentDate")}>
            <Input type="date" value={form.refund_date} min={note.issue_date} onChange={(e) => setForm({ ...form, refund_date: e.target.value })} required ltr />
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
          <Button type="submit" variant="primary" loading={refund.isPending}>{t("credit.refund")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
