import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, Check, ChevronLeft, ChevronRight, Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { Tabs } from "../components/business";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea,
  errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";
import type { MessageKey } from "../i18n/en";
import { locale, type Lang } from "../lib/format";

type Tab = "week" | "requests" | "setup";
type Booking = Schemas["BookingOut"];
type Settings = Schemas["BookingSettingsOut"];
type Hours = Schemas["OpeningHoursIn"];

const TONE = { requested: "warning", confirmed: "success", declined: "danger", cancelled: "neutral" } as const;
// Saturday first: the order a week reads in the region (Monday = 0).
const WEEK_ORDER = [5, 6, 0, 1, 2, 3, 4];

/** Formatting in the business's booking time zone, whatever the viewer's own clock says. */
function useZone(tz: string, lang: Lang) {
  return useMemo(() => {
    const loc = locale(lang);
    const time = new Intl.DateTimeFormat(loc, { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    const key = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
    const label = new Intl.DateTimeFormat(loc, { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" });
    return {
      time: (iso: string) => time.format(new Date(iso)),
      dayOf: (iso: string) => key.format(new Date(iso)),
      today: () => key.format(new Date()),
      dayLabel: (day: string) => label.format(new Date(`${day}T12:00:00Z`)),
    };
  }, [tz, lang]);
}

function addDays(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function weekdayNames(lang: Lang): string[] {
  const fmt = new Intl.DateTimeFormat(locale(lang), { weekday: "long", timeZone: "UTC" });
  return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(Date.UTC(2024, 0, 1 + i, 12))));
}

function useSettings() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["bookings", "settings", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bookings/settings", { params: { path: { org_id: org.id } } })),
  });
}

function useServices() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["bookings", "services", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bookings/services", { params: { path: { org_id: org.id } } })),
  });
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  return <Badge tone={TONE[status as keyof typeof TONE] ?? "neutral"}>{t(`bookings.status.${status}` as MessageKey)}</Badge>;
}

export function BookingsPage() {
  const settings = useSettings();
  return <QueryView query={settings}>{(s) => <BookingsView settings={s} />}</QueryView>;
}

