import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Server, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { StatusBadge } from "../components/business";
import { Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select, Textarea, useToast } from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";

type Options = { hosting_mode: string; options: { kinds: string[]; sizes: Record<string, string>; regions: string[]; note: string } };
type Req = Schemas["RequestOut"];

/** Private hosting is never created automatically: request, then an owner approves the exact monthly cost. */
export function HostingPage() {
  const { t } = useI18n();
  const { org } = useOrg();
  const status = useQuery({
    queryKey: ["hosting", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hosting", { params: { path: { org_id: org.id } } })) as Promise<Options>,
  });
  const requests = useQuery({
    queryKey: ["hosting-requests", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hosting/requests", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader title={t("nav.hosting")} subtitle={t("hosting.subtitle")} />
      <QueryView query={status}>
        {(s) => (
          <>
            <Card tight>
              <div className="row">
                <ShieldCheck size={22} aria-hidden style={{ color: "var(--gold-deep)" }} />
                <div className="grow stack-sm" style={{ gap: 2 }}>
                  <strong>{t("hosting.current")}: {t(`hosting.mode.${s.hosting_mode}` as MessageKey)}</strong>
                  <span className="muted small">{t("hosting.sharedNote")}</span>
                </div>
              </div>
            </Card>
            <RequestForm options={s.options} />
          </>
        )}
      </QueryView>
      <Card title={t("hosting.requests")}>
        <QueryView query={requests}>
          {(list) => (list.length === 0 ? <Empty>{t("hosting.noRequests")}</Empty> : <Requests list={list} />)}
        </QueryView>
      </Card>
    </div>
  );
}

function RequestForm({ options }: { options: Options["options"] }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ kind: options.kinds[0] ?? "", size: Object.keys(options.sizes)[0] ?? "", region: options.regions[0] ?? "", reason: "" });
  const quote = useQuery({
    queryKey: ["hosting-quote", org.id, form.kind, form.size, form.region],
    queryFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/hosting/quote", {
        params: { path: { org_id: org.id } }, body: { kind: form.kind, size: form.size, region: form.region },
      })) as Promise<{ monthly_usd: string; specification: string }>,
  });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hosting/requests", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => {
      toast(t("hosting.requested"));
      setForm({ ...form, reason: "" });
      void qc.invalidateQueries({ queryKey: ["hosting-requests", org.id] });
    },
  });
  return (
    <Card title={t("hosting.newRequest")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && <ErrorNotice error={create.error} />}
        <div className="grid-3">
          <Field label={t("hosting.kind")}>
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {options.kinds.map((k) => <option key={k} value={k}>{t(`hosting.kind.${k}` as MessageKey)}</option>)}
            </Select>
          </Field>
          <Field label={t("hosting.size")}>
            <Select value={form.size} onChange={(e) => setForm({ ...form, size: e.target.value })}>
              {Object.keys(options.sizes).map((k) => <option key={k} value={k}>{t(`hosting.size.${k}` as MessageKey)}</option>)}
            </Select>
          </Field>
          <Field label={t("hosting.region")}>
            <Select value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })}>
              {options.regions.map((k) => <option key={k} value={k}>{t(`hosting.region.${k}` as MessageKey)}</option>)}
            </Select>
          </Field>
        </div>
        <div className="quote">
          <Server size={20} aria-hidden />
          <div className="grow stack-sm" style={{ gap: 2 }}>
            <span className="muted small" dir="ltr" style={{ textAlign: "start" }}>{quote.data?.specification ?? "…"}</span>
            <span>
              <strong className="num" style={{ fontSize: "1.3rem" }}>${quote.data?.monthly_usd ?? "…"}</strong> <span className="muted">{t("hosting.perMonth")}</span>
            </span>
          </div>
          <Badge tone="warning">{t("hosting.placeholderPrice")}</Badge>
        </div>
        <Field label={t("hosting.reason")} optional>
          <Textarea value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} maxLength={2000} />
        </Field>
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={create.isPending}>{t("hosting.request")}</Button>
        </div>
      </form>
    </Card>
  );
}

function Requests({ list }: { list: Req[] }) {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [approving, setApproving] = useState<Req | null>(null);
  const cancel = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/orgs/{org_id}/hosting/requests/{request_id}/cancel", { params: { path: { org_id: org.id, request_id: id } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hosting-requests", org.id] }),
    onError: (e) => toast(e instanceof ApiError ? e.message : t("common.error"), "error"),
  });
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t("hosting.kind")}</th>
            <th>{t("hosting.size")}</th>
            <th>{t("hosting.region")}</th>
            <th className="end">{t("hosting.monthly")}</th>
            <th>{t("common.status")}</th>
            <th>{t("common.date")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {list.map((r) => (
            <tr key={r.id}>
              <td>{t(`hosting.kind.${r.kind}` as MessageKey)}</td>
              <td>{t(`hosting.size.${r.size}` as MessageKey)}</td>
              <td>{t(`hosting.region.${r.region}` as MessageKey)}</td>
              <td className="end num">${r.monthly_usd}</td>
              <td>
                <StatusBadge status={r.status} />
                {r.operator_note && <div className="muted small" dir="auto">{r.operator_note}</div>}
              </td>
              <td className="num small">{date(r.created_at)}</td>
              <td className="end">
                <span className="row" style={{ justifyContent: "flex-end", gap: "var(--space-2)" }}>
                  {r.status === "pending_approval" && can("org.manage") && (
                    <Button size="sm" variant="primary" onClick={() => setApproving(r)}>{t("hosting.approve")}</Button>
                  )}
                  {(r.status === "pending_approval" || r.status === "approved") && (
                    <Button size="sm" variant="ghost" icon={<Ban size={14} />} loading={cancel.isPending && cancel.variables === r.id} onClick={() => cancel.mutate(r.id)}>
                      {t("common.cancel")}
                    </Button>
                  )}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {approving && <ApproveDialog req={approving} onClose={() => setApproving(null)} />}
    </div>
  );
}

function ApproveDialog({ req, onClose }: { req: Req; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [typed, setTyped] = useState("");
  const approve = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/hosting/requests/{request_id}/approve", {
        params: { path: { org_id: org.id, request_id: req.id } }, body: { confirm_monthly_usd: typed },
      })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["hosting-requests", org.id] });
      onClose();
    },
  });
  return (
    <Dialog open onClose={onClose} title={t("hosting.approveTitle")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); approve.mutate(); }}>
        <p>{t("hosting.approveBody", { amount: req.monthly_usd })}</p>
        <p className="muted small" dir="ltr" style={{ textAlign: "start" }}>{req.specification}</p>
        {approve.isError && <ErrorNotice error={approve.error} />}
        <Field label={t("hosting.typeAmount", { amount: req.monthly_usd })}>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} inputMode="decimal" placeholder={req.monthly_usd} required ltr />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={approve.isPending} disabled={typed.trim() !== req.monthly_usd}>
            {t("hosting.approve")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
