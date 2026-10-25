import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api, tokenStore, toApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { download } from "../components/business";
import { Badge, Button, Card, Empty, IconButton, Input, PageHeader, QueryView, errorText, useToast } from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { useDebounced } from "../lib/useDebounced";

type StoredFile = Schemas["FileOut"];

const ACCEPT = ".pdf,.docx,.txt,.csv,.md,.json,application/pdf,text/plain,text/csv";

async function uploadFile(orgId: string, file: File): Promise<StoredFile> {
  const token = tokenStore.get();
  const res = await fetch(`/api/v1/orgs/${orgId}/files?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: file,
  });
  const body = await res.json().catch(() => undefined);
  if (!res.ok) throw toApiError(res.status, body);
  return body as StoredFile;
}

function size(bytes: number, lang: string): string {
  const units = lang === "ar" ? ["بايت", "ك.ب", "م.ب"] : ["B", "KB", "MB"];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${i ? n.toFixed(1) : n} ${units[i]}`;
}

/** Whether the assistant and agents can search this file's text. */
export function TextBadge({ file }: { file: Pick<StoredFile, "text_status"> }) {
  const { t } = useI18n();
  if (file.text_status === "ready") return <Badge tone="success">{t("docs.text.ready")}</Badge>;
  if (file.text_status === "failed") return <Badge tone="warning">{t("docs.text.failed")}</Badge>;
  if (file.text_status === "pending") return <Badge>{t("docs.text.pending")}</Badge>;
  return <Badge>{t("docs.text.none")}</Badge>;
}

/** The organization's documents: upload, search inside them (ranked passages) and manage them. */
export function DocumentsPage() {
  const { t, date, lang } = useI18n();
  const { org, can } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const picker = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim(), 350);
  const path = { path: { org_id: org.id } };
  const files = useQuery({ queryKey: ["files", org.id], queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/files", { params: path })) });
  const found = useQuery({
    queryKey: ["file-search", org.id, query],
    enabled: query.length >= 2,
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/files/search", { params: { ...path, query: { q: query, limit: 8 } } })),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["files", org.id] });
    void qc.invalidateQueries({ queryKey: ["file-search", org.id] });
  };
  const upload = useMutation({
    mutationFn: async (list: File[]) => {
      const out: StoredFile[] = [];
      for (const f of list) out.push(await uploadFile(org.id, f));
      return out;
    },
    onSuccess: (out) => {
      refresh();
      toast(t("docs.uploaded", { n: out.length }));
    },
    onError: (e) => {
      refresh();
      toast(errorText(e, t("common.error")), "error");
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/orgs/{org_id}/files/{file_id}", { params: { path: { org_id: org.id, file_id: id } } })),
    onSuccess: () => {
      refresh();
      toast(t("docs.deleted"));
    },
    onError: (e) => toast(errorText(e, t("common.error")), "error"),
  });

  return (
    <div className="stack">
      <PageHeader
        title={t("docs.title")}
        subtitle={t("docs.subtitle")}
        actions={can("files.write") && (
          <>
            <input ref={picker} type="file" multiple accept={ACCEPT} hidden aria-label={t("docs.upload")}
              onChange={(e) => {
                const list = Array.from(e.target.files ?? []);
                e.target.value = "";
                if (list.length) upload.mutate(list);
              }} />
            <Button variant="primary" icon={<Upload size={16} />} loading={upload.isPending} onClick={() => picker.current?.click()}>
              {t("docs.upload")}
            </Button>
          </>
        )}
      />
      <Card title={t("docs.search")}>
        <div className="stack-sm">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("docs.searchHint")} aria-label={t("docs.search")} />
          <p className="muted small">{t("docs.searchAbout")}</p>
          {query.length >= 2 && (
            <QueryView query={found}>
              {(list) => list.length === 0 ? <Empty>{t("docs.noMatch")}</Empty> : (
                <ol className="passages" aria-label={t("docs.results")}>
                  {list.map((p) => (
                    <li key={`${p.file_id}-${p.position}`}>
                      <div className="row-between small">
                        <strong dir="auto">{p.filename}</strong>
                        <span className="muted">{t("docs.passage", { n: p.position + 1 })}</span>
                      </div>
                      <p>{p.text.split("\n").map((line, i) => <span key={i} dir="auto" className="line">{line}</span>)}</p>
                    </li>
                  ))}
                </ol>
              )}
            </QueryView>
          )}
        </div>
      </Card>
      <Card title={t("docs.all")}>
        <QueryView query={files}>
          {(list) => list.length === 0 ? <Empty>{t("docs.none")}</Empty> : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.name")}</th>
                    <th className="num">{t("docs.size")}</th>
                    <th>{t("docs.uploadedAt")}</th>
                    <th>{t("docs.textColumn")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((f) => (
                    <tr key={f.id}>
                      <td dir="auto">{f.filename}</td>
                      <td className="num small">{size(f.size_bytes, lang)}</td>
                      <td className="small">{date(f.created_at)}</td>
                      <td><TextBadge file={f} /></td>
                      <td className="end">
                        <div className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
                          <IconButton label={t("docs.download", { name: f.filename })}
                            onClick={() => download(`/api/v1/orgs/${org.id}/files/${f.id}/content`, f.filename)
                              .catch((e) => toast(errorText(e, t("common.error")), "error"))}>
                            <Download size={14} />
                          </IconButton>
                          {can("files.delete") && (
                            <IconButton label={t("docs.delete", { name: f.filename })}
                              onClick={() => window.confirm(t("docs.deleteConfirm", { name: f.filename })) && remove.mutate(f.id)}>
                              <Trash2 size={14} />
                            </IconButton>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryView>
      </Card>
    </div>
  );
}
