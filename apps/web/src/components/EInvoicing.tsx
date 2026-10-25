import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, PlugZap, Power, Send } from "lucide-react";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import { download } from "./business";
import { Badge, Button, Card, ErrorNotice, Field, Input, Select, errorText, useToast, type Tone } from "./ui";

type Settings = Schemas["EInvoiceSettingsOut"];
type SettingsIn = Schemas["EInvoiceSettingsIn"];
type Summary = Schemas["EInvoiceSummary"];

const TONE: Record<Summary["status"], Tone> = { pending: "warning", reported: "success", cleared: "success", rejected: "danger" };

/** ZATCA e-invoicing (phase 2): the seller's details, then the device onboarded with a one-time password. */
export function EInvoicingCard() {
  const { org } = useOrg();
  const qc = useQueryClient();
  const key = ["einvoicing", org.id];
  const current = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/einvoicing", { params: { path: { org_id: org.id } } })),
  });
  return current.data ? <EInvoicingForm settings={current.data} onSaved={(s) => { qc.setQueryData(key, s); }} />
    : current.isError ? <ErrorNotice error={current.error} /> : null;
}

function EInvoicingForm({ settings: s, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const path = { params: { path: { org_id: org.id } } };
  const [form, setForm] = useState<SettingsIn>({
    environment: s.environment, crn: s.crn, street: s.street, building_number: s.building_number, district: s.district,
    city: s.city, postal_code: s.postal_code, branch_name: s.branch_name, industry: s.industry,
  });
  const [otp, setOtp] = useState("");
  const done = (x: Settings) => {
    onSaved(x);
    void qc.invalidateQueries({ queryKey: ["einvoicing-status", org.id] });
  };
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/einvoicing", { ...path, body: form })),
    onSuccess: (x) => { done(x); toast(t("common.saved")); },
  });
  const onboard = useMutation({
    mutationFn: async () => {
      await unwrap(api.PUT("/api/v1/orgs/{org_id}/einvoicing", { ...path, body: form }));
      return unwrap(api.POST("/api/v1/orgs/{org_id}/einvoicing/onboard", { ...path, body: { otp } }));
    },
    onSuccess: (x) => { done(x); setOtp(""); toast(t("einvoice.onboardedToast")); },
  });
  const off = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/einvoicing/off", path)),
    onSuccess: done,
  });
  const set = (k: keyof SettingsIn) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const on = s.status === "onboarded";
  return (
    <Card title={t("einvoice.title")} actions={on ? <Badge tone="success">{t("einvoice.on")}</Badge> : <Badge>{t("einvoice.off")}</Badge>}>
      <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="muted small">{t("einvoice.hint")}</p>
        {on && (
          <p className="notice notice-success small">
            {t("einvoice.onboardedOn", { env: t(`einvoice.env.${s.environment}`), date: date(s.onboarded_at) })}
            {s.certificate_expires_at && ` ${t("einvoice.certUntil", { date: date(s.certificate_expires_at) })}`}
            {s.pending > 0 && ` ${t("einvoice.pendingCount", { n: s.pending })}`}
            {s.rejected > 0 && ` ${t("einvoice.rejectedCount", { n: s.rejected })}`}
          </p>
        )}
        {(save.isError || onboard.isError || off.isError) && <ErrorNotice error={save.error ?? onboard.error ?? off.error} />}
        <Field label={t("einvoice.environment")} hint={on ? t("einvoice.environmentLocked") : t("einvoice.environmentHint")}>
          <Select value={form.environment} disabled={on} onChange={(e) => setForm({ ...form, environment: e.target.value as SettingsIn["environment"] })}>
            <option value="sandbox">{t("einvoice.env.sandbox")}</option>
            <option value="simulation">{t("einvoice.env.simulation")}</option>
            <option value="production">{t("einvoice.env.production")}</option>
          </Select>
        </Field>
        <div className="grid-2">
          <Field label={t("einvoice.crn")} optional={false}>
            <Input value={form.crn} onChange={set("crn")} maxLength={10} inputMode="numeric" ltr />
          </Field>
          <Field label={t("einvoice.branch")} optional>
            <Input value={form.branch_name} onChange={set("branch_name")} maxLength={64} />
          </Field>
          <Field label={t("einvoice.street")}>
            <Input value={form.street} onChange={set("street")} maxLength={120} />
          </Field>
          <Field label={t("customers.buildingNumber")}>
            <Input value={form.building_number} onChange={set("building_number")} maxLength={4} inputMode="numeric" ltr />
          </Field>
          <Field label={t("customers.district")}>
            <Input value={form.district} onChange={set("district")} maxLength={120} />
          </Field>
          <Field label={t("customers.city")}>
            <Input value={form.city} onChange={set("city")} maxLength={120} />
          </Field>
          <Field label={t("einvoice.postalCode")}>
            <Input value={form.postal_code} onChange={set("postal_code")} maxLength={5} inputMode="numeric" ltr />
          </Field>
          <Field label={t("einvoice.industry")} optional>
            <Input value={form.industry} onChange={set("industry")} maxLength={64} />
          </Field>
        </div>
        {s.problems.length > 0 && !on && (
          <ul className="small muted" aria-label={t("einvoice.missing")}>
            {s.problems.map((p) => <li key={p} dir="auto">{p}</li>)}
          </ul>
        )}
        <div className="form-actions">
          <Button type="submit" loading={save.isPending}>{t("einvoice.save")}</Button>
        </div>
      </form>
      {!on ? (
        <form className="row" style={{ alignItems: "flex-end", marginBlockStart: "var(--space-4)" }}
          onSubmit={(e) => { e.preventDefault(); onboard.mutate(); }}>
          <Field label={t("einvoice.otp")} hint={form.environment === "sandbox" ? t("einvoice.otpSandbox") : t("einvoice.otpHint")}>
            <Input value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} maxLength={6} inputMode="numeric" required ltr />
          </Field>
          <Button type="submit" variant="primary" icon={<PlugZap size={16} />} loading={onboard.isPending}>{t("einvoice.onboard")}</Button>
        </form>
      ) : (
        <div className="form-actions" style={{ marginBlockStart: "var(--space-4)" }}>
          <Button variant="danger" icon={<Power size={16} />} loading={off.isPending}
            onClick={() => window.confirm(t("einvoice.offConfirm")) && off.mutate()}>{t("einvoice.turnOff")}</Button>
        </div>
      )}
    </Card>
  );
}

