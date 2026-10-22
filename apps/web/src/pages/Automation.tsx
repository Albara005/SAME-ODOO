import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, Bell, ClipboardCheck, Filter, ListChecks, Play, Plus, Bot, Trash2, Zap, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { StatusBadge } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, errorText, useToast,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";

type Workflow = Schemas["WorkflowOut"];
type Catalog = { triggers: Record<string, string>; operators: string[]; actions: Record<string, { required: string[]; optional: string[] }> };
type Condition = { field: string; op: string; value?: string };
type Action = Record<string, string | number> & { type: string };

const ACTION_ICON: Record<string, LucideIcon> = { create_task: ListChecks, notify: Bell, require_approval: ClipboardCheck, run_agent: Bot };

function useCatalog() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["wf-catalog", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/workflows/catalog", { params: { path: { org_id: org.id } } })) as Promise<Catalog>,
    staleTime: Infinity,
  });
}

function useTriggerLabel() {
  const { t } = useI18n();
  return (key: string) => {
    const k = `wf.trigger.${key}` as MessageKey;
    return t(k) === k ? key : t(k);
  };
}

// ---------------------------------------------------------------- list

export function AutomationPage() {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const triggerLabel = useTriggerLabel();
  const workflows = useQuery({
    queryKey: ["workflows", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/workflows", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.automation")}
        subtitle={t("wf.subtitle")}
        actions={<Link className="btn btn-primary" to={path("automation/new")}><Plus size={16} aria-hidden /> {t("wf.new")}</Link>}
      />
      <QueryView query={workflows}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t("wf.none")}</Empty>
          ) : (
            <div className="stack-sm">
              {list.map((w) => (
                <Link key={w.id} to={path(`automation/${w.id}`)} className="card card-tight wf-row">
                  <span className="app-icon hue-lapis"><Zap size={20} aria-hidden /></span>
                  <span className="grow stack-sm" style={{ gap: 2 }}>
                    <strong>{w.name}</strong>
                    <span className="muted small">
                      {t("wf.when")} {triggerLabel(w.trigger)} · {t("wf.steps", { n: w.actions.length })}
                    </span>
                  </span>
                  <Badge tone={w.enabled ? "success" : "neutral"}>{w.enabled ? t("common.active") : t("wf.paused")}</Badge>
                </Link>
              ))}
            </div>
          )
        }
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- builder

