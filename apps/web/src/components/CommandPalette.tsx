import { useQuery } from "@tanstack/react-query";
import {
  CornerDownLeft, FilePlus2, FileText, Package, Search, ShoppingBag, ShoppingCart, Sparkles, Truck, UsersRound, Workflow,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import type { MessageKey } from "../i18n/en";
import { useDebounced } from "../lib/useDebounced";
import { visibleNav } from "./nav";

type Group = "apps" | "actions" | "records";
type Command = { id: string; group: Group; label: string; hint?: string; icon: LucideIcon; to: string };

/** Common starting points, for people allowed to do them. */
export const ACTIONS: { to: string; label: MessageKey; icon: LucideIcon; anyOf: string[]; create?: boolean }[] = [
  { to: "invoices/new", label: "palette.newInvoice", create: true, icon: FilePlus2, anyOf: ["invoices.write"] },
  { to: "sales/new", label: "palette.newQuote", create: true, icon: ShoppingCart, anyOf: ["sales.write"] },
  { to: "purchases/orders/new", label: "palette.newPurchaseOrder", create: true, icon: ShoppingBag, anyOf: ["purchases.write"] },
  { to: "purchases/bills/new", label: "palette.newBill", create: true, icon: FileText, anyOf: ["purchases.write"] },
  { to: "customers?new=1", label: "palette.newCustomer", create: true, icon: UsersRound, anyOf: ["customers.write"] },
  { to: "products?new=1", label: "palette.newProduct", create: true, icon: Package, anyOf: ["products.write"] },
  { to: "automation/new", label: "palette.newWorkflow", create: true, icon: Workflow, anyOf: ["workflows.manage"] },
  { to: "assistant", label: "palette.askAssistant", icon: Sparkles, anyOf: ["ai.use"] },
];

/** Arabic folded the way people type it: no diacritics or tatweel, one alef, ta marbuta as ha, alef maqsura as ya. */
const normal = (s: string) =>
  s.toLowerCase().replace(/[\u064B-\u0652\u0640]/g, "").replace(/[\u0623\u0625\u0622]/g, "\u0627").replace(/\u0629/g, "\u0647").replace(/\u0649/g, "\u064A");

/** Records whose name or number matches: customers, vendors, products, invoices and sales orders the person can see. */
function useRecords(q: string): Command[] {
  const { org, can } = useOrg();
  const { t } = useI18n();
  const draft = t("status.draft");
  const term = useDebounced(q.trim(), 200);
  const on = term.length >= 2;
  const path = { org_id: org.id };
  const opts = { staleTime: 30_000 };
  const customers = useQuery({
    queryKey: ["palette", org.id, "customers", term], enabled: on && can("customers.read"), ...opts,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/customers", { params: { path, query: { q: term } } })),
  });
  const vendors = useQuery({
    queryKey: ["palette", org.id, "vendors", term], enabled: on && can("purchases.read"), ...opts,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/vendors", { params: { path, query: { q: term } } })),
  });
  const products = useQuery({
    queryKey: ["palette", org.id, "products", term], enabled: on && can("products.read"), ...opts,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/products", { params: { path, query: { q: term } } })),
  });
  const invoices = useQuery({
    queryKey: ["palette", org.id, "invoices", term], enabled: on && can("invoices.read"), ...opts,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/invoices", { params: { path, query: { q: term, limit: 5 } } })),
  });
  const orders = useQuery({
    queryKey: ["palette", org.id, "orders", term], enabled: on && can("sales.read"), ...opts,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/sales-orders", { params: { path, query: { q: term, limit: 5 } } })),
  });
  if (!on) return [];
  return [
    ...(customers.data ?? []).slice(0, 5).map((c) => ({ id: `c-${c.id}`, group: "records" as const, label: c.name, hint: "nav.customers", icon: UsersRound, to: `customers?q=${encodeURIComponent(c.name)}` })),
    ...(vendors.data ?? []).slice(0, 5).map((v) => ({ id: `v-${v.id}`, group: "records" as const, label: v.name, hint: "palette.vendor", icon: Truck, to: `purchases?tab=vendors&q=${encodeURIComponent(v.name)}` })),
    ...(products.data ?? []).slice(0, 5).map((p) => ({ id: `p-${p.id}`, group: "records" as const, label: p.name, hint: "nav.products", icon: Package, to: `products?q=${encodeURIComponent(p.name)}` })),
    ...(invoices.data ?? []).map((i) => ({ id: `i-${i.id}`, group: "records" as const, label: `${i.number ?? draft} · ${i.customer_name}`, hint: "nav.invoices", icon: FileText, to: `invoices/${i.id}` })),
    ...(orders.data ?? []).map((o) => ({ id: `o-${o.id}`, group: "records" as const, label: `${o.number} · ${o.customer_name}`, hint: "nav.sales", icon: ShoppingCart, to: `sales/${o.id}` })),
  ];
}

/** Ctrl/⌘ K: go to any app, start a common action, or open a record by its name or number. */
export function CommandPalette({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org, can, path } = useOrg();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const records = useRecords(q);

  const commands = useMemo<Command[]>(() => {
    const apps = visibleNav(can, org.apps).flatMap((g) => g.items).map((i) => ({
      id: `a-${i.to}`, group: "apps" as const, label: t(i.label), hint: i.desc ? t(i.desc) : undefined, icon: i.icon, to: i.to,
    }));
    const actions = ACTIONS.filter((a) => a.anyOf.some((p) => can(p))).map((a) => ({
      id: `x-${a.to}`, group: "actions" as const, label: t(a.label), icon: a.icon, to: a.to,
    }));
    const needle = normal(q.trim());
    const match = (c: Command) => !needle || normal(c.label).includes(needle) || normal(c.hint ?? "").includes(needle);
    return [...apps.filter(match).slice(0, needle ? 8 : 6), ...actions.filter(match).slice(0, needle ? 6 : 4)];
  }, [can, org.apps, q, t]);
  const all = [...commands, ...records.map((r) => ({ ...r, hint: r.hint ? t(r.hint as MessageKey) : undefined }))];
  const current = Math.min(active, Math.max(all.length - 1, 0));

  useEffect(() => {
    input.current?.focus();
  }, []);
  useEffect(() => {
    setActive(0);
  }, [q]);

  const go = (c: Command | undefined) => {
    if (!c) return;
    onClose();
    navigate(path(c.to));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((current + 1) % Math.max(all.length, 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((current - 1 + all.length) % Math.max(all.length, 1)); }
    else if (e.key === "Enter") { e.preventDefault(); go(all[current]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };
  const groups: Group[] = ["apps", "actions", "records"];
  return (
    <div className="palette-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label={t("palette.title")}>
        <div className="palette-input">
          <Search size={18} aria-hidden />
          <input
            ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
            placeholder={t("palette.placeholder")} aria-label={t("palette.placeholder")}
            role="combobox" aria-expanded="true" aria-controls={listId}
            aria-activedescendant={all[current] ? `${listId}-${all[current]!.id}` : undefined} autoComplete="off" spellCheck={false}
          />
          <kbd className="kbd">Esc</kbd>
        </div>
        <div className="palette-list" id={listId} role="listbox" aria-label={t("palette.title")}>
          {all.length === 0 && <p className="palette-empty muted">{t("palette.nothing")}</p>}
          {groups.map((g) => {
            const items = all.filter((c) => c.group === g);
            if (!items.length) return null;
            return (
              <div key={g} role="group" aria-label={t(`palette.${g}` as MessageKey)}>
                <div className="palette-group">{t(`palette.${g}` as MessageKey)}</div>
                {items.map((c) => {
                  const index = all.indexOf(c);
                  return (
                    <div
                      key={c.id} id={`${listId}-${c.id}`} role="option" aria-selected={index === current}
                      className={`palette-item ${index === current ? "active" : ""}`}
                      onMouseMove={() => setActive(index)} onClick={() => go(c)}
                    >
                      <span className="palette-icon" aria-hidden><c.icon size={16} /></span>
                      <span className="palette-label" dir="auto">{c.label}</span>
                      {c.hint && <span className="palette-hint muted small">{c.hint}</span>}
                      {index === current && <CornerDownLeft size={14} className="palette-enter" aria-hidden />}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
        <div className="palette-foot muted small">
          <span><kbd className="kbd">↑</kbd> <kbd className="kbd">↓</kbd> {t("palette.move")}</span>
          <span><kbd className="kbd">↵</kbd> {t("palette.open")}</span>
        </div>
      </div>
    </div>
  );
}
