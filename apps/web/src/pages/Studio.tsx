import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, Download, ExternalLink, FileCode2, FilePlus2, GitCommitVertical, LayoutTemplate, Plus, Rocket, RotateCcw, Save, Send,
  ShieldCheck, Trash2, XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, tokenStore, toApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { StarMark } from "../components/Art";
import { CodeEditor } from "../components/CodeEditor";
import { StatusBadge, Tabs, download } from "../components/business";
import {
  Badge, Button, Card, Dialog, Empty, ErrorNotice, Field, IconButton, Input, PageHeader, QueryView, Select, Textarea, errorText, useToast,
} from "../components/ui";
import { useI18n } from "../i18n/I18n";

type Project = Schemas["ProjectOut"];
type Version = Schemas["novara__studio__router__VersionOut"];
type Check = Schemas["CheckOut"];
type Tab = "build" | "changes" | "versions" | "deployments";

const slugify = (s: string) =>
  s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);

function useProject(id: string) {
  const { org } = useOrg();
  return useQuery({
    queryKey: ["project", org.id, id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/studio/projects/{project_id}", { params: { path: { org_id: org.id, project_id: id } } })),
  });
}

/** Files are stored as raw bytes; the editor works on UTF-8 text. */
async function fileRequest(path: string, init: RequestInit = {}) {
  const token = tokenStore.get();
  const res = await fetch(path, { ...init, headers: { ...(init.headers ?? {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  if (!res.ok) throw toApiError(res.status, await res.json().catch(() => undefined));
  return res;
}

// ---------------------------------------------------------------- list

export function StudioPage() {
  const { t } = useI18n();
  const { org, can, path } = useOrg();
  const [creating, setCreating] = useState(false);
  const projects = useQuery({
    queryKey: ["projects", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/studio/projects", { params: { path: { org_id: org.id } } })),
  });
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.studio")}
        subtitle={t("studio.subtitle")}
        actions={can("apps.write") && <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>{t("studio.new")}</Button>}
      />
      <QueryView query={projects}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t("studio.none")}</Empty>
          ) : (
            <div className="apps" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
              {list.map((p) => (
                <Link key={p.id} to={path(`studio/${p.id}`)} className="app-tile hue-cyan">
                  <span className="row-between">
                    <span className="app-icon"><LayoutTemplate size={22} aria-hidden /></span>
                    <span className="row" style={{ gap: 6 }}>
                      {p.base_version > 0 && <Badge tone="primary">v{p.base_version}</Badge>}
                      {p.has_unsaved_changes && <Badge tone="warning">{t("studio.unsaved")}</Badge>}
                    </span>
                  </span>
                  <span className="stack-sm" style={{ gap: 2 }}>
                    <strong>{p.name}</strong>
                    <span className="muted small mono">{p.slug}</span>
                    {p.description && <span className="muted small">{p.description}</span>}
                  </span>
                </Link>
              ))}
            </div>
          )
        }
      </QueryView>
      {creating && <NewProjectDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const templates = useQuery({
    queryKey: ["templates", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/studio/templates", { params: { path: { org_id: org.id } } })) as Promise<{ key: string; name: string; description: string }[]>,
  });
  const [form, setForm] = useState({ name: "", slug: "", template: "static-site", description: "" });
  const [slugTouched, setSlugTouched] = useState(false);
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects", { params: { path: { org_id: org.id } }, body: form })),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ["projects", org.id] });
      navigate(path(`studio/${p.id}`));
    },
  });
  const err = create.error instanceof ApiError ? create.error.fields : {};
  return (
    <Dialog open onClose={onClose} title={t("studio.new")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        {create.isError && !Object.keys(err).length && <ErrorNotice error={create.error} />}
        <Field label={t("common.name")} error={err["name"]}>
          <Input value={form.name} required maxLength={120}
            onChange={(e) => setForm({ ...form, name: e.target.value, slug: slugTouched ? form.slug : slugify(e.target.value) })} />
        </Field>
        <Field label={t("studio.slug")} hint={t("studio.slugHint")} error={err["slug"]}>
          <Input value={form.slug} required minLength={3} maxLength={60} onChange={(e) => { setSlugTouched(true); setForm({ ...form, slug: e.target.value }); }} ltr />
        </Field>
        <Field label={t("studio.template")}>
          <Select value={form.template} onChange={(e) => setForm({ ...form, template: e.target.value })}>
            {(templates.data ?? [{ key: "static-site", name: "Static web app", description: "" }]).map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}
          </Select>
        </Field>
        <Field label={t("common.description")} optional>
          <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={2000} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>{t("common.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------- project

export function ProjectPage() {
  const { projectId = "" } = useParams();
  const project = useProject(projectId);
  return <QueryView query={project}>{(p) => <ProjectView project={p} />}</QueryView>;
}

function ProjectView({ project: p }: { project: Project }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("build");
  const [cutting, setCutting] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const ids = { org_id: org.id, project_id: p.id };
  const runChecks = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects/{project_id}/checks", { params: { path: ids } })),
    onSuccess: setChecks,
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["project", org.id, p.id] });
    void qc.invalidateQueries({ queryKey: ["projects", org.id] });
    void qc.invalidateQueries({ queryKey: ["diff", org.id, p.id] });
  };
  return (
    <div className="stack">
      <PageHeader
        title={p.name}
        subtitle={
          <span className="row">
            <span className="mono">{p.slug}</span>
            {p.base_version > 0 ? <Badge tone="primary">{t("studio.basedOn", { v: p.base_version })}</Badge> : <Badge>{t("studio.noVersion")}</Badge>}
            {p.has_unsaved_changes && <Badge tone="warning">{t("studio.unsaved")}</Badge>}
          </span>
        }
        actions={
          <>
            <Button icon={<ShieldCheck size={16} />} loading={runChecks.isPending} onClick={() => runChecks.mutate()}>{t("studio.check")}</Button>
            {can("apps.write") && (
              <Button variant="primary" icon={<GitCommitVertical size={16} />} onClick={() => setCutting(true)} disabled={!p.has_unsaved_changes && p.base_version > 0}>
                {t("studio.saveVersion")}
              </Button>
            )}
          </>
        }
      />
      {checks && <ChecksList checks={checks} onClose={() => setChecks(null)} />}
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "build", label: t("studio.tab.build") },
          { key: "changes", label: t("studio.tab.changes") },
          { key: "versions", label: t("studio.tab.versions") },
          { key: "deployments", label: t("studio.tab.deployments") },
        ]}
      />
      {tab === "build" && <BuildTab project={p} onChanged={refresh} />}
      {tab === "changes" && <ChangesTab project={p} />}
      {tab === "versions" && <VersionsTab project={p} onChanged={refresh} />}
      {tab === "deployments" && <DeploymentsTab project={p} />}
      {cutting && (
        <CutVersionDialog project={p} onClose={() => setCutting(false)} onDone={(v) => {
          setCutting(false);
          refresh();
          void qc.invalidateQueries({ queryKey: ["versions", org.id, p.id] });
          toast(v.checks_passed ? t("studio.versionOk", { v: v.number }) : t("studio.versionChecksFailed", { v: v.number }), v.checks_passed ? "info" : "error");
        }} />
      )}
    </div>
  );
}

function ChecksList({ checks, onClose }: { checks: Check[]; onClose?: () => void }) {
  const { t } = useI18n();
  const failed = checks.filter((c) => c.status === "fail");
  return (
    <div className={`notice ${failed.length ? "notice-error" : "notice-success"}`} role="status">
      {failed.length ? <XCircle size={18} aria-hidden /> : <CheckCircle2 size={18} aria-hidden />}
      <div className="grow stack-sm" style={{ gap: 2 }}>
        <strong>{failed.length ? t("studio.checksFailed", { n: failed.length }) : t("studio.checksPassed", { n: checks.length })}</strong>
        {checks.filter((c) => c.status !== "pass").map((c, i) => (
          <span key={i} className="small" dir="ltr" style={{ textAlign: "start" }}>
            <span className="mono">{c.path || c.check}</span>: {c.message}
          </span>
        ))}
      </div>
      {onClose && <Button size="sm" variant="ghost" onClick={onClose}>{t("common.close")}</Button>}
    </div>
  );
}

function BuildTab({ project: p, onChanged }: { project: Project; onChanged: () => void }) {
  const { t } = useI18n();
  const { org, can } = useOrg();
  const toast = useToast();
  const files = p.files.map((f) => f.path);
  const [current, setCurrent] = useState(files.includes("index.html") ? "index.html" : files[0] ?? "");
  const [text, setText] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const base = `/api/v1/orgs/${org.id}/studio/projects/${p.id}/files/`;
  const sha = p.files.find((f) => f.path === current)?.sha256;
  const content = useQuery({
    queryKey: ["file", org.id, p.id, current, sha],
    queryFn: async () => (await fileRequest(base + current)).text(),
    enabled: Boolean(current) && Boolean(sha),
  });
  useEffect(() => {
    if (content.data !== undefined) {
      setText(content.data);
      setDirty(false);
    }
  }, [content.data]);
  const save = useMutation({
    mutationFn: (args: { path: string; body: string }) => fileRequest(base + args.path, { method: "PUT", body: args.body }),
    onSuccess: () => {
      setDirty(false);
      onChanged();
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  const remove = useMutation({
    mutationFn: (path: string) => fileRequest(base + path, { method: "DELETE" }),
    onSuccess: () => {
      setCurrent(files.find((f) => f !== current) ?? "");
      onChanged();
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });
  function newFile() {
    const name = window.prompt(t("studio.newFilePrompt"), "page.html")?.trim();
    if (!name) return;
    save.mutate({ path: name, body: "" }, { onSuccess: () => setCurrent(name) });
  }
  const saveCurrent = () => {
    if (text !== null && dirty) save.mutate({ path: current, body: text });
  };
  const write = can("apps.write");
  const [view, setView] = useState<"code" | "preview">("code");
  return (
    <div className="studio">
      <aside className="file-tree" aria-label={t("studio.files")}>
        <div className="row-between">
          <strong className="small">{t("studio.files")}</strong>
          {write && <IconButton label={t("studio.newFile")} onClick={newFile}><FilePlus2 size={16} /></IconButton>}
        </div>
        {files.map((f) => (
          <button key={f} type="button" className={`file ${f === current ? "active" : ""}`} onClick={() => setCurrent(f)} dir="ltr">
            <FileCode2 size={14} aria-hidden /> {f}
          </button>
        ))}
      </aside>
      <section className="editor">
        <div className="editor-bar">
          <span className="seg" role="tablist" aria-label={t("studio.view")}>
            <button type="button" role="tab" aria-selected={view === "code"} onClick={() => setView("code")}>{t("studio.code")}</button>
            <button type="button" role="tab" aria-selected={view === "preview"} onClick={() => setView("preview")}>{t("studio.quickPreview")}</button>
          </span>
          <span className="mono small" dir="ltr">{current || "—"}</span>
          {dirty && <Badge tone="warning">{t("studio.notSaved")}</Badge>}
          <span className="grow" />
          {write && current && (
            <>
              <IconButton label={`${t("common.delete")} ${current}`} onClick={() => window.confirm(t("studio.deleteFile", { path: current })) && remove.mutate(current)}>
                <Trash2 size={16} />
              </IconButton>
              <Button size="sm" variant="primary" icon={<Save size={14} />} loading={save.isPending} disabled={!dirty} onClick={saveCurrent}>
                {t("common.save")}
              </Button>
            </>
          )}
        </div>
        {view === "preview" ? (
          <QuickPreview project={p} current={current} draft={text} />
        ) : content.isError ? (
          <ErrorNotice error={content.error} />
        ) : (
          <CodeEditor
            key={current}
            path={current}
            label={t("studio.code")}
            value={text ?? ""}
            onChange={(v) => { setText(v); setDirty(true); }}
            onSave={saveCurrent}
            readOnly={!write}
          />
        )}
      </section>
      {write && can("ai.use") && <BuilderChat project={p} onChanged={onChanged} />}
    </div>
  );
}

/**
 * The working copy rendered in a sandboxed frame, with its stylesheets inlined (unsaved edits included).
 * Scripts don't run here; a preview deployment runs the real thing.
 */
function QuickPreview({ project: p, current, draft }: { project: Project; current: string; draft: string | null }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const page = current.endsWith(".html") ? current : "index.html";
  const base = `/api/v1/orgs/${org.id}/studio/projects/${p.id}/files/`;
  const doc = useQuery({
    queryKey: ["quick-preview", org.id, p.id, page, p.files.map((f) => f.sha256).join(), current, draft],
    queryFn: async () => {
      const read = async (path: string) => (path === current && draft !== null ? draft : (await fileRequest(base + path)).text());
      let html = await read(page);
      const sheets = p.files.map((f) => f.path).filter((x) => x.endsWith(".css"));
      for (const sheet of sheets) {
        const cssText = (await read(sheet)).replace(/<\/style/gi, "<\\/style");
        const link = new RegExp(`<link[^>]*href=["']\\.?/?${sheet.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["'][^>]*>`, "gi");
        html = html.replace(link, () => `<style>${cssText}</style>`);
      }
      return html.replace(/<script\b[\s\S]*?<\/script>/gi, "");
    },
    enabled: p.files.some((f) => f.path === page),
  });
  return (
    <div className="quick-preview">
      <p className="muted small">{t("studio.quickPreviewHint")}</p>
      {doc.isError ? <ErrorNotice error={doc.error} /> : <iframe title={t("studio.quickPreview")} sandbox="" srcDoc={doc.data ?? ""} />}
    </div>
  );
}

function BuilderChat({ project: p, onChanged }: { project: Project; onChanged: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [text, setText] = useState("");
  const [log, setLog] = useState<{ role: "user" | "assistant"; text: string; files?: string[] }[]>([]);
  const send = useMutation({
    mutationFn: (content: string) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects/{project_id}/builder/messages", {
        params: { path: { org_id: org.id, project_id: p.id } }, body: { content },
      })),
    onSuccess: (r) => {
      setLog((l) => [...l, { role: "assistant", text: r.reply, files: r.changed_files }]);
      if (r.changed_files.length) onChanged();
    },
  });
  function submit() {
    const content = text.trim();
    if (!content || send.isPending) return;
    setLog((l) => [...l, { role: "user", text: content }]);
    setText("");
    send.mutate(content);
  }
  return (
    <aside className="builder" aria-label={t("studio.builder")}>
      <div className="row" style={{ gap: "var(--space-2)" }}>
        <StarMark size={18} />
        <strong className="small">{t("studio.builder")}</strong>
      </div>
      <div className="builder-log">
        {log.length === 0 && <p className="muted small">{t("studio.builderHint")}</p>}
        {log.map((m, i) => (
          <div key={i} className={`bubble ${m.role === "user" ? "bubble-user" : "bubble-assistant"}`} style={{ maxInlineSize: "100%" }}>
            <div className="bubble-text small" dir="auto">
              {m.text}
              {m.files && m.files.length > 0 && (
                <span className="changed">{t("studio.changed")}: <span dir="ltr">{m.files.join(", ")}</span></span>
              )}
            </div>
          </div>
        ))}
        {send.isPending && <div className="bubble bubble-assistant thinking small"><StarMark size={16} /> {t("assistant.thinking")}</div>}
        {send.isError && <ErrorNotice error={send.error} />}
      </div>
      <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <Textarea aria-label={t("studio.builderMessage")} placeholder={t("studio.builderPlaceholder")} value={text} rows={3}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }} />
        <Button type="submit" size="sm" variant="primary" icon={<Send size={14} />} loading={send.isPending} disabled={!text.trim()}>{t("assistant.send")}</Button>
      </form>
    </aside>
  );
}

function DiffView({ files }: { files: { path: string; status: string; diff: string }[] }) {
  const { t } = useI18n();
  if (files.length === 0) return <Empty>{t("studio.noChanges")}</Empty>;
  return (
    <div className="stack-sm">
      {files.map((f) => (
        <details key={f.path} open className="diff">
          <summary>
            <Badge tone={f.status === "added" ? "success" : f.status === "deleted" ? "danger" : "warning"}>{t(`studio.status.${f.status}` as "studio.status.added")}</Badge>{" "}
            <span className="mono" dir="ltr">{f.path}</span>
          </summary>
          <pre dir="ltr">
            {f.diff.split("\n").map((line, i) => (
              <span key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}>
                {line + "\n"}
              </span>
            ))}
          </pre>
        </details>
      ))}
    </div>
  );
}

function ChangesTab({ project: p }: { project: Project }) {
  const { org } = useOrg();
  const diff = useQuery({
    queryKey: ["diff", org.id, p.id],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/studio/projects/{project_id}/diff", { params: { path: { org_id: org.id, project_id: p.id } } })) as Promise<{
        base: string; target: string; files: { path: string; status: string; diff: string }[];
      }>,
  });
  return <QueryView query={diff}>{(d) => <DiffView files={d.files} />}</QueryView>;
}

