import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, Check, Pencil, Plus, Trash2, UserPlus, X } from "lucide-react";
import { useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { Tabs } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea,
  errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";
import type { MessageKey } from "../i18n/en";
import { today } from "../lib/format";

type Tab = "mine" | "requests" | "people" | "away" | "settings";
type Leave = Schemas["LeaveOut"];
type Employee = Schemas["EmployeeOut"];

const STATUS_TONE = { pending: "warning", approved: "success", rejected: "danger", cancelled: "neutral" } as const;

function useHr<T>(key: string, fetcher: (orgId: string) => Promise<T>, enabled = true) {
  const { org } = useOrg();
  return useQuery({ queryKey: ["hr", key, org.id], queryFn: () => fetcher(org.id), enabled });
}

function useLeaveTypes() {
  return useHr("leave-types", (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/leave-types", { params: { path: { org_id } } })));
}

function useDepartments(enabled = true) {
  return useHr("departments", (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/departments", { params: { path: { org_id } } })), enabled);
}

function useMembers() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["members", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })),
    enabled: can("members.read"),
  });
}

/** Weekday names, Monday (0) to Sunday (6), in the current language. */
function weekdays(lang: string): string[] {
  const fmt = new Intl.DateTimeFormat(lang === "ar" ? "ar-SA-u-ca-gregory-nu-latn" : "en-GB", { weekday: "long" });
  return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(Date.UTC(2024, 0, 1 + i, 12))));
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  return <Badge tone={STATUS_TONE[status as keyof typeof STATUS_TONE] ?? "neutral"}>{t(`hr.status.${status}` as MessageKey)}</Badge>;
}

