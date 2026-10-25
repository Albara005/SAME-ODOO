import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, tokenStore, toApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { computeTotals, formatUnits } from "../lib/decimal";
import { StepStar } from "./Art";
import { Badge, Button, IconButton, Input, Select, type Tone } from "./ui";

// ---------------------------------------------------------------- status

const TONES: Record<string, Tone> = {
  draft: "neutral", quotation: "neutral", posted: "info", confirmed: "info", partially_paid: "warning", partial: "warning",
  paid: "success", delivered: "success", invoiced: "success", cancelled: "danger", pending: "neutral",
  not_applicable: "neutral", nothing_to_invoice: "neutral", credited: "success", received: "success", billed: "success",
  nothing_to_bill: "neutral",
  approved: "success", rejected: "neutral", approval_failed: "danger", waiting_approval: "warning",
  completed: "success", failed: "danger", running: "info", queued: "neutral", skipped: "neutral", open: "info", done: "success",
  pending_approval: "warning", provisioning: "info", live: "success", superseded: "neutral", offline: "neutral",
  expired: "neutral", unapplied: "danger",
};

export function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  const key = `status.${status}` as MessageKey;
  return <Badge tone={TONES[status] ?? "neutral"}>{t(key) === key ? status : t(key)}</Badge>;
}

// ---------------------------------------------------------------- tabs

