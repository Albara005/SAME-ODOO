import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, ArrowRight, Boxes, Briefcase, CalendarClock, Check, CheckCircle2, Copy, ExternalLink, FolderKanban, HardHat,
  LayoutGrid, LayoutTemplate, ListChecks, Package, Percent, Store, Truck, UsersRound, UtensilsCrossed, type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { useOrg, type Org } from "../auth/Org";
import { StarLattice } from "../components/Art";
import { Journey, journeySteps } from "../components/business";
import { BrandMark, TopActions } from "../components/Layout";
import { AppsPicker } from "../components/AppsPicker";
import { Button, Checkbox, ErrorNotice, Field, Input, Loading, Select, useToast } from "../components/ui";
import { useI18n } from "../i18n/I18n";
import type { MessageKey } from "../i18n/en";
import { locale } from "../lib/format";

type Kind = "store" | "restaurant" | "services" | "appointments" | "contracting" | "trading" | "other";
const KINDS: { key: Kind; icon: LucideIcon; hue: string }[] = [
  { key: "store", icon: Store, hue: "amber" },
  { key: "restaurant", icon: UtensilsCrossed, hue: "terracotta" },
  { key: "services", icon: Briefcase, hue: "lapis" },
  { key: "appointments", icon: CalendarClock, hue: "teal" },
  { key: "contracting", icon: HardHat, hue: "gold" },
  { key: "trading", icon: Truck, hue: "cyan" },
  { key: "other", icon: LayoutGrid, hue: "violet" },
];
// Opening a store takes these (services/api/novara/modules/organizations/setup.py STORE_NEEDS).
const STORE_NEEDS = ["apps.write", "apps.deploy", "products.read", "sales.write"];
const STEPS = ["setup.step.kind", "setup.step.apps", "setup.step.details", "setup.step.done"] as const;

