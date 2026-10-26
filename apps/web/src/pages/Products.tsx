import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea,
} from "../components/ui";
import { CsvActions } from "../components/CsvTools";
import { DataTable } from "../components/DataTable";
import { ListToolbar } from "../components/ListToolbar";
import { useTaxes } from "../components/business";
import { useI18n } from "../i18n/I18n";
import { useDebounced } from "../lib/useDebounced";

type Product = Schemas["ProductOut"];
type Tax = Schemas["TaxOut"];
type TaxCategory = Schemas["TaxIn"]["zatca_category"];
type KindFilter = "" | "goods" | "service";

export function ProductsPage() {
  const { t, money } = useI18n();
  const { org, can } = useOrg();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const query = useDebounced(q);
  const [kind, setKind] = useState<KindFilter>("");
  const [editing, setEditing] = useState<Product | "new" | null>(params.get("new") ? "new" : null);
  const taxes = useTaxes();
  const products = useQuery({
    queryKey: ["products", org.id, query],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/products", { params: { path: { org_id: org.id }, query: { q: query || undefined } } })),
    placeholderData: keepPreviousData,
  });
  const taxName = (id: string | null) => taxes.data?.find((x) => x.id === id)?.name ?? "—";
  const all = products.data ?? [];
  const shown = kind ? all.filter((p) => p.kind === kind) : all;
  const close = () => {
    setEditing(null);
    if (params.get("new")) setParams({}, { replace: true });
  };

  return (
    <div className="stack">
      <PageHeader
        title={t("products.title")}
        actions={
          <>
            <CsvActions kind="products" canWrite={can("products.write")} />
            {can("products.write") && (
              <Button variant="primary" icon={<Plus size={16} />} onClick={() => setEditing("new")}>
                {t("products.new")}
              </Button>
            )}
          </>
        }
      />
      <ListToolbar
        search={q} onSearch={setQ} placeholder={t("products.searchHint")}
        filters={[{ key: "" as KindFilter, label: t("common.all"), count: all.length },
          { key: "goods" as KindFilter, label: t("products.goods"), count: all.filter((p) => p.kind === "goods").length },
          { key: "service" as KindFilter, label: t("products.service"), count: all.filter((p) => p.kind === "service").length }]}
        filter={kind} onFilter={setKind} filterLabel={t("products.kind")} shown={products.data ? shown.length : undefined}
      />
      <QueryView query={products}>
        {() =>
          shown.length === 0 ? (
            <Empty>{query || kind ? t("common.noMatches") : undefined}</Empty>
          ) : (
            <DataTable
              rows={shown} rowKey={(p) => p.id} label={t("products.title")}
              onRowClick={can("products.write") ? (p) => setEditing(p) : undefined}
              columns={[
                {
                  key: "name", header: t("common.name"), sort: (p) => p.name,
                  cell: (p) => (
                    <span className="stack-xs" style={{ gap: 0 }}>
                      <span className="cell-strong" dir="auto">{p.name}</span>
                      <span className="cell-sub">{p.unit}</span>
                    </span>
                  ),
                },
                { key: "sku", header: t("products.sku"), sort: (p) => p.sku ?? "", cell: (p) => <span className="mono">{p.sku ?? "—"}</span> },
                {
                  key: "kind", header: t("products.kind"), sort: (p) => p.kind,
                  cell: (p) => <Badge tone={p.kind === "goods" ? "info" : "neutral"}>{p.kind === "goods" ? t("products.goods") : t("products.service")}</Badge>,
                },
                { key: "price", header: t("products.price"), align: "end", sort: (p) => Number(p.sale_price),
                  cell: (p) => <span className="num">{money(p.sale_price, org.default_currency)}</span> },
                { key: "tax", header: t("products.tax"), cell: (p) => taxName(p.default_tax_id) },
                ...(can("products.write") ? [{
                  key: "actions", header: <span className="sr-only">{t("common.actions")}</span>, align: "end" as const,
                  cell: (p: Product) => (
                    <span className="row-actions">
                      <IconButton label={`${t("common.edit")} ${p.name}`} onClick={() => setEditing(p)}><Pencil size={16} /></IconButton>
                    </span>
                  ),
                }] : []),
              ]}
            />
          )
        }
      </QueryView>
      {can("taxes.manage") && <TaxesCard taxes={taxes.data ?? []} />}
      {editing && <ProductDialog product={editing === "new" ? null : editing} taxes={taxes.data ?? []} onClose={close} />}
    </div>
  );
}

