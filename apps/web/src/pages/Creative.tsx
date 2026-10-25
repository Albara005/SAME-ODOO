import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, FileCode2, Paintbrush, Play, Plus, Sparkles, Star, Wand2 } from "lucide-react";
import { useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { download, useBlobUrl } from "../components/business";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, Input, PageHeader, QueryView, Select, Textarea, errorText, useToast,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { useDebounced } from "../lib/useDebounced";

type Kit = Schemas["KitOut"];
type Logo = Schemas["LogoOut"];
type Tokens = Record<string, string>;
type Adjustment = { kind: "button" | "text"; mode: "light" | "dark"; original: string; used: string };
type System = {
  all_checks_pass: boolean; notes: string[]; adjustments: Adjustment[];
  modes: { light: Tokens; dark: Tokens }; scales: Record<string, Record<string, string>>;
};

/** Colour codes stay left-to-right inside Arabic sentences. */
const isolate = (hex: string) => `\u2066${hex}\u2069`;

const HEX = /^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$/;
const FONTS = ["kufi", "plex", "naskh", "system"] as const;
const LAYOUTS = ["horizontal", "stacked", "wordmark", "icon"] as const;
const SYMBOLS = ["circle", "rounded_square", "hexagon", "diamond", "arch", "none"] as const;
const PERSONALITIES = ["calm", "lively", "crisp", "still"] as const;
const SPEEDS = ["0.75", "1", "1.25", "1.5"] as const;

function usePalette(primary: string, secondary: string) {
  const { org } = useOrg();
  const p = useDebounced(primary, 250);
  const s = useDebounced(secondary, 250);
  return useQuery({
    queryKey: ["palette", org.id, p, s],
    queryFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/creative/palette", {
        params: { path: { org_id: org.id } }, body: { primary: p, secondary: s || null },
      })) as Promise<System>,
    enabled: HEX.test(p) && (s === "" || HEX.test(s)),
    placeholderData: (prev) => prev,
  });
}

// ---------------------------------------------------------------- list