export function WorkflowEditorPage() {
  const { workflowId } = useParams();
  const { org } = useOrg();
  const catalog = useCatalog();
  const existing = useQuery({
    queryKey: ["workflows", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/workflows", { params: { path: { org_id: org.id } } })),
    enabled: Boolean(workflowId),
  });
  if (catalog.isPending || (workflowId && existing.isPending)) return <QueryView query={catalog}>{() => null}</QueryView>;
  if (catalog.isError) return <ErrorNotice error={catalog.error} />;
  const wf = existing.data?.find((w) => w.id === workflowId);
  return <WorkflowEditor catalog={catalog.data} workflow={wf} />;
}

function blankAction(type: string): Action {
  return type === "create_task" ? { type, title: "", assignee: "owner" } : type === "notify" ? { type, title: "", to: "owner" } : type === "require_approval" ? { type, permission: "invoices.post", message: "" } : { type };
}

function WorkflowEditor({ catalog, workflow }: { catalog: Catalog; workflow?: Workflow }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const triggerLabel = useTriggerLabel();
  const [name, setName] = useState(workflow?.name ?? "");
  const [trigger, setTrigger] = useState(workflow?.trigger ?? "invoice.posted");
  const [conditions, setConditions] = useState<Condition[]>((workflow?.conditions as Condition[]) ?? []);
  const [actions, setActions] = useState<Action[]>((workflow?.actions as Action[]) ?? [blankAction("create_task")]);
  const permissions = useQuery({ queryKey: ["permissions"], queryFn: () => unwrap(api.GET("/api/v1/permissions")) as Promise<Record<string, string>>, staleTime: Infinity });
  const agents = useQuery({
    queryKey: ["agents", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/agents", { params: { path: { org_id: org.id } } })),
  });

  const save = useMutation({
    mutationFn: () => {
      const cleanActions = actions.map((a) => Object.fromEntries(Object.entries(a).filter(([, v]) => v !== "")));
      const cleanConditions = conditions.filter((c) => c.field).map((c) => (c.op === "exists" ? { field: c.field, op: c.op } : c));
      return workflow
        ? unwrap(api.PATCH("/api/v1/orgs/{org_id}/workflows/{workflow_id}", {
            params: { path: { org_id: org.id, workflow_id: workflow.id } }, body: { name, conditions: cleanConditions, actions: cleanActions },
          }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/workflows", {
            params: { path: { org_id: org.id } },
            body: { name, trigger, conditions: cleanConditions, actions: cleanActions, enabled: true, notify_owner_on_failure: true },
          }));
    },
    onSuccess: (w) => {
      void qc.invalidateQueries({ queryKey: ["workflows", org.id] });
      navigate(path(`automation/${w.id}`));
    },
  });
  const updateAction = (i: number, patch: Partial<Action>) => setActions(actions.map((a, j) => (j === i ? ({ ...a, ...patch } as Action) : a)));
  const updateCond = (i: number, patch: Partial<Condition>) => setConditions(conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <PageHeader title={workflow ? t("wf.edit") : t("wf.new")} subtitle={t("wf.templateHint")} />
      {save.isError && <ErrorNotice error={save.error} />}
      <Card>
        <Field label={t("common.name")}>
          <Input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} placeholder={t("wf.namePlaceholder")} />
        </Field>
      </Card>

      <ol className="flow">
        <li className="flow-node trigger">
          <span className="flow-icon"><Zap size={18} aria-hidden /></span>
          <div className="grow stack-sm">
            <strong>{t("wf.trigger")}</strong>
            <Select aria-label={t("wf.trigger")} value={trigger} onChange={(e) => setTrigger(e.target.value)} disabled={Boolean(workflow)}>
              {Object.keys(catalog.triggers).map((k) => <option key={k} value={k}>{triggerLabel(k)}</option>)}
            </Select>
          </div>
        </li>

        <li className="flow-node">
          <span className="flow-icon"><Filter size={18} aria-hidden /></span>
          <div className="grow stack-sm">
            <span className="row-between">
              <strong>{t("wf.conditions")}</strong>
              <Button size="sm" icon={<Plus size={14} />} onClick={() => setConditions([...conditions, { field: "total", op: "gte", value: "" }])}>
                {t("wf.addCondition")}
              </Button>
            </span>
            {conditions.length === 0 && <span className="muted small">{t("wf.always")}</span>}
            {conditions.map((c, i) => (
              <div key={i} className="row" style={{ alignItems: "flex-end" }}>
                <Input aria-label={`${t("wf.field")} ${i + 1}`} value={c.field} onChange={(e) => updateCond(i, { field: e.target.value })} placeholder="total" style={{ maxWidth: 160 }} ltr />
                <Select aria-label={`${t("wf.operator")} ${i + 1}`} value={c.op} onChange={(e) => updateCond(i, { op: e.target.value })} style={{ maxWidth: 170 }}>
                  {catalog.operators.map((o) => <option key={o} value={o}>{t(`wf.op.${o}` as MessageKey)}</option>)}
                </Select>
                {c.op !== "exists" && (
                  <Input aria-label={`${t("wf.value")} ${i + 1}`} value={String(c.value ?? "")} onChange={(e) => updateCond(i, { value: e.target.value })} style={{ maxWidth: 180 }} ltr />
                )}
                <IconButton label={`${t("common.delete")} ${i + 1}`} onClick={() => setConditions(conditions.filter((_, j) => j !== i))}><Trash2 size={16} /></IconButton>
              </div>
            ))}
          </div>
        </li>

        {actions.map((a, i) => {
          const Icon = ACTION_ICON[a.type] ?? ListChecks;
          return (
            <li key={i} className="flow-node">
              <span className="flow-icon step"><Icon size={18} aria-hidden /></span>
              <div className="grow stack-sm">
                <span className="row-between">
                  <Select aria-label={`${t("wf.action")} ${i + 1}`} value={a.type} onChange={(e) => updateAction(i, blankAction(e.target.value))} style={{ maxWidth: 260 }}>
                    {Object.keys(catalog.actions).map((k) => <option key={k} value={k}>{t(`wf.action.${k}` as MessageKey)}</option>)}
                  </Select>
                  <IconButton label={`${t("common.delete")} ${t("wf.action")} ${i + 1}`} onClick={() => setActions(actions.filter((_, j) => j !== i))} disabled={actions.length === 1}>
                    <Trash2 size={16} />
                  </IconButton>
                </span>
                <ActionFields action={a} onChange={(p) => updateAction(i, p)} permissions={permissions.data ?? {}} agents={agents.data ?? []} />
              </div>
            </li>
          );
        })}
        <li className="flow-add">
          <ArrowDown size={16} aria-hidden />
          <Button size="sm" icon={<Plus size={14} />} onClick={() => setActions([...actions, blankAction("notify")])}>{t("wf.addAction")}</Button>
        </li>
      </ol>

      <div className="form-actions">
        <Button onClick={() => navigate(-1)}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
      </div>
    </form>
  );
}