function CutVersionDialog({ project: p, onClose, onDone }: { project: Project; onClose: () => void; onDone: (v: Version) => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const [message, setMessage] = useState("");
  const cut = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects/{project_id}/versions", { params: { path: { org_id: org.id, project_id: p.id } }, body: { message } })),
    onSuccess: onDone,
  });
  return (
    <Dialog open onClose={onClose} title={t("studio.saveVersion")}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); cut.mutate(); }}>
        <p className="muted small">{t("studio.versionHint")}</p>
        {cut.isError && <ErrorNotice error={cut.error} />}
        <Field label={t("studio.versionMessage")} optional>
          <Input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={300} placeholder={t("studio.versionPlaceholder")} />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={cut.isPending}>{t("studio.saveVersion")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function VersionsTab({ project: p, onChanged }: { project: Project; onChanged: () => void }) {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const ids = { org_id: org.id, project_id: p.id };
  const versions = useQuery({
    queryKey: ["versions", org.id, p.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/studio/projects/{project_id}/versions", { params: { path: ids } })),
  });
  const fail = (e: unknown) => toast(errorText(e, t("common.error")), "error");
  const restore = useMutation({
    mutationFn: (n: number) => unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects/{project_id}/versions/{number}/restore", { params: { path: { ...ids, number: n } } })),
    onSuccess: (_, n) => {
      toast(t("studio.restored", { v: n }));
      onChanged();
    },
    onError: fail,
  });
  const deploy = useMutation({
    mutationFn: (args: { version: number; environment: "preview" | "production" }) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects/{project_id}/deployments", { params: { path: ids }, body: args })),
    onSuccess: (d) => {
      toast(d.status === "live" ? t("studio.previewLive") : t("studio.productionRequested"));
      void qc.invalidateQueries({ queryKey: ["deployments", org.id, p.id] });
    },
    onError: fail,
  });
  return (
    <QueryView query={versions}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("studio.noVersions")}</Empty>
        ) : (
          <div className="stack-sm">
            {[...list].reverse().map((v) => (
              <Card key={v.id} tight>
                <div className="row-between">
                  <span className="row">
                    <strong>v{v.number}</strong>
                    <span dir="auto">{v.message || "—"}</span>
                    <Badge tone={v.checks_passed ? "success" : "danger"}>{v.checks_passed ? t("studio.checksOk") : t("studio.checksBad")}</Badge>
                    {v.author_kind === "assistant" && <Badge tone="primary">{t("studio.byBuilder")}</Badge>}
                    <span className="muted small num">{date(v.created_at)}</span>
                  </span>
                  <span className="row" style={{ gap: "var(--space-2)" }}>
                    <Button size="sm" variant="ghost" icon={<Download size={14} />}
                      onClick={() => download(`/api/v1/orgs/${org.id}/studio/projects/${p.id}/versions/${v.number}/export.zip`, `${p.slug}-v${v.number}.zip`).catch(fail)}>
                      ZIP
                    </Button>
                    {can("apps.write") && (
                      <>
                        <Button size="sm" icon={<RotateCcw size={14} />} onClick={() => window.confirm(t("studio.restoreConfirm", { v: v.number })) && restore.mutate(v.number)}>
                          {t("studio.restore")}
                        </Button>
                        <Button size="sm" disabled={!v.checks_passed} onClick={() => deploy.mutate({ version: v.number, environment: "preview" })}>
                          {t("studio.preview")}
                        </Button>
                        <Button size="sm" variant="primary" icon={<Rocket size={14} />} disabled={!v.checks_passed}
                          onClick={() => deploy.mutate({ version: v.number, environment: "production" })}>
                          {t("studio.production")}
                        </Button>
                      </>
                    )}
                  </span>
                </div>
                {!v.checks_passed && <ChecksList checks={v.checks} />}
              </Card>
            ))}
          </div>
        )
      }
    </QueryView>
  );
}