export function CreativePage() {
  const { t } = useI18n();
  const { org, can, path } = useOrg();
  const [creating, setCreating] = useState(false);
  const kits = useQuery({
    queryKey: ["kits", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/creative/brand-kits", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.creative")}
        subtitle={t("creative.subtitle")}
        actions={can("brand.manage") && (
          <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>{t("creative.newKit")}</Button>
        )}
      />
      <QueryView query={kits}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t("creative.noKits")}</Empty>
          ) : (
            <div className="apps" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
              {list.map((k) => (
                <Link key={k.id} to={path(`creative/${k.id}`)} className="app-tile kit-tile" style={{ "--hue": k.primary_color } as CSSProperties}>
                  <KitLogo kit={k} height={72} />
                  <span className="stack-sm" style={{ gap: 2 }}>
                    <strong>{k.name}</strong>
                    {k.tagline && <span className="muted small">{k.tagline}</span>}
                  </span>
                  <span className="swatches" aria-hidden>
                    {["color-primary", "color-secondary", "color-primary-subtle", "color-bg", "color-text"].map((key) => {
                      const hex = (k.tokens.light as Tokens | undefined)?.[key];
                      return hex ? <span key={key} style={{ background: hex }} /> : null;
                    })}
                  </span>
                </Link>
              ))}
            </div>
          )
        }
      </QueryView>
      {creating && <NewKitDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function KitLogo({ kit, height, variant = "full" }: { kit: Kit; height: number; variant?: string }) {
  const { org } = useOrg();
  const { t } = useI18n();
  const main = kit.logos.find((l) => l.primary) ?? kit.logos[0];
  const src = useBlobUrl(main ? logoPath(org.id, kit, main, "svg", variant) : null);
  if (!main) return <div className="logo-frame empty" style={{ height }}>{t("creative.noLogo")}</div>;
  return <div className="logo-frame" style={{ height }}>{src && <img src={src} alt={kit.name} />}</div>;
}

const logoPath = (orgId: string, kit: Kit, logo: Logo, fmt: "svg" | "png", variant = "full") =>
  `/api/v1/orgs/${orgId}/creative/brand-kits/${kit.id}/logos/${logo.id}.${fmt}?variant=${variant}&v=${encodeURIComponent(`${kit.primary_color}${kit.secondary_color ?? ""}${kit.name}`)}`;

// ---------------------------------------------------------------- colour picking

function ColorField({ label, value, onChange, optional }: { label: string; value: string; onChange: (v: string) => void; optional?: boolean }) {
  const { t } = useI18n();
  const valid = value === "" ? optional : HEX.test(value);
  return (
    <Field label={label} optional={optional} error={valid ? undefined : t("creative.badHex")}>
      <div className="color-field">
        <input type="color" aria-label={`${label} (${t("creative.picker")})`} className={value ? "" : "unset"}
          value={HEX.test(value) && value.length === 7 ? value : "#ffffff"}
          onChange={(e) => onChange(e.target.value)} />
        <Input value={value} onChange={(e) => onChange(e.target.value.trim())} placeholder="#2747b5" maxLength={7} ltr />
      </div>
    </Field>
  );
}

/** A small screen painted with the generated tokens, in one theme. */
function MiniApp({ tokens, name, mode }: { tokens: Tokens; name: string; mode: "light" | "dark" }) {
  const { t } = useI18n();
  const v = (k: string) => tokens[`color-${k}`];
  return (
    <div className="mini-app" style={{ background: v("bg"), color: v("text"), borderColor: v("border") }} aria-label={t(`creative.${mode}` as MessageKey)}>
      <div className="mini-bar" style={{ background: v("surface"), borderColor: v("border") }}>
        <span className="mini-dot" style={{ background: v("primary") }} />
        <strong>{name || "NOVARA"}</strong>
        <span className="mini-mode" style={{ color: v("text-muted") }}>{t(`creative.${mode}` as MessageKey)}</span>
      </div>
      <div className="mini-body">
        <div className="mini-card" style={{ background: v("surface"), borderColor: v("border") }}>
          <span style={{ color: v("text-muted") }}>{t("creative.sampleLabel")}</span>
          <strong style={{ fontSize: "1.2rem" }}>12,480.00</strong>
          <span className="mini-badges">
            <span style={{ color: v("success-text"), background: `color-mix(in srgb, ${v("success")} 15%, transparent)` }}>{t("status.paid")}</span>
            <span style={{ color: v("warning-text"), background: `color-mix(in srgb, ${v("warning")} 15%, transparent)` }}>{t("status.partial")}</span>
            <span style={{ color: v("danger-text"), background: `color-mix(in srgb, ${v("danger")} 15%, transparent)` }}>{t("tasks.overdue")}</span>
          </span>
        </div>
        <div className="mini-actions">
          <span className="mini-btn" style={{ background: v("primary"), color: v("on-primary") }}>{t("creative.sampleAction")}</span>
          {v("secondary") && <span className="mini-btn" style={{ background: v("secondary"), color: v("on-secondary") }}>{t("creative.sampleSecondary")}</span>}
          <span style={{ color: v("link") }}>{t("creative.sampleLink")}</span>
        </div>
      </div>
    </div>
  );
}

function PalettePreview({ system, name }: { system: System | undefined; name: string }) {
  const { t } = useI18n();
  if (!system) return null;
  return (
    <div className="stack-sm">
      <div className="row">
        {system.all_checks_pass ? (
          <Badge tone="success">{t("creative.checksPass")}</Badge>
        ) : (
          <Badge tone="danger">{t("creative.checksFail")}</Badge>
        )}
        <span className="muted small">{t("creative.wcag")}</span>
      </div>
      <div className="scale" aria-label={t("creative.scale")}>
        {Object.entries(system.scales.primary ?? {}).map(([step, hex]) => (
          <span key={step} style={{ background: hex }} title={`${step} ${hex}`} />
        ))}
      </div>
      {system.scales.secondary && (
        <div className="scale" aria-label={t("creative.scaleSecondary")}>
          {Object.entries(system.scales.secondary).map(([step, hex]) => (
            <span key={step} style={{ background: hex }} title={`${step} ${hex}`} />
          ))}
        </div>
      )}
      <div className="grid-2">
        <MiniApp tokens={system.modes.light} name={name} mode="light" />
        <MiniApp tokens={system.modes.dark} name={name} mode="dark" />
      </div>
      {(system.adjustments ?? []).length > 0 && (
        <ul className="muted small" style={{ margin: 0, paddingInlineStart: "1.2em" }}>
          {system.adjustments.map((a) => (
            <li key={`${a.kind}-${a.mode}`}>
              {t(a.kind === "button" ? "creative.adj.button" : "creative.adj.text", {
                mode: t(`creative.mode.${a.mode}`),
                original: isolate(a.original),
                used: isolate(a.used),
                tone: t(a.mode === "light" ? "creative.tone.light" : "creative.tone.dark"),
              })}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewKitDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", tagline: "", primary: "#0f766e", secondary: "", font_pair: "kufi" });
  const palette = usePalette(form.primary, form.secondary);
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/creative/brand-kits", {
        params: { path: { org_id: org.id } }, body: { ...form, secondary: form.secondary || null },
      })),
    onSuccess: (kit) => {
      void qc.invalidateQueries({ queryKey: ["kits", org.id] });
      navigate(path(`creative/${kit.id}`));
    },
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("creative.newKit")} wide>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && !Object.keys(err).length && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("common.name")} error={err["name"]}>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} />
          </Field>
          <Field label={t("creative.tagline")} optional>
            <Input value={form.tagline} onChange={(e) => setForm({ ...form, tagline: e.target.value })} maxLength={120} />
          </Field>
          <ColorField label={t("creative.primary")} value={form.primary} onChange={(primary) => setForm({ ...form, primary })} />
          <ColorField label={t("creative.secondary")} value={form.secondary} onChange={(secondary) => setForm({ ...form, secondary })} optional />
          <Field label={t("creative.font")}>
            <Select value={form.font_pair} onChange={(e) => setForm({ ...form, font_pair: e.target.value })}>
              {FONTS.map((f) => <option key={f} value={f}>{t(`creative.font.${f}` as MessageKey)}</option>)}
            </Select>
          </Field>
        </div>
        <PalettePreview system={palette.data} name={form.name} />
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- kit

export function BrandKitPage() {
  const { kitId = "" } = useParams();
  const { org } = useOrg();
  const kit = useQuery({
    queryKey: ["kit", org.id, kitId],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}", { params: { path: { org_id: org.id, kit_id: kitId } } })),
  });
  return <QueryView query={kit}>{(k) => <BrandKit kit={k} />}</QueryView>;
}

function BrandKit({ kit }: { kit: Kit }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const manage = can("brand.manage");
  const [colors, setColors] = useState({ primary: kit.primary_color, secondary: kit.secondary_color ?? "" });
  const [applying, setApplying] = useState(false);
  const palette = usePalette(colors.primary, colors.secondary);
  const changed = colors.primary !== kit.primary_color || colors.secondary !== (kit.secondary_color ?? "");
  const setKit = (k: Kit) => {
    qc.setQueryData(["kit", org.id, kit.id], k);
    void qc.invalidateQueries({ queryKey: ["kits", org.id] });
  };
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}", {
        params: { path: { org_id: org.id, kit_id: kit.id } }, body: { primary: colors.primary, secondary: colors.secondary || null },
      })),
    onSuccess: (k) => {
      setKit(k);
      toast(t("creative.recoloured"));
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const base = `/api/v1/orgs/${org.id}/creative/brand-kits/${kit.id}`;
  const fail = (e: unknown) => toast(errorText(e, t("common.error")), "error");

  return (
    <div className="stack">
      <PageHeader
        title={kit.name}
        subtitle={kit.tagline || undefined}
        actions={
          <>
            <Button icon={<FileCode2 size={16} />} onClick={() => download(`${base}/tokens.css`, "tokens.css").catch(fail)}>tokens.css</Button>
            <Button icon={<Download size={16} />} onClick={() => download(`${base}/export.zip`, `${kit.name}-brand-kit.zip`).catch(fail)}>
              {t("creative.exportKit")}
            </Button>
            {can("apps.write") && (
              <Button variant="primary" icon={<Paintbrush size={16} />} onClick={() => setApplying(true)}>{t("creative.applyToApp")}</Button>
            )}
          </>
        }
      />
      <Card title={t("creative.colours")}>
        <div className="stack">
          {manage && (
            <div className="row" style={{ alignItems: "flex-end" }}>
              <div style={{ minWidth: 220 }}>
                <ColorField label={t("creative.primary")} value={colors.primary} onChange={(primary) => setColors({ ...colors, primary })} />
              </div>
              <div style={{ minWidth: 220 }}>
                <ColorField label={t("creative.secondary")} value={colors.secondary} onChange={(secondary) => setColors({ ...colors, secondary })} optional />
              </div>
              {changed && (
                <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>{t("creative.recolour")}</Button>
              )}
            </div>
          )}
          <PalettePreview system={palette.data} name={kit.name} />
        </div>
      </Card>
      <Logos kit={kit} onKit={setKit} />
      <Motion kit={kit} onKit={setKit} />
      {manage && can("ai.use") && <DesignAgent kit={kit} />}
      {applying && <ApplyDialog kit={kit} onClose={() => setApplying(false)} />}
    </div>
  );
}