function ActionFields({ action: a, onChange, permissions, agents }: {
  action: Action; onChange: (p: Partial<Action>) => void; permissions: Record<string, string>; agents: Schemas["AgentOut"][];
}) {
  const { t } = useI18n();
  const text = (key: string, label: MessageKey, opts: { required?: boolean; ltr?: boolean } = {}) => (
    <Field label={t(label)} optional={!opts.required}>
      <Input value={String(a[key] ?? "")} onChange={(e) => onChange({ [key]: e.target.value })} required={opts.required} ltr={opts.ltr} />
    </Field>
  );
  const who = (key: string, label: MessageKey) => (
    <Field label={t(label)}>
      <Select value={String(a[key] ?? "owner")} onChange={(e) => onChange({ [key]: e.target.value })}>
        <option value="owner">{t("wf.who.owner")}</option>
        <option value="actor">{t("wf.who.actor")}</option>
        {["accountant", "sales", "warehouse", "admin"].map((r) => <option key={r} value={`role:${r}`}>{t("wf.who.role", { role: t(`role.${r}` as MessageKey) })}</option>)}
      </Select>
    </Field>
  );
  if (a.type === "create_task")
    return (
      <div className="grid-2">
        {text("title", "wf.f.title", { required: true })}
        {text("description", "wf.f.description")}
        {who("assignee", "wf.f.assignee")}
        <Field label={t("wf.f.dueInDays")} optional>
          <Input type="number" min={0} max={365} value={String(a.due_in_days ?? "")} onChange={(e) => onChange({ due_in_days: e.target.value === "" ? "" : Number(e.target.value) })} ltr />
        </Field>
      </div>
    );
  if (a.type === "notify")
    return (
      <div className="grid-2">
        {text("title", "wf.f.title", { required: true })}
        {text("body", "wf.f.body")}
        {who("to", "wf.f.to")}
      </div>
    );
  if (a.type === "require_approval")
    return (
      <div className="grid-2">
        <Field label={t("wf.f.permission")} hint={t("wf.f.permissionHint")}>
          <Select value={String(a.permission ?? "")} onChange={(e) => onChange({ permission: e.target.value })}>
            {Object.keys(permissions).map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
        </Field>
        {text("message", "wf.f.message")}
      </div>
    );
  return (
    <div className="grid-2">
      <Field label={t("wf.f.agent")}>
        <Select value={String(a.agent_id ?? "")} onChange={(e) => onChange({ agent_id: e.target.value })} required>
          <option value="">—</option>
          {agents.filter((x) => x.published_version !== null).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </Select>
      </Field>
      {text("input", "wf.f.input", { required: true })}
    </div>
  );
}

// ---------------------------------------------------------------- detail

export function WorkflowPage() {
  const { workflowId = "" } = useParams();
  const { t, date } = useI18n();
  const { org, path } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const triggerLabel = useTriggerLabel();
  const workflows = useQuery({
    queryKey: ["workflows", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/workflows", { params: { path: { org_id: org.id } } })),
  });
  const runs = useQuery({
    queryKey: ["wf-runs", org.id, workflowId],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/workflows/{workflow_id}/runs", { params: { path: { org_id: org.id, workflow_id: workflowId } } })),
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/workflows/{workflow_id}", { params: { path: { org_id: org.id, workflow_id: workflowId } }, body: { enabled } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["workflows", org.id] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const process = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/automation/process", { params: { path: { org_id: org.id } } })) as Promise<Record<string, number>>,
    onSuccess: (r) => {
      toast(t("wf.processed", { n: r.processed ?? 0 }));
      void qc.invalidateQueries({ queryKey: ["wf-runs", org.id, workflowId] });
      void qc.invalidateQueries({ queryKey: ["approvals", org.id] });
      void qc.invalidateQueries({ queryKey: ["tasks", org.id] });
      void qc.invalidateQueries({ queryKey: ["notifications", org.id] });
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : t("common.error"), "error"),
  });
  const w = workflows.data?.find((x) => x.id === workflowId);
  if (workflows.isPending) return <QueryView query={workflows}>{() => null}</QueryView>;
  if (!w) return <ErrorNotice error={new Error(t("error.notFound"))} />;
  return (
    <div className="stack">
      <PageHeader
        title={w.name}
        subtitle={`${t("wf.when")} ${triggerLabel(w.trigger)}`}
        actions={
          <>
            <Checkbox label={t("common.active")} checked={w.enabled} onChange={(e) => toggle.mutate(e.target.checked)} />
            <Link className="btn" to={path(`automation/${w.id}/edit`)}>{t("common.edit")}</Link>
            <Button variant="primary" icon={<Play size={16} />} loading={process.isPending} onClick={() => process.mutate()}>{t("wf.processNow")}</Button>
          </>
        }
      />
      <Card title={t("wf.runs")}>
        <QueryView query={runs}>
          {(list) =>
            list.length === 0 ? (
              <Empty>{t("wf.noRuns")}</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("common.date")}</th>
                      <th>{t("common.status")}</th>
                      <th>{t("wf.stepsCol")}</th>
                      <th>{t("wf.attempts")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((r) => (
                      <tr key={r.id}>
                        <td className="num small">{date(r.created_at)}</td>
                        <td>
                          <StatusBadge status={r.status} />
                          {r.error && <div className="small" style={{ color: "var(--color-danger-text)" }} dir="ltr">{r.error}</div>}
                        </td>
                        <td>
                          <span className="row" style={{ gap: 4 }}>
                            {r.steps.map((s) => (
                              <span key={s.index} title={`${t(`wf.action.${s.type}` as MessageKey)} · ${s.status}`} className={`step-pill step-pill-${s.status}`}>
                                {s.index + 1}
                              </span>
                            ))}
                          </span>
                        </td>
                        <td className="num">{r.attempts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </QueryView>
      </Card>
    </div>
  );
}
