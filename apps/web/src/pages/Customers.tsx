import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Badge, Button, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea, useToast,
} from "../components/ui";
import { CsvActions } from "../components/CsvTools";
import { DataTable } from "../components/DataTable";
import { ListToolbar } from "../components/ListToolbar";
import { useI18n } from "../i18n/I18n";
import { initialsOf } from "../lib/format";
import { useDebounced } from "../lib/useDebounced";

type Customer = Schemas["CustomerOut"];
type CustomerForm = {
  name: string; kind: "company" | "individual"; email: string; phone: string; tax_number: string;
  commercial_registration: string; address_line: string; building_number: string; district: string; city: string;
  postal_code: string; country: string; notes: string;
};

const EMPTY: CustomerForm = {
  name: "", kind: "company", email: "", phone: "", tax_number: "", commercial_registration: "", address_line: "",
  building_number: "", district: "", city: "", postal_code: "", country: "SA", notes: "",
};

export function CustomersPage() {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState<Customer | "new" | null>(params.get("new") ? "new" : null);
  const query = useDebounced(q);
  const qc = useQueryClient();
  const toast = useToast();

  const customers = useQuery({
    queryKey: ["customers", org.id, query, archived],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/orgs/{org_id}/customers", {
          params: { path: { org_id: org.id }, query: { q: query || undefined, include_archived: archived } },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/orgs/{org_id}/customers/{customer_id}", { params: { path: { org_id: org.id, customer_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["customers", org.id] }),
    onError: (e) => toast(e instanceof ApiError ? e.message : t("common.error"), "error"),
  });

  function close() {
    setEditing(null);
    if (params.get("new")) setParams({}, { replace: true });
  }

  return (
    <div className="stack">
      <PageHeader
        title={t("customers.title")}
        actions={
          <>
            <CsvActions kind="customers" canWrite={can("customers.write")} />
            {can("customers.write") && (
              <Button variant="primary" icon={<Plus size={16} />} onClick={() => setEditing("new")}>
                {t("customers.new")}
              </Button>
            )}
          </>
        }
      />
      <ListToolbar
        search={q} onSearch={setQ} placeholder={t("customers.searchHint")}
        extra={<Checkbox label={t("customers.includeArchived")} checked={archived} onChange={(e) => setArchived(e.target.checked)} />}
        shown={customers.data?.length}
      />
      <QueryView query={customers}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{query ? t("common.noMatches") : undefined}</Empty>
          ) : (
            <DataTable
              rows={list} rowKey={(c) => c.id} label={t("customers.title")}
              onRowClick={can("customers.write") ? (c) => setEditing(c) : undefined}
              columns={[
                {
                  key: "name", header: t("common.name"), sort: (c) => c.name,
                  cell: (c) => (
                    <span className="row" style={{ gap: 10, flexWrap: "nowrap" }}>
                      <span className="avatar" aria-hidden>{initialsOf(c.name)}</span>
                      <span className="stack-xs" style={{ gap: 0, minWidth: 0 }}>
                        <span className="cell-strong" dir="auto">{c.name}</span>
                        {c.email && <span className="cell-sub ltr-inline">{c.email}</span>}
                      </span>
                    </span>
                  ),
                },
                { key: "phone", header: t("common.phone"), cell: (c) => <span className="num">{c.phone}</span> },
                { key: "tax", header: t("customers.taxNumber"), sort: (c) => c.tax_number, cell: (c) => <span className="num">{c.tax_number}</span> },
                { key: "city", header: t("customers.city"), sort: (c) => c.city, cell: (c) => <span dir="auto">{c.city}</span> },
                { key: "status", header: t("common.status"), cell: (c) => (c.active ? <Badge tone="success">{t("common.active")}</Badge> : <Badge>{t("common.archived")}</Badge>) },
                ...(can("customers.write") ? [{
                  key: "actions", header: <span className="sr-only">{t("common.actions")}</span>, align: "end" as const,
                  cell: (c: Customer) => (
                    <span className="row-actions">
                      <IconButton label={`${t("common.edit")} ${c.name}`} onClick={() => setEditing(c)}><Pencil size={16} /></IconButton>
                      {c.active && (
                        <IconButton label={`${t("common.delete")} ${c.name}`}
                          onClick={() => window.confirm(t("customers.archiveConfirm")) && remove.mutate(c.id)}>
                          <Trash2 size={16} />
                        </IconButton>
                      )}
                    </span>
                  ),
                }] : []),
              ]}
            />
          )
        }
      </QueryView>
      {editing && <CustomerDialog customer={editing === "new" ? null : editing} onClose={close} />}
    </div>
  );
}

function CustomerDialog({ customer, onClose }: { customer: Customer | null; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<CustomerForm>(
    customer ? { ...EMPTY, ...customer, kind: customer.kind as CustomerForm["kind"] } : EMPTY,
  );
  const set = (k: keyof CustomerForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, email: form.email || null, country: form.country.toUpperCase() };
      return customer
        ? unwrap(api.PATCH("/api/v1/orgs/{org_id}/customers/{customer_id}", {
            params: { path: { org_id: org.id, customer_id: customer.id } }, body,
          }))
        : unwrap(api.POST("/api/v1/orgs/{org_id}/customers", { params: { path: { org_id: org.id } }, body }));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["customers", org.id] });
      toast(t("common.saved"));
      onClose();
    },
  });
  const err = save.error instanceof ApiError ? save.error.fields : {};
  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }
  return (
    <Dialog open onClose={onClose} title={customer ? t("customers.edit") : t("customers.new")}>
      <form className="stack" onSubmit={submit}>
        {save.isError && !Object.keys(err).length && <ErrorNotice error={save.error} />}
        <div className="grid-2">
          <Field label={t("common.name")} error={err["name"]}>
            <Input value={form.name} onChange={set("name")} required />
          </Field>
          <Field label={t("customers.kind")}>
            <Select value={form.kind} onChange={set("kind")}>
              <option value="company">{t("customers.company")}</option>
              <option value="individual">{t("customers.individual")}</option>
            </Select>
          </Field>
          <Field label={t("common.email")} error={err["email"]} optional>
            <Input type="email" value={form.email} onChange={set("email")} ltr />
          </Field>
          <Field label={t("common.phone")} error={err["phone"]} optional>
            <Input type="tel" value={form.phone} onChange={set("phone")} ltr />
          </Field>
          <Field label={t("customers.taxNumber")} error={err["tax_number"]} optional>
            <Input value={form.tax_number} onChange={set("tax_number")} ltr />
          </Field>
          <Field label={t("customers.cr")} error={err["commercial_registration"]} optional>
            <Input value={form.commercial_registration} onChange={set("commercial_registration")} ltr />
          </Field>
          <Field label={t("customers.city")} optional>
            <Input value={form.city} onChange={set("city")} />
          </Field>
          <Field label={t("customers.country")} error={err["country"]} optional>
            <Input value={form.country} onChange={set("country")} maxLength={2} ltr />
          </Field>
        </div>
        <Field label={t("customers.address")} optional>
          <Input value={form.address_line} onChange={set("address_line")} />
        </Field>
        <div className="grid-2">
          <Field label={t("customers.buildingNumber")} hint={t("customers.nationalAddressHint")} optional>
            <Input value={form.building_number} onChange={set("building_number")} maxLength={10} inputMode="numeric" ltr />
          </Field>
          <Field label={t("customers.district")} optional>
            <Input value={form.district} onChange={set("district")} maxLength={100} />
          </Field>
        </div>
        <Field label={t("customers.notes")} optional>
          <Textarea value={form.notes} onChange={set("notes")} />
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
