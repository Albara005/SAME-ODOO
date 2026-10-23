import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { Badge, Button, Card, Checkbox, ErrorNotice, Field, Input, PageHeader, QueryView, Select, useToast } from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { parseDec } from "../lib/decimal";

type Settings = Schemas["SettingsOut"];

/** Share of the budget spent, 0..1 (display only). */
function spentShare(spend: string, budget: string): number {
  const s = parseDec(spend);
  const b = parseDec(budget);
  if (!s || !b || b.n === 0n) return 0;
  return Math.min(1, Number(s.n * 10n ** BigInt(b.scale)) / Number(b.n * 10n ** BigInt(s.scale)));
}

/** "0.0040" -> "0.004", "50.0000" -> "50.00": at least cents, never drops a significant digit. */
export function usd(value: string): string {
  const [i, f = ""] = value.split(".");
  return `${i}.${f.replace(/0+$/, "").padEnd(2, "0")}`;
}

export function AiSettingsPage() {
  const { t } = useI18n();
  const { org } = useOrg();
  const settings = useQuery({
    queryKey: ["ai-settings", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/assistant/settings", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader title={t("nav.aiSettings")} subtitle={t("ai.subtitle")} />
      <QueryView query={settings}>{(s) => <SettingsForm settings={s} />}</QueryView>
      <Usage />
    </div>
  );
}

const ROUTE_LABEL: Record<string, MessageKey> = {
  assistant: "ai.route.assistant",
  assistant_deep: "ai.route.assistant_deep",
  app_builder: "ai.route.app_builder",
};

function SettingsForm({ settings: s }: { settings: Settings }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [enabled, setEnabled] = useState(s.enabled);
  const [budget, setBudget] = useState(usd(s.monthly_budget_usd).replace(/\.00$/, ""));
  const [perUser, setPerUser] = useState(s.user_monthly_budget_usd ? usd(s.user_monthly_budget_usd).replace(/\.00$/, "") : "");
  const [routes, setRoutes] = useState(s.routes);
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/assistant/settings", {
        params: { path: { org_id: org.id } },
        body: { enabled, monthly_budget_usd: budget, user_monthly_budget_usd: perUser.trim() || null, routes },
      })),
    onSuccess: (updated) => {
      qc.setQueryData(["ai-settings", org.id], updated);
      toast(t("common.saved"));
    },
  });
  const share = spentShare(s.month_spend_usd, s.monthly_budget_usd);
  const tone = share >= 0.9 ? "danger" : share >= 0.7 ? "warning" : "primary";
  const err = save.error instanceof ApiError ? save.error.fields : {};
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
      <div className="grid-2">
        <Card title={t("ai.status")}>
          <div className="stack-sm">
            <Checkbox label={t("ai.enabled")} checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <p className="muted small">{t("ai.enabledHint")}</p>
          </div>
        </Card>
        <Card title={t("ai.budget")}>
          <div className="stack-sm">
            <Field label={t("ai.monthlyBudget")} error={err["monthly_budget_usd"]}>
              <Input inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} ltr />
            </Field>
            <div className={`meter meter-${tone}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}
              aria-label={t("ai.spent")}>
              <span style={{ inlineSize: `${share * 100}%` }} />
            </div>
            <span className="small">
              {t("ai.spentOf", { spent: usd(s.month_spend_usd), budget: usd(s.monthly_budget_usd) })}
            </span>
            <Field label={t("ai.perUser")} hint={t("ai.perUserHint")} error={err["user_monthly_budget_usd"]} optional>
              <Input inputMode="decimal" value={perUser} onChange={(e) => setPerUser(e.target.value)} ltr />
            </Field>
          </div>
        </Card>
      </div>
      <Card title={t("ai.routes")}>
        <p className="muted small" style={{ marginBlockEnd: "var(--space-3)" }}>{t("ai.routesHint")}</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("ai.task")}</th>
                <th>{t("ai.model")}</th>
                <th>{t("ai.effort")}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(routes).map(([name, r]) => (
                <tr key={name}>
                  <td>{ROUTE_LABEL[name] ? t(ROUTE_LABEL[name]!) : name}</td>
                  <td>
                    <Select aria-label={`${t("ai.model")} ${name}`} value={r.model} onChange={(e) => setRoutes({ ...routes, [name]: { ...r, model: e.target.value } })}>
                      {s.models.map((m) => <option key={m} value={m}>{m}</option>)}
                    </Select>
                  </td>
                  <td>
                    <Select aria-label={`${t("ai.effort")} ${name}`} value={r.effort} onChange={(e) => setRoutes({ ...routes, [name]: { ...r, effort: e.target.value } })}>
                      {s.efforts.map((x) => <option key={x} value={x}>{x}</option>)}
                    </Select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="form-actions">
        <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
      </div>
    </form>
  );
}

function Usage() {
  const { t, amount } = useI18n();
  const { org, can } = useOrg();
  const usage = useQuery({
    queryKey: ["ai-usage", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/assistant/usage", { params: { path: { org_id: org.id } } })),
  });
  const members = useQuery({
    queryKey: ["members", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })),
    enabled: can("members.read"),
  });
  const who = (id: string) => {
    const m = members.data?.find((x) => x.user_id === id);
    return m ? m.full_name || m.email : id ? `${id.slice(0, 8)}…` : t("ai.system");
  };
  return (
    <Card title={t("ai.usage")}>
      <QueryView query={usage}>
        {(u) =>
          u.by_model.length === 0 ? (
            <p className="muted">{t("ai.noUsage")}</p>
          ) : (
            <div className="grid-2">
              {([["by_model", t("ai.model")], ["by_user", t("ai.user")]] as const).map(([key, label]) => (
                <div key={key} className="stack-sm">
                  <span className="row-between">
                    <strong>{label}</strong>
                    {key === "by_model" && <Badge tone="primary">{u.month} · ${usd(u.total_cost_usd)}</Badge>}
                  </span>
                  <table>
                    <thead>
                      <tr>
                        <th>{label}</th>
                        <th className="end">{t("ai.requests")}</th>
                        <th className="end">{t("ai.tokens")}</th>
                        <th className="end">USD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {u[key].map((r) => (
                        <tr key={r.key}>
                          <td className={key === "by_model" ? "mono" : ""}>{key === "by_model" ? r.key : who(r.key)}</td>
                          <td className="end num">{r.requests}</td>
                          <td className="end num">{amount(String(r.input_tokens + r.output_tokens))}</td>
                          <td className="end num">{usd(r.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )
        }
      </QueryView>
    </Card>
  );
}
