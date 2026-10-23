import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, PackagePlus, Plus, Trash2, Warehouse as WarehouseIcon } from "lucide-react";
import { useState } from "react";
import { api, ApiError, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import { Tabs, useIdempotencyKey, useProducts, useWarehouses } from "../components/business";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, useToast,
} from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { isZero } from "../lib/decimal";
import { today } from "../lib/format";

type Tab = "levels" | "moves" | "warehouses";

export function InventoryPage() {
  const { t } = useI18n();
  const { can } = useOrg();
  const [tab, setTab] = useState<Tab>("levels");
  const [dialog, setDialog] = useState<"receive" | "adjust" | null>(null);
  return (
    <div className="stack">
      <PageHeader
        title={t("inventory.title")}
        actions={
          can("inventory.write") && (
            <>
              <Button icon={<ClipboardList size={16} />} onClick={() => setDialog("adjust")}>
                {t("inventory.adjust")}
              </Button>
              <Button variant="primary" icon={<PackagePlus size={16} />} onClick={() => setDialog("receive")}>
                {t("inventory.receive")}
              </Button>
            </>
          )
        }
      />
      <Valuation />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "levels", label: t("inventory.levels") },
          { key: "moves", label: t("inventory.moves") },
          { key: "warehouses", label: t("inventory.warehouses") },
        ]}
      />
      {tab === "levels" && <Levels />}
      {tab === "moves" && <Moves />}
      {tab === "warehouses" && <Warehouses />}
      {dialog && <StockDialog mode={dialog} onClose={() => setDialog(null)} />}
    </div>
  );
}