/** What ZATCA said about one invoice's or credit note's e-invoice. */
export function EInvoiceStatus({ doc, onChange }: { doc: Summary; onChange: () => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const toast = useToast();
  const send = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/einvoicing/documents/{doc_id}/send", {
      params: { path: { org_id: org.id, doc_id: doc.id } },
    })),
    onSuccess: (d) => { onChange(); toast(d.status === "pending" ? t("einvoice.stillPending") : t(`einvoice.status.${d.status}`)); },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <Card title={t("einvoice.document")} tight actions={
      <span className="row">
        <Badge tone={TONE[doc.status]}>{t(`einvoice.status.${doc.status}`)}</Badge>
        <Button size="sm" icon={<Download size={14} />}
          onClick={() => download(`/api/v1/orgs/${org.id}/einvoicing/documents/${doc.id}/xml`, `${doc.number}.xml`)
            .catch((e: Error) => toast(errorText(e, t("common.error")), "error"))}>
          XML
        </Button>
        {doc.status === "pending" && can("invoices.post") && (
          <Button size="sm" icon={<Send size={14} />} loading={send.isPending} onClick={() => send.mutate()}>{t("einvoice.sendAgain")}</Button>
        )}
      </span>
    }>
      <div className="stack-sm small">
        <span className="muted">
          {t(doc.kind === "standard" ? "einvoice.kind.standard" : "einvoice.kind.simplified")} · {t("einvoice.counter", { n: doc.icv })}
          {doc.environment !== "production" && ` · ${t(`einvoice.env.${doc.environment}`)}`}
        </span>
        {doc.note && <span dir="auto">{doc.note}</span>}
        {doc.errors.map((e) => <span key={e} dir="ltr" style={{ color: "var(--color-danger-text)" }}>{e}</span>)}
        {doc.warnings.map((w) => <span key={w} dir="ltr" className="muted">{w}</span>)}
      </div>
    </Card>
  );
}
