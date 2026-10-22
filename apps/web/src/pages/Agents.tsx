import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CheckCircle2, FlaskConical, Play, Plus, Rocket, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { Tabs } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select, Textarea, errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { usd } from "./AiSettings";

type Agent = Schemas["AgentOut"];
type Version = Schemas["novara__agents__router__VersionOut"];
type Run = Schemas["novara__agents__router__RunOut"];
type Tab = "run" | "build" | "tests" | "versions" | "history";

function useAgent(id: string) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["agent", org.id, id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/agents/{agent_id}", { params: { path: { org_id: org.id, agent_id: id } } })),
  });
}

// ---------------------------------------------------------------- list

export function AgentsPage() {
  const { t } = useI18n();
  const { org, can, path } = useOrg();
  const [creating, setCreating] = useState(false);
  const agents = useQuery({
    queryKey: ["agents", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/agents", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.agents")}
        subtitle={t("agents.subtitle")}
        actions={can("agents.manage") && <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>{t("agents.new")}</Button>}
      />
      <QueryView query={agents}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t("agents.none")}</Empty>
          ) : (
            <div className="apps" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
              {list.map((a) => (
                <Link key={a.id} to={path(`agents/${a.id}`)} className="app-tile hue-slate">
                  <span className="row-between">
                    <span className="app-icon"><Bot size={22} aria-hidden /></span>
                    {a.published_version !== null ? (
                      <Badge tone={a.enabled ? "success" : "neutral"}>{a.enabled ? t("agents.live", { v: a.published_version }) : t("agents.off")}</Badge>
                    ) : (
                      <Badge>{t("agents.unpublished")}</Badge>
                    )}
                  </span>
                  <span className="stack-sm" style={{ gap: 2 }}>
                    <strong>{a.name}</strong>
                    {a.description && <span className="muted small">{a.description}</span>}
                  </span>
                </Link>
              ))}
            </div>
          )
        }
      </QueryView>
      {creating && <NewAgentDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewAgentDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: "", description: "" });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/agents", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: (a) => {
      void qc.invalidateQueries({ queryKey: ["agents", org.id] });
      navigate(path(`agents/${a.id}?tab=build`));
    },
  });
  return (
    <Dialog open onClose={onClose} title={t("agents.new")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && <ErrorNotice error={create.error} />}
        <Field label={t("common.name")}>
          <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} placeholder={t("agents.namePlaceholder")} />
        </Field>
        <Field label={t("common.description")} optional>
          <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={2000} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- agent

export function AgentPage() {
  const { agentId = "" } = useParams();
  const agent = useAgent(agentId);
  return <QueryView query={agent}>{(a) => <AgentView agent={a} />}</QueryView>;
}

