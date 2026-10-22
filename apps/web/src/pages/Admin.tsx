import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, UserMinus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, ApiError, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import {
  Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, useToast,
} from "../components/ui";
import { useI18n, useRoleLabel } from "../i18n/I18n";
import { useDebounced } from "../lib/useDebounced";

function useRoles() {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["roles", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/roles", { params: { path: { org_id: org.id } } })),
  });
}

// ---------------------------------------------------------------- members

export function MembersPage() {
  const { t, date } = useI18n();
  const roleLabel = useRoleLabel();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const roles = useRoles();
  const members = useQuery({
    queryKey: ["members", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })),
  });
  const onError = (e: Error) => toast(e instanceof ApiError ? e.message : t("common.error"), "error");
  const done = () => qc.invalidateQueries({ queryKey: ["members", org.id] });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; status?: string } }) =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/members/{member_id}", { params: { path: { org_id: org.id, member_id: id } }, body })),
    onSuccess: done,
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/orgs/{org_id}/members/{member_id}", { params: { path: { org_id: org.id, member_id: id } } })),
    onSuccess: done,
    onError,
  });
  const manage = can("members.manage");

  return (
    <div className="stack">
      <PageHeader
        title={t("members.title")}
        actions={
          manage && (
            <Button variant="primary" icon={<Plus size={16} />} onClick={() => setAdding(true)}>
              {t("members.add")}
            </Button>
          )
        }
      />
      <QueryView query={members}>
        {(list) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("common.email")}</th>
                  <th>{t("members.role")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("common.date")}</th>
                  {manage && <th className="end">{t("common.actions")}</th>}
                </tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.id}>
                    <td>{m.full_name || "—"}</td>
                    <td className="mono">{m.email}</td>
                    <td>
                      {manage ? (
                        <Select
                          aria-label={`${t("members.role")} ${m.email}`}
                          value={m.role}
                          onChange={(e) => update.mutate({ id: m.id, body: { role: e.target.value } })}
                          style={{ maxWidth: 180 }}
                        >
                          {(roles.data ?? []).map((r) => (
                            <option key={r.id} value={r.key}>
                              {roleLabel(r.key, r.name)}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        roleLabel(m.role)
                      )}
                    </td>
                    <td>
                      {m.status === "active" ? (
                        <Badge tone="success">{t("common.active")}</Badge>
                      ) : (
                        <Badge tone="warning">{t("members.suspended")}</Badge>
                      )}
                    </td>
                    <td className="small">{date(m.created_at)}</td>
                    {manage && (
                      <td className="end">
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          <Button
                            size="sm"
                            onClick={() =>
                              update.mutate({ id: m.id, body: { status: m.status === "active" ? "suspended" : "active" } })
                            }
                          >
                            {m.status === "active" ? t("members.suspend") : t("members.reactivate")}
                          </Button>
                          <IconButton
                            label={`${t("members.remove")} ${m.email}`}
                            onClick={() => window.confirm(t("members.removeConfirm", { email: m.email })) && remove.mutate(m.id)}
                          >
                            <UserMinus size={16} />
                          </IconButton>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {adding && <AddMemberDialog roles={(roles.data ?? []).map((r) => ({ key: r.key, name: roleLabel(r.key, r.name) }))} onClose={() => setAdding(false)} />}
    </div>
  );
}

function AddMemberDialog({ roles, onClose }: { roles: { key: string; name: string }[]; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } }, body: { email, role } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["members", org.id] });
      onClose();
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    add.mutate();
  }
  return (
    <Dialog open onClose={onClose} title={t("members.add")}>
      <form className="stack" onSubmit={submit}>
        {add.isError && <ErrorNotice error={add.error} />}
        <Field label={t("common.email")} hint={t("members.addHint")}>
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required ltr />
        </Field>
        <Field label={t("members.role")}>
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => (
              <option key={r.key} value={r.key}>
                {r.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={add.isPending}>
            {t("common.add")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- roles

export function RolesPage() {
  const { t } = useI18n();
  const roleLabel = useRoleLabel();
  const { can } = useOrg();
  const roles = useRoles();
  const [creating, setCreating] = useState(false);
  const catalog = useQuery({ queryKey: ["permissions"], queryFn: () => unwrap(api.GET("/api/v1/permissions")) });
  return (
    <div className="stack">
      <PageHeader
        title={t("roles.title")}
        actions={
          can("roles.manage") && (
            <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>
              {t("roles.new")}
            </Button>
          )
        }
      />
      <QueryView query={roles}>
        {(list) => (
          <div className="stack">
            {list.map((r) => (
              <Card key={r.id} tight>
                <div className="row-between">
                  <h3>
                    {r.is_system ? roleLabel(r.key) : r.name} <span className="muted mono">{r.key}</span>
                  </h3>
                  <Badge tone={r.is_system ? "info" : "primary"}>{r.is_system ? t("roles.system") : t("roles.custom")}</Badge>
                </div>
                <div className="row" style={{ marginBlockStart: "var(--space-3)" }}>
                  {r.permissions.map((p) => (
                    <Badge key={p}>
                      <span title={(catalog.data as Record<string, string> | undefined)?.[p]}>{p}</span>
                    </Badge>
                  ))}
                </div>
              </Card>
            ))}
          </div>
        )}
      </QueryView>
      {creating && <RoleDialog catalog={(catalog.data as Record<string, string>) ?? {}} onClose={() => setCreating(false)} />}
    </div>
  );
}

function RoleDialog({ catalog, onClose }: { catalog: Record<string, string>; onClose: () => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const [form, setForm] = useState({ key: "", name: "" });
  const [perms, setPerms] = useState<Set<string>>(new Set());
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/roles", { params: { path: { org_id: org.id } }, body: { ...form, permissions: [...perms] } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["roles", org.id] });
      onClose();
    },
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("roles.new")}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        {create.isError && !Object.keys(err).length && <ErrorNotice error={create.error} />}
        <div className="grid-2">
          <Field label={t("common.name")} error={err["name"]}>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </Field>
          <Field label={t("roles.key")} hint="a-z, 0-9, _" error={err["key"]}>
            <Input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} required ltr />
          </Field>
        </div>
        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small muted">{t("roles.permissions")}</legend>
          <div className="perm-grid">
            {Object.entries(catalog).map(([key, description]) => (
              <Checkbox
                key={key}
                // You can only grant what you hold; the server enforces this too.
                disabled={!can(key)}
                checked={perms.has(key)}
                onChange={(e) => {
                  const next = new Set(perms);
                  if (e.target.checked) next.add(key);
                  else next.delete(key);
                  setPerms(next);
                }}
                label={
                  <span title={description}>
                    <span className="mono">{key}</span>
                  </span>
                }
              />
            ))}
          </div>
        </fieldset>
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

// ---------------------------------------------------------------- audit

export function AuditPage() {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const [action, setAction] = useState("");
  const filter = useDebounced(action.trim());
  const members = useQuery({
    queryKey: ["members", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })),
    retry: false,
  });
  const who = (id: string | null) => (id ? (members.data?.find((m) => m.user_id === id)?.email ?? id.slice(0, 8)) : "system");
  const logs = useQuery({
    queryKey: ["audit", org.id, filter],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/orgs/{org_id}/audit-logs", {
          params: { path: { org_id: org.id }, query: { action: filter || undefined, limit: 200 } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="stack">
      <PageHeader title={t("audit.title")} />
      <div style={{ maxWidth: 320 }}>
        <Input placeholder="invoice.posted" aria-label={t("audit.filter")} value={action} onChange={(e) => setAction(e.target.value)} ltr />
      </div>
      <QueryView query={logs}>
        {(list) =>
          list.length === 0 ? (
            <Empty />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.date")}</th>
                    <th>{t("audit.action")}</th>
                    <th>{t("audit.actor")}</th>
                    <th>{t("audit.target")}</th>
                    <th>{t("audit.details")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((l) => (
                    <tr key={l.id}>
                      <td className="small num">{date(l.created_at)}</td>
                      <td>
                        <Badge tone="primary">
                          <span className="mono">{l.action}</span>
                        </Badge>
                      </td>
                      <td className="mono small">{who(l.actor_user_id)}</td>
                      <td className="mono small">{l.target_type}</td>
                      <td className="mono small" style={{ maxWidth: 420, wordBreak: "break-word" }}>
                        {JSON.stringify(l.details)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- organization

export function OrgSettingsPage() {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ name: org.name, default_currency: org.default_currency, default_locale: org.default_locale });
  const save = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/v1/orgs/{org_id}", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["org", org.id] });
      void qc.invalidateQueries({ queryKey: ["orgs"] });
      toast(t("common.saved"));
    },
  });
  return (
    <div className="stack" style={{ maxWidth: 640 }}>
      <PageHeader title={t("org.settings")} />
      <Card>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          {save.isError && <ErrorNotice error={save.error} />}
          <Field label={t("common.name")}>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </Field>
          <div className="grid-2">
            <Field label={t("orgs.currency")} hint={t("org.currencyLocked")}>
              <Input value={form.default_currency} onChange={(e) => setForm({ ...form, default_currency: e.target.value.toUpperCase() })} maxLength={3} ltr />
            </Field>
            <Field label={t("orgs.language")}>
              <Select value={form.default_locale} onChange={(e) => setForm({ ...form, default_locale: e.target.value })}>
                <option value="ar">العربية</option>
                <option value="en">English</option>
              </Select>
            </Field>
          </div>
          <dl className="kv small">
            <dt>ID</dt>
            <dd className="mono">{org.id}</dd>
            <dt>{t("orgs.slug")}</dt>
            <dd className="mono">{org.slug}</dd>
            <dt>{t("nav.hosting")}</dt>
            <dd className="mono">{org.hosting_mode}</dd>
          </dl>
          <div className="form-actions">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {t("common.save")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