function Logos({ kit, onKit }: { kit: Kit; onKit: (k: Kit) => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const primary = useMutation({
    mutationFn: (logoId: string) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}/logos/{logo_id}/primary", {
        params: { path: { org_id: org.id, kit_id: kit.id, logo_id: logoId } },
      })),
    onSuccess: onKit,
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  return (
    <Card
      title={t("creative.logos")}
      actions={can("brand.manage") && <Button icon={<Plus size={16} />} onClick={() => setAdding(true)}>{t("creative.newLogo")}</Button>}
    >
      {kit.logos.length === 0 ? (
        <Empty>{t("creative.noLogos")}</Empty>
      ) : (
        <div className="logo-grid">
          {kit.logos.map((l) => (
            <LogoCard key={l.id} kit={kit} logo={l} onPrimary={() => primary.mutate(l.id)} canManage={can("brand.manage")} />
          ))}
        </div>
      )}
      {adding && (
        <LogoDialog kit={kit} onClose={() => setAdding(false)} onDone={() => {
          setAdding(false);
          void qc.invalidateQueries({ queryKey: ["kit", org.id, kit.id] });
          void qc.invalidateQueries({ queryKey: ["kits", org.id] });
        }} />
      )}
    </Card>
  );
}

function LogoCard({ kit, logo, onPrimary, canManage }: { kit: Kit; logo: Logo; onPrimary: () => void; canManage: boolean }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const toast = useToast();
  const full = useBlobUrl(logoPath(org.id, kit, logo, "svg"));
  const icon = useBlobUrl(logoPath(org.id, kit, logo, "svg", "icon"));
  const inverse = useBlobUrl(logoPath(org.id, kit, logo, "svg", "inverse"));
  const mono = useBlobUrl(logoPath(org.id, kit, logo, "svg", "mono"));
  const name = String(logo.spec.name ?? kit.name);
  const fail = (e: unknown) => toast(errorText(e, t("common.error")), "error");
  return (
    <article className={`logo-card ${logo.primary ? "primary" : ""}`}>
      <div className="logo-frame" style={{ height: 120 }}>{full && <img src={full} alt={name} />}</div>
      <div className="logo-variants">
        <div className="logo-frame small">{icon && <img src={icon} alt={`${name} · ${t("creative.icon")}`} />}</div>
        <div className="logo-frame small dark">{inverse && <img src={inverse} alt={`${name} · ${t("creative.inverse")}`} />}</div>
        <div className="logo-frame small">{mono && <img src={mono} alt={`${name} · ${t("creative.mono")}`} />}</div>
      </div>
      {logo.note && <p className="muted small" dir="auto">{logo.note}</p>}
      <div className="row-between">
        <span className="row" style={{ gap: "var(--space-2)" }}>
          {logo.primary ? <Badge tone="success">{t("creative.primaryLogo")}</Badge> : canManage && (
            <Button size="sm" icon={<Star size={14} />} onClick={onPrimary}>{t("creative.makePrimary")}</Button>
          )}
          {logo.source === "assistant" && <Badge tone="primary">{t("creative.byAgent")}</Badge>}
        </span>
        <span className="row" style={{ gap: "var(--space-1)" }}>
          <Button size="sm" variant="ghost" onClick={() => download(logoPath(org.id, kit, logo, "svg"), `${name}.svg`).catch(fail)}>SVG</Button>
          <Button size="sm" variant="ghost" onClick={() => download(logoPath(org.id, kit, logo, "png"), `${name}.png`).catch(fail)}>PNG</Button>
        </span>
      </div>
    </article>
  );
}