export function HrPage() {
  const { t } = useI18n();
  const { can } = useOrg();
  const tabs: { key: Tab; label: string }[] = [
    ...(can("leave.request") ? [{ key: "mine" as Tab, label: t("hr.tab.mine") }] : []),
    ...(can("leave.request") || can("hr.manage") ? [{ key: "requests" as Tab, label: t("hr.tab.requests") }] : []),
    ...(can("hr.read") ? [{ key: "people" as Tab, label: t("hr.tab.people") }] : []),
    { key: "away" as Tab, label: t("hr.tab.away") },
    ...(can("hr.manage") ? [{ key: "settings" as Tab, label: t("hr.tab.settings") }] : []),
  ];
  const [tab, setTab] = useState<Tab>(tabs[0]?.key ?? "away");
  const [asking, setAsking] = useState(false);
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.hr")}
        subtitle={t("hr.subtitle")}
        actions={(can("leave.request") || can("hr.manage")) && (
          <Button variant="primary" icon={<CalendarPlus size={16} />} onClick={() => setAsking(true)}>{t("hr.requestLeave")}</Button>
        )}
      />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={tabs} />
      {tab === "mine" && <MyLeave />}
      {tab === "requests" && <Requests />}
      {tab === "people" && <People />}
      {tab === "away" && <Away />}
      {tab === "settings" && <Settings />}
      {asking && <RequestDialog onClose={() => setAsking(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------- my leave

function MyLeave() {
  const { t } = useI18n();
  const thisYear = Number(today().slice(0, 4));
  const [year, setYear] = useState(thisYear);
  const me = useHr(`me-${year}`, (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/me", { params: { path: { org_id }, query: { year } } })));
  return (
    <QueryView query={me}>
      {(m) => !m.employee ? <Empty>{t("hr.notLinked")}</Empty> : (
        <div className="stack">
          <div className="row">
            <Field label={t("hr.year")}>
              <Select value={String(year)} onChange={(e) => setYear(Number(e.target.value))}>
                {[thisYear - 1, thisYear, thisYear + 1].map((y) => <option key={y} value={y}>{y}</option>)}
              </Select>
            </Field>
          </div>
          {m.balances.length > 0 && (
            <div className="stat-row">
              {m.balances.map((b) => (
                <Card tight key={b.leave_type_id}>
                  <span className="muted small">{b.leave_type}{!b.paid && ` · ${t("hr.unpaid")}`}</span>
                  <strong className="num">
                    {b.allowance == null ? t("hr.daysTaken", { days: b.approved }) : t("hr.daysLeft", { left: b.remaining ?? 0, of: b.allowance })}
                  </strong>
                  {b.pending > 0 && <span className="muted small">{t("hr.daysPending", { days: b.pending })}</span>}
                </Card>
              ))}
            </div>
          )}
          <LeaveTable mineOnly />
        </div>
      )}
    </QueryView>
  );
}

// ---------------------------------------------------------------- requests

function Requests() {
  const { t } = useI18n();
  const [filter, setFilter] = useState("pending");
  return (
    <div className="stack">
      <div className="row">
        <Field label={t("hr.show")}>
          <Select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {["pending", "approved", "rejected", "cancelled", ""].map((s) => (
              <option key={s} value={s}>{s ? t(`hr.status.${s}` as MessageKey) : t("hr.all")}</option>
            ))}
          </Select>
        </Field>
      </div>
      <LeaveTable status={filter || undefined} />
    </div>
  );
}

function LeaveTable({ status, mineOnly = false }: { status?: string; mineOnly?: boolean }) {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [declining, setDeclining] = useState<Leave | null>(null);
  const list = useQuery({
    queryKey: ["hr", "leave", org.id, status ?? ""],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/leave-requests", {
      params: { path: { org_id: org.id }, query: { status_filter: status } },
    })),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["hr"] });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "cancel" }) => action === "approve"
      ? unwrap(api.POST("/api/v1/orgs/{org_id}/hr/leave-requests/{request_id}/approve", { params: { path: { org_id: org.id, request_id: id } }, body: { note: "" } }))
      : unwrap(api.POST("/api/v1/orgs/{org_id}/hr/leave-requests/{request_id}/cancel", { params: { path: { org_id: org.id, request_id: id } } })),
    onSuccess: (r) => { refresh(); toast(t(`hr.done.${r.status}` as MessageKey)); },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const now = today();
  return (
    <QueryView query={list}>
      {(all) => {
        const rows = mineOnly ? all.filter((r) => r.mine) : all;
        if (rows.length === 0) return <Empty>{mineOnly ? t("hr.noneMine") : t("hr.noRequests")}</Empty>;
        return (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {!mineOnly && <th>{t("hr.employee")}</th>}
                    <th>{t("hr.leaveType")}</th>
                    <th>{t("hr.from")}</th>
                    <th>{t("hr.to")}</th>
                    <th className="end">{t("hr.days")}</th>
                    <th>{t("common.status")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const canCancel = (r.status === "pending" || r.status === "approved")
                      && (can("hr.manage") || (r.mine && (r.status === "pending" || r.start_date > now)));
                    return (
                      <tr key={r.id}>
                        {!mineOnly && (
                          <td>
                            <span className="stack-sm" style={{ gap: 2 }}>
                              <strong dir="auto">{r.employee_name}</strong>
                              {r.department_name && <span className="muted small" dir="auto">{r.department_name}</span>}
                            </span>
                          </td>
                        )}
                        <td>
                          <span className="stack-sm" style={{ gap: 2 }}>
                            <span dir="auto">{r.leave_type}</span>
                            {r.reason && <span className="muted small" dir="auto">{r.reason}</span>}
                          </span>
                        </td>
                        <td className="num small">{date(r.start_date)}</td>
                        <td className="num small">{date(r.end_date)}</td>
                        <td className="end num">{r.days}</td>
                        <td>
                          <span className="stack-sm" style={{ gap: 2 }}>
                            <StatusBadge status={r.status} />
                            {r.decision_note && <span className="muted small" dir="auto">{r.decision_note}</span>}
                          </span>
                        </td>
                        <td className="end">
                          <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                            {r.can_decide && (
                              <>
                                <Button size="sm" variant="primary" icon={<Check size={14} />} loading={act.isPending && act.variables?.id === r.id}
                                  onClick={() => act.mutate({ id: r.id, action: "approve" })}>{t("hr.approve")}</Button>
                                <Button size="sm" icon={<X size={14} />} onClick={() => setDeclining(r)}>{t("hr.decline")}</Button>
                            </>
                          )}
                          {canCancel && !r.can_decide && (
                            <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: r.id, action: "cancel" })}>{t("hr.withdraw")}</Button>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            {declining && <DeclineDialog leave={declining} onClose={() => setDeclining(null)} onDone={() => { setDeclining(null); refresh(); }} />}
          </>
        );
      }}
    </QueryView>
  );
}

function DeclineDialog({ leave, onClose, onDone }: { leave: Leave; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [note, setNote] = useState("");
  const decline = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/leave-requests/{request_id}/reject", {
      params: { path: { org_id: org.id, request_id: leave.id } }, body: { note },
    })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("hr.declineTitle", { name: leave.employee_name })}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); decline.mutate(); }}>
        {decline.isError && <ErrorNotice error={decline.error} />}
        <Field label={t("hr.note")} hint={t("hr.noteHint")} optional>
          <Textarea value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="danger" loading={decline.isPending}>{t("hr.decline")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function RequestDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const types = useLeaveTypes();
  const people = useHr("employees", (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/employees", { params: { path: { org_id } } })), can("hr.manage"));
  const [form, setForm] = useState({ leave_type_id: "", start_date: "", end_date: "", reason: "", employee_id: "" });
  const ready = Boolean(form.start_date && form.end_date && form.end_date >= form.start_date);
  const days = useQuery({
    queryKey: ["hr", "leave-days", org.id, form.start_date, form.end_date],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/leave-days", {
      params: { path: { org_id: org.id }, query: { start: form.start_date, end: form.end_date } },
    })),
    enabled: ready,
  });
  const ask = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/leave-requests", {
      params: { path: { org_id: org.id } },
      body: { leave_type_id: form.leave_type_id, start_date: form.start_date, end_date: form.end_date, reason: form.reason,
              employee_id: form.employee_id || null },
    })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["hr"] });
      toast(t("hr.sent"));
      onClose();
    },
  });
  const err = ask.error instanceof ApiError ? ask.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("hr.requestLeave")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); ask.mutate(); }}>
        {ask.isError && !Object.keys(err).length && <ErrorNotice error={ask.error} />}
        {can("hr.manage") && (
          <Field label={t("hr.forWhom")} optional>
            <Select value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })}>
              <option value="">{t("hr.myself")}</option>
              {(people.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
            </Select>
          </Field>
        )}
        <Field label={t("hr.leaveType")}>
          <Select value={form.leave_type_id} required onChange={(e) => setForm({ ...form, leave_type_id: e.target.value })}>
            <option value="" disabled>—</option>
            {(types.data ?? []).filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </Select>
        </Field>
        <div className="grid-2">
          <Field label={t("hr.from")} error={err["start_date"]}>
            <Input type="date" required value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} ltr />
          </Field>
          <Field label={t("hr.to")} error={err["end_date"]}>
            <Input type="date" required min={form.start_date || undefined} value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} ltr />
          </Field>
        </div>
        {ready && days.data && <p className="muted small" role="status">{t("hr.workingDays", { days: days.data.days })}</p>}
        <Field label={t("hr.reason")} optional>
          <Textarea value={form.reason} maxLength={500} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={ask.isPending}>{t("hr.send")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- people

function People() {
  const { t, date } = useI18n();
  const { can } = useOrg();
  const [filter, setFilter] = useState("active");
  const [editing, setEditing] = useState<Employee | "new" | null>(null);
  const people = useHr(`employees-${filter}`, (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/employees", {
    params: { path: { org_id }, query: { status_filter: filter } },
  })));
  return (
    <div className="stack">
      <div className="row-between">
        <Field label={t("hr.show")}>
          <Select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="active">{t("hr.current")}</option>
            <option value="left">{t("hr.former")}</option>
          </Select>
        </Field>
        {can("hr.manage") && <Button icon={<UserPlus size={16} />} onClick={() => setEditing("new")}>{t("hr.newEmployee")}</Button>}
      </div>
      <QueryView query={people}>
        {(list) => list.length === 0 ? <Empty>{t("hr.noPeople")}</Empty> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("hr.department")}</th>
                  <th>{t("hr.contact")}</th>
                  <th>{t("hr.hired")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <span className="stack-sm" style={{ gap: 2 }}>
                        <strong dir="auto">{p.full_name}</strong>
                        {p.job_title && <span className="muted small" dir="auto">{p.job_title}</span>}
                      </span>
                    </td>
                    <td dir="auto">{p.department_name ?? "—"}</td>
                    <td className="small">
                      <span className="stack-sm" style={{ gap: 2 }}>
                        {p.work_email && <span className="mono">{p.work_email}</span>}
                        {p.phone && <span className="num" dir="ltr">{p.phone}</span>}
                      </span>
                    </td>
                    <td className="num small">{p.status === "left" ? `${date(p.hire_date)} – ${date(p.end_date)}` : date(p.hire_date)}</td>
                    <td className="end">
                      <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                        {p.away_today && <Badge tone="info">{t("hr.awayToday")}</Badge>}
                        {can("hr.manage") && (
                          <IconButton label={`${t("common.edit")} ${p.full_name}`} onClick={() => setEditing(p)}><Pencil size={14} /></IconButton>
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
      {editing && <EmployeeDialog employee={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function EmployeeDialog({ employee, onClose }: { employee: Employee | null; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const depts = useDepartments();
  const members = useMembers();
  const [form, setForm] = useState({
    full_name: employee?.full_name ?? "", job_title: employee?.job_title ?? "", department_id: employee?.department_id ?? "",
    user_id: employee?.user_id ?? "", work_email: employee?.work_email ?? "", phone: employee?.phone ?? "",
    hire_date: employee?.hire_date ?? "", end_date: employee?.end_date ?? "",
  });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        full_name: form.full_name, job_title: form.job_title, department_id: form.department_id || null, user_id: form.user_id || null,
        work_email: form.work_email, phone: form.phone, hire_date: form.hire_date || null,
      };
      return employee
        ? unwrap(api.PATCH("/api/v1/orgs/{org_id}/hr/employees/{employee_id}", {
          params: { path: { org_id: org.id, employee_id: employee.id } }, body: { ...body, end_date: form.end_date || null },
        }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/hr/employees", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["hr"] }); onClose(); },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  return (
    <Dialog open onClose={onClose} title={employee ? employee.full_name : t("hr.newEmployee")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("common.name")} error={err["full_name"]}>
            <Input value={form.full_name} required maxLength={200} onChange={set("full_name")} />
          </Field>
          <Field label={t("hr.jobTitle")} optional>
            <Input value={form.job_title} maxLength={120} onChange={set("job_title")} />
          </Field>
          <Field label={t("hr.department")} optional>
            <Select value={form.department_id} onChange={set("department_id")}>
              <option value="">—</option>
              {(depts.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
          </Field>
          <Field label={t("hr.account")} hint={t("hr.accountHint")} optional>
            <Select value={form.user_id} onChange={set("user_id")}>
              <option value="">—</option>
              {(members.data ?? []).filter((m) => m.status === "active").map((m) => (
                <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("hr.workEmail")} optional error={err["work_email"]}>
            <Input type="email" value={form.work_email} maxLength={320} onChange={set("work_email")} ltr />
          </Field>
          <Field label={t("hr.phone")} optional>
            <Input type="tel" value={form.phone} maxLength={40} onChange={set("phone")} ltr />
          </Field>
          <Field label={t("hr.hireDate")} optional>
            <Input type="date" value={form.hire_date} onChange={set("hire_date")} ltr />
          </Field>
          {employee && (
            <Field label={t("hr.endDate")} hint={t("hr.endDateHint")} optional>
              <Input type="date" value={form.end_date} min={form.hire_date || undefined} onChange={set("end_date")} ltr />
            </Field>
          )}
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{employee ? t("common.save") : t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- who is away

function Away() {
  const { t, date } = useI18n();
  const away = useHr("away", (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/away", { params: { path: { org_id } } })));
  return (
    <QueryView query={away}>
      {(list) => list.length === 0 ? <Empty>{t("hr.nobodyAway")}</Empty> : (
        <div className="stack-sm">
          <p className="muted small" style={{ margin: 0 }}>{t("hr.awayHint")}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>{t("common.name")}</th><th>{t("hr.department")}</th><th>{t("hr.from")}</th><th>{t("hr.to")}</th></tr>
              </thead>
              <tbody>
                {list.map((a, i) => (
                  <tr key={`${a.employee_id}-${i}`}>
                    <td dir="auto"><strong>{a.employee_name}</strong></td>
                    <td dir="auto">{a.department_name ?? "—"}</td>
                    <td className="num small">{date(a.start_date)}</td>
                    <td className="num small">{date(a.end_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </QueryView>
  );
}

// ---------------------------------------------------------------- settings

function Settings() {
  return (
    <div className="grid-2" style={{ alignItems: "start" }}>
      <div className="stack">
        <Departments />
        <LeaveTypes />
      </div>
      <div className="stack">
        <Weekend />
        <Holidays />
      </div>
    </div>
  );
}

function Departments() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const depts = useDepartments();
  const members = useMembers();
  const [name, setName] = useState("");
  const [manager, setManager] = useState("");
  const names = Object.fromEntries((members.data ?? []).map((m) => [m.user_id, m.full_name || m.email]));
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/departments", {
      params: { path: { org_id: org.id } }, body: { name, manager_user_id: manager || null },
    })),
    onSuccess: () => { setName(""); setManager(""); void qc.invalidateQueries({ queryKey: ["hr"] }); },
  });
  return (
    <Card title={t("hr.departments")}>
      <div className="stack">
        <QueryView query={depts}>
          {(list) => list.length === 0 ? <p className="muted small">{t("hr.noDepartments")}</p> : (
            <ul className="plain-list">
              {list.map((d) => (
                <li key={d.id} className="row-between">
                  <span dir="auto"><strong>{d.name}</strong> <span className="muted small">· {t("hr.headcount", { n: d.employees })}</span></span>
                  <span className="muted small">{d.manager_user_id ? t("hr.managedBy", { name: names[d.manager_user_id] ?? "—" }) : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
        <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          {add.isError && <ErrorNotice error={add.error} />}
          <div className="grid-2">
            <Field label={t("hr.departmentName")}>
              <Input value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={t("hr.manager")} hint={t("hr.managerHint")} optional>
              <Select value={manager} onChange={(e) => setManager(e.target.value)}>
                <option value="">—</option>
                {(members.data ?? []).filter((m) => m.status === "active").map((m) => <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>)}
              </Select>
            </Field>
          </div>
          <div><Button type="submit" icon={<Plus size={16} />} loading={add.isPending}>{t("hr.addDepartment")}</Button></div>
        </form>
      </div>
    </Card>
  );
}

function LeaveTypes() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const types = useLeaveTypes();
  const [form, setForm] = useState({ name: "", annual_days: "", paid: true });
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/leave-types", {
      params: { path: { org_id: org.id } },
      body: { name: form.name, annual_days: form.annual_days ? Number(form.annual_days) : null, paid: form.paid },
    })),
    onSuccess: () => { setForm({ name: "", annual_days: "", paid: true }); void qc.invalidateQueries({ queryKey: ["hr"] }); },
  });
  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => unwrap(api.PATCH("/api/v1/orgs/{org_id}/hr/leave-types/{type_id}", {
      params: { path: { org_id: org.id, type_id: id } }, body: { active },
    })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hr"] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <Card title={t("hr.leaveTypes")}>
      <div className="stack">
        <QueryView query={types}>
          {(list) => list.length === 0 ? <p className="muted small">{t("hr.noTypes")}</p> : (
            <ul className="plain-list">
              {list.map((x) => (
                <li key={x.id} className={`row-between ${x.active ? "" : "muted"}`}>
                  <span dir="auto">
                    <strong>{x.name}</strong>{" "}
                    <span className="muted small">
                      · {x.annual_days ? t("hr.perYear", { days: x.annual_days }) : t("hr.noAllowance")}{!x.paid && ` · ${t("hr.unpaid")}`}
                    </span>
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => toggle.mutate({ id: x.id, active: !x.active })}>
                    {x.active ? t("hr.retire") : t("hr.offer")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
        <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          {add.isError && <ErrorNotice error={add.error} />}
          <div className="grid-2">
            <Field label={t("hr.typeName")}>
              <Input value={form.name} required maxLength={80} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label={t("hr.annualDays")} hint={t("hr.annualDaysHint")} optional>
              <Input inputMode="numeric" pattern="[0-9]*" value={form.annual_days} onChange={(e) => setForm({ ...form, annual_days: e.target.value })} ltr />
            </Field>
          </div>
          <Checkbox label={t("hr.paid")} checked={form.paid} onChange={(e) => setForm({ ...form, paid: e.target.checked })} />
          <div><Button type="submit" icon={<Plus size={16} />} loading={add.isPending}>{t("hr.addType")}</Button></div>
        </form>
      </div>
    </Card>
  );
}

function Weekend() {
  const { t, lang } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const policy = useHr("policy", (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/policy", { params: { path: { org_id } } })));
  const save = useMutation({
    mutationFn: (weekend: number[]) => unwrap(api.PUT("/api/v1/orgs/{org_id}/hr/policy", { params: { path: { org_id: org.id } }, body: { weekend } })),
    onSuccess: (p) => { qc.setQueryData(["hr", "policy", org.id], p); void qc.invalidateQueries({ queryKey: ["hr", "leave-days"] }); },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const names = weekdays(lang);
  // Saturday first: the order a week reads in the region.
  const order = [5, 6, 0, 1, 2, 3, 4];
  return (
    <Card title={t("hr.weekend")}>
      <QueryView query={policy}>
        {(p) => {
          const current = save.isPending && save.variables ? save.variables : p.weekend;
          return (
            <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="muted small">{t("hr.weekendHint")}</legend>
              <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
                {order.map((d) => (
                  <Checkbox key={d} label={names[d]} checked={current.includes(d)}
                    onChange={(e) => save.mutate(e.target.checked ? [...current, d] : current.filter((x) => x !== d))} />
                ))}
              </div>
            </fieldset>
          );
        }}
      </QueryView>
    </Card>
  );
}

function Holidays() {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const year = Number(today().slice(0, 4));
  const holidays = useHr(`holidays-${year}`, (org_id) => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/holidays", {
    params: { path: { org_id }, query: { year } },
  })));
  const [form, setForm] = useState({ day: "", name: "" });
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/holidays", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => { setForm({ day: "", name: "" }); void qc.invalidateQueries({ queryKey: ["hr"] }); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/orgs/{org_id}/hr/holidays/{holiday_id}", { params: { path: { org_id: org.id, holiday_id: id } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hr"] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <Card title={t("hr.holidays", { year })}>
      <div className="stack">
        <QueryView query={holidays}>
          {(list) => list.length === 0 ? <p className="muted small">{t("hr.noHolidays")}</p> : (
            <ul className="plain-list">
              {list.map((h) => (
                <li key={h.id} className="row-between">
                  <span><span className="num small">{date(h.day)}</span> · <span dir="auto">{h.name}</span></span>
                  <IconButton label={`${t("common.delete")} ${h.name}`} onClick={() => remove.mutate(h.id)}><Trash2 size={14} /></IconButton>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
        <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          {add.isError && <ErrorNotice error={add.error} />}
          <div className="grid-2">
            <Field label={t("hr.holidayDay")}>
              <Input type="date" required value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value })} ltr />
            </Field>
            <Field label={t("hr.holidayName")}>
              <Input value={form.name} required maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
          </div>
          <div><Button type="submit" icon={<Plus size={16} />} loading={add.isPending}>{t("hr.addHoliday")}</Button></div>
        </form>
      </div>
    </Card>
  );
}
