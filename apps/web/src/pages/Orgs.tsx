import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, unwrap } from "../api/client";
import { rememberOrg } from "../auth/Org";
import { BrandMark, TopActions } from "../components/Layout";
import { Button, Card, Dialog, Empty, ErrorNotice, Field, Input, QueryView, Select } from "../components/ui";
import { useI18n } from "../i18n/I18n";

const CURRENCIES = ["SAR", "AED", "KWD", "BHD", "OMR", "QAR", "EGP", "JOD", "USD", "EUR"];

export function useMyOrgs() {
  return useQuery({ queryKey: ["orgs"], queryFn: () => unwrap(api.GET("/api/v1/orgs")) });
}

export function OrgsPage() {
  const { t } = useI18n();
  const orgs = useMyOrgs();
  const [creating, setCreating] = useState(false);
  return (
    <div className="content" style={{ maxWidth: 760 }}>
      <div className="row-between" style={{ marginBlockEnd: "var(--space-5)" }}>
        <BrandMark />
        <div className="row">
          <TopActions />
        </div>
      </div>
      <Card
        title={t("orgs.title")}
        actions={
          <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>
            {t("orgs.create")}
          </Button>
        }
      >
        <QueryView query={orgs}>
          {(list) =>
            list.length === 0 ? (
              <Empty>{t("orgs.empty")}</Empty>
            ) : (
              <div className="stack-sm">
                {list.map((o) => (
                  <Link key={o.id} to={`/o/${o.id}`} className="org-switch row" onClick={() => rememberOrg(o.id)}>
                    <Building2 size={20} aria-hidden />
                    <span className="grow">
                      <strong>{o.name}</strong>
                      <span className="muted small">
                        {t("orgs.role")}: {o.role} · {o.default_currency}
                      </span>
                    </span>
                  </Link>
                ))}
              </div>
            )
          }
        </QueryView>
      </Card>
      <CreateOrgDialog open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

function slugify(name: string) {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

function CreateOrgDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, lang } = useI18n();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", slug: "", default_currency: "SAR", default_locale: lang });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs", { body: form })),
    onSuccess: (org) => {
      void qc.invalidateQueries({ queryKey: ["orgs"] });
      rememberOrg(org.id);
      navigate(`/o/${org.id}`);
    },
  });
  const fieldErr = create.error instanceof ApiError ? create.error.fields : {};
  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }
  return (
    <Dialog open={open} onClose={onClose} title={t("orgs.create")}>
      <form className="stack" onSubmit={submit}>
        {create.isError && !Object.keys(fieldErr).length && <ErrorNotice error={create.error} />}
        <Field label={t("common.name")} error={fieldErr["name"]}>
          <Input
            value={form.name}
            onChange={(e) => {
              const name = e.target.value;
              setForm((f) => ({ ...f, name, slug: f.slug && f.slug !== slugify(f.name) ? f.slug : slugify(name) }));
            }}
            required
          />
        </Field>
        <Field label={t("orgs.slug")} hint={t("orgs.slugHint")} error={fieldErr["slug"]}>
          <Input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} required ltr />
        </Field>
        <div className="grid-2">
          <Field label={t("orgs.currency")}>
            <Select value={form.default_currency} onChange={(e) => setForm({ ...form, default_currency: e.target.value })}>
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("orgs.language")}>
            <Select value={form.default_locale} onChange={(e) => setForm({ ...form, default_locale: e.target.value as "ar" | "en" })}>
              <option value="ar">العربية</option>
              <option value="en">English</option>
            </Select>
          </Field>
        </div>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>
            {t("common.create")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