function LogoDialog({ kit, onClose, onDone }: { kit: Kit; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [spec, setSpec] = useState({ name: kit.name.slice(0, 40), tagline: kit.tagline.slice(0, 60), layout: "horizontal", symbol: "circle", monogram: "", weight: 700 });
  const add = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}/logos", {
        params: { path: { org_id: org.id, kit_id: kit.id } },
        body: spec as Schemas["LogoSpec"],
      })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("creative.newLogo")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        {add.isError && <ErrorNotice error={add.error} />}
        <div className="grid-2">
          <Field label={t("common.name")}>
            <Input value={spec.name} onChange={(e) => setSpec({ ...spec, name: e.target.value })} required maxLength={40} />
          </Field>
          <Field label={t("creative.tagline")} optional>
            <Input value={spec.tagline} onChange={(e) => setSpec({ ...spec, tagline: e.target.value })} maxLength={60} />
          </Field>
          <Field label={t("creative.layout")}>
            <Select value={spec.layout} onChange={(e) => setSpec({ ...spec, layout: e.target.value })}>
              {LAYOUTS.map((l) => <option key={l} value={l}>{t(`creative.layout.${l}` as MessageKey)}</option>)}
            </Select>
          </Field>
          <Field label={t("creative.symbol")}>
            <Select value={spec.symbol} onChange={(e) => setSpec({ ...spec, symbol: e.target.value })}>
              {SYMBOLS.map((s) => <option key={s} value={s}>{t(`creative.symbol.${s}` as MessageKey)}</option>)}
            </Select>
          </Field>
          <Field label={t("creative.monogram")} hint={t("creative.monogramHint")} optional>
            <Input value={spec.monogram} onChange={(e) => setSpec({ ...spec, monogram: e.target.value })} maxLength={2} />
          </Field>
          <Field label={t("creative.weight")}>
            <Select value={String(spec.weight)} onChange={(e) => setSpec({ ...spec, weight: Number(e.target.value) })}>
              <option value="700">{t("creative.bold")}</option>
              <option value="400">{t("creative.regular")}</option>
            </Select>
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={add.isPending}>{t("creative.draw")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

type Personality = Kit["motion"]["personality"];

function Motion({ kit, onKit }: { kit: Kit; onKit: (k: Kit) => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const toast = useToast();
  const manage = can("brand.manage");
  const [replay, setReplay] = useState(0);
  // The choice shows at once; the server's answer replaces it (or puts the old one back if saving fails).
  const [choice, setChoice] = useState<Personality | null>(null);
  const set = useMutation({
    mutationFn: (body: { motion_personality?: Personality; motion_speed?: number }) =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}", { params: { path: { org_id: org.id, kit_id: kit.id } }, body })),
    onSuccess: (k) => {
      onKit(k);
      setReplay((n) => n + 1);
      toast(t("creative.motionSaved"));
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
    onSettled: () => setChoice(null),
  });
  const m = kit.motion;
  const shown = choice ?? m.personality;
  return (
    <Card title={t("creative.motion")} actions={
      <Button size="sm" icon={<Play size={14} />} onClick={() => setReplay((n) => n + 1)}>{t("creative.motionReplay")}</Button>
    }>
      <div className="motion-layout">
        <div className="stack-sm">
          <p className="muted small">{t("creative.motionHint")}</p>
          <fieldset className="motion-choices" disabled={!manage || set.isPending}>
            <legend className="sr-only">{t("creative.motionPersonality")}</legend>
            {PERSONALITIES.map((p) => (
              <label key={p} className={`motion-choice${shown === p ? " is-selected" : ""}`}>
                <input type="radio" name="motion-personality" value={p} checked={shown === p}
                  onChange={() => { setChoice(p); set.mutate({ motion_personality: p }); }} />
                <strong>{t(`creative.motion.${p}`)}</strong>
                <span className="small muted">{t(`creative.motion.${p}Hint`)}</span>
              </label>
            ))}
          </fieldset>
          <Field label={t("creative.motionSpeed")}>
            <Select value={String(m.speed)} disabled={!manage || set.isPending || m.personality === "still"}
              onChange={(e) => set.mutate({ motion_speed: Number(e.target.value) })}>
              {(SPEEDS as readonly string[]).includes(String(m.speed)) ? null : <option value={String(m.speed)}>{`×${m.speed}`}</option>}
              {SPEEDS.map((v) => <option key={v} value={v}>{t(`creative.motionSpeed.${v}`)}</option>)}
            </Select>
          </Field>
          <p className="small">{t("creative.motionDurations", {
            fast: m.tokens["motion-fast"] ?? "", base: m.tokens["motion-base"] ?? "", slow: m.tokens["motion-slow"] ?? "" })}</p>
          {m.notes.map((n) => <p key={n} className="small muted" dir="ltr">{n}</p>)}
        </div>
        <MotionPreview key={`${m.personality}-${m.speed}-${replay}`} css={m.css} tokens={kit.tokens.light as Tokens} name={kit.name} />
      </div>
    </Card>
  );
}

/** The motion on sample content, in an iframe of its own: the generated CSS as an app would use it. */
function MotionPreview({ css, tokens, name }: { css: string; tokens: Tokens; name: string }) {
  const { t, lang } = useI18n();
  const esc = (text: string) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
  const cards = [1, 2, 3].map((n) =>
    `<div class="card motion-rise motion-lift" style="--motion-order:${n + 1}">${esc(t("creative.motionSampleCard", { n }))}</div>`).join("");
  const doc = `<!doctype html><html lang="${lang}" dir="${lang === "ar" ? "rtl" : "ltr"}"><head><meta charset="utf-8"><style>
body{margin:0;padding:16px;font:14px/1.6 system-ui,"Segoe UI",Tahoma,sans-serif;background:${tokens["color-bg"]};color:${tokens["color-text"]}}
h1{font-size:20px;margin:0 0 4px}p{margin:0 0 12px;color:${tokens["color-text-muted"]}}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.card{padding:16px 8px;border-radius:10px;background:${tokens["color-surface"]};border:1px solid ${tokens["color-border"]};text-align:center}
.row{display:flex;gap:8px;align-items:center}
button{font:inherit;padding:8px 16px;border:0;border-radius:8px;background:${tokens["color-primary"]};color:${tokens["color-on-primary"]};cursor:pointer}
.badge{padding:2px 10px;border-radius:999px;background:${tokens["color-primary"]};color:${tokens["color-on-primary"]};font-size:12px;display:inline-block}
${css}</style></head><body>
<h1 class="motion-rise">${esc(t("creative.motionSample", { name }))}</h1>
<p class="motion-fade" style="--motion-order:1">${esc(t("creative.motionSampleText"))}</p>
<div class="cards">${cards}</div>
<div class="row motion-slide" style="--motion-order:5"><button class="motion-press" type="button">${esc(t("creative.motionSampleButton"))}</button>
<span class="badge motion-pulse">${esc(t("creative.motionSampleNew"))}</span></div>
</body></html>`;
  return <iframe className="motion-preview" title={t("creative.motionPreview")} sandbox="" srcDoc={doc} />;
}

function DesignAgent({ kit }: { kit: Kit }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [brief, setBrief] = useState("");
  const ask = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}/concepts", {
        params: { path: { org_id: org.id, kit_id: kit.id } }, body: { brief },
      })) as Promise<{ reply: string; concepts: Logo[]; cost_usd: string }>,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["kit", org.id, kit.id] });
      void qc.invalidateQueries({ queryKey: ["kits", org.id] });
    },
  });
  return (
    <Card title={t("creative.agent")}>
      <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); ask.mutate(); }}>
        <p className="muted small">{t("creative.agentHint")}</p>
        <Textarea aria-label={t("creative.brief")} placeholder={t("creative.briefPlaceholder")} value={brief} onChange={(e) => setBrief(e.target.value)} maxLength={4000} />
        <div>
          <Button type="submit" variant="primary" icon={<Wand2 size={16} />} loading={ask.isPending} disabled={!brief.trim()}>
            {t("creative.askAgent")}
          </Button>
        </div>
        {ask.isError && <ErrorNotice error={ask.error} />}
        {ask.data && (
          <div className="notice notice-info" role="status">
            <Sparkles size={16} aria-hidden />
            <span className="grow" style={{ whiteSpace: "pre-wrap" }} dir="auto">
              {ask.data.reply}
              {ask.data.concepts.length > 0 && <><br /><strong>{t("creative.conceptsAdded", { n: ask.data.concepts.length })}</strong></>}
            </span>
          </div>
        )}
      </form>
    </Card>
  );
}

