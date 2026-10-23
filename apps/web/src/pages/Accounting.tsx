import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, BookPlus, CalendarCheck, Plus, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { Tabs, useIdempotencyKey } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select,
  errorText, useToast,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { digitsFor, isPositive, isZero, parseDec, sumDec } from "../lib/decimal";
import { today } from "../lib/format";
import { BooksLock, ExportButton, qs, useAccountName } from "./Reports";

type Tab = "journal" | "accounts" | "year";
type Account = Schemas["AccountOut"];
type Entry = Schemas["JournalEntryOut"];

const TYPES = ["asset", "liability", "equity", "income", "expense"] as const;

function useAccounts() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["accounts", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/accounts", { params: { path: { org_id: org.id } } })),
  });
}

export function AccountingPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("journal");
  return (
    <div className="stack">
      <PageHeader title={t("nav.accounting")} subtitle={t("accounting.subtitle")} actions={<BooksLock />} />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "journal", label: t("reports.journal") },
          { key: "accounts", label: t("accounting.chart") },
          { key: "year", label: t("yearEnd.tab") },
        ]}
      />
      {tab === "journal" && <Journal />}
      {tab === "accounts" && <ChartOfAccounts />}
      {tab === "year" && <YearEnd />}
    </div>
  );
}

// ---------------------------------------------------------------- journal

const SOURCES = ["manual", "invoice", "payment", "credit_note", "refund", "vendor_bill", "bill_payment", "vendor_credit",
  "vendor_refund", "stock", "year_end_close"];

function Journal() {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const [range, setRange] = useState({ date_from: "", date_to: "", source_type: "" });
  const [writing, setWriting] = useState(false);
  const entries = useQuery({
    queryKey: ["journal", org.id, range],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/journal-entries", {
        params: {
          path: { org_id: org.id },
          query: { limit: 200, date_from: range.date_from || undefined, date_to: range.date_to || undefined,
                   source_type: range.source_type || undefined },
        },
      })),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <div className="row filters">
        <Field label={t("reports.from")} optional>
          <Input type="date" value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} ltr />
        </Field>
        <Field label={t("reports.to")} optional>
          <Input type="date" value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} ltr />
        </Field>
        <Field label={t("journal.source")} optional>
          <Select value={range.source_type} onChange={(e) => setRange({ ...range, source_type: e.target.value })}>
            <option value="">{t("journal.allSources")}</option>
            {SOURCES.map((s) => <option key={s} value={s}>{t(`journal.src.${s}` as MessageKey)}</option>)}
          </Select>
        </Field>
        <div className="grow" />
        <ExportButton path={`/api/v1/orgs/${org.id}/accounting/journal.csv${qs({ date_from: range.date_from, date_to: range.date_to })}`}
          filename="journal.csv" />
        {can("accounting.journal") && (
          <Button variant="primary" icon={<BookPlus size={16} />} onClick={() => setWriting(true)}>{t("journal.new")}</Button>
        )}
      </div>
      <p className="muted small">{t("reports.journalHint")}</p>
      <QueryView query={entries}>
        {(list) => list.length === 0 ? <Empty /> : (
          <div className="stack-sm">
            {list.map((e) => <EntryCard key={e.id} entry={e} />)}
          </div>
        )}
      </QueryView>
      {writing && <ManualEntryDialog onClose={() => setWriting(false)} />}
    </div>
  );
}

