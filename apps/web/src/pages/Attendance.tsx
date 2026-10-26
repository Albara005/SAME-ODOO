import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clock, LogIn, LogOut, Pencil, Plus, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, IconButton, Input, QueryView, Textarea, errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { addDays, hm, locale, today } from "../lib/format";

type Entry = Schemas["EntryOut"];
type Row = Schemas["MonthOut"];

/** The time of an instant on the clock in the zone ("08:05"). */
function clock(iso: string, tz: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));
}

/** A wall-clock time on a day in the zone, as an instant with that day's offset ("2026-04-05T08:00:00+03:00"). */
function zoned(day: string, time: string, tz: string): string {
  const probe = new Date(`${day}T${time}:00Z`);
  const local = new Date(probe.toLocaleString("en-US", { timeZone: tz }));
  const utc = new Date(probe.toLocaleString("en-US", { timeZone: "UTC" }));
  const minutes = Math.round((local.getTime() - utc.getTime()) / 60000);
  const abs = Math.abs(minutes);
  return `${day}T${time}:00${minutes >= 0 ? "+" : "-"}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

function usePolicy() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["hr", "policy", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/policy", { params: { path: { org_id: org.id } } })),
  });
}

function Hours({ minutes }: { minutes: number }) {
  const { t } = useI18n();
  return <>{t("att.hours", { h: hm(minutes) })}</>;
}

// ---------------------------------------------------------------- your own

/** Checking in and out, and this month's hours. Nothing shows until HR links your account to an employee record. */
export function MyAttendance() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const path = { params: { path: { org_id: org.id } } };
  const me = useQuery({
    queryKey: ["hr", "attendance", "me", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/attendance/me", path)),
  });
  const onError = (e: Error) => toast(errorText(e, t("common.error")), "error");
  const done = (message: string) => { void qc.invalidateQueries({ queryKey: ["hr", "attendance"] }); toast(message); };
  const checkIn = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/attendance/check-in", { ...path, body: { note: "" } })),
    onSuccess: () => done(t("att.checkedIn")), onError,
  });
  const checkOut = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/hr/attendance/check-out", path)),
    onSuccess: () => done(t("att.checkedOut")), onError,
  });
  const m = me.data;
  if (!m?.employee_id) return null;
  return (
    <Card
      title={t("att.title")}
      actions={m.open
        ? <Button variant="primary" icon={<LogOut size={16} />} loading={checkOut.isPending} onClick={() => checkOut.mutate()}>{t("att.checkOut")}</Button>
        : <Button variant="primary" icon={<LogIn size={16} />} loading={checkIn.isPending} onClick={() => checkIn.mutate()}>{t("att.checkIn")}</Button>}
    >
      <div className="stack-sm">
        <p role="status">{m.open ? t("att.inSince", { time: clock(m.open.check_in, m.timezone) }) : t("att.notIn")}</p>
        <div className="stat-row">
          <Card tight>
            <span className="muted small">{t("att.worked")}</span>
            <strong className="num"><Hours minutes={m.worked_minutes} /></strong>
          </Card>
          <Card tight>
            <span className="muted small">{t("att.overtime")}</span>
            <strong className="num"><Hours minutes={m.overtime_minutes} /></strong>
          </Card>
          <Card tight>
            <span className="muted small">{t("att.approved")}</span>
            <strong className="num">{m.approved_minutes == null ? t("att.notApproved") : <Hours minutes={m.approved_minutes} />}</strong>
            {m.paid && <Badge tone="success">{t("att.paid")}</Badge>}
          </Card>
        </div>
        {m.entries.length === 0 ? <p className="muted small">{t("att.noEntries")}</p> : <EntryTable entries={m.entries} tz={m.timezone} />}
        <p className="muted small">{t("att.zoneHint", { zone: m.timezone })}</p>
      </div>
    </Card>
  );
}

function EntryTable({ entries, tz, onEdit, onDelete }: {
  entries: Entry[]; tz: string; onEdit?: (e: Entry) => void; onDelete?: (e: Entry) => void;
}) {
  const { t, date } = useI18n();
  return (
    <div className="table-wrap">
      <table aria-label={t("att.entries")}>
        <thead>
          <tr>
            <th>{t("att.day")}</th>
            <th>{t("att.in")}</th>
            <th>{t("att.out")}</th>
            <th className="end">{t("att.duration")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td className="num">{date(e.day)}</td>
              <td className="num">{clock(e.check_in, tz)}</td>
              <td className="num">{e.check_out ? clock(e.check_out, tz) : <Badge tone="info">{t("att.stillIn")}</Badge>}</td>
              <td className="end num">{e.minutes == null ? "—" : hm(e.minutes)}</td>
              <td className="end">
                <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                  {e.source === "hr" && <Badge>{t("att.byHr")}</Badge>}
                  {e.note && <span className="muted small" dir="auto">{e.note}</span>}
                  {e.locked && <Badge tone="success">{t("att.locked")}</Badge>}
                  {onEdit && !e.locked && (
                    <IconButton label={t("att.editEntry", { day: date(e.day) })} onClick={() => onEdit(e)}><Pencil size={14} /></IconButton>
                  )}
                  {onDelete && !e.locked && (
                    <IconButton label={t("att.deleteEntry", { day: date(e.day) })} onClick={() => onDelete(e)}><Trash2 size={14} /></IconButton>
                  )}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- everyone's month (HR and department managers)

export function AttendanceTab() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [month, setMonth] = useState(today().slice(0, 7));
  const [approving, setApproving] = useState<Row | null>(null);
  const [viewing, setViewing] = useState<Row | null>(null);
  const policy = usePolicy();
  const rows = useQuery({
    queryKey: ["hr", "attendance", "month", org.id, month],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/overtime", { params: { path: { org_id: org.id }, query: { month } } })),
    enabled: /^\d{4}-\d{2}$/.test(month),
  });
  const withdraw = useMutation({
    mutationFn: (r: Row) => unwrap(api.DELETE("/api/v1/orgs/{org_id}/hr/overtime/{employee_id}/{month}", {
      params: { path: { org_id: org.id, employee_id: r.employee_id, month } },
    })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hr", "attendance"] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <div className="stack">
      <div className="row" style={{ alignItems: "end", flexWrap: "wrap" }}>
        <Field label={t("att.month")}>
          <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} ltr />
        </Field>
        <p className="muted small" style={{ flex: 1, minWidth: 240 }}>
          {t("att.overtimeHint", { hours: hm(policy.data?.daily_minutes ?? 480) })}
        </p>
      </div>
      <QueryView query={rows}>
        {(list) => list.length === 0 ? <Empty>{t("att.nobody")}</Empty> : (
          <div className="table-wrap">
            <table aria-label={t("hr.tab.attendance")}>
              <thead>
                <tr>
                  <th>{t("att.employee")}</th>
                  <th className="end">{t("att.daysPresent")}</th>
                  <th className="end">{t("att.worked")}</th>
                  <th className="end">{t("att.overtime")}</th>
                  <th className="end">{t("att.approved")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.employee_id}>
                    <td>
                      {r.employee_name}
                      {r.department_name && <div className="muted small">{r.department_name}</div>}
                      {r.checked_in && <Badge tone="info">{t("att.inNow")}</Badge>}
                    </td>
                    <td className="end num">{r.days_present}</td>
                    <td className="end num">{hm(r.worked_minutes)}</td>
                    <td className="end num">{hm(r.overtime_minutes)}</td>
                    <td className="end num">
                      {r.approved_minutes == null ? "—" : hm(r.approved_minutes)}
                      {r.paid && <> <Badge tone="success">{t("att.paid")}</Badge></>}
                    </td>
                    <td className="end">
                      <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                        <Button size="sm" icon={<Clock size={14} />} onClick={() => setViewing(r)}>{t("att.entries")}</Button>
                        {r.can_approve && r.overtime_minutes > 0 && (
                          <Button size="sm" variant="primary" icon={<Check size={14} />} onClick={() => setApproving(r)}>
                            {r.approved_minutes == null ? t("att.approve") : t("att.change")}
                          </Button>
                        )}
                        {r.can_approve && r.approved_minutes != null && (
                          <Button size="sm" variant="ghost" icon={<Undo2 size={14} />} loading={withdraw.isPending && withdraw.variables?.employee_id === r.employee_id}
                            onClick={() => window.confirm(t("att.withdrawConfirm")) && withdraw.mutate(r)}>{t("att.withdraw")}</Button>
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
      {approving && <ApproveDialog row={approving} month={month} onClose={() => setApproving(null)} />}
      {viewing && policy.data && <EntriesDialog row={viewing} month={month} tz={policy.data.timezone} onClose={() => setViewing(null)} />}
    </div>
  );
}

function ApproveDialog({ row, month, onClose }: { row: Row; month: string; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const start = row.approved_minutes ?? row.overtime_minutes;
  const [form, setForm] = useState({ hours: String(Math.floor(start / 60)), minutes: String(start % 60), note: row.approval_note });
  const total = Number(form.hours || 0) * 60 + Number(form.minutes || 0);
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/hr/overtime/{employee_id}/{month}", {
      params: { path: { org_id: org.id, employee_id: row.employee_id, month } },
      body: { minutes: total, note: form.note },
    })),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["hr", "attendance"] }); toast(t("att.approvedToast")); onClose(); },
  });
  return (
    <Dialog open onClose={onClose} title={t("att.approveFor", { name: row.employee_name, month })}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && <ErrorNotice error={save.error} />}
        <p className="small">{t("att.recorded", { h: hm(row.overtime_minutes) })}</p>
        <div className="grid-2">
          <Field label={t("att.hoursField")}>
            <Input inputMode="numeric" pattern="[0-9]*" required value={form.hours} onChange={(e) => setForm({ ...form, hours: e.target.value })} ltr />
          </Field>
          <Field label={t("att.minutesField")}>
            <Input inputMode="numeric" pattern="[0-5]?[0-9]" required value={form.minutes} onChange={(e) => setForm({ ...form, minutes: e.target.value })} ltr />
          </Field>
        </div>
        <Field label={t("att.note")} optional>
          <Textarea value={form.note} maxLength={500} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        </Field>
        <p className="muted small">{t("att.approveHint")}</p>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" disabled={total <= 0 || total > row.overtime_minutes} loading={save.isPending}>{t("att.approve")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function EntriesDialog({ row, month, tz, onClose }: { row: Row; month: string; tz: string; onClose: () => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<Entry | "new" | null>(null);
  const entries = useQuery({
    queryKey: ["hr", "attendance", "entries", org.id, row.employee_id, month],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/hr/attendance", {
      params: { path: { org_id: org.id }, query: { employee_id: row.employee_id, month } },
    })),
  });
  const remove = useMutation({
    mutationFn: (e: Entry) => unwrap(api.DELETE("/api/v1/orgs/{org_id}/hr/attendance/{entry_id}", {
      params: { path: { org_id: org.id, entry_id: e.id } },
    })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hr", "attendance"] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const hr = can("hr.manage");
  return (
    <Dialog open wide onClose={onClose} title={t("att.entriesOf", { name: row.employee_name, month })}>
      <div className="stack">
        <QueryView query={entries}>
          {(list) => list.length === 0 ? <Empty>{t("att.noEntries")}</Empty> : (
            <EntryTable entries={list} tz={tz} onEdit={hr ? setEditing : undefined}
              onDelete={hr ? (e) => window.confirm(t("att.deleteConfirm")) && remove.mutate(e) : undefined} />
          )}
        </QueryView>
        <p className="muted small">{t("att.zoneHint", { zone: tz })}</p>
        {hr && !row.paid && editing === null && (
          <div><Button size="sm" icon={<Plus size={14} />} onClick={() => setEditing("new")}>{t("att.addEntry")}</Button></div>
        )}
        {hr && editing !== null && (
          <EntryForm employeeId={row.employee_id} month={month} tz={tz} entry={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
        )}
      </div>
    </Dialog>
  );
}

function EntryForm({ employeeId, month, tz, entry, onDone }: {
  employeeId: string; month: string; tz: string; entry: Entry | null; onDone: () => void;
}) {
  const { t, lang } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    day: entry?.day ?? (today().startsWith(month) ? today() : `${month}-01`),
    in: entry ? clock(entry.check_in, tz) : "08:00",
    out: entry?.check_out ? clock(entry.check_out, tz) : "16:00",
    note: entry?.note ?? "",
  });
  const overnight = Boolean(form.in && form.out && form.out <= form.in);
  const times = () => ({
    check_in: zoned(form.day, form.in, tz),
    check_out: zoned(overnight ? addDays(form.day, 1) : form.day, form.out, tz),
  });
  const save = useMutation({
    mutationFn: () => entry
      ? unwrap(api.PATCH("/api/v1/orgs/{org_id}/hr/attendance/{entry_id}", {
          params: { path: { org_id: org.id, entry_id: entry.id } }, body: { ...times(), note: form.note },
        }))
      : unwrap(api.POST("/api/v1/orgs/{org_id}/hr/attendance", {
          params: { path: { org_id: org.id } }, body: { employee_id: employeeId, ...times(), note: form.note },
        })),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["hr", "attendance"] }); onDone(); },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  const weekday = new Intl.DateTimeFormat(locale(lang), { weekday: "long", timeZone: "UTC" }).format(new Date(`${form.day}T00:00:00Z`));
  return (
    <Card title={entry ? t("att.editTitle") : t("att.addEntry")}>
      <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <div className="grid-3">
          <Field label={t("att.day")} hint={form.day ? weekday : undefined}>
            <Input type="date" required value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value })} ltr />
          </Field>
          <Field label={t("att.in")}>
            <Input type="time" required value={form.in} onChange={(e) => setForm({ ...form, in: e.target.value })} ltr />
          </Field>
          <Field label={t("att.out")} hint={overnight ? t("att.nextDay") : undefined}>
            <Input type="time" required value={form.out} onChange={(e) => setForm({ ...form, out: e.target.value })} ltr />
          </Field>
        </div>
        <Field label={t("att.note")} optional>
          <Input value={form.note} maxLength={300} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        </Field>
        <div className="form-actions">
          <Button onClick={onDone}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
        </div>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------- the working day (HR settings)

const ZONES = ["Asia/Riyadh", "Asia/Dubai", "Asia/Kuwait", "Asia/Qatar", "Asia/Bahrain", "Asia/Muscat", "Asia/Amman",
  "Asia/Baghdad", "Africa/Cairo", "Europe/London", "UTC"];

export function WorkingDay() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const policy = usePolicy();
  return (
    <Card title={t("att.workingDay")}>
      <QueryView query={policy}>
        {(p) => <WorkingDayForm key={`${p.timezone}-${p.daily_minutes}`} timezone={p.timezone} dailyMinutes={p.daily_minutes}
          onSaved={(x) => { qc.setQueryData(["hr", "policy", org.id], x); void qc.invalidateQueries({ queryKey: ["hr", "attendance"] }); toast(t("common.saved")); }} />}
      </QueryView>
    </Card>
  );
}

function WorkingDayForm({ timezone, dailyMinutes, onSaved }: {
  timezone: string; dailyMinutes: number; onSaved: (p: Schemas["PolicyOut"]) => void;
}) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [form, setForm] = useState({ timezone, hours: String(dailyMinutes / 60) });
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/hr/policy", {
      params: { path: { org_id: org.id } },
      body: { timezone: form.timezone.trim(), daily_minutes: Math.round(Number(form.hours) * 60) },
    })),
    onSuccess: onSaved,
  });
  return (
    <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      {save.isError && <ErrorNotice error={save.error} />}
      <p className="muted small">{t("att.workingDayHint")}</p>
      <div className="grid-2">
        <Field label={t("att.timezone")}>
          <Input list="hr-zones" required value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} ltr />
        </Field>
        <Field label={t("att.dailyHours")}>
          <Input type="number" min={1} max={16} step={0.25} required value={form.hours} onChange={(e) => setForm({ ...form, hours: e.target.value })} ltr />
        </Field>
      </div>
      <datalist id="hr-zones">{ZONES.map((z) => <option key={z} value={z} />)}</datalist>
      <div><Button type="submit" loading={save.isPending}>{t("common.save")}</Button></div>
    </form>
  );
}
