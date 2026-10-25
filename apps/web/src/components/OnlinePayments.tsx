import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Copy, ExternalLink, Link2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import { isPositive } from "../lib/decimal";
import { StatusBadge } from "./business";
import { Button, Card, ErrorNotice, Field, Input, Select, useToast } from "./ui";

type Invoice = Schemas["InvoiceOut"];
type Provider = Schemas["PaymentSettingsIn"]["provider"];

/** Which provider takes online payments ("none" when they're off). */
export function usePaymentProvider() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["payments-available", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payments/available", { params: { path: { org_id: org.id } } })),
    enabled: can("invoices.read"),
    staleTime: 60_000,
  });
}

/** Links a customer pays through. The invoice is only marked paid when the provider confirms the payment. */
export function PaymentLinksCard({ invoice: inv, onPaid }: { invoice: Invoice; onPaid: () => void }) {
  const { t, money, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const provider = usePaymentProvider().data?.provider ?? "none";
  const ids = { org_id: org.id, invoice_id: inv.id };
  const key = ["payment-links", org.id, inv.id];
  const links = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/invoices/{invoice_id}/payment-links", { params: { path: ids } })),
    enabled: inv.status !== "draft",
    // While a link is open, look for the provider's confirmation every few seconds.
    refetchInterval: (q) => (q.state.data?.some((l) => l.status === "open") ? 5000 : false),
  });
  const paidCount = links.data?.filter((l) => l.status === "paid").length;
  const seen = useRef(paidCount);
  useEffect(() => {
    if (paidCount !== undefined && seen.current !== undefined && paidCount > seen.current) onPaid();
    seen.current = paidCount;
  }, [paidCount, onPaid]);

  const onError = (e: Error) => toast(e instanceof ApiError ? e.message : t("common.error"), "error");
  const copy = (text: string) => void navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")), () => undefined);
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/payment-links", { params: { path: ids }, body: {} })),
    onSuccess: (link) => {
      void qc.invalidateQueries({ queryKey: key });
      copy(link.url);
    },
    onError,
  });
  const cancel = useMutation({
    mutationFn: (linkId: string) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/invoices/{invoice_id}/payment-links/{link_id}/cancel", {
        params: { path: { ...ids, link_id: linkId } },
      })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
    onError,
  });

  const list = links.data ?? [];
  const canCreate = can("payments.write") && provider !== "none" && isPositive(inv.amount_due)
    && (inv.status === "posted" || inv.status === "partially_paid");
  if (!canCreate && list.length === 0) return null;
  return (
    <Card
      title={t("pay.title")}
      actions={canCreate && (
        <Button size="sm" icon={<Link2 size={14} />} loading={create.isPending} onClick={() => create.mutate()}>
          {t("pay.create")}
        </Button>
      )}
    >
      <div className="stack-sm">
        <p className="muted small">{provider === "test" ? t("pay.testHint") : t("pay.hint")}</p>
        {links.isError && <ErrorNotice error={links.error} />}
        {list.length > 0 && (
          <table aria-label={t("pay.title")}>
            <thead>
              <tr>
                <th>{t("common.status")}</th>
                <th className="end">{t("invoices.amount")}</th>
                <th>{t("pay.when")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((l) => (
                <tr key={l.id}>
                  <td>
                    <StatusBadge status={l.status} />
                    {l.note && <div className="small" style={{ color: "var(--color-danger-text)" }} dir="auto">{l.note}</div>}
                  </td>
                  <td className="end num">{money(l.amount, l.currency)}</td>
                  <td className="small num">
                    {l.paid_at ? date(l.paid_at) : l.status === "open" ? `${t("pay.until")} ${date(l.expires_at)}` : date(l.created_at)}
                  </td>
                  <td className="end">
                    {l.status === "open" && (
                      <span className="row" style={{ justifyContent: "flex-end" }}>
                        <Button size="sm" icon={<Copy size={14} />} onClick={() => copy(l.url)}>{t("pay.copy")}</Button>
                        <a className="btn btn-sm" href={l.url} target="_blank" rel="noopener noreferrer">
                          <ExternalLink size={14} aria-hidden /> {t("pay.open")}
                        </a>
                        {can("payments.write") && (
                          <Button size="sm" variant="danger" icon={<Ban size={14} />} loading={cancel.isPending && cancel.variables === l.id}
                            onClick={() => window.confirm(t("pay.cancelConfirm")) && cancel.mutate(l.id)}>
                            {t("pay.cancel")}
                          </Button>
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  );
}

/** The organization's payment provider. Secrets go in once and are never shown again. */
export function OnlinePaymentsCard() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["payment-settings", org.id];
  const current = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payments/settings", { params: { path: { org_id: org.id } } })),
  });
  const [provider, setProvider] = useState<Provider | null>(null);
  const [secretKey, setSecretKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [moyasarKey, setMoyasarKey] = useState("");
  const [moyasarToken, setMoyasarToken] = useState("");
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.PUT("/api/v1/orgs/{org_id}/payments/settings", {
        params: { path: { org_id: org.id } },
        body: {
          provider: provider ?? current.data?.provider ?? "none",
          stripe_secret_key: secretKey.trim() || null,
          stripe_webhook_secret: webhookSecret.trim() || null,
          moyasar_secret_key: moyasarKey.trim() || null,
          moyasar_webhook_secret: moyasarToken.trim() || null,
        },
      })),
    onSuccess: (s) => {
      qc.setQueryData(key, s);
      void qc.invalidateQueries({ queryKey: ["payments-available", org.id] });
      setProvider(null);
      setSecretKey("");
      setWebhookSecret("");
      setMoyasarKey("");
      setMoyasarToken("");
      toast(t("common.saved"));
    },
  });
  const copy = (text: string) => void navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")), () => undefined);
  const s = current.data;
  if (!s) return current.isError ? <ErrorNotice error={current.error} /> : null;
  const chosen = provider ?? s.provider;
  return (
    <Card title={t("pay.settings")}>
      <form
        className="stack-sm"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted small">{t("pay.settingsHint")}</p>
        {save.isError && <ErrorNotice error={save.error} />}
        <Field label={t("pay.provider")}>
          <Select value={chosen} onChange={(e) => setProvider(e.target.value as Provider)}>
            <option value="none">{t("pay.providerNone")}</option>
            <option value="moyasar">{t("pay.providerMoyasar")}</option>
            <option value="stripe">Stripe</option>
            {s.test_provider_available && <option value="test">{t("pay.providerTest")}</option>}
          </Select>
        </Field>
        {chosen === "stripe" && (
          <>
            <Field label={t("pay.stripeKey")} hint={s.has_stripe_key ? `${t("pay.stored")} ${s.stripe_key_hint}` : t("pay.stripeKeyHint")}
              optional={s.has_stripe_key}>
              <Input type="password" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} autoComplete="off"
                placeholder="sk_live_…" ltr />
            </Field>
            <Field label={t("pay.webhookUrl")} hint={t("pay.webhookUrlHint")}>
              <div className="row">
                <Input value={s.webhook_url} readOnly ltr aria-label={t("pay.webhookUrl")} />
                <Button size="sm" icon={<Copy size={14} />} onClick={() => copy(s.webhook_url)}>{t("pay.copy")}</Button>
              </div>
            </Field>
            <Field label={t("pay.webhookSecret")} hint={s.has_webhook_secret ? t("pay.stored") : t("pay.webhookSecretHint")}
              optional={s.has_webhook_secret}>
              <Input type="password" value={webhookSecret} onChange={(e) => setWebhookSecret(e.target.value)} autoComplete="off"
                placeholder="whsec_…" ltr />
            </Field>
          </>
        )}
        {chosen === "moyasar" && (
          <>
            <Field label={t("pay.moyasarKey")} hint={s.has_moyasar_key ? `${t("pay.stored")} ${s.moyasar_key_hint}` : t("pay.moyasarKeyHint")}
              optional={s.has_moyasar_key}>
              <Input type="password" value={moyasarKey} onChange={(e) => setMoyasarKey(e.target.value)} autoComplete="off"
                placeholder="sk_live_…" ltr />
            </Field>
            <Field label={t("pay.webhookUrl")} hint={t("pay.moyasarWebhookHint")}>
              <div className="row">
                <Input value={s.moyasar_webhook_url} readOnly ltr aria-label={t("pay.webhookUrl")} />
                <Button size="sm" icon={<Copy size={14} />} onClick={() => copy(s.moyasar_webhook_url)}>{t("pay.copy")}</Button>
              </div>
            </Field>
            <Field label={t("pay.moyasarToken")} hint={s.has_moyasar_webhook_secret ? t("pay.stored") : t("pay.moyasarTokenHint")}
              optional={s.has_moyasar_webhook_secret}>
              <Input type="password" value={moyasarToken} onChange={(e) => setMoyasarToken(e.target.value)} autoComplete="off" ltr />
            </Field>
          </>
        )}
        {chosen === "test" && <p className="notice notice-info small">{t("pay.testNotice")}</p>}
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={save.isPending}>{t("pay.save")}</Button>
        </div>
      </form>
    </Card>
  );
}