function BookingsView({ settings: s }: { settings: Settings }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const [tab, setTab] = useState<Tab>("week");
  const [booking, setBooking] = useState(false);
  const waiting = useQuery({
    queryKey: ["bookings", "requests", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bookings/requests", { params: { path: { org_id: org.id } } })),
  });
  const n = waiting.data?.length ?? 0;
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.bookings")}
        subtitle={t("bookings.subtitle", { tz: s.timezone })}
        actions={can("bookings.manage") && (
          <Button variant="primary" icon={<CalendarPlus size={16} />} onClick={() => setBooking(true)}>{t("bookings.new")}</Button>
        )}
      />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { key: "week", label: t("bookings.tab.week") },
        { key: "requests", label: n ? t("bookings.tab.requestsN", { n }) : t("bookings.tab.requests") },
        { key: "setup", label: t("bookings.tab.setup") },
      ]} />
      {tab === "week" && <Week settings={s} />}
      {tab === "requests" && <Requests settings={s} />}
      {tab === "setup" && <Setup settings={s} />}
      {booking && <BookDialog settings={s} onClose={() => setBooking(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------- the week

function Week({ settings: s }: { settings: Settings }) {
  const { t, lang } = useI18n();
  const { org } = useOrg();
  const z = useZone(s.timezone, lang);
  const [start, setStart] = useState(() => z.today());
  const end = addDays(start, 6);
  const list = useQuery({
    queryKey: ["bookings", "week", org.id, start],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bookings", { params: { path: { org_id: org.id }, query: { start, end } } })),
  });
  return (
    <div className="stack">
      <div className="row-between">
        <div className="row" style={{ gap: 4 }}>
          <IconButton label={t("bookings.prevWeek")} onClick={() => setStart(addDays(start, -7))}><ChevronRight size={16} className="back-arrow" aria-hidden /></IconButton>
          <Button size="sm" variant="ghost" onClick={() => setStart(z.today())}>{t("bookings.thisWeek")}</Button>
          <IconButton label={t("bookings.nextWeek")} onClick={() => setStart(addDays(start, 7))}><ChevronLeft size={16} className="back-arrow" aria-hidden /></IconButton>
        </div>
        <span className="muted small">{z.dayLabel(start)} – {z.dayLabel(end)}</span>
      </div>
      <QueryView query={list}>
        {(rows) => {
          const live = rows.filter((b) => b.status === "requested" || b.status === "confirmed");
          if (live.length === 0) return <Empty>{t("bookings.emptyWeek")}</Empty>;
          const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
          return (
            <div className="stack">
              {days.map((day) => {
                const items = live.filter((b) => z.dayOf(b.starts_at) === day);
                if (items.length === 0) return null;
                return (
                  <Card key={day} title={z.dayLabel(day)}>
                    <BookingList items={items} zoneOf={z} />
                  </Card>
                );
              })}
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}

function BookingList({ items, zoneOf: z }: { items: Booking[]; zoneOf: ReturnType<typeof useZone> }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [declining, setDeclining] = useState<Booking | null>(null);
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "confirm" | "cancel" }) => action === "confirm"
      ? unwrap(api.POST("/api/v1/orgs/{org_id}/bookings/{booking_id}/confirm", { params: { path: { org_id: org.id, booking_id: id } } }))
      : unwrap(api.POST("/api/v1/orgs/{org_id}/bookings/{booking_id}/cancel", { params: { path: { org_id: org.id, booking_id: id } }, body: { note: "" } })),
    onSuccess: (b) => { void qc.invalidateQueries({ queryKey: ["bookings"] }); toast(t(`bookings.done.${b.status}` as MessageKey)); },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <>
      <ul className="booking-list">
        {items.map((b) => (
          <li key={b.id} className={`booking ${b.status}`}>
            <span className="booking-time num" dir="ltr">{z.time(b.starts_at)}–{z.time(b.ends_at)}</span>
            <span className="grow stack-sm" style={{ gap: 2 }}>
              <strong dir="auto">{b.name}</strong>
              <span className="muted small">
                <span dir="auto">{b.service_name}</span>
                {(b.phone || b.email) && <> · <span dir="ltr">{b.phone || b.email}</span></>}
                {b.source && <> · {t("bookings.fromSite", { site: b.source })}</>}
              </span>
              {b.note && <span className="small" dir="auto">{b.note}</span>}
            </span>
            <StatusBadge status={b.status} />
            {can("bookings.manage") && (
              <span className="row" style={{ gap: 4 }}>
                {b.status === "requested" && (
                  <>
                    <Button size="sm" variant="primary" icon={<Check size={14} />} loading={act.isPending && act.variables?.id === b.id}
                      onClick={() => act.mutate({ id: b.id, action: "confirm" })}>{t("bookings.confirm")}</Button>
                    <Button size="sm" icon={<X size={14} />} onClick={() => setDeclining(b)}>{t("bookings.decline")}</Button>
                  </>
                )}
                {b.status === "confirmed" && (
                  <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: b.id, action: "cancel" })}>{t("bookings.cancel")}</Button>
                )}
              </span>
            )}
          </li>
        ))}
      </ul>
      {declining && <DeclineDialog booking={declining} onClose={() => setDeclining(null)} />}
    </>
  );
}

function DeclineDialog({ booking, onClose }: { booking: Booking; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const decline = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/bookings/{booking_id}/decline", {
      params: { path: { org_id: org.id, booking_id: booking.id } }, body: { note },
    })),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["bookings"] }); onClose(); },
  });
  return (
    <Dialog open onClose={onClose} title={t("bookings.declineTitle", { name: booking.name })}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); decline.mutate(); }}>
        {decline.isError && <ErrorNotice error={decline.error} />}
        <Field label={t("bookings.note")} hint={t("bookings.noteHint")} optional>
          <Textarea value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="danger" loading={decline.isPending}>{t("bookings.decline")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- requests from sites

function Requests({ settings: s }: { settings: Settings }) {
  const { t, lang } = useI18n();
  const { org } = useOrg();
  const z = useZone(s.timezone, lang);
  const waiting = useQuery({
    queryKey: ["bookings", "requests", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bookings/requests", { params: { path: { org_id: org.id } } })),
  });
  return (
    <QueryView query={waiting}>
      {(rows) => rows.length === 0 ? <Empty>{t("bookings.noRequests")}</Empty> : (
        <Card>
          <p className="muted small" style={{ marginBlockStart: 0 }}>{t("bookings.requestsHint")}</p>
          {Object.entries(rows.reduce<Record<string, Booking[]>>((acc, b) => {
            (acc[z.dayOf(b.starts_at)] ??= []).push(b);
            return acc;
          }, {})).map(([day, items]) => (
            <div key={day} className="stack-sm" style={{ marginBlockEnd: "var(--space-4)" }}>
              <h3 className="small">{z.dayLabel(day)}</h3>
              <BookingList items={items} zoneOf={z} />
            </div>
          ))}
        </Card>
      )}
    </QueryView>
  );
}

// ---------------------------------------------------------------- booking someone in

function BookDialog({ settings: s, onClose }: { settings: Settings; onClose: () => void }) {
  const { t, lang } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const z = useZone(s.timezone, lang);
  const services = useServices();
  const [form, setForm] = useState({ service_id: "", day: z.today(), time: "", custom: "", outside: false, name: "", phone: "", email: "", note: "" });
  const free = useQuery({
    queryKey: ["bookings", "free", org.id, form.service_id, form.day],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/bookings/free-times", {
      params: { path: { org_id: org.id }, query: { service_id: form.service_id, day: form.day } },
    })),
    enabled: Boolean(form.service_id && form.day),
  });
  // A time typed by hand is in the booking time zone: find its offset from a free time that day, or the zone's offset.
  const startsAt = (): string => {
    if (!form.outside) return form.time;
    const probe = new Date(`${form.day}T${form.custom}:00Z`);
    const local = new Date(probe.toLocaleString("en-US", { timeZone: s.timezone }));
    const utc = new Date(probe.toLocaleString("en-US", { timeZone: "UTC" }));
    const minutes = Math.round((local.getTime() - utc.getTime()) / 60000);
    const sign = minutes >= 0 ? "+" : "-";
    const abs = Math.abs(minutes);
    return `${form.day}T${form.custom}:00${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
  };
  const save = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/bookings", {
      params: { path: { org_id: org.id } },
      body: { service_id: form.service_id, starts_at: startsAt(), name: form.name, phone: form.phone, email: form.email || null,
              note: form.note, outside_hours: form.outside },
    })),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["bookings"] }); toast(t("bookings.booked")); onClose(); },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  return (
    <Dialog open onClose={onClose} title={t("bookings.new")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("bookings.service")}>
            <Select value={form.service_id} required onChange={(e) => setForm({ ...form, service_id: e.target.value, time: "" })}>
              <option value="" disabled>—</option>
              {(services.data ?? []).filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Select>
          </Field>
          <Field label={t("bookings.day")}>
            <Input type="date" required value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value, time: "" })} ltr />
          </Field>
        </div>
        {!form.outside ? (
          <Field label={t("bookings.time")} hint={form.service_id && free.data && free.data.times.length === 0 ? t("bookings.noTimes") : undefined}>
            <Select value={form.time} required onChange={set("time")} disabled={!free.data}>
              <option value="" disabled>—</option>
              {(free.data?.times ?? []).map((iso) => <option key={iso} value={iso}>{z.time(iso)}</option>)}
            </Select>
          </Field>
        ) : (
          <Field label={t("bookings.time")}>
            <Input type="time" required value={form.custom} onChange={set("custom")} ltr />
          </Field>
        )}
        <Checkbox label={t("bookings.outsideHours")} checked={form.outside} onChange={(e) => setForm({ ...form, outside: e.target.checked })} />
        <div className="grid-2">
          <Field label={t("common.name")} error={err["name"]}>
            <Input value={form.name} required maxLength={200} onChange={set("name")} />
          </Field>
          <Field label={t("bookings.phone")} optional>
            <Input type="tel" value={form.phone} maxLength={40} onChange={set("phone")} ltr />
          </Field>
          <Field label={t("bookings.email")} optional error={err["email"]}>
            <Input type="email" value={form.email} maxLength={320} onChange={set("email")} ltr />
          </Field>
        </div>
        <Field label={t("bookings.note")} optional>
          <Textarea value={form.note} maxLength={1000} onChange={set("note")} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{t("bookings.book")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- services, hours and rules

function Setup({ settings: s }: { settings: Settings }) {
  return (
    <div className="grid-2" style={{ alignItems: "start" }}>
      <Services />
      <Rules settings={s} />
    </div>
  );
}

function Services() {
  const { t, money } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const services = useServices();
  const [form, setForm] = useState({ name: "", duration_minutes: "60", price: "" });
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/bookings/services", {
      params: { path: { org_id: org.id } },
      body: { name: form.name, description: "", duration_minutes: Number(form.duration_minutes), price: form.price || null },
    })),
    onSuccess: () => { setForm({ name: "", duration_minutes: "60", price: "" }); void qc.invalidateQueries({ queryKey: ["bookings"] }); },
  });
  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => unwrap(api.PATCH("/api/v1/orgs/{org_id}/bookings/services/{service_id}", {
      params: { path: { org_id: org.id, service_id: id } }, body: { active },
    })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["bookings"] }),
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <Card title={t("bookings.services")}>
      <div className="stack">
        <QueryView query={services}>
          {(list) => list.length === 0 ? <p className="muted small">{t("bookings.noServices")}</p> : (
            <ul className="plain-list">
              {list.map((x) => (
                <li key={x.id} className={`row-between ${x.active ? "" : "muted"}`}>
                  <span dir="auto">
                    <strong>{x.name}</strong>{" "}
                    <span className="muted small">· {t("bookings.minutes", { n: x.duration_minutes })}{x.price && ` · ${money(x.price, org.default_currency)}`}</span>
                  </span>
                  {can("bookings.manage") && (
                    <Button size="sm" variant="ghost" onClick={() => toggle.mutate({ id: x.id, active: !x.active })}>
                      {x.active ? t("bookings.retire") : t("bookings.offer")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </QueryView>
        {can("bookings.manage") && (
          <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            {add.isError && <ErrorNotice error={add.error} />}
            <Field label={t("bookings.serviceName")}>
              <Input value={form.name} required maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <div className="grid-2">
              <Field label={t("bookings.duration")}>
                <Select value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: e.target.value })}>
                  {[15, 30, 45, 60, 90, 120, 180].map((m) => <option key={m} value={m}>{t("bookings.minutes", { n: m })}</option>)}
                </Select>
              </Field>
              <Field label={t("bookings.price")} optional>
                <Input inputMode="decimal" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} ltr />
              </Field>
            </div>
            <div><Button type="submit" icon={<Plus size={16} />} loading={add.isPending}>{t("bookings.addService")}</Button></div>
          </form>
        )}
      </div>
    </Card>
  );
}

function Rules({ settings: s }: { settings: Settings }) {
  const { t, lang } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const names = weekdayNames(lang);
  const [form, setForm] = useState({
    timezone: s.timezone, slot_minutes: String(s.slot_minutes), notice_hours: String(s.notice_hours), days_ahead: String(s.days_ahead),
  });
  const [hours, setHours] = useState<Hours[]>(s.hours);
  const editable = can("bookings.manage");
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/orgs/{org_id}/bookings/settings", {
      params: { path: { org_id: org.id } },
      body: { timezone: form.timezone, slot_minutes: Number(form.slot_minutes), notice_hours: Number(form.notice_hours),
              days_ahead: Number(form.days_ahead), hours },
    })),
    onSuccess: (saved) => { qc.setQueryData(["bookings", "settings", org.id], saved); void qc.invalidateQueries({ queryKey: ["bookings"] }); toast(t("common.saved")); },
  });
  const update = (i: number, patch: Partial<Hours>) => setHours(hours.map((h, j) => (j === i ? { ...h, ...patch } : h)));
  return (
    <Card title={t("bookings.rules")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.isError && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("bookings.timezone")}>
            <Input value={form.timezone} required maxLength={64} list="booking-zones" disabled={!editable}
              onChange={(e) => setForm({ ...form, timezone: e.target.value })} ltr />
          </Field>
          <datalist id="booking-zones">
            {["Asia/Riyadh", "Asia/Dubai", "Asia/Kuwait", "Asia/Qatar", "Asia/Bahrain", "Asia/Muscat", "Africa/Cairo", "Asia/Amman", "Europe/London"]
              .map((z) => <option key={z} value={z} />)}
          </datalist>
          <Field label={t("bookings.slotEvery")}>
            <Select value={form.slot_minutes} disabled={!editable} onChange={(e) => setForm({ ...form, slot_minutes: e.target.value })}>
              {[15, 30, 60].map((m) => <option key={m} value={m}>{t("bookings.minutes", { n: m })}</option>)}
            </Select>
          </Field>
          <Field label={t("bookings.notice")} hint={t("bookings.noticeHint")}>
            <Input inputMode="numeric" value={form.notice_hours} disabled={!editable} onChange={(e) => setForm({ ...form, notice_hours: e.target.value })} ltr />
          </Field>
          <Field label={t("bookings.daysAhead")}>
            <Input inputMode="numeric" value={form.days_ahead} disabled={!editable} onChange={(e) => setForm({ ...form, days_ahead: e.target.value })} ltr />
          </Field>
        </div>
        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend><strong>{t("bookings.openingHours")}</strong></legend>
          {WEEK_ORDER.map((d) => {
            const ranges = hours.map((h, i) => ({ h, i })).filter(({ h }) => h.weekday === d);
            return (
              <div key={d} className="hours-row">
                <span className="hours-day">{names[d]}</span>
                <span className="grow row" style={{ flexWrap: "wrap", gap: 8 }}>
                  {ranges.length === 0 && <span className="muted small">{t("bookings.closed")}</span>}
                  {ranges.map(({ h, i }) => (
                    <span key={i} className="row" style={{ gap: 4 }}>
                      <Input type="time" aria-label={t("bookings.opens", { day: names[d]! })} value={h.opens_at} disabled={!editable}
                        onChange={(e) => update(i, { opens_at: e.target.value })} ltr style={{ inlineSize: "7.5rem" }} />
                      <span aria-hidden>–</span>
                      <Input type="time" aria-label={t("bookings.closes", { day: names[d]! })} value={h.closes_at} disabled={!editable}
                        onChange={(e) => update(i, { closes_at: e.target.value })} ltr style={{ inlineSize: "7.5rem" }} />
                      {editable && (
                        <IconButton label={t("bookings.removeRange", { day: names[d]! })} onClick={() => setHours(hours.filter((_, j) => j !== i))}>
                          <Trash2 size={14} />
                        </IconButton>
                      )}
                    </span>
                  ))}
                </span>
                {editable && (
                  <Button size="sm" variant="ghost" icon={<Plus size={14} />} aria-label={t("bookings.addRange", { day: names[d]! })}
                    onClick={() => setHours([...hours, { weekday: d, opens_at: "09:00", closes_at: "17:00" }])}>
                    {t("bookings.addHours")}
                  </Button>
                )}
              </div>
            );
          })}
        </fieldset>
        <p className="muted small">{t("bookings.holidaysHint")}</p>
        {editable && <div><Button type="submit" variant="primary" loading={save.isPending}>{t("common.save")}</Button></div>}
      </form>
    </Card>
  );
}