function EntryCard({ entry: e }: { entry: Entry }) {
  const { t, amount, date } = useI18n();
  const { can } = useOrg();
  const name = useAccountName();
  const [reversing, setReversing] = useState(false);
  const reversible = (e.source_type === "manual" && can("accounting.journal"))
    || (e.source_type === "year_end_close" && can("accounting.journal") && can("accounting.close"));
  return (
    <Card tight>
      <div className="row-between" style={{ marginBlockEnd: "var(--space-2)" }}>
        <span className="row">
          <strong className="mono">{e.number}</strong>
          <span className="num small muted">{date(e.entry_date)}</span>
          <Badge tone={e.source_type === "manual" ? "primary" : "neutral"}>
            {SOURCES.includes(e.source_type) ? t(`journal.src.${e.source_type}` as MessageKey) : e.source_type || "—"}
          </Badge>
          {e.reversal_of_id && <Badge tone="warning">{t("reports.reversal")}</Badge>}
          {e.reversed_by_id && <Badge tone="warning">{t("journal.reversed")}</Badge>}
        </span>
        <span className="row">
          <span className="muted small" dir="auto">{e.memo}</span>
          {reversible && !e.reversal_of_id && !e.reversed_by_id && (
            <Button size="sm" icon={<Undo2 size={14} />} onClick={() => setReversing(true)}>{t("journal.reverse")}</Button>
          )}
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th>{t("reports.account")}</th>
            <th>{t("common.description")}</th>
            <th className="end">{t("reports.debit")}</th>
            <th className="end">{t("reports.credit")}</th>
          </tr>
        </thead>
        <tbody>
          {e.lines.map((l, i) => (
            <tr key={i}>
              <td><span className="mono">{l.account_code}</span> {name(l.account_code, l.account_name ?? "")}</td>
              <td dir="auto">{l.label}</td>
              <td className="end num">{isZero(l.debit) ? "" : amount(l.debit)}</td>
              <td className="end num">{isZero(l.credit) ? "" : amount(l.credit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {reversing && <ReverseDialog entry={e} onClose={() => setReversing(false)} />}
    </Card>
  );
}

type DraftLine = { key: number; account_id: string; debit: string; credit: string; label: string };
let nextKey = 1;
const blankLine = (): DraftLine => ({ key: nextKey++, account_id: "", debit: "", credit: "", label: "" });

/** Debits and credits must balance; accounts kept by documents (receivable, payable, stock) aren't offered. */
function ManualEntryDialog({ onClose }: { onClose: () => void }) {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const name = useAccountName();
  const accounts = useAccounts();
  const [idempotencyKey] = useIdempotencyKey();
  const currency = org.default_currency;
  const digits = digitsFor(currency);
  const [form, setForm] = useState({ entry_date: today(), memo: "" });
  const [lines, setLines] = useState<DraftLine[]>([blankLine(), blankLine()]);
  const debit = sumDec(lines.map((l) => l.debit), digits);
  const credit = sumDec(lines.map((l) => l.credit), digits);
  const difference = sumDec([debit, `-${credit}`], digits);
  const balanced = isZero(difference) && isPositive(debit);
  const valid = lines.every((l) => !l.account_id || (parseDec(l.debit || "0") && parseDec(l.credit || "0")));
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/accounting/journal-entries", {
        params: { path: { org_id: org.id }, header: { "idempotency-key": idempotencyKey } },
        body: {
          ...form,
          lines: lines.filter((l) => l.account_id).map((l) => ({
            account_id: l.account_id, debit: l.debit || "0", credit: l.credit || "0", label: l.label,
          })),
        },
      })),
    onSuccess: (e) => {
      toast(t("journal.posted", { number: e.number }));
      void qc.invalidateQueries({ queryKey: ["journal", org.id] });
      void qc.invalidateQueries({ queryKey: ["report", org.id] });
      onClose();
    },
  });
  const set = (key: number, patch: Partial<DraftLine>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const postable = (accounts.data ?? []).filter((a) => a.active && !a.control);
  return (
    <Dialog open onClose={onClose} title={t("journal.new")} wide>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="muted small">{t("journal.newHint")}</p>
        {save.isError && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("journal.date")}>
            <Input type="date" value={form.entry_date} onChange={(e) => setForm({ ...form, entry_date: e.target.value })} required ltr />
          </Field>
          <Field label={t("journal.memo")}>
            <Input value={form.memo} maxLength={300} onChange={(e) => setForm({ ...form, memo: e.target.value })} required />
          </Field>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("reports.account")}</th>
                <th style={{ width: 130 }}>{t("reports.debit")}</th>
                <th style={{ width: 130 }}>{t("reports.credit")}</th>
                <th>{t("common.description")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={l.key}>
                  <td>
                    <Select aria-label={`${t("reports.account")} ${i + 1}`} value={l.account_id} onChange={(e) => set(l.key, { account_id: e.target.value })}>
                      <option value="">—</option>
                      {postable.map((a) => <option key={a.id} value={a.id}>{a.code} · {name(a.code, a.name)}</option>)}
                    </Select>
                  </td>
                  <td>
                    <Input aria-label={`${t("reports.debit")} ${i + 1}`} inputMode="decimal" value={l.debit} placeholder="0"
                      onChange={(e) => set(l.key, { debit: e.target.value, credit: e.target.value ? "" : l.credit })} ltr />
                  </td>
                  <td>
                    <Input aria-label={`${t("reports.credit")} ${i + 1}`} inputMode="decimal" value={l.credit} placeholder="0"
                      onChange={(e) => set(l.key, { credit: e.target.value, debit: e.target.value ? "" : l.debit })} ltr />
                  </td>
                  <td>
                    <Input aria-label={`${t("common.description")} ${i + 1}`} value={l.label} maxLength={300}
                      onChange={(e) => set(l.key, { label: e.target.value })} />
                  </td>
                  <td className="end">
                    {lines.length > 2 && (
                      <IconButton label={`${t("common.delete")} ${i + 1}`} onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}>
                        <Trash2 size={16} />
                      </IconButton>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>
                  <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setLines((ls) => [...ls, blankLine()])}>
                    {t("journal.addLine")}
                  </Button>
                </td>
                <td className="num"><strong>{money(debit, currency)}</strong></td>
                <td className="num"><strong>{money(credit, currency)}</strong></td>
                <td colSpan={2}>
                  {balanced ? <Badge tone="success">{t("reports.balanced")}</Badge>
                    : <Badge tone="danger">{t("journal.difference", { amount: money(difference.replace("-", ""), currency) })}</Badge>}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!balanced || !valid}>{t("journal.post")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function ReverseDialog({ entry, onClose }: { entry: Entry; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    entry_date: entry.entry_date > today() ? entry.entry_date : today(),
    memo: t("journal.reversalOf", { number: entry.number }),
  });
  const reverse = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/accounting/journal-entries/{entry_id}/reverse", {
        params: { path: { org_id: org.id, entry_id: entry.id } }, body: form,
      })),
    onSuccess: (r) => {
      toast(t("journal.reversedToast", { number: entry.number, reversal: r.number }));
      void qc.invalidateQueries({ queryKey: ["journal", org.id] });
      void qc.invalidateQueries({ queryKey: ["year-end", org.id] });
      onClose();
    },
  });
  return (
    <Dialog open onClose={onClose} title={t("journal.reverseTitle", { number: entry.number })}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); reverse.mutate(); }}>
        <p className="muted">{entry.source_type === "year_end_close" ? t("yearEnd.reopenHint") : t("journal.reverseHint")}</p>
        {reverse.isError && <ErrorNotice error={reverse.error} />}
        <Field label={t("journal.date")}>
          <Input type="date" value={form.entry_date} min={entry.entry_date} onChange={(e) => setForm({ ...form, entry_date: e.target.value })} required ltr />
        </Field>
        <Field label={t("journal.memo")} optional>
          <Input value={form.memo} maxLength={300} onChange={(e) => setForm({ ...form, memo: e.target.value })} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="danger" loading={reverse.isPending}>{t("journal.reverse")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- chart of accounts

function ChartOfAccounts() {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const name = useAccountName();
  const accounts = useAccounts();
  const [adding, setAdding] = useState(false);
  const toggle = useMutation({
    mutationFn: (a: Account) =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/accounting/accounts/{account_id}", {
        params: { path: { org_id: org.id, account_id: a.id } }, body: { active: !a.active },
      })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["accounts", org.id] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const write = can("accounting.journal");
  return (
    <div className="stack">
      <div className="row-between">
        <p className="muted small">{t("accounting.chartHint")}</p>
        {write && <Button variant="primary" icon={<Plus size={16} />} onClick={() => setAdding(true)}>{t("accounting.newAccount")}</Button>}
      </div>
      <QueryView query={accounts}>
        {(list) => (
          <div className="stack">
            {TYPES.map((type) => {
              const rows = list.filter((a) => a.type === type);
              if (rows.length === 0) return null;
              return (
                <Card key={type} title={t(`accounting.type.${type}`)} tight>
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: 90 }}>{t("accounting.code")}</th>
                        <th>{t("common.name")}</th>
                        <th>{t("common.status")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((a) => (
                        <tr key={a.id} className={a.active ? "" : "muted"}>
                          <td className="mono">{a.code}</td>
                          <td>{name(a.code, a.name)}</td>
                          <td>
                            <span className="row" style={{ gap: 4 }}>
                              {!a.active && <Badge>{t("accounting.archived")}</Badge>}
                              {a.control && <Badge tone="info">{t("accounting.control")}</Badge>}
                              {a.system_key && !a.control && <Badge>{t("accounting.system")}</Badge>}
                            </span>
                          </td>
                          <td className="end">
                            {write && !a.system_key && (
                              <IconButton label={`${a.active ? t("accounting.archive") : t("accounting.restore")} ${a.code}`}
                                onClick={() => toggle.mutate(a)}>
                                {a.active ? <Archive size={16} /> : <ArchiveRestore size={16} />}
                              </IconButton>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              );
            })}
          </div>
        )}
      </QueryView>
      {adding && <NewAccountDialog onClose={() => setAdding(false)} />}
    </div>
  );
}

function NewAccountDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [form, setForm] = useState({ code: "", name: "", type: "expense" as (typeof TYPES)[number] });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/accounting/accounts", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["accounts", org.id] });
      onClose();
    },
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("accounting.newAccount")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && !Object.keys(err).length && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("accounting.code")} hint={t("accounting.codeHint")} error={err["code"]}>
            <Input value={form.code} required maxLength={20} onChange={(e) => setForm({ ...form, code: e.target.value })} ltr />
          </Field>
          <Field label={t("accounting.typeLabel")}>
            <Select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as (typeof TYPES)[number] })}>
              {TYPES.map((x) => <option key={x} value={x}>{t(`accounting.type.${x}`)}</option>)}
            </Select>
          </Field>
        </div>
        <Field label={t("common.name")} error={err["name"]}>
          <Input value={form.name} required maxLength={200} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- closing the year