function AgentView({ agent: a }: { agent: Agent }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const manage = can("agents.manage");
  const initial = new URLSearchParams(window.location.search).get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(initial ?? (a.published_version !== null ? "run" : manage ? "build" : "run"));
  const update = useMutation({
    mutationFn: (body: Schemas["AgentUpdate"]) =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/agents/{agent_id}", { params: { path: { org_id: org.id, agent_id: a.id } }, body })),
    onSuccess: (updated) => {
      qc.setQueryData(["agent", org.id, a.id], updated);
      void qc.invalidateQueries({ queryKey: ["agents", org.id] });
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const tabs: { key: Tab; label: string }[] = [
    { key: "run", label: t("agents.tab.run") },
    ...(manage ? ([
      { key: "build", label: t("agents.tab.build") },
      { key: "tests", label: t("agents.tab.tests") },
      { key: "versions", label: t("agents.tab.versions") },
    ] as const) : []),
    { key: "history", label: t("agents.tab.history") },
  ];
  return (
    <div className="stack">
      <PageHeader
        title={a.name}
        subtitle={a.description || undefined}
        actions={
          <>
            {a.published_version !== null ? <Badge tone="success">{t("agents.live", { v: a.published_version })}</Badge> : <Badge>{t("agents.unpublished")}</Badge>}
            <Badge tone="primary">${usd(a.month_spend_usd)} / ${usd(a.monthly_budget_usd)}</Badge>
            {manage && <Checkbox label={t("agents.enabled")} checked={a.enabled} onChange={(e) => update.mutate({ enabled: e.target.checked })} />}
          </>
        }
      />
      <Tabs<Tab> tabs={tabs} value={tab} onChange={setTab} />
      {tab === "run" && <RunTab agent={a} />}
      {tab === "build" && <BuildTab agent={a} />}
      {tab === "tests" && <TestsTab agent={a} />}
      {tab === "versions" && <VersionsTab agent={a} />}
      {tab === "history" && <HistoryTab agent={a} />}
    </div>
  );
}

function RunResult({ run }: { run: Run }) {
  const { t } = useI18n();
  return (
    <div className="stack-sm">
      <div className="bubble bubble-assistant" style={{ maxInlineSize: "100%" }}>
        <div className="bubble-text" dir="auto">{run.reply || "—"}</div>
      </div>
      <div className="row small muted">
        <span>{t("agents.version", { v: run.version })}</span>
        <span>· ${usd(run.cost_usd)}</span>
        {run.tool_calls.map((c, i) => (
          <Badge key={i} tone={c.status === "pending_approval" ? "warning" : "neutral"}>
            {String(c.name)}{c.status === "pending_approval" ? ` · ${t("assistant.needsApproval")}` : ""}
          </Badge>
        ))}
      </div>
      {run.tool_calls.some((c) => c.status === "pending_approval") && <p className="small muted">{t("agents.pendingHint")}</p>}
    </div>
  );
}

function RunTab({ agent: a }: { agent: Agent }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const run = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/agents/{agent_id}/runs", { params: { path: { org_id: org.id, agent_id: a.id } }, body: { input } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["agent-runs", org.id, a.id] });
      void qc.invalidateQueries({ queryKey: ["agent", org.id, a.id] });
    },
  });
  if (a.published_version === null) return <Empty>{t("agents.notPublished")}</Empty>;
  return (
    <Card>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <Field label={t("agents.input")} hint={t("agents.inputHint")}>
          <Textarea value={input} onChange={(e) => setInput(e.target.value)} required maxLength={8000} />
        </Field>
        <div>
          <Button type="submit" variant="primary" icon={<Play size={16} />} loading={run.isPending} disabled={!input.trim() || !a.enabled}>
            {t("agents.run")}
          </Button>
        </div>
        {run.isError && <ErrorNotice error={run.error} />}
        {run.data && <RunResult run={run.data} />}
      </form>
    </Card>
  );
}

function BuildTab({ agent: a }: { agent: Agent }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const latest: Version | undefined = a.versions[a.versions.length - 1];
  const [draft, setDraft] = useState({
    instructions: latest?.instructions ?? "",
    tools: latest?.tools ?? [],
    route: latest?.route ?? "assistant",
    max_steps: latest?.max_steps ?? 6,
    max_cost_usd: latest?.max_cost_usd ?? "0.20",
  });
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/agents/{agent_id}/draft", { params: { path: { org_id: org.id, agent_id: a.id } }, body: draft })),
    onSuccess: (v) => {
      toast(t("agents.draftSaved", { v: v.number }));
      void qc.invalidateQueries({ queryKey: ["agent", org.id, a.id] });
    },
  });
  const toggleTool = (name: string, on: boolean) => setDraft({ ...draft, tools: on ? [...draft.tools, name] : draft.tools.filter((x) => x !== name) });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
      {latest?.status === "frozen" && <div className="notice notice-info">{t("agents.frozenHint", { v: latest.number })}</div>}
      <Card title={t("agents.instructions")}>
        <Textarea aria-label={t("agents.instructions")} value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
          rows={8} maxLength={20000} placeholder={t("agents.instructionsPlaceholder")} dir="auto" />
      </Card>
      <Card title={t("agents.tools")}>
        <p className="muted small" style={{ marginBlockEnd: "var(--space-3)" }}>{t("agents.toolsHint")}</p>
        <div className="perm-grid">
          {a.available_tools.map((tool) => {
            const name = String(tool.name);
            return (
              <Checkbox
                key={name}
                checked={draft.tools.includes(name)}
                onChange={(e) => toggleTool(name, e.target.checked)}
                label={
                  <span>
                    <span className="mono">{name}</span> {tool.kind !== "read" && <Badge tone="warning">{t("agents.needsApproval")}</Badge>}
                    <br />
                    <span className="muted small">{String(tool.description)}</span>
                  </span>
                }
              />
            );
          })}
        </div>
      </Card>
      <Card title={t("agents.limits")}>
        <div className="grid-3">
          <Field label={t("agents.route")}>
            <Select value={draft.route} onChange={(e) => setDraft({ ...draft, route: e.target.value })}>
              <option value="assistant">{t("ai.route.assistant")}</option>
              <option value="assistant_deep">{t("ai.route.assistant_deep")}</option>
            </Select>
          </Field>
          <Field label={t("agents.maxSteps")} error={err["max_steps"]}>
            <Input type="number" min={1} max={12} value={draft.max_steps} onChange={(e) => setDraft({ ...draft, max_steps: Number(e.target.value) })} ltr />
          </Field>
          <Field label={t("agents.maxCost")} error={err["max_cost_usd"]}>
            <Input inputMode="decimal" value={draft.max_cost_usd} onChange={(e) => setDraft({ ...draft, max_cost_usd: e.target.value })} ltr />
          </Field>
        </div>
      </Card>
      <div className="form-actions">
        <Button type="submit" variant="primary" loading={save.isPending}>{t("agents.saveDraft")}</Button>
      </div>
    </form>
  );
}