/** Odoo-style first run: what the business does, the apps it runs, its country, then everything set up at once. */
export function SetupPage() {
  const { org, can, path } = useOrg();
  const [doneBefore] = useState(() => Boolean(org.setup_completed_at));
  const catalog = useQuery({
    queryKey: ["setup", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/setup", { params: { path: { org_id: org.id } } })),
    enabled: can("org.manage") && !doneBefore,
  });
  if (!can("org.manage") || doneBefore) return <Navigate to={path("")} replace />;
  return (
    <div className="setup">
      <SetupTop org={org} />
      {catalog.isPending ? <Loading /> : catalog.isError ? (
        <main className="setup-main"><ErrorNotice error={catalog.error} onRetry={() => void catalog.refetch()} /></main>
      ) : (
        <Wizard catalog={catalog.data} />
      )}
    </div>
  );
}

function SetupTop({ org }: { org: Org }) {
  return (
    <header className="setup-top">
      <BrandMark />
      <span className="setup-org muted small" dir="auto">{org.name}</span>
      <div className="row"><TopActions /></div>
    </header>
  );
}

function Wizard({ catalog }: { catalog: Schemas["SetupCatalogOut"] }) {
  const { t, lang } = useI18n();
  const { org, can, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [kind, setKind] = useState<Kind | null>(null);
  const [apps, setApps] = useState<string[]>([]);
  const [country, setCountry] = useState(
    () => catalog.countries.find((c) => c.currency === org.default_currency)?.code ?? catalog.countries[0]?.code ?? "SA",
  );
  const [vat, setVat] = useState(org.vat_number);
  const [samples, setSamples] = useState(true);
  const [store, setStore] = useState(false);
  const [storeName, setStoreName] = useState(org.name);
  const regions = useMemo(() => {
    try {
      return new Intl.DisplayNames([locale(lang)], { type: "region" });
    } catch {
      return null;
    }
  }, [lang]);
  const countryName = (code: string) => regions?.of(code) ?? code;
  const suggested = (k: Kind) => catalog.business_types.find((b) => b.key === k)?.apps ?? [];
  const mayOpenStore = STORE_NEEDS.every(can);
  const picked = catalog.countries.find((c) => c.code === country);

  useEffect(() => {
    document.title = `${t("setup.title")} · ${org.name} · ${t("app.name")}`;
  }, [t, org.name]);
  // Braces: an effect returns its cleanup, and newer browsers' scrollTo returns a promise React would call.
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [step]);

  const keep = (o: Schemas["OrgOut"]) => {
    qc.setQueryData<Org>(["org", org.id], (old) => (old ? { ...old, ...o } : old));
    void qc.invalidateQueries({ queryKey: ["orgs"] });
  };
  const finish = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/setup", {
        params: { path: { org_id: org.id } },
        body: { business_type: kind ?? "other", apps, country, vat_number: vat, sample_data: samples,
                store: store && mayOpenStore, store_name: storeName.trim() },
      })),
    onSuccess: (res) => {
      keep(res.organization);
      setStep(3);
    },
  });
  const skip = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/setup/skip", { params: { path: { org_id: org.id } } })),
    onSuccess: (o) => {
      keep(o);
      navigate(path(""), { replace: true });
    },
  });

  function choose(k: Kind) {
    setKind(k);
    setApps(suggested(k));
    setStore(k === "store" && mayOpenStore);
  }
  function next(e: FormEvent) {
    e.preventDefault();
    if (step < 2) setStep(step + 1);
    else finish.mutate();
  }
  const canNext = step === 0 ? kind !== null : step === 1 ? apps.length > 0 : true;
  const Back = lang === "ar" ? ArrowRight : ArrowLeft;
  const Next = lang === "ar" ? ArrowLeft : ArrowRight;
  const heads: Record<number, [MessageKey, MessageKey]> = {
    0: ["setup.kind.title", "setup.kind.lead"],
    1: ["setup.apps.title", "setup.apps.lead"],
    2: ["setup.details.title", "setup.details.lead"],
  };

  if (step === 3 && finish.data) return <Done result={finish.data} rate={picked?.vat_rate ?? ""} />;
  return (
    <main className="setup-main" id="main">
      <section className="setup-hero">
        <StarLattice />
        <span className="setup-eyebrow small">{t("setup.eyebrow")}</span>
        <h1>{t("setup.headline", { org: org.name })}</h1>
        <p>{t("setup.lead")}</p>
        <Journey label={t("setup.steps")} steps={journeySteps(STEPS.map((s) => t(s)), step)} />
      </section>

      <form className="setup-body" onSubmit={next} aria-labelledby="setup-step-title">
        <div className="setup-head">
          <h2 id="setup-step-title">{t(heads[step]![0])}</h2>
          <p className="muted">{t(heads[step]![1])}</p>
        </div>

        {step === 0 && (
          <fieldset className="choice-grid">
            <legend className="sr-only">{t("setup.kind.title")}</legend>
            {KINDS.map((k) => (
              <label key={k.key} className={`choice hue-${k.hue} ${kind === k.key ? "selected" : ""}`}>
                <input type="radio" name="kind" className="sr-only" checked={kind === k.key} onChange={() => choose(k.key)} />
                <span className="app-icon"><k.icon size={22} aria-hidden /></span>
                <span className="choice-text">
                  <strong>{t(`setup.kind.${k.key}` as MessageKey)}</strong>
                  <span className="muted small">{t(`setup.kind.${k.key}Desc` as MessageKey)}</span>
                </span>
                <span className="choice-check" aria-hidden><Check size={14} /></span>
              </label>
            ))}
          </fieldset>
        )}

        {step === 1 && (
          <AppsPicker value={apps} onChange={setApps} suggested={kind ? suggested(kind) : []} />
        )}

        {step === 2 && (
          <div className="setup-details">
            <section className="card stack">
              <h3 className="setup-card-title"><Percent size={18} aria-hidden /> {t("setup.taxes")}</h3>
              <div className="grid-2">
                <Field label={t("setup.country")}>
                  <Select value={country} onChange={(e) => setCountry(e.target.value)}>
                    {catalog.countries.map((c) => <option key={c.code} value={c.code}>{countryName(c.code)}</option>)}
                  </Select>
                </Field>
                <Field label={t("setup.currency")} hint={t("org.currencyLocked")}>
                  <Input value={picked?.currency ?? ""} readOnly ltr />
                </Field>
              </div>
              <p className={`setup-vat small ${picked && Number(picked.vat_rate) > 0 ? "" : "muted"}`}>
                {picked && Number(picked.vat_rate) > 0
                  ? t("setup.vatRate", { rate: picked.vat_rate })
                  : t("setup.noVat", { country: countryName(country) })}
              </p>
              {picked && Number(picked.vat_rate) > 0 && (
                <Field label={t("org.vatNumber")} hint={t("org.vatHint")} optional>
                  <Input value={vat} onChange={(e) => setVat(e.target.value.toUpperCase().replace(/\s/g, ""))}
                    maxLength={20} inputMode="numeric" pattern="[0-9A-Z]*" ltr />
                </Field>
              )}
            </section>
            <section className="card stack">
              <h3 className="setup-card-title"><Package size={18} aria-hidden /> {t("setup.start")}</h3>
              <div className="stack-sm">
                <Checkbox label={<strong>{t("setup.samples")}</strong>} checked={samples} onChange={(e) => setSamples(e.target.checked)} />
                <p className="muted small setup-indent">{t("setup.samplesHint")}</p>
              </div>
              <div className={`setup-store ${store ? "on" : ""}`}>
                <div className="stack-sm">
                  <Checkbox label={<strong>{t("setup.store")}</strong>} checked={store} disabled={!mayOpenStore}
                    onChange={(e) => setStore(e.target.checked)} />
                  <p className="muted small setup-indent">{mayOpenStore ? t("setup.storeHint") : t("setup.storeNoPermission")}</p>
                </div>
                {store && (
                  <>
                    <Field label={t("setup.storeName")}>
                      <Input value={storeName} onChange={(e) => setStoreName(e.target.value)} maxLength={120} required />
                    </Field>
                    {!samples && <p className="small setup-indent">{t("setup.storeNeedsProducts")}</p>}
                  </>
                )}
              </div>
            </section>
          </div>
        )}

        {finish.isError && <ErrorNotice error={finish.error} />}
        {skip.isError && <ErrorNotice error={skip.error} />}
        <div className="setup-actions">
          {step > 0 && (
            <Button icon={<Back size={16} aria-hidden />} onClick={() => setStep(step - 1)} disabled={finish.isPending}>
              {t("setup.back")}
            </Button>
          )}
          <span className="grow" />
          <Button variant="ghost" loading={skip.isPending} disabled={finish.isPending} onClick={() => skip.mutate()}
            title={t("setup.skipHint")}>
            {t("setup.skip")}
          </Button>
          <Button type="submit" variant="primary" disabled={!canNext} loading={finish.isPending}>
            {step < 2 ? t("setup.next") : finish.isPending ? t("setup.working") : t("setup.finish")}
            {step < 2 && <Next size={16} aria-hidden />}
          </Button>
        </div>
        {step === 1 && apps.length === 0 && <p className="muted small setup-why" role="status">{t("setup.apps.none")}</p>}
      </form>
    </main>
  );
}