function DeploymentsTab({ project: p }: { project: Project }) {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const ids = { org_id: org.id, project_id: p.id };
  const deployments = useQuery({
    queryKey: ["deployments", org.id, p.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/studio/projects/{project_id}/deployments", { params: { path: ids } })),
  });
  const done = () => void qc.invalidateQueries({ queryKey: ["deployments", org.id, p.id] });
  const fail = (e: unknown) => toast(errorText(e, t("common.error")), "error");
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      unwrap(api.POST(approve ? "/api/v1/orgs/{org_id}/studio/projects/{project_id}/deployments/{deployment_id}/approve" : "/api/v1/orgs/{org_id}/studio/projects/{project_id}/deployments/{deployment_id}/reject", {
        params: { path: { ...ids, deployment_id: id } },
      })),
    onSuccess: done,
    onError: fail,
  });
  const offline = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/orgs/{org_id}/studio/projects/{project_id}/offline", { params: { path: ids } })),
    onSuccess: done,
    onError: fail,
  });
  return (
    <QueryView query={deployments}>
      {(list) =>
        list.length === 0 ? (
          <Empty>{t("studio.noDeployments")}</Empty>
        ) : (
          <div className="stack-sm">
            {can("apps.deploy") && list.some((d) => d.status === "live") && (
              <div>
                <Button variant="danger" size="sm" loading={offline.isPending} onClick={() => window.confirm(t("studio.offlineConfirm")) && offline.mutate()}>
                  {t("studio.offline")}
                </Button>
              </div>
            )}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("studio.environment")}</th>
                    <th>{t("studio.version")}</th>
                    <th>{t("common.status")}</th>
                    <th>{t("common.date")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((d) => (
                    <tr key={d.id}>
                      <td><Badge tone={d.environment === "production" ? "primary" : "neutral"}>{d.environment === "production" ? t("studio.production") : t("studio.preview")}</Badge></td>
                      <td className="num">v{d.version}</td>
                      <td><StatusBadge status={d.status} /></td>
                      <td className="num small">{date(d.created_at)}</td>
                      <td className="end">
                        <span className="row" style={{ justifyContent: "flex-end", gap: "var(--space-2)" }}>
                          {d.url && (
                            <a className="btn btn-sm" href={d.url} target="_blank" rel="noreferrer noopener">
                              <ExternalLink size={14} aria-hidden /> {t("studio.open")}
                            </a>
                          )}
                          {d.status === "pending_approval" && can("apps.deploy") && (
                            <>
                              <Button size="sm" variant="primary" onClick={() => decide.mutate({ id: d.id, approve: true })}>{t("assistant.approve")}</Button>
                              <Button size="sm" onClick={() => decide.mutate({ id: d.id, approve: false })}>{t("assistant.reject")}</Button>
                            </>
                          )}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      }
    </QueryView>
  );
}

