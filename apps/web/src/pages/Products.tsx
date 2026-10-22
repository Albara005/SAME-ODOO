import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea,
} from "../components/ui";
import { CsvActions } from "../components/CsvTools";
import { useTaxes } from "../components/business";
import { useI18n } from "../i18n/I18n";
import { useDebounced } from "../lib/useDebounced";

type Product = Schemas["ProductOut"];
type Tax = Schemas["TaxOut"];

export function ProductsPage() {
  const { t, money } = useI18n();
  const { org, can } = useOrg();
  const [q, setQ] = useState("");
  const query = useDebounced(q);
  const [editing, setEditing] = useState<Product | "new" | null>(null);
  const taxes = useTaxes();
  const products = useQuery({
    queryKey: ["products", org.id, query],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/products", { params: { path: { org_id: org.id }, query: { q: query || undefined } } })),
    placeholderData: keepPreviousData,
  });
  const taxName = (id: string | null) => taxes.data?.find((x) => x.id === id)?.name ?? "—";

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
      <div style={{ maxWidth: 420 }}>
        <Input type="search" placeholder={t("common.search")} aria-label={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <QueryView query={products}>
        {(list) =>
          list.length === 0 ? (
            <Empty />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.name")}</th>
                    <th>{t("products.sku")}</th>
                    <th>{t("products.kind")}</th>
                    <th className="end">{t("products.price")}</th>
                    <th>{t("products.tax")}</th>
                    {can("products.write") && <th className="end">{t("common.actions")}</th>}
                  </tr>
                </thead>
                <tbody>
                  {list.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <strong>{p.name}</strong>
                        <div className="muted small">{p.unit}</div>
                      </td>
                      <td className="mono">{p.sku ?? "—"}</td>
                      <td>
                        <Badge tone={p.kind === "goods" ? "info" : "neutral"}>
                          {p.kind === "goods" ? t("products.goods") : t("products.service")}
                        </Badge>
                      </td>
                      <td className="end num">{money(p.sale_price, org.default_currency)}</td>
                      <td>{taxName(p.default_tax_id)}</td>
                      {can("products.write") && (
                        <td className="end">
                          <IconButton label={`${t("common.edit")} ${p.name}`} onClick={() => setEditing(p)}>
                            <Pencil size={16} />
                          </IconButton>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </QueryView>
      {can("taxes.manage") && <TaxesCard taxes={taxes.data ?? []} />}
      {editing && <ProductDialog product={editing === "new" ? null : editing} taxes={taxes.data ?? []} onClose={() => setEditing(null)} />}
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
  const [form, setForm] = useState({ name: "", rate: "" });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/taxes", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => {
      setForm({ name: "", rate: "" });
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
          <Button type="submit" loading={create.isPending} icon={<Plus size={16} />}>
            {t("common.add")}
          </Button>
        </form>
        {create.isError && <ErrorNotice error={create.error} />}
      </div>
    </Card>
  );
}