const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

function TestsTab({ agent: a }: { agent: Agent }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const tests = useQuery({
    queryKey: ["agent-tests", org.id, a.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/agents/{agent_id}/tests", { params: { path: { org_id: org.id, agent_id: a.id } } })),
  });
  const [form, setForm] = useState({ name: "", input: "", expect_tools: "", forbid_tools: "", reply_contains: "" });
  const add = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/agents/{agent_id}/tests", {
        params: { path: { org_id: org.id, agent_id: a.id } },
        body: { name: form.name, input: form.input, expect_tools: csv(form.expect_tools), forbid_tools: csv(form.forbid_tools), reply_contains: csv(form.reply_contains) },
      })),
    onSuccess: () => {
      setForm({ name: "", input: "", expect_tools: "", forbid_tools: "", reply_contains: "" });
      void qc.invalidateQueries({ queryKey: ["agent-tests", org.id, a.id] });
    },
  });
  return (
    <div className="stack">
      <QueryView query={tests}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t("agents.noTests")}</Empty>
          ) : (
            <div className="stack-sm">
              {list.map((c) => (
                <Card key={c.id} tight>
                  <strong>{c.name}</strong>
                  <p className="small" dir="auto">{c.input}</p>
                  <span className="row small muted">
                    {(c.expect_tools ?? []).map((x) => <Badge key={x} tone="success">+ {x}</Badge>)}
                    {(c.forbid_tools ?? []).map((x) => <Badge key={x} tone="danger">− {x}</Badge>)}
                    {(c.reply_contains ?? []).map((x) => <Badge key={x} tone="primary">“{x}”</Badge>)}
                  </span>
                </Card>
              ))}
            </div>
          )
        }
      </QueryView>
      <Card title={t("agents.addTest")}>
        <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          {add.isError && <ErrorNotice error={add.error} />}
          <div className="grid-2">
            <Field label={t("common.name")}>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} />
            </Field>
            <Field label={t("agents.testInput")}>
              <Input value={form.input} onChange={(e) => setForm({ ...form, input: e.target.value })} required />
            </Field>
            <Field label={t("agents.expectTools")} hint={t("agents.commaHint")} optional>
              <Input value={form.expect_tools} onChange={(e) => setForm({ ...form, expect_tools: e.target.value })} ltr />
            </Field>
            <Field label={t("agents.forbidTools")} hint={t("agents.commaHint")} optional>
              <Input value={form.forbid_tools} onChange={(e) => setForm({ ...form, forbid_tools: e.target.value })} ltr />
            </Field>
            <Field label={t("agents.replyContains")} hint={t("agents.commaHint")} optional>
              <Input value={form.reply_contains} onChange={(e) => setForm({ ...form, reply_contains: e.target.value })} />
            </Field>
          </div>
          <div>
            <Button type="submit" icon={<Plus size={16} />} loading={add.isPending}>{t("agents.addTest")}</Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

