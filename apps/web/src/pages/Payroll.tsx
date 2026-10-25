import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Calculator, CheckCircle2, Download, Pencil, Plus, Trash2, Wallet } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { download, StatusBadge, Tabs } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select,
  errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { today } from "../lib/format";

type Tab = "runs" | "pay" | "contributions" | "end";
type Run = Schemas["PayRunOut"];
type Contribution = Schemas["ContributionOut"];
type PaySummary = Schemas["PaySummaryOut"];
type ItemDraft = { name: string; kind: "earning" | "deduction"; amount: string; contributory: boolean; end_of_service: boolean };
type Settlement = Schemas["SettlementOut"];
type SettlementIn = Schemas["SettlementIn"];
type Reason = SettlementIn["reason"];

function useContributions() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["payroll", org.id, "contributions"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/contributions", { params: { path: { org_id: org.id } } })),
  });
}

export function PayrollPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("runs");
  return (
    <div className="stack">
      <PageHeader title={t("payroll.title")} subtitle={t("payroll.subtitle")} />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { key: "runs", label: t("payroll.tab.runs") },
        { key: "pay", label: t("payroll.tab.pay") },
        { key: "contributions", label: t("payroll.tab.contributions") },
        { key: "end", label: t("payroll.tab.end") },
      ]} />
      {tab === "runs" && <Runs />}
      {tab === "pay" && <EmployeesPay />}
      {tab === "contributions" && <Contributions />}
      {tab === "end" && <EndOfServiceTab />}
    </div>
  );
}

// ---------------------------------------------------------------- runs

function Runs() {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [month, setMonth] = useState(today().slice(0, 7));
  const [selected, setSelected] = useState<string | null>(null);
  const runs = useQuery({
    queryKey: ["payroll", org.id, "runs"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/runs", { params: { path: { org_id: org.id } } })),
  });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/runs", { params: { path: { org_id: org.id } }, body: { month } })),
    onSuccess: (run) => {
      qc.setQueryData(["payroll", org.id, "run", run.id], run);
      void qc.invalidateQueries({ queryKey: ["payroll", org.id, "runs"] });
      setSelected(run.id);
    },
  });
  return (
    <div className="stack">
      <Card>
        <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
          <Field label={t("payroll.month")}>
            <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} required ltr />
          </Field>
          <Button type="submit" variant="primary" icon={<Plus size={16} />} loading={create.isPending}>{t("payroll.newRun")}</Button>
        </form>
        {create.isError && <ErrorNotice error={create.error} />}
      </Card>
      <QueryView query={runs}>
        {(list) => list.length === 0 ? <Empty>{t("payroll.noRuns")}</Empty> : (
          <div className="table-wrap">
            <table aria-label={t("payroll.tab.runs")}>
              <thead>
                <tr>
                  <th>{t("payroll.month")}</th>
                  <th>{t("common.status")}</th>
                  <th className="end">{t("payroll.people")}</th>
                  <th className="end">{t("payroll.gross")}</th>
                  <th className="end">{t("payroll.net")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id} aria-selected={r.id === selected}>
                    <td className="num">{r.month}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="end num">{r.payslip_count}</td>
                    <td className="end num">{money(r.gross, r.currency)}</td>
                    <td className="end num">{money(r.net, r.currency)}</td>
                    <td className="end"><Button size="sm" onClick={() => setSelected(r.id)}>{t("payroll.open")}</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {selected && <RunDetail runId={selected} onGone={() => setSelected(null)} />}
    </div>
  );
}

