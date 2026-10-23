import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, FileText, FolderKanban, ListPlus, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/Auth";
import { useOrg } from "../auth/Org";
import { Tabs, useCustomers, useTaxes } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea,
  errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { today } from "../lib/format";

type Project = Schemas["WorkProjectOut"];
type Tab = "time" | "tasks";

function useMembers() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["members", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })),
    enabled: can("members.read"),
  });
}

/** Hours used against the budget, as a bar; over budget shows in the warning colour. */
function BudgetBar({ project: p }: { project: Project }) {
  const { t } = useI18n();
  if (!p.budget_used_percent) return null;
  const used = Number(p.budget_used_percent);
  return (
    <div className="budget" title={t("projects.budgetUsed", { percent: p.budget_used_percent })}>
      <div className={`budget-fill ${used > 100 ? "over" : ""}`} style={{ inlineSize: `${Math.min(used, 100)}%` }} />
    </div>
  );
}

export function ProjectsPage() {
  const { t, money, amount } = useI18n();
  const { org, can, path } = useOrg();
  const [creating, setCreating] = useState(false);
  const projects = useQuery({
    queryKey: ["projects", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/projects", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.projects")}
        subtitle={t("projects.subtitle")}
        actions={can("projects.manage") && <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>{t("projects.new")}</Button>}
      />
      <QueryView query={projects}>
        {(list) => list.length === 0 ? <Empty>{t("projects.none")}</Empty> : (
          <div className="apps" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
            {list.map((p) => (
              <Link key={p.id} to={path(`projects/${p.id}`)} className={`app-tile hue-violet ${p.status === "closed" ? "muted" : ""}`}>
                <span className="row-between">
                  <span className="app-icon"><FolderKanban size={22} aria-hidden /></span>
                  {p.status === "closed" ? <Badge>{t("projects.closed")}</Badge> : p.unbilled_amount && Number(p.unbilled_amount) > 0
                    ? <Badge tone="warning">{t("projects.toInvoice", { amount: money(p.unbilled_amount, p.currency) })}</Badge> : null}
                </span>
                <span className="stack-sm" style={{ gap: 4 }}>
                  <strong dir="auto">{p.name}</strong>
                  <span className="muted small">{p.customer_name ?? t("projects.internal")}</span>
                  <span className="small num">
                    {p.budget_hours
                      ? t("projects.hoursOfBudget", { used: amount(p.hours), budget: amount(p.budget_hours) })
                      : t("projects.hoursLogged", { hours: amount(p.hours) })}
                  </span>
                  <BudgetBar project={p} />
                </span>
              </Link>
            ))}
          </div>
        )}
      </QueryView>
      {creating && <NewProjectDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const customers = useCustomers();
  const taxes = useTaxes();
  const [form, setForm] = useState({ name: "", description: "", customer_id: "", hourly_rate: "", tax_id: "", budget_hours: "" });
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/projects", {
        params: { path: { org_id: org.id } },
        body: {
          name: form.name, description: form.description, customer_id: form.customer_id || null,
          hourly_rate: form.hourly_rate || null, tax_id: form.tax_id || null, budget_hours: form.budget_hours || null,
        },
      })),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ["projects", org.id] });
      navigate(path(`projects/${p.id}`));
    },
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("projects.new")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && !Object.keys(err).length && <ErrorNotice error={create.error} />}
        <Field label={t("common.name")} error={err["name"]}>
          <Input value={form.name} required maxLength={200} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label={t("invoices.customer")} hint={t("projects.customerHint")} optional>
          <Select value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: e.target.value })}>
            <option value="">{t("projects.internal")}</option>
            {(customers.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </Field>
        <div className="grid-2">
          <Field label={t("projects.hourlyRate")} optional error={err["hourly_rate"]}>
            <Input inputMode="decimal" value={form.hourly_rate} onChange={(e) => setForm({ ...form, hourly_rate: e.target.value })} ltr />
          </Field>
          <Field label={t("invoices.taxCol")} optional>
            <Select value={form.tax_id} onChange={(e) => setForm({ ...form, tax_id: e.target.value })}>
              <option value="">—</option>
              {(taxes.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Select>
          </Field>
          <Field label={t("projects.budgetHours")} optional error={err["budget_hours"]}>
            <Input inputMode="decimal" value={form.budget_hours} onChange={(e) => setForm({ ...form, budget_hours: e.target.value })} ltr />
          </Field>
        </div>
        <Field label={t("common.description")} optional>
          <Textarea value={form.description} maxLength={5000} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function ProjectPage() {
  const { projectId = "" } = useParams();
  const { org } = useOrg();
  const project = useQuery({
    queryKey: ["project-work", org.id, projectId],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/projects/{project_id}", { params: { path: { org_id: org.id, project_id: projectId } } })),
  });
  return <QueryView query={project}>{(p) => <ProjectView project={p} />}</QueryView>;
}

function ProjectView({ project: p }: { project: Project }) {
  const { t, money, amount } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("time");
  const [logging, setLogging] = useState(false);
  const [invoicing, setInvoicing] = useState(false);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["project-work", org.id, p.id] });
    void qc.invalidateQueries({ queryKey: ["projects", org.id] });
    void qc.invalidateQueries({ queryKey: ["project-time", org.id, p.id] });
    void qc.invalidateQueries({ queryKey: ["project-tasks", org.id, p.id] });
  };
  const toggle = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/v1/orgs/{org_id}/projects/{project_id}", {
      params: { path: { org_id: org.id, project_id: p.id } }, body: { status: p.status === "active" ? "closed" : "active" },
    })),
    onSuccess: refresh,
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const canInvoice = can("projects.manage") && can("invoices.write") && Boolean(p.unbilled_amount) && Number(p.unbilled_hours) > 0;
  return (
    <div className="stack">
      <PageHeader
        title={p.name}
        subtitle={<span className="row"><Badge tone={p.status === "active" ? "success" : "neutral"}>{p.status === "active" ? t("projects.active") : t("projects.closed")}</Badge><span>{p.customer_name ?? t("projects.internal")}</span></span>}
        actions={
          <div className="row">
            {p.status === "active" && can("timesheets.write") && (
              <Button variant="primary" icon={<Clock size={16} />} onClick={() => setLogging(true)}>{t("projects.logTime")}</Button>
            )}
            {canInvoice && <Button icon={<FileText size={16} />} onClick={() => setInvoicing(true)}>{t("projects.invoice")}</Button>}
            {can("projects.manage") && (
              <Button variant="ghost" loading={toggle.isPending} onClick={() => toggle.mutate()}>
                {p.status === "active" ? t("projects.close") : t("projects.reopen")}
              </Button>
            )}
          </div>
        }
      />
      <div className="stat-row">
        <Card tight>
          <span className="muted small">{t("projects.hours")}</span>
          <strong className="num">
            {p.budget_hours ? t("projects.ofBudget", { used: amount(p.hours), budget: amount(p.budget_hours) }) : amount(p.hours)}
          </strong>
          <BudgetBar project={p} />
        </Card>
        <Card tight><span className="muted small">{t("projects.billableHours")}</span><strong className="num">{amount(p.billable_hours)}</strong></Card>
        <Card tight><span className="muted small">{t("projects.unbilled")}</span><strong className="num">{amount(p.unbilled_hours)}{p.unbilled_amount && <span className="muted small"> · {money(p.unbilled_amount, p.currency)}</span>}</strong></Card>
        <Card tight><span className="muted small">{t("projects.hourlyRate")}</span><strong className="num">{p.hourly_rate ? money(p.hourly_rate, p.currency) : "—"}</strong></Card>
      </div>
      {p.description && <Card tight><p style={{ whiteSpace: "pre-wrap", margin: 0 }} dir="auto">{p.description}</p></Card>}
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[{ key: "time", label: t("projects.tab.time") }, { key: "tasks", label: t("projects.tab.tasks") }]} />
      {tab === "time" ? <TimeList project={p} onChanged={refresh} /> : <TaskList project={p} onChanged={refresh} />}
      {logging && <LogTimeDialog project={p} onClose={() => setLogging(false)} onDone={() => { setLogging(false); refresh(); }} />}
      {invoicing && <InvoiceHoursDialog project={p} onClose={() => setInvoicing(false)} />}
    </div>
  );
}