function VersionsTab({ agent: a }: { agent: Agent }) {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [results, setResults] = useState<Record<number, Run[]>>({});
  const test = useMutation({
    mutationFn: (n: number) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/agents/{agent_id}/versions/{number}/test-runs", { params: { path: { org_id: org.id, agent_id: a.id, number: n } } })),
    onSuccess: (runs, n) => {
      setResults({ ...results, [n]: runs });
      void qc.invalidateQueries({ queryKey: ["agent", org.id, a.id] });
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const publish = useMutation({
    mutationFn: (n: number) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/agents/{agent_id}/versions/{number}/publish", { params: { path: { org_id: org.id, agent_id: a.id, number: n } } })),
    onSuccess: (updated) => {
      qc.setQueryData(["agent", org.id, a.id], updated);
      void qc.invalidateQueries({ queryKey: ["agents", org.id] });
      toast(t("agents.published", { v: updated.published_version ?? "" }));
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  if (a.versions.length === 0) return <Empty>{t("agents.noVersions")}</Empty>;
  return (
    <div className="stack-sm">
      {[...a.versions].reverse().map((v) => {
        const res = results[v.number];
        const passed = res && res.length > 0 && res.every((r) => r.passed);
        return (
          <Card key={v.number} tight>
            <div className="row-between">
              <span className="row">
                <strong>{t("agents.version", { v: v.number })}</strong>
                <Badge tone={v.status === "frozen" ? "info" : "neutral"}>{v.status === "frozen" ? t("agents.frozen") : t("status.draft")}</Badge>
                {a.published_version === v.number && <Badge tone="success">{t("agents.livePlain")}</Badge>}
                <span className="muted small num">{date(v.created_at)}</span>
              </span>
              <span className="row">
                <Button size="sm" icon={<FlaskConical size={14} />} loading={test.isPending && test.variables === v.number} onClick={() => test.mutate(v.number)}>
                  {t("agents.runTests")}
                </Button>
                {a.published_version !== v.number && (
                  <Button size="sm" variant="primary" icon={<Rocket size={14} />} loading={publish.isPending && publish.variables === v.number} onClick={() => publish.mutate(v.number)}>
                    {t("agents.publish")}
                  </Button>
                )}
              </span>
            </div>
            <p className="muted small">{v.tools.join(", ") || t("agents.noTools")} · {v.route} · {v.max_steps} · ${usd(v.max_cost_usd)}</p>
            {res && (
              <div className="stack-sm" style={{ marginBlockStart: "var(--space-2)" }}>
                <Badge tone={passed ? "success" : "danger"}>{passed ? t("agents.allPassed") : t("agents.someFailed")}</Badge>
                {res.map((r) => (
                  <div key={r.id} className="row small">
                    {r.passed ? <CheckCircle2 size={16} color="var(--color-success-text)" aria-label={t("agents.pass")} /> : <XCircle size={16} color="var(--color-danger-text)" aria-label={t("agents.fail")} />}
                    <span dir="auto">{r.input}</span>
                    {r.failures.map((f) => <span key={f} className="muted" dir="ltr">— {f}</span>)}
                  </div>
                ))}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function HistoryTab({ agent: a }: { agent: Agent }) {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const runs = useQuery({
    queryKey: ["agent-runs", org.id, a.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/agents/{agent_id}/runs", { params: { path: { org_id: org.id, agent_id: a.id } } })),
  });
  return (
    <QueryView query={runs}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("agents.noRuns")}</Empty>
        ) : (
          <div className="stack-sm">
            {list.map((r) => (
              <Card key={r.id} tight>
                <div className="row-between">
                  <strong dir="auto">{r.input}</strong>
                  <span className="row small muted">
                    <Badge tone={r.trigger === "test" ? "info" : "neutral"}>{r.trigger}</Badge>
                    {r.passed !== null && <Badge tone={r.passed ? "success" : "danger"}>{r.passed ? t("agents.pass") : t("agents.fail")}</Badge>}
                    <span className="num">{date(r.created_at)}</span>
                  </span>
                </div>
                <RunResult run={r} />
              </Card>
            ))}
          </div>
        )
      }
    </QueryView>
  );
}