function RunDetail({ runId, onGone }: { runId: string; onGone: () => void }) {
  const { t, money, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [dialog, setDialog] = useState<"pay" | "cancel" | null>(null);
  const ids = { org_id: org.id, run_id: runId };
  const key = ["payroll", org.id, "run", runId];
  const run = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/runs/{run_id}", { params: { path: ids } })),
  });
  const done = (r: Run) => {
    qc.setQueryData(key, r);
    void qc.invalidateQueries({ queryKey: ["payroll", org.id, "runs"] });
  };
  const onError = (e: Error) => toast(errorText(e, t("common.error")), "error");
  const recalc = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/runs/{run_id}/recalculate", { params: { path: ids } })),
    onSuccess: done, onError,
  });
  const post = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/runs/{run_id}/post", { params: { path: ids } })),
    onSuccess: (r) => { done(r); toast(t("payroll.posted")); }, onError,
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/orgs/{org_id}/payroll/runs/{run_id}", { params: { path: ids } })),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["payroll", org.id, "runs"] }); onGone(); }, onError,
  });
  return (
    <QueryView query={run}>
      {(r) => (
        <Card
          title={t("payroll.runOf", { month: r.month })}
          actions={
            <span className="row">
              <StatusBadge status={r.status} />
              {r.status === "draft" && (
                <>
                  <Button size="sm" icon={<Calculator size={14} />} loading={recalc.isPending} onClick={() => recalc.mutate()}>{t("payroll.recalculate")}</Button>
                  <Button size="sm" variant="danger" icon={<Trash2 size={14} />} loading={remove.isPending}
                    onClick={() => window.confirm(t("payroll.deleteConfirm")) && remove.mutate()}>{t("common.delete")}</Button>
                  <Button size="sm" variant="primary" icon={<CheckCircle2 size={14} />} loading={post.isPending}
                    onClick={() => window.confirm(t("payroll.postConfirm")) && post.mutate()}>{t("payroll.post")}</Button>
                </>
              )}
              {(r.status === "posted" || r.status === "paid") && (
                <Button size="sm" icon={<Download size={14} />}
                  onClick={() => download(`/api/v1/orgs/${org.id}/payroll/runs/${r.id}/bank-file.csv`, `payroll-${r.month}.csv`).catch(onError)}>
                  {t("payroll.bankFile")}
                </Button>
              )}
              {r.status === "posted" && (
                <>
                  <Button size="sm" variant="danger" icon={<Ban size={14} />} onClick={() => setDialog("cancel")}>{t("common.cancel")}</Button>
                  {can("payments.write") && (
                    <Button size="sm" variant="primary" icon={<Wallet size={14} />} onClick={() => setDialog("pay")}>{t("payroll.pay")}</Button>
                  )}
                </>
              )}
            </span>
          }
        >
          <div className="stack-sm">
            {r.without_pay.length > 0 && (
              <p className="notice notice-warning small">{t("payroll.withoutPay", { names: r.without_pay.join("، ") })}</p>
            )}
            <dl className="kv">
              <dt>{t("payroll.gross")}</dt><dd className="num">{money(r.gross, r.currency)}</dd>
              <dt>{t("payroll.deductions")}</dt><dd className="num">{money(r.deductions, r.currency)}</dd>
              <dt>{t("payroll.employeeContributions")}</dt><dd className="num">{money(r.employee_contributions, r.currency)}</dd>
              <dt>{t("payroll.employerContributions")}</dt><dd className="num">{money(r.employer_contributions, r.currency)}</dd>
              <dt><strong>{t("payroll.net")}</strong></dt><dd className="num"><strong>{money(r.net, r.currency)}</strong></dd>
              {r.end_of_service_change !== "0.00" && (
                <><dt>{t("payroll.eosChange")}</dt><dd className="num">{money(r.end_of_service_change, r.currency)}</dd></>
              )}
              {r.payment_date && (<><dt>{t("payroll.paidOn")}</dt><dd className="num">{date(r.payment_date)}</dd></>)}
            </dl>
            {r.payslips.length === 0 ? <Empty>{t("payroll.noSlips")}</Empty> : (
              <div className="table-wrap">
                <table aria-label={t("payroll.slips")}>
                  <thead>
                    <tr>
                      <th>{t("payroll.employee")}</th>
                      <th className="end">{t("payroll.daysPaid")}</th>
                      <th className="end">{t("payroll.gross")}</th>
                      <th className="end">{t("payroll.deductions")}</th>
                      <th className="end">{t("payroll.contributions")}</th>
                      <th className="end">{t("payroll.net")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.payslips.map((s) => (
                      <tr key={s.id}>
                        <td>
                          {s.employee_name}
                          {s.unpaid_leave_days !== "0" && <div className="muted small">{t("payroll.unpaidDays", { days: s.unpaid_leave_days })}</div>}
                        </td>
                        <td className="end num">{s.days_paid}/{s.days_in_period}</td>
                        <td className="end num">{money(s.gross, r.currency)}</td>
                        <td className="end num">{money(s.deductions, r.currency)}</td>
                        <td className="end num">{money(s.employee_contributions, r.currency)}</td>
                        <td className="end num"><strong>{money(s.net, r.currency)}</strong></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {dialog === "pay" && <PayDialog run={r} onClose={() => setDialog(null)} onDone={(x) => { setDialog(null); done(x); toast(t("payroll.paid")); }} />}
          {dialog === "cancel" && <CancelDialog run={r} onClose={() => setDialog(null)} onDone={(x) => { setDialog(null); done(x); }} />}
        </Card>
      )}
    </QueryView>
  );
}

function PayDialog({ run, onClose, onDone }: { run: Run; onClose: () => void; onDone: (r: Run) => void }) {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const [form, setForm] = useState({ payment_date: today() < run.period_start ? run.period_end : today(), method: "bank" as "bank" | "cash" });
  const pay = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/runs/{run_id}/pay", {
      params: { path: { org_id: org.id, run_id: run.id } }, body: form,
    })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("payroll.pay")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); pay.mutate(); }}>
        <p>{t("payroll.payHint", { amount: money(run.net, run.currency) })}</p>
        {pay.isError && <ErrorNotice error={pay.error} />}
        <div className="grid-2">
          <Field label={t("invoices.paymentDate")}>
            <Input type="date" value={form.payment_date} min={run.period_start} required ltr
              onChange={(e) => setForm({ ...form, payment_date: e.target.value })} />
          </Field>
          <Field label={t("invoices.method")}>
            <Select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value as "bank" | "cash" })}>
              <option value="bank">{t("invoices.bank")}</option>
              <option value="cash">{t("invoices.cash")}</option>
            </Select>
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={pay.isPending}>{t("payroll.pay")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function CancelDialog({ run, onClose, onDone }: { run: Run; onClose: () => void; onDone: (r: Run) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [day, setDay] = useState(today() < run.period_end ? run.period_end : today());
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/runs/{run_id}/cancel", {
      params: { path: { org_id: org.id, run_id: run.id } }, body: { cancel_date: day },
    })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("payroll.cancelRun")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); cancel.mutate(); }}>
        <p className="muted small">{t("payroll.cancelHint")}</p>
        {cancel.isError && <ErrorNotice error={cancel.error} />}
        <Field label={t("invoices.cancelDate")}>
          <Input type="date" value={day} min={run.period_end} required ltr onChange={(e) => setDay(e.target.value)} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.close")}</Button>
          <Button type="submit" variant="danger" loading={cancel.isPending}>{t("payroll.cancelRun")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- employees' pay

function EmployeesPay() {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const [editing, setEditing] = useState<PaySummary | null>(null);
  const people = useQuery({
    queryKey: ["payroll", org.id, "employees"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/employees", { params: { path: { org_id: org.id } } })),
  });
  return (
    <QueryView query={people}>
      {(list) => list.length === 0 ? <Empty>{t("payroll.noEmployees")}</Empty> : (
        <div className="table-wrap">
          <table aria-label={t("payroll.tab.pay")}>
            <thead>
              <tr>
                <th>{t("payroll.employee")}</th>
                <th className="end">{t("payroll.monthlyEarnings")}</th>
                <th className="end">{t("payroll.deductions")}</th>
                <th>{t("payroll.iban")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((p) => (
                <tr key={p.employee_id}>
                  <td>
                    {p.employee_name}
                    {p.job_title && <div className="muted small">{p.job_title}</div>}
                    {p.status === "left" && <Badge>{t("payroll.left")}</Badge>}
                  </td>
                  <td className="end num">{p.monthly_earnings === "0.00" ? "—" : money(p.monthly_earnings, org.default_currency)}</td>
                  <td className="end num">{p.monthly_deductions === "0.00" ? "—" : money(p.monthly_deductions, org.default_currency)}</td>
                  <td>{p.has_iban ? <Badge tone="success">{t("payroll.ibanSet")}</Badge> : <span className="muted">—</span>}</td>
                  <td className="end">
                    <IconButton label={t("payroll.editPay", { name: p.employee_name })} onClick={() => setEditing(p)}><Pencil size={16} /></IconButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {editing && <PayDialogEditor employee={editing} onClose={() => setEditing(null)} />}
        </div>
      )}
    </QueryView>
  );
}

function PayDialogEditor({ employee, onClose }: { employee: PaySummary; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const ids = { org_id: org.id, employee_id: employee.employee_id };
  const current = useQuery({
    queryKey: ["payroll", org.id, "pay", employee.employee_id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/employees/{employee_id}", { params: { path: ids } })),
  });
  return (
    <Dialog open onClose={onClose} title={t("payroll.payOf", { name: employee.employee_name })} wide>
      <QueryView query={current}>
        {(pay) => <PayForm initial={pay} onClose={onClose} />}
      </QueryView>
    </Dialog>
  );
}

function PayForm({ initial, onClose }: { initial: Schemas["EmployeePayOut"]; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const contributions = useContributions();
  const [iban, setIban] = useState(initial.iban);
  const [covered, setCovered] = useState<string[]>(initial.contribution_ids);
  const [items, setItems] = useState<ItemDraft[]>(initial.items.length ? initial.items.map((i) => ({ ...i }))
    : [{ name: t("payroll.basic"), kind: "earning", amount: "", contributory: true, end_of_service: true }]);
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/payroll/employees/{employee_id}", {
      params: { path: { org_id: org.id, employee_id: initial.employee_id } },
      body: { iban, contribution_ids: covered, items: items.filter((i) => i.name.trim() && i.amount) },
    })),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["payroll", org.id] }); onClose(); },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  const setItem = (n: number, patch: Partial<ItemDraft>) => setItems(items.map((it, i) => (i === n ? { ...it, ...patch } : it)));
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      {save.isError && <ErrorNotice error={save.error} />}
      <Field label={t("payroll.iban")} optional error={err["iban"]}>
        <Input value={iban} onChange={(e) => setIban(e.target.value.toUpperCase())} maxLength={42} placeholder="SA00 0000 0000 0000 0000 0000" ltr />
      </Field>
      <div className="table-wrap">
        <table aria-label={t("payroll.items")}>
          <thead>
            <tr>
              <th>{t("common.name")}</th>
              <th>{t("payroll.kind")}</th>
              <th className="end">{t("payroll.monthlyAmount")}</th>
              <th>{t("payroll.contributory")}</th>
              <th>{t("payroll.eosItem")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((it, n) => (
              <tr key={n}>
                <td><Input aria-label={t("payroll.itemName", { n: n + 1 })} value={it.name} maxLength={80} onChange={(e) => setItem(n, { name: e.target.value })} /></td>
                <td>
                  <Select aria-label={t("payroll.itemKind", { n: n + 1 })} value={it.kind}
                    onChange={(e) => setItem(n, { kind: e.target.value as ItemDraft["kind"], contributory: e.target.value === "earning" && it.contributory,
                      end_of_service: e.target.value === "earning" && it.end_of_service })}>
                    <option value="earning">{t("payroll.earning")}</option>
                    <option value="deduction">{t("payroll.deduction")}</option>
                  </Select>
                </td>
                <td><Input aria-label={t("payroll.itemAmount", { n: n + 1 })} value={it.amount} inputMode="decimal" onChange={(e) => setItem(n, { amount: e.target.value })} ltr /></td>
                <td>
                  <Checkbox label={t("payroll.contributoryShort")} checked={it.contributory} disabled={it.kind !== "earning"}
                    onChange={(e) => setItem(n, { contributory: e.target.checked })} />
                </td>
                <td>
                  <Checkbox label={t("payroll.eosItemShort", { n: n + 1 })} checked={it.end_of_service} disabled={it.kind !== "earning"}
                    onChange={(e) => setItem(n, { end_of_service: e.target.checked })} />
                </td>
                <td className="end">
                  <IconButton label={t("common.delete")} onClick={() => setItems(items.filter((_, i) => i !== n))}><Trash2 size={16} /></IconButton>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <Button size="sm" icon={<Plus size={14} />} onClick={() => setItems([...items, { name: "", kind: "earning", amount: "", contributory: false, end_of_service: false }])}>
          {t("payroll.addItem")}
        </Button>
      </div>
      {(contributions.data ?? []).length > 0 && (
        <fieldset className="stack-sm">
          <legend>{t("payroll.coveredBy")}</legend>
          {(contributions.data ?? []).filter((c) => c.active || covered.includes(c.id)).map((c) => (
            <Checkbox key={c.id} label={`${c.name} (${c.employee_rate}% / ${c.employer_rate}%)`} checked={covered.includes(c.id)}
              onChange={(e) => setCovered(e.target.checked ? [...covered, c.id] : covered.filter((x) => x !== c.id))} />
          ))}
        </fieldset>
      )}
      <p className="muted small">{t("payroll.payFormHint")}</p>
      <div className="form-actions">
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- contributions

function Contributions() {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const list = useContributions();
  const [editing, setEditing] = useState<Contribution | "new" | null>(null);
  return (
    <Card title={t("payroll.tab.contributions")} actions={
      <Button size="sm" icon={<Plus size={14} />} onClick={() => setEditing("new")}>{t("payroll.newContribution")}</Button>
    }>
      <p className="muted small">{t("payroll.contributionsHint")}</p>
      <QueryView query={list}>
        {(rows) => rows.length === 0 ? <Empty /> : (
          <table aria-label={t("payroll.tab.contributions")}>
            <thead>
              <tr>
                <th>{t("common.name")}</th>
                <th className="end">{t("payroll.employeeRate")}</th>
                <th className="end">{t("payroll.employerRate")}</th>
                <th className="end">{t("payroll.cap")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>{c.name} {!c.active && <Badge>{t("payroll.inactive")}</Badge>}</td>
                  <td className="end num">{c.employee_rate}%</td>
                  <td className="end num">{c.employer_rate}%</td>
                  <td className="end num">{c.base_cap ? money(c.base_cap, org.default_currency) : "—"}</td>
                  <td className="end"><IconButton label={t("common.edit")} onClick={() => setEditing(c)}><Pencil size={16} /></IconButton></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {editing && <ContributionDialog contribution={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function ContributionDialog({ contribution, onClose }: { contribution: Contribution | null; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: contribution?.name ?? "", employee_rate: contribution?.employee_rate ?? "", employer_rate: contribution?.employer_rate ?? "",
    base_cap: contribution?.base_cap ?? "", active: contribution?.active ?? true,
  });
  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, base_cap: form.base_cap || null };
      return contribution
        ? unwrap(api.PUT("/api/v1/orgs/{org_id}/payroll/contributions/{contribution_id}", {
          params: { path: { org_id: org.id, contribution_id: contribution.id } }, body,
        }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/contributions", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["payroll", org.id, "contributions"] }); onClose(); },
  });
  return (
    <Dialog open onClose={onClose} title={contribution ? contribution.name : t("payroll.newContribution")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && <ErrorNotice error={save.error} />}
        <Field label={t("common.name")}>
          <Input value={form.name} required maxLength={80} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <div className="grid-2">
          <Field label={t("payroll.employeeRate")} hint={t("payroll.percent")}>
            <Input value={form.employee_rate} required inputMode="decimal" onChange={(e) => setForm({ ...form, employee_rate: e.target.value })} ltr />
          </Field>
          <Field label={t("payroll.employerRate")} hint={t("payroll.percent")}>
            <Input value={form.employer_rate} required inputMode="decimal" onChange={(e) => setForm({ ...form, employer_rate: e.target.value })} ltr />
          </Field>
        </div>
        <Field label={t("payroll.cap")} hint={t("payroll.capHint")} optional>
          <Input value={form.base_cap} inputMode="decimal" onChange={(e) => setForm({ ...form, base_cap: e.target.value })} ltr />
        </Field>
        <Checkbox label={t("payroll.active")} checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- end of service

const REASONS: Reason[] = ["termination", "contract_end", "resignation", "resignation_full", "dismissal"];

function EndOfServiceTab() {
  const { t, money, date } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const path = { params: { path: { org_id: org.id } } };
  const settings = useQuery({
    queryKey: ["payroll", org.id, "settings"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/settings", path)),
  });
  const list = useQuery({
    queryKey: ["payroll", org.id, "end-of-service"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/end-of-service", path)),
  });
  const save = useMutation({
    mutationFn: (eosb_rule: "saudi" | "uae" | "none") => unwrap(api.PUT("/api/v1/orgs/{org_id}/payroll/settings", { ...path, body: { eosb_rule } })),
    onSuccess: (x) => { qc.setQueryData(["payroll", org.id, "settings"], x); toast(t("payroll.eos.rulesSaved")); },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const rule = settings.data?.eosb_rule;
  return (
    <div className="stack">
      <Card title={t("payroll.eos.rules")}>
        <div className="stack-sm">
          <p className="muted small">{t("payroll.eos.rulesHint")}</p>
          <Field label={t("payroll.eos.rules")}>
            <Select value={rule ?? "none"} disabled={!settings.data || save.isPending}
              onChange={(e) => save.mutate(e.target.value as "saudi" | "uae" | "none")}>
              <option value="none">{t("payroll.eos.rule.none")}</option>
              <option value="saudi">{t("payroll.eos.rule.saudi")}</option>
              <option value="uae">{t("payroll.eos.rule.uae")}</option>
            </Select>
          </Field>
          {rule && rule !== "none" && <p className="small">{t(`payroll.eos.ruleText.${rule}`)}</p>}
        </div>
      </Card>
      <Card title={t("payroll.eos.settlements")} actions={
        <Button variant="primary" icon={<Plus size={16} />} disabled={!rule || rule === "none"} onClick={() => setCreating(true)}>
          {t("payroll.eos.new")}
        </Button>
      }>
        {rule === "none" && <p className="notice notice-info small">{t("payroll.eos.chooseRules")}</p>}
        <QueryView query={list}>
          {(rows) => rows.length === 0 ? <Empty>{t("payroll.eos.none")}</Empty> : (
            <div className="table-wrap">
              <table aria-label={t("payroll.eos.settlements")}>
                <thead>
                  <tr>
                    <th>{t("payroll.employee")}</th>
                    <th>{t("payroll.eos.lastDay")}</th>
                    <th>{t("payroll.eos.reason")}</th>
                    <th>{t("common.status")}</th>
                    <th className="end">{t("payroll.eos.total")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((d) => (
                    <tr key={d.id} aria-selected={d.id === open}>
                      <td>{d.employee_name}</td>
                      <td className="num">{date(d.last_day)}</td>
                      <td>{t(`payroll.eos.reason.${d.reason as Reason}`)}</td>
                      <td><StatusBadge status={d.status} /></td>
                      <td className="end num">{money(d.total, d.currency)}</td>
                      <td className="end"><Button size="sm" onClick={() => setOpen(d.id)}>{t("payroll.open")}</Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryView>
      </Card>
      {open && list.data?.find((d) => d.id === open) && (
        <SettlementCard doc={list.data.find((d) => d.id === open)!} onGone={() => setOpen(null)} />
      )}
      {creating && settings.data && (
        <NewSettlementDialog reasons={settings.data.reasons as Reason[]} onClose={() => setCreating(false)}
          onCreated={(d) => { setCreating(false); setOpen(d.id); }} />
      )}
    </div>
  );
}

function Breakdown({ d }: { d: Schemas["SettlementPreviewOut"] }) {
  const { t, money } = useI18n();
  const c = d.currency;
  return (
    <dl className="kv">
      <dt>{t("payroll.eos.service")}</dt>
      <dd className="num">{t("payroll.eos.serviceValue", { days: d.service_days, years: d.service_years })}</dd>
      <dt>{t("payroll.eos.wage")}</dt><dd className="num">{money(d.wage, c)}</dd>
      <dt>{t("payroll.eos.months")}</dt><dd className="num">{d.months}</dd>
      <dt>{t("payroll.eos.fullAward")}</dt><dd className="num">{money(d.full_award, c)}</dd>
      <dt>{t("payroll.eos.share")}</dt><dd className="num"><bdi>{d.share}</bdi></dd>
      <dt>{t("payroll.eos.award")}</dt><dd className="num">{money(d.award, c)}</dd>
      <dt>{t("payroll.eos.leavePay", { days: d.leave_days })}</dt><dd className="num">{money(d.leave_pay, c)}</dd>
      <dt><strong>{t("payroll.eos.total")}</strong></dt><dd className="num"><strong>{money(d.total, c)}</strong></dd>
      <dt>{t("payroll.eos.provision")}</dt><dd className="num muted">{money(d.provision, c)}</dd>
    </dl>
  );
}

function NewSettlementDialog({ reasons, onClose, onCreated }: { reasons: Reason[]; onClose: () => void; onCreated: (d: Settlement) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const people = useQuery({
    queryKey: ["payroll", org.id, "employees"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/employees", { params: { path: { org_id: org.id } } })),
  });
  const [form, setForm] = useState<SettlementIn>({ employee_id: "", last_day: today(), reason: reasons[0] ?? "termination", leave_days: "0", notes: "" });
  const path = { params: { path: { org_id: org.id } } };
  const ready = Boolean(form.employee_id && form.last_day);
  const preview = useQuery({
    queryKey: ["payroll", org.id, "eos-preview", form.employee_id, form.last_day, form.reason, form.leave_days],
    queryFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/end-of-service/preview", { ...path, body: form })),
    enabled: ready && /^\d+(\.5|\.0)?$/.test(String(form.leave_days)),
    retry: false,
  });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/end-of-service", { ...path, body: form })),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ["payroll", org.id, "end-of-service"] }); onCreated(d); },
  });
  const set = <K extends keyof SettlementIn>(k: K, v: SettlementIn[K]) => setForm({ ...form, [k]: v });
  return (
    <Dialog open onClose={onClose} title={t("payroll.eos.new")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("payroll.employee")}>
            <Select value={form.employee_id} required onChange={(e) => set("employee_id", e.target.value)}>
              <option value="">{t("payroll.eos.choose")}</option>
              {(people.data ?? []).map((p) => <option key={p.employee_id} value={p.employee_id}>{p.employee_name}</option>)}
            </Select>
          </Field>
          <Field label={t("payroll.eos.lastDay")}>
            <Input type="date" value={form.last_day} required ltr onChange={(e) => set("last_day", e.target.value)} />
          </Field>
          <Field label={t("payroll.eos.reason")}>
            <Select value={form.reason} onChange={(e) => set("reason", e.target.value as Reason)}>
              {REASONS.filter((r) => reasons.includes(r)).map((r) => <option key={r} value={r}>{t(`payroll.eos.reason.${r}`)}</option>)}
            </Select>
          </Field>
          <Field label={t("payroll.eos.leaveDays")} hint={t("payroll.eos.leaveHint")}>
            <Input value={String(form.leave_days ?? "")} inputMode="decimal" ltr onChange={(e) => set("leave_days", e.target.value)} />
          </Field>
        </div>
        {preview.data && preview.data.leave_balances.length > 0 && (
          <p className="small muted">
            {t("payroll.eos.balances", { list: preview.data.leave_balances.map((b) => `${b.leave_type}: \u2066${b.remaining}\u2069`).join("، ") })}
          </p>
        )}
        {preview.isError && <ErrorNotice error={preview.error} />}
        {preview.data && <Breakdown d={preview.data} />}
        <Field label={t("payroll.eos.notes")} optional>
          <Input value={form.notes ?? ""} maxLength={2000} onChange={(e) => set("notes", e.target.value)} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending} disabled={!preview.data}>{t("payroll.eos.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function SettlementCard({ doc, onGone }: { doc: Settlement; onGone: () => void }) {
  const { t, money, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [dialog, setDialog] = useState<"pay" | "cancel" | null>(null);
  const [day, setDay] = useState(today() < doc.last_day ? doc.last_day : today());
  const [method, setMethod] = useState<"bank" | "cash">("bank");
  const ids = { params: { path: { org_id: org.id, doc_id: doc.id } } };
  const refresh = () => void qc.invalidateQueries({ queryKey: ["payroll", org.id] });
  const onError = (e: Error) => toast(errorText(e, t("common.error")), "error");
  const post = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/end-of-service/{doc_id}/post", ids)),
    onSuccess: () => { refresh(); toast(t("payroll.eos.posted")); }, onError,
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/orgs/{org_id}/payroll/end-of-service/{doc_id}", ids)),
    onSuccess: () => { refresh(); onGone(); }, onError,
  });
  const pay = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/end-of-service/{doc_id}/pay", { ...ids, body: { payment_date: day, method } })),
    onSuccess: () => { setDialog(null); refresh(); toast(t("payroll.eos.paid")); },
  });
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/payroll/end-of-service/{doc_id}/cancel", { ...ids, body: { cancel_date: day } })),
    onSuccess: () => { setDialog(null); refresh(); },
  });
  return (
    <Card title={t("payroll.eos.of", { name: doc.employee_name })} actions={
      <span className="row">
        <StatusBadge status={doc.status} />
        {doc.status === "draft" && (
          <>
            <Button size="sm" variant="danger" icon={<Trash2 size={14} />} loading={remove.isPending}
              onClick={() => window.confirm(t("payroll.eos.deleteConfirm")) && remove.mutate()}>{t("common.delete")}</Button>
            <Button size="sm" variant="primary" icon={<CheckCircle2 size={14} />} loading={post.isPending}
              onClick={() => window.confirm(t("payroll.eos.postConfirm")) && post.mutate()}>{t("payroll.eos.post")}</Button>
          </>
        )}
        {doc.status === "posted" && (
          <>
            <Button size="sm" variant="danger" icon={<Ban size={14} />} onClick={() => setDialog("cancel")}>{t("payroll.eos.cancel")}</Button>
            {can("payments.write") && (
              <Button size="sm" variant="primary" icon={<Wallet size={14} />} onClick={() => setDialog("pay")}>{t("payroll.eos.pay")}</Button>
            )}
          </>
        )}
      </span>
    }>
      <div className="stack-sm">
        <p className="small muted">
          {t(`payroll.eos.reason.${doc.reason as Reason}`)} · {t("payroll.eos.lastDay")}: <span className="num">{date(doc.last_day)}</span>
          {doc.payment_date && <> · {t("payroll.paidOn")}: <span className="num">{date(doc.payment_date)}</span></>}
        </p>
        {doc.notes && <p className="small" dir="auto">{doc.notes}</p>}
        <Breakdown d={doc} />
        {doc.status === "draft" && <p className="small muted">{t("payroll.eos.draftHint")}</p>}
      </div>
      {dialog && (
        <Dialog open onClose={() => setDialog(null)} title={dialog === "pay" ? t("payroll.eos.pay") : t("payroll.eos.cancel")}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); (dialog === "pay" ? pay : cancel).mutate(); }}>
            <p className="small">{dialog === "pay" ? t("payroll.eos.payHint", { amount: money(doc.total, doc.currency) }) : t("payroll.eos.cancelHint")}</p>
            {(pay.isError || cancel.isError) && <ErrorNotice error={pay.error ?? cancel.error} />}
            <div className="grid-2">
              <Field label={dialog === "pay" ? t("invoices.paymentDate") : t("invoices.cancelDate")}>
                <Input type="date" value={day} min={doc.last_day} required ltr onChange={(e) => setDay(e.target.value)} />
              </Field>
              {dialog === "pay" && (
                <Field label={t("invoices.method")}>
                  <Select value={method} onChange={(e) => setMethod(e.target.value as "bank" | "cash")}>
                    <option value="bank">{t("invoices.bank")}</option>
                    <option value="cash">{t("invoices.cash")}</option>
                  </Select>
                </Field>
              )}
            </div>
            <div className="form-actions">
              <Button onClick={() => setDialog(null)}>{t("common.close")}</Button>
              <Button type="submit" variant={dialog === "pay" ? "primary" : "danger"} loading={pay.isPending || cancel.isPending}>
                {dialog === "pay" ? t("payroll.eos.pay") : t("payroll.eos.cancel")}
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </Card>
  );
}