function Valuation() {
  const { t, money } = useI18n();
  const { org, can } = useOrg();
  const valuation = useQuery({
    queryKey: ["stock", org.id, "valuation"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/inventory/valuation", { params: { path: { org_id: org.id } } })),
  });
  if (!valuation.data) return null;
  const v = valuation.data;
  return (
    <div className="grid-2">
      <Card tight>
        <div className="stat">
          <span className="muted small">{t("inventory.stockValue")}</span>
          <span className="value num">{money(v.stock_value, v.currency)}</span>
        </div>
      </Card>
      {can("accounting.read") && (
        <Card tight>
          <div className="stat">
            <span className="muted small">{t("inventory.ledgerValue")}</span>
            <span className="value num">{money(v.ledger_inventory_balance, v.currency)}</span>
            <span>
              <Badge tone={v.matches_ledger ? "success" : "danger"}>{v.matches_ledger ? t("inventory.matches") : t("inventory.mismatch")}</Badge>
            </span>
          </div>
        </Card>
      )}
    </div>
  );
}

function Levels() {
  const { t, money, amount } = useI18n();
  const { org } = useOrg();
  const levels = useQuery({
    queryKey: ["stock", org.id, "levels"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/inventory/levels", { params: { path: { org_id: org.id } } })),
  });
  return (
    <QueryView query={levels}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("inventory.empty")}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("invoices.product")}</th>
                  <th>{t("products.sku")}</th>
                  <th>{t("inventory.warehouse")}</th>
                  <th className="end">{t("inventory.quantity")}</th>
                  <th className="end">{t("inventory.reserved")}</th>
                  <th className="end">{t("inventory.available")}</th>
                  <th className="end">{t("inventory.avgCost")}</th>
                  <th className="end">{t("inventory.value")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((l) => (
                  <tr key={`${l.product_id}:${l.warehouse_id}`}>
                    <td>{l.product_name}</td>
                    <td className="mono">{l.sku ?? "—"}</td>
                    <td className="mono">{l.warehouse_code}</td>
                    <td className="end num">{amount(l.quantity)}</td>
                    <td className="end num">{isZero(l.reserved) ? "—" : amount(l.reserved)}</td>
                    <td className={`end num ${l.available.startsWith("-") ? "short" : ""}`}>{amount(l.available)}</td>
                    <td className="end num">{money(l.average_cost, org.default_currency)}</td>
                    <td className="end num">{money(l.value, org.default_currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }
    </QueryView>
  );
}

function Moves() {
  const { t, money, amount, date } = useI18n();
  const { org } = useOrg();
  const products = useProducts();
  const warehouses = useWarehouses();
  const moves = useQuery({
    queryKey: ["stock", org.id, "moves"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/inventory/moves", { params: { path: { org_id: org.id }, query: { limit: 200 } } })),
  });
  const productName = (id: string) => products.data?.find((p) => p.id === id)?.name ?? "…";
  const whCode = (id: string) => warehouses.data?.find((w) => w.id === id)?.code ?? "…";
  return (
    <QueryView query={moves}>
      {(list) =>
        list.length === 0 ? (
          <Empty />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("common.date")}</th>
                  <th>{t("inventory.reference")}</th>
                  <th>{t("products.kind")}</th>
                  <th>{t("invoices.product")}</th>
                  <th>{t("inventory.warehouse")}</th>
                  <th className="end">{t("inventory.quantity")}</th>
                  <th className="end">{t("inventory.value")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.id}>
                    <td className="num small">{date(m.move_date)}</td>
                    <td className="mono">{m.reference}</td>
                    <td>{t(`inventory.kind.${m.kind}` as MessageKey)}</td>
                    <td>{productName(m.product_id)}</td>
                    <td className="mono">{whCode(m.warehouse_id)}</td>
                    <td className="end num">{amount(m.quantity)}</td>
                    <td className="end num">{money(m.value, org.default_currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }
    </QueryView>
  );
}

function Warehouses() {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const warehouses = useWarehouses();
  const [form, setForm] = useState({ code: "", name: "" });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/warehouses", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => {
      setForm({ code: "", name: "" });
      void qc.invalidateQueries({ queryKey: ["warehouses", org.id] });
    },
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <div className="stack">
      <QueryView query={warehouses}>
        {(list) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("inventory.code")}</th>
                  <th>{t("common.name")}</th>
                  <th>{t("common.status")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((w) => (
                  <tr key={w.id}>
                    <td className="mono">{w.code}</td>
                    <td>{w.name}</td>
                    <td><Badge tone={w.active ? "success" : "neutral"}>{w.active ? t("common.active") : t("common.archived")}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {can("inventory.write") && (
        <Card title={t("inventory.newWarehouse")} tight>
          <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <Field label={t("inventory.code")} error={err["code"]} hint={t("inventory.codeHint")}>
              <Input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} required maxLength={20} ltr />
            </Field>
            <div className="grow">
              <Field label={t("common.name")} error={err["name"]}>
                <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              </Field>
            </div>
            <Button type="submit" icon={<WarehouseIcon size={16} />} loading={create.isPending}>
              {t("common.add")}
            </Button>
          </form>
          {create.isError && !Object.keys(err).length && <ErrorNotice error={create.error} />}
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- receipt / count

type StockLine = { key: string; product_id: string; quantity: string; unit_cost: string };
const blank = (): StockLine => ({ key: crypto.randomUUID(), product_id: "", quantity: "", unit_cost: "" });

function StockDialog({ mode, onClose }: { mode: "receive" | "adjust"; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [idempotencyKey] = useIdempotencyKey();
  const goods = (useProducts().data ?? []).filter((p) => p.kind === "goods" && p.active);
  const warehouses = (useWarehouses().data ?? []).filter((w) => w.active);
  const [warehouseId, setWarehouseId] = useState("");
  const [moveDate, setMoveDate] = useState(today());
  const [counterpart, setCounterpart] = useState<"payable" | "equity" | "bank" | "cash">("payable");
  const [memo, setMemo] = useState("");
  const [lines, setLines] = useState<StockLine[]>([blank()]);
  const update = (i: number, patch: Partial<StockLine>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  const save = useMutation({
    mutationFn: () => {
      const common = { warehouse_id: warehouseId || null, move_date: moveDate, memo };
      return mode === "receive"
        ? unwrap(api.POST("/api/v1/orgs/{org_id}/inventory/receipts", {
            params: { path: { org_id: org.id }, header: { "idempotency-key": idempotencyKey } },
            body: { ...common, counterpart, lines: lines.map((l) => ({ product_id: l.product_id, quantity: l.quantity, unit_cost: l.unit_cost })) },
          }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/inventory/adjustments", {
            params: { path: { org_id: org.id } },
            body: {
              ...common,
              lines: lines.map((l) => ({ product_id: l.product_id, counted_quantity: l.quantity, unit_cost: l.unit_cost === "" ? null : l.unit_cost })),
            },
          }));
    },
    onSuccess: (r) => {
      toast(t("inventory.saved", { ref: r.reference }));
      void qc.invalidateQueries({ queryKey: ["stock", org.id] });
      onClose();
    },
  });

  return (
    <Dialog open onClose={onClose} title={mode === "receive" ? t("inventory.receive") : t("inventory.adjust")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="muted small">{mode === "receive" ? t("inventory.receiveHint") : t("inventory.adjustHint")}</p>
        {save.isError && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("common.date")}>
            <Input type="date" value={moveDate} onChange={(e) => setMoveDate(e.target.value)} required ltr />
          </Field>
          {warehouses.length > 1 && (
            <Field label={t("inventory.warehouse")}>
              <Select value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
                <option value="">{t("sales.defaultWarehouse")}</option>
                {warehouses.map((w) => (
                  <option key={w.id} value={w.id}>{w.code} · {w.name}</option>
                ))}
              </Select>
            </Field>
          )}
          {mode === "receive" && (
            <Field label={t("inventory.counterpart")}>
              <Select value={counterpart} onChange={(e) => setCounterpart(e.target.value as typeof counterpart)}>
                <option value="payable">{t("inventory.payable")}</option>
                <option value="bank">{t("invoices.bank")}</option>
                <option value="cash">{t("invoices.cash")}</option>
                <option value="equity">{t("inventory.equity")}</option>
              </Select>
            </Field>
          )}
          <Field label={t("inventory.memo")} optional>
            <Input value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={200} />
          </Field>
        </div>
        <div className="stack-sm">
          {lines.map((l, i) => (
            <div key={l.key} className="row" style={{ alignItems: "flex-end" }}>
              <div className="grow" style={{ minWidth: 180 }}>
                <Field label={`${t("invoices.product")} ${i + 1}`}>
                  <Select value={l.product_id} onChange={(e) => update(i, { product_id: e.target.value })} required>
                    <option value="">—</option>
                    {goods.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}{p.sku ? ` (${p.sku})` : ""}</option>
                    ))}
                  </Select>
                </Field>
              </div>
              <div style={{ width: 110 }}>
                <Field label={mode === "receive" ? t("inventory.quantity") : t("inventory.counted")}>
                  <Input inputMode="decimal" value={l.quantity} onChange={(e) => update(i, { quantity: e.target.value })} required ltr />
                </Field>
              </div>
              <div style={{ width: 120 }}>
                <Field label={t("inventory.unitCost")} optional={mode === "adjust"}>
                  <Input inputMode="decimal" value={l.unit_cost} onChange={(e) => update(i, { unit_cost: e.target.value })} required={mode === "receive"} ltr />
                </Field>
              </div>
              <IconButton label={`${t("common.delete")} ${i + 1}`} onClick={() => setLines(lines.filter((_, j) => j !== i))} disabled={lines.length === 1}>
                <Trash2 size={16} />
              </IconButton>
            </div>
          ))}
          <div>
            <Button size="sm" icon={<Plus size={16} />} onClick={() => setLines([...lines, blank()])}>
              {t("invoices.addLine")}
            </Button>
          </div>
          <p className="muted small">{t("inventory.goodsOnly")}</p>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>
            {t("common.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