function TimeList({ project: p, onChanged }: { project: Project; onChanged: () => void }) {
  const { t, date, amount } = useI18n();
  const { org, can, path } = useOrg();
  const { user } = useAuth();
  const toast = useToast();
  const ids = { org_id: org.id, project_id: p.id };
  const time = useQuery({
    queryKey: ["project-time", org.id, p.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/projects/{project_id}/time", { params: { path: ids } })),
  });
  const tasks = useQuery({
    queryKey: ["project-tasks", org.id, p.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/projects/{project_id}/tasks", { params: { path: ids } })),
  });
  const titles = Object.fromEntries((tasks.data ?? []).map((x) => [x.id, x.title]));
  const remove = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/orgs/{org_id}/projects/{project_id}/time/{entry_id}", { params: { path: { ...ids, entry_id: id } } })),
    onSuccess: onChanged,
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <QueryView query={time}>
      {(list) => list.length === 0 ? <Empty>{t("projects.noTime")}</Empty> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("common.date")}</th>
                <th>{t("projects.who")}</th>
                <th>{t("projects.tab.tasks")}</th>
                <th>{t("common.description")}</th>
                <th className="end">{t("projects.hours")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((e) => (
                <tr key={e.id}>
                  <td className="num small">{date(e.entry_date)}</td>
                  <td className="small">{e.user_name || <span className="mono">{e.user_email}</span>}</td>
                  <td>{e.task_id ? titles[e.task_id] ?? "—" : "—"}</td>
                  <td dir="auto">{e.description}</td>
                  <td className="end num">{amount(e.hours)}</td>
                  <td className="end">
                    <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                      {!e.billable && <Badge>{t("projects.notBillable")}</Badge>}
                      {e.billed && e.invoice_id && (
                        <Link className="badge badge-success" to={path(`invoices/${e.invoice_id}`)}>{t("projects.billed")}</Link>
                      )}
                      {!e.billed && p.status === "active" && (e.user_id === user?.id ? can("timesheets.write") : can("projects.manage")) && (
                        <IconButton label={`${t("common.delete")} ${date(e.entry_date)}`} onClick={() => remove.mutate(e.id)}>
                          <Trash2 size={14} />
                        </IconButton>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryView>
  );
}

function TaskList({ project: p, onChanged }: { project: Project; onChanged: () => void }) {
  const { t, date, amount } = useI18n();
  const { org, can } = useOrg();
  const members = useMembers();
  const [adding, setAdding] = useState(false);
  const tasks = useQuery({
    queryKey: ["project-tasks", org.id, p.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/projects/{project_id}/tasks", { params: { path: { org_id: org.id, project_id: p.id } } })),
  });
  const who = (id: string | null) => (id ? members.data?.find((m) => m.user_id === id)?.email ?? "" : "—");
  return (
    <div className="stack-sm">
      {can("projects.manage") && (
        <div><Button size="sm" icon={<ListPlus size={14} />} onClick={() => setAdding(true)}>{t("projects.addTask")}</Button></div>
      )}
      <QueryView query={tasks}>
        {(list) => list.length === 0 ? <Empty>{t("projects.noTasks")}</Empty> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("wf.f.title")}</th>
                  <th>{t("projects.assignee")}</th>
                  <th>{t("tasks.due")}</th>
                  <th>{t("common.status")}</th>
                  <th className="end">{t("projects.hours")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((x) => (
                  <tr key={x.id}>
                    <td dir="auto">{x.title}</td>
                    <td className="small mono">{who(x.assignee_user_id)}</td>
                    <td className="num small">{x.due_date ? date(x.due_date) : "—"}</td>
                    <td><Badge tone={x.status === "done" ? "success" : "neutral"}>{x.status === "done" ? t("projects.taskDone") : t("projects.taskOpen")}</Badge></td>
                    <td className="end num">{amount(x.hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {adding && <NewTaskDialog project={p} onClose={() => setAdding(false)} onDone={() => { setAdding(false); onChanged(); }} />}
    </div>
  );
}

function NewTaskDialog({ project: p, onClose, onDone }: { project: Project; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const members = useMembers();
  const [form, setForm] = useState({ title: "", assignee_user_id: "", due_date: "" });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/projects/{project_id}/tasks", {
      params: { path: { org_id: org.id, project_id: p.id } },
      body: { title: form.title, description: "", assignee_user_id: form.assignee_user_id || null, due_date: form.due_date || null },
    })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("projects.addTask")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && <ErrorNotice error={create.error} />}
        <Field label={t("wf.f.title")}>
          <Input value={form.title} required maxLength={300} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </Field>
        <div className="grid-2">
          <Field label={t("projects.assignee")} optional>
            <Select value={form.assignee_user_id} onChange={(e) => setForm({ ...form, assignee_user_id: e.target.value })}>
              <option value="">—</option>
              {(members.data ?? []).filter((m) => m.status === "active").map((m) => <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>)}
            </Select>
          </Field>
          <Field label={t("tasks.due")} optional>
            <Input type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} ltr />
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function LogTimeDialog({ project: p, onClose, onDone }: { project: Project; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const ids = { org_id: org.id, project_id: p.id };
  const tasks = useQuery({
    queryKey: ["project-tasks", org.id, p.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/projects/{project_id}/tasks", { params: { path: ids } })),
  });
  const [form, setForm] = useState({ entry_date: today(), hours: "", description: "", task_id: "", billable: Boolean(p.hourly_rate && p.customer_id) });
  const log = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/projects/{project_id}/time", {
      params: { path: ids }, body: { ...form, task_id: form.task_id || null },
    })),
    onSuccess: onDone,
  });
  const err = log.error instanceof ApiError ? log.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("projects.logTime")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); log.mutate(); }}>
        {log.isError && !Object.keys(err).length && <ErrorNotice error={log.error} />}
        <div className="grid-2">
          <Field label={t("common.date")} error={err["entry_date"]}>
            <Input type="date" value={form.entry_date} max={today()} onChange={(e) => setForm({ ...form, entry_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("projects.hours")} hint={t("projects.hoursHint")} error={err["hours"]}>
            <Input inputMode="decimal" value={form.hours} onChange={(e) => setForm({ ...form, hours: e.target.value })} required ltr />
          </Field>
        </div>
        <Field label={t("projects.tab.tasks")} optional>
          <Select value={form.task_id} onChange={(e) => setForm({ ...form, task_id: e.target.value })}>
            <option value="">—</option>
            {(tasks.data ?? []).filter((x) => x.status !== "done").map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
          </Select>
        </Field>
        <Field label={t("common.description")} optional>
          <Input value={form.description} maxLength={500} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <Checkbox label={t("projects.billable")} checked={form.billable} onChange={(e) => setForm({ ...form, billable: e.target.checked })} />
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={log.isPending}>{t("projects.logTime")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function InvoiceHoursDialog({ project: p, onClose }: { project: Project; onClose: () => void }) {
  const { t, money, amount } = useI18n();
  const { org, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ issue_date: today(), up_to: today() });
  const invoice = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/projects/{project_id}/invoice", {
      params: { path: { org_id: org.id, project_id: p.id } }, body: form,
    })),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
      navigate(path(`invoices/${r.invoice_id}`));
    },
  });
  return (
    <Dialog open onClose={onClose} title={t("projects.invoice")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); invoice.mutate(); }}>
        <p className="muted">{t("projects.invoiceHint", { hours: amount(p.unbilled_hours), amount: p.unbilled_amount ? money(p.unbilled_amount, p.currency) : "—" })}</p>
        {invoice.isError && <ErrorNotice error={invoice.error} />}
        <div className="grid-2">
          <Field label={t("invoices.issueDate")}>
            <Input type="date" value={form.issue_date} onChange={(e) => setForm({ ...form, issue_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("projects.upTo")}>
            <Input type="date" value={form.up_to} onChange={(e) => setForm({ ...form, up_to: e.target.value })} required ltr />
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={invoice.isPending}>{t("projects.makeInvoice")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