function ProductDialog({ product, taxes, onClose }: { product: Product | null; taxes: Tax[]; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: product?.name ?? "",
    sku: product?.sku ?? "",
    kind: (product?.kind ?? "goods") as "goods" | "service",
    unit: product?.unit ?? "unit",
    sale_price: product?.sale_price ?? "",
    default_tax_id: product?.default_tax_id ?? "",
    description: product?.description ?? "",
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, sku: form.sku || null, default_tax_id: form.default_tax_id || null };
      return product
        ? unwrap(api.PATCH("/api/v1/orgs/{org_id}/products/{product_id}", {
            params: { path: { org_id: org.id, product_id: product.id } }, body,
          }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/products", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["products", org.id] });
      onClose();
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }
  return (
    <Dialog open onClose={onClose} title={product ? t("products.edit") : t("products.new")}>
      <form className="stack" onSubmit={submit}>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("common.name")} error={err["name"]}>
            <Input value={form.name} onChange={set("name")} required />
          </Field>
          <Field label={t("products.sku")} error={err["sku"]} optional>
            <Input value={form.sku} onChange={set("sku")} ltr />
          </Field>
          <Field label={t("products.kind")}>
            <Select value={form.kind} onChange={set("kind")}>
              <option value="goods">{t("products.goods")}</option>
              <option value="service">{t("products.service")}</option>
            </Select>
          </Field>
          <Field label={t("products.unit")}>
            <Input value={form.unit} onChange={set("unit")} />
          </Field>
          <Field label={`${t("products.price")} (${org.default_currency})`} error={err["sale_price"]}>
            <Input inputMode="decimal" value={form.sale_price} onChange={set("sale_price")} required ltr />
          </Field>
          <Field label={t("products.tax")}>
            <Select value={form.default_tax_id} onChange={set("default_tax_id")}>
              <option value="">{t("products.noTax")}</option>
              {taxes.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name} ({x.rate}%)
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label={t("common.description")} optional>
          <Textarea value={form.description} onChange={set("description")} />
        </Field>
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

function TaxesCard({ taxes }: { taxes: Tax[] }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const blank = { name: "", rate: "", zatca_category: "" as TaxCategory, exemption_code: "", exemption_reason: "" };
  const [form, setForm] = useState(blank);
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/taxes", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => {
      setForm(blank);
      void qc.invalidateQueries({ queryKey: ["taxes", org.id] });
    },
  });
  return (
    <Card title={t("taxes.title")}>
      <div className="stack">
        {taxes.length > 0 && (
          <div className="row">
            {taxes.map((x) => (
              <Badge key={x.id} tone="primary">
                <bdi>{x.name}</bdi>&nbsp;·&nbsp;<span className="num">{x.rate}%</span>
              </Badge>
            ))}
          </div>
        )}
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Input aria-label={t("common.name")} placeholder={t("taxes.new")} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ maxWidth: 220 }} required />
          <Input aria-label={t("taxes.rate")} placeholder={t("taxes.rate")} inputMode="decimal" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} style={{ maxWidth: 120 }} ltr required />
          <Select aria-label={t("taxes.category")} value={form.zatca_category} style={{ maxWidth: 220 }}
            onChange={(e) => setForm({ ...form, zatca_category: e.target.value as TaxCategory })}>
            <option value="">{t("taxes.categoryAuto")}</option>
            <option value="S">{t("taxes.categoryS")}</option>
            <option value="Z">{t("taxes.categoryZ")}</option>
            <option value="E">{t("taxes.categoryE")}</option>
            <option value="O">{t("taxes.categoryO")}</option>
          </Select>
          {(form.zatca_category === "Z" || form.zatca_category === "E" || form.zatca_category === "O") && (
            <>
              <Input aria-label={t("taxes.exemptionCode")} placeholder="VATEX-SA-32" value={form.exemption_code} style={{ maxWidth: 160 }} ltr
                onChange={(e) => setForm({ ...form, exemption_code: e.target.value.toUpperCase() })} />
              <Input aria-label={t("taxes.exemptionReason")} placeholder={t("taxes.exemptionReason")} value={form.exemption_reason}
                style={{ maxWidth: 220 }} onChange={(e) => setForm({ ...form, exemption_reason: e.target.value })} />
            </>
          )}
          <Button type="submit" loading={create.isPending} icon={<Plus size={16} />}>
            {t("common.add")}
          </Button>
        </form>
        {create.isError && <ErrorNotice error={create.error} />}
      </div>
    </Card>
  );
}