function ApplyDialog({ kit, onClose }: { kit: Kit; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const toast = useToast();
  const projects = useQuery({
    queryKey: ["projects", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/studio/projects", { params: { path: { org_id: org.id } } })),
  });
  const [projectId, setProjectId] = useState("");
  const apply = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/creative/brand-kits/{kit_id}/apply/{project_id}", {
        params: { path: { org_id: org.id, kit_id: kit.id, project_id: projectId } },
      })),
    onSuccess: () => {
      toast(t("creative.applied"));
      onClose();
    },
  });
  return (
    <Dialog open onClose={onClose} title={t("creative.applyToApp")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); apply.mutate(); }}>
        <p className="muted small">{t("creative.applyHint")}</p>
        {apply.isError && <ErrorNotice error={apply.error} />}
        <QueryView query={projects}>
          {(list) =>
            list.length === 0 ? (
              <Empty>{t("creative.noProjects")}</Empty>
            ) : (
              <Field label={t("creative.project")}>
                <Select value={projectId} onChange={(e) => setProjectId(e.target.value)} required>
                  <option value="">—</option>
                  {list.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              </Field>
            )
          }
        </QueryView>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" icon={<CheckCircle2 size={16} />} loading={apply.isPending} disabled={!projectId}>
            {t("creative.apply")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