export function Tabs<K extends string>({ tabs, value, onChange }: { tabs: { key: K; label: string }[]; value: K; onChange: (k: K) => void }) {
  return (
    <div role="tablist" className="tabs">
      {tabs.map((tab) => (
        <button key={tab.key} role="tab" type="button" aria-selected={value === tab.key} className="tab" onClick={() => onChange(tab.key)}>
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- journey

/** Where a document is in its life: each step a star, gold once reached. */
export function Journey({ steps, label }: { steps: { label: string; state: "done" | "current" | "todo" }[]; label: string }) {
  return (
    <ol className="journey" aria-label={label}>
      {steps.map((s, i) => (
        <li key={i} className={`journey-step ${s.state} ${s.state !== "todo" ? "reached" : ""}`} aria-current={s.state === "current" ? "step" : undefined}>
          <StepStar state={s.state} />
          <span className="journey-label">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}

/** Steps up to `reached` are done; `reached` itself is current unless the document is complete. */
export function journeySteps(labels: string[], reached: number, complete = false) {
  return labels.map((label, i) => ({
    label,
    state: (i < reached || (complete && i === reached) ? "done" : i === reached ? "current" : "todo") as "done" | "current" | "todo",
  }));
}

// ---------------------------------------------------------------- downloads

/** Authenticated download (CSV, ZIP, PNG): fetch with the bearer token, then save as a file. */
export async function download(path: string, filename: string) {
  const token = tokenStore.get();
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw toApiError(res.status, await res.json().catch(() => undefined));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** An authenticated image (logo SVG/PNG) as an object URL an <img> can show. Cached per path. */
export function useBlobUrl(path: string | null) {
  return useQuery({
    queryKey: ["blob", path],
    queryFn: async () => {
      const token = tokenStore.get();
      const res = await fetch(path!, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!res.ok) throw toApiError(res.status, await res.json().catch(() => undefined));
      return URL.createObjectURL(await res.blob());
    },
    enabled: Boolean(path),
    staleTime: Infinity,
  }).data;
}

/** One key per user intent: a retried click (or network retry) reuses it, so it can't pay or ship twice. */
export function useIdempotencyKey(): [string, () => void] {
  const [key, setKey] = useState(() => crypto.randomUUID());
  return [key, () => setKey(crypto.randomUUID())];
}

// ---------------------------------------------------------------- pickers

export function useCustomers() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["customers", org.id, "", false],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/customers", { params: { path: { org_id: org.id }, query: { limit: 500 } } })),
    enabled: can("customers.read"),
  });
}

export function useTaxes() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["taxes", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/taxes", { params: { path: { org_id: org.id } } })),
    enabled: can("products.read") || can("invoices.read") || can("taxes.manage"),
  });
}

export function useWarehouses() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["warehouses", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/warehouses", { params: { path: { org_id: org.id } } })),
    enabled: can("inventory.read"),
  });
}

export function useVendors() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["vendors", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/vendors", { params: { path: { org_id: org.id }, query: { limit: 500 } } })),
    enabled: can("purchases.read"),
  });
}

export function useProducts() {
  const { org, can } = useOrg();
  return useQuery({
    queryKey: ["products", org.id, ""],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/products", { params: { path: { org_id: org.id }, query: { limit: 500 } } })),
    enabled: can("products.read"),
  });
}

// ---------------------------------------------------------------- line editor

export type LineDraft = {
  key: string;
  product_id: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount_percent: string;
  /** "" = no tax, "product" = the product's default, otherwise a tax id */
  tax: string;
};

export const newLine = (): LineDraft => ({
  key: crypto.randomUUID(), product_id: "", description: "", quantity: "1", unit_price: "", discount_percent: "0", tax: "product",
});

/** Lines as the API expects them (LineIn): omitting tax_id means "use the product's tax". */
export function toLineIn(l: LineDraft): Schemas["LineIn"] {
  const out: Schemas["LineIn"] = {
    product_id: l.product_id || null,
    description: l.description || null,
    quantity: l.quantity,
    unit_price: l.unit_price === "" ? null : l.unit_price,
    discount_percent: l.discount_percent || "0",
  };
  if (l.tax !== "product") out.tax_id = l.tax || null;
  return out;
}

/** Lines as the purchasing API expects them: a cost (required), no discount. */
export function toPurchaseLineIn(l: LineDraft): Schemas["PurchaseLineIn"] {
  const out: Schemas["PurchaseLineIn"] = {
    product_id: l.product_id || null,
    description: l.description || null,
    quantity: l.quantity,
    unit_cost: l.unit_price,
  };
  if (l.tax !== "product") out.tax_id = l.tax || null;
  return out;
}

/**
 * Sales lines (price defaults to the product's, with discount) or, with `purchase`, purchase lines
 * (a unit cost, no discount). `services` limits the products to services (direct vendor bills).
 */
export function LinesEditor({ lines, onChange, currency, purchase, services }: {
  lines: LineDraft[]; onChange: (l: LineDraft[]) => void; currency: string; purchase?: boolean; services?: boolean;
}) {
  const { t, money } = useI18n();
  const productList = (useProducts().data ?? []).filter((p) => !services || p.kind === "service");
  const taxList = useTaxes().data ?? [];
  const productOf = (id: string) => productList.find((p) => p.id === id);

  const effective = lines.map((l) => {
    const p = productOf(l.product_id);
    const taxId = l.tax === "product" ? (p?.default_tax_id ?? null) : l.tax || null;
    return {
      quantity: l.quantity,
      unitPrice: l.unit_price !== "" || purchase ? l.unit_price : (p?.sale_price ?? ""),
      discount: purchase ? "0" : l.discount_percent,
      taxKey: taxId,
      taxRate: taxList.find((x) => x.id === taxId)?.rate ?? "0",
    };
  });
  const totals = computeTotals(effective, currency);
  const update = (i: number, patch: Partial<LineDraft>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const m = (units: bigint) => money(formatUnits(units, totals.digits), currency);

  return (
    <div className="stack-sm">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ minWidth: 200 }}>{t("invoices.product")}</th>
              <th style={{ minWidth: 180 }}>{t("invoices.description")}</th>
              <th style={{ width: 90 }}>{t("invoices.qty")}</th>
              <th style={{ width: 120 }}>{purchase ? t("purchases.unitCost") : t("invoices.unitPrice")}</th>
              {!purchase && <th style={{ width: 90 }}>{t("invoices.discount")}</th>}
              <th style={{ width: 160 }}>{t("invoices.taxCol")}</th>
              <th className="end">{t("invoices.net")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => {
              const p = productOf(l.product_id);
              const net = totals.lines[i];
              return (
                <tr key={l.key}>
                  <td>
                    <Select
                      aria-label={`${t("invoices.product")} ${i + 1}`}
                      value={l.product_id}
                      onChange={(e) => update(i, { product_id: e.target.value })}
                    >
                      <option value="">{t("invoices.freeLine")}</option>
                      {productList.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name}
                          {x.sku ? ` (${x.sku})` : ""}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td>
                    <Input
                      aria-label={`${t("invoices.description")} ${i + 1}`}
                      value={l.description}
                      placeholder={p?.name ?? ""}
                      onChange={(e) => update(i, { description: e.target.value })}
                    />
                  </td>
                  <td>
                    <Input aria-label={`${t("invoices.qty")} ${i + 1}`} inputMode="decimal" value={l.quantity} onChange={(e) => update(i, { quantity: e.target.value })} ltr />
                  </td>
                  <td>
                    <Input
                      aria-label={`${purchase ? t("purchases.unitCost") : t("invoices.unitPrice")} ${i + 1}`}
                      inputMode="decimal"
                      value={l.unit_price}
                      placeholder={purchase ? "" : (p?.sale_price ?? "")}
                      onChange={(e) => update(i, { unit_price: e.target.value })}
                      ltr
                    />
                  </td>
                  {!purchase && (
                    <td>
                      <Input aria-label={`${t("invoices.discount")} ${i + 1}`} inputMode="decimal" value={l.discount_percent} onChange={(e) => update(i, { discount_percent: e.target.value })} ltr />
                    </td>
                  )}
                  <td>
                    <Select aria-label={`${t("invoices.taxCol")} ${i + 1}`} value={l.tax} onChange={(e) => update(i, { tax: e.target.value })}>
                      <option value="product">{t("invoices.productTax")}</option>
                      <option value="">{t("products.noTax")}</option>
                      {taxList.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name} ({x.rate}%)
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="end num">{net === null || net === undefined ? "—" : m(net)}</td>
                  <td>
                    <IconButton label={`${t("common.delete")} ${i + 1}`} onClick={() => onChange(lines.filter((_, j) => j !== i))} disabled={lines.length === 1}>
                      <Trash2 size={16} />
                    </IconButton>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="row-between">
        <Button size="sm" icon={<Plus size={16} />} onClick={() => onChange([...lines, newLine()])}>
          {t("invoices.addLine")}
        </Button>
        <TotalsBox subtotal={m(totals.subtotal)} tax={m(totals.tax)} total={m(totals.total)} note={t("invoices.estimate")} />
      </div>
    </div>
  );
}

export function TotalsBox({ subtotal, tax, total, note, extra }: { subtotal: string; tax: string; total: string; note?: string; extra?: ReactNode }) {
  const { t } = useI18n();
  return (
    <div style={{ minWidth: 260, marginInlineStart: "auto" }}>
      <dl className="kv">
        <dt>{t("invoices.subtotal")}</dt>
        <dd className="num" style={{ textAlign: "end" }}>{subtotal}</dd>
        <dt>{t("invoices.tax")}</dt>
        <dd className="num" style={{ textAlign: "end" }}>{tax}</dd>
        <dt>
          <strong>{t("invoices.total")}</strong>
        </dt>
        <dd className="num" style={{ textAlign: "end" }}>
          <strong>{total}</strong>
        </dd>
        {extra}
      </dl>
      {note && <p className="muted small" style={{ marginBlockStart: "var(--space-2)", maxWidth: 320 }}>{note}</p>}
    </div>
  );
}