function lastYearEnd(): string {
  return `${new Date().getFullYear() - 1}-12-31`;
}

function YearEnd() {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({
    queryKey: ["year-end", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/accounting/year-end", { params: { path: { org_id: org.id } } })),
  });
  const [closing, setClosing] = useState(false);
  const [form, setForm] = useState({ period_end: lastYearEnd(), lock: true });
  const close = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/accounting/year-end", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: (r) => {
      qc.setQueryData(["year-end", org.id], r);
      void qc.invalidateQueries({ queryKey: ["books-lock", org.id] });
      void qc.invalidateQueries({ queryKey: ["journal", org.id] });
      void qc.invalidateQueries({ queryKey: ["report", org.id] });
      setClosing(false);
      toast(t("yearEnd.done", { date: date(form.period_end) }));
    },
  });
  return (
    <Card title={t("yearEnd.title")}>
      <div className="stack">
        <p className="muted">{t("yearEnd.about")}</p>
        <QueryView query={status}>
          {(s) => (
            <dl className="kv">
              <dt>{t("yearEnd.last")}</dt>
              <dd className="num">{s.last_closed ? date(s.last_closed) : t("yearEnd.never")}</dd>
              <dt>{t("lock.date")}</dt>
              <dd className="num">{s.locked_until ? date(s.locked_until) : t("lock.open")}</dd>
            </dl>
          )}
        </QueryView>
        {can("accounting.close") && (
          <div>
            <Button variant="primary" icon={<CalendarCheck size={16} />} onClick={() => setClosing(true)}>{t("yearEnd.close")}</Button>
          </div>
        )}
      </div>
      {closing && (
        <Dialog open onClose={() => setClosing(false)} title={t("yearEnd.close")}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); close.mutate(); }}>
            <p className="muted">{t("yearEnd.confirmHint")}</p>
            {close.isError && <ErrorNotice error={close.error} />}
            <Field label={t("yearEnd.periodEnd")}>
              <Input type="date" value={form.period_end} max={today()} onChange={(e) => setForm({ ...form, period_end: e.target.value })} required ltr />
            </Field>
            <Checkbox label={t("yearEnd.lock")} checked={form.lock} onChange={(e) => setForm({ ...form, lock: e.target.checked })} />
            <div className="form-actions">
              <Button onClick={() => setClosing(false)}>{t("common.cancel")}</Button>
              <Button type="submit" variant="primary" loading={close.isPending}>{t("yearEnd.close")}</Button>
            </div>
          </form>
        </Dialog>
      )}
    </Card>
  );
}