const CREATED: { key: string; label: MessageKey; icon: LucideIcon }[] = [
  { key: "products", label: "setup.created.products", icon: Package },
  { key: "customers", label: "setup.created.customers", icon: UsersRound },
  { key: "stock", label: "setup.created.stock", icon: Boxes },
  { key: "services", label: "setup.created.services", icon: CalendarClock },
  { key: "projects", label: "setup.created.projects", icon: FolderKanban },
];

function Done({ result, rate }: { result: Schemas["SetupOut"]; rate: string }) {
  const { t } = useI18n();
  const { org, can, path } = useOrg();
  const toast = useToast();
  const o = result.organization;
  const created = CREATED.filter((c) => (result.created[c.key] ?? 0) > 0);
  const next: { to: string; label: MessageKey; show: boolean }[] = [
    { to: "products", label: "setup.nextProducts", show: (o.apps ?? []).includes("products") && can("products.write") },
    { to: "settings/members", label: "setup.nextTeam", show: can("members.manage") || can("members.read") },
    { to: "settings/organization", label: "setup.nextEinvoicing", show: can("org.manage") },
  ];
  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      toast(t("common.copied"));
    } catch {
      toast(t("common.error"), "error");
    }
  }
  return (
    <main className="setup-main" id="main">
      <section className="setup-hero setup-hero-done">
        <StarLattice />
        <span className="setup-done-mark" aria-hidden><CheckCircle2 size={34} /></span>
        <h1>{t("setup.done.title", { org: org.name })}</h1>
        <p>{t("setup.done.lead")}</p>
        <div className="hero-actions">
          <Link className="btn btn-gold" to={path("")}>{t("setup.goHome")}</Link>
        </div>
      </section>

      <div className="setup-details">
        {result.store_url && (
          <section className="card stack setup-store-ready">
            <h2 className="setup-card-title"><Store size={18} aria-hidden /> {t("setup.storeReady")}</h2>
            <p className="muted small">{t("setup.storeReadyHint")}</p>
            <a className="setup-link mono small" href={result.store_url} target="_blank" rel="noopener noreferrer" dir="ltr">
              {result.store_url}
            </a>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <a className="btn btn-primary" href={result.store_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={16} aria-hidden /> {t("setup.openStore")}
              </a>
              <Button icon={<Copy size={16} aria-hidden />} onClick={() => void copy(result.store_url!)}>{t("common.copy")}</Button>
              {result.store_project_id && (
                <Link className="btn btn-ghost" to={path(`studio/${result.store_project_id}`)}>
                  <LayoutTemplate size={16} aria-hidden /> {t("setup.editStore")}
                </Link>
              )}
            </div>
          </section>
        )}
        <section className="card stack">
          <h2 className="setup-card-title"><CheckCircle2 size={18} aria-hidden /> {t("setup.done.what")}</h2>
          <ul className="plain-list setup-created">
            <li><LayoutGrid size={16} aria-hidden /><span className="grow">{t("setup.created.apps")}</span><span className="num">{(o.apps ?? []).length}</span></li>
            {result.created["taxes"] ? (
              <li><Percent size={16} aria-hidden /><span className="grow">{t("setup.created.taxes", { rate })}</span><Check size={16} aria-hidden /></li>
            ) : null}
            {created.map((c) => (
              <li key={c.key}><c.icon size={16} aria-hidden /><span className="grow">{t(c.label)}</span><span className="num">{result.created[c.key]}</span></li>
            ))}
          </ul>
          {(result.notes ?? []).map((n) => (
            <p key={n} className="small setup-note">{t(`setup.note.${n}` as MessageKey, { currency: o.default_currency })}</p>
          ))}
        </section>
        <section className="card stack">
          <h2 className="setup-card-title"><ListChecks size={18} aria-hidden /> {t("setup.nextTitle")}</h2>
          <ul className="plain-list setup-next">
            {next.filter((n) => n.show).map((n) => (
              <li key={n.to}><Link to={path(n.to)}>{t(n.label)}</Link></li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}
