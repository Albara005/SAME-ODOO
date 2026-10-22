import { useQueryClient } from "@tanstack/react-query";
import { Download, Upload } from "lucide-react";
import { useState } from "react";
import { ApiError, tokenStore, toApiError, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import { download } from "./business";
import { Button, Dialog, ErrorNotice, Field, errorText, useToast } from "./ui";

type Kind = "customers" | "products";
type Result = Schemas["ImportResult"];

/** Export and (with write permission) import buttons for a CSV-backed list. */
export function CsvActions({ kind, canWrite }: { kind: Kind; canWrite: boolean }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const toast = useToast();
  const [importing, setImporting] = useState(false);
  return (
    <>
      <Button
        icon={<Download size={16} />}
        onClick={() => download(`/api/v1/orgs/${org.id}/${kind}/export.csv`, `${kind}.csv`).catch((e) => toast(errorText(e, t("common.error")), "error"))}
      >
        {t("io.export")}
      </Button>
      {canWrite && (
        <Button icon={<Upload size={16} />} onClick={() => setImporting(true)}>
          {t("io.import")}
        </Button>
      )}
      {importing && <ImportDialog kind={kind} onClose={() => setImporting(false)} />}
    </>
  );
}

async function upload(orgId: string, kind: Kind, file: File, dryRun: boolean): Promise<Result> {
  const token = tokenStore.get();
  const res = await fetch(`/api/v1/orgs/${orgId}/${kind}/import?dry_run=${dryRun}`, {
    method: "POST",
    headers: { "Content-Type": "text/csv", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: file,
  });
  const body = await res.json().catch(() => undefined);
  if (!res.ok) throw toApiError(res.status, body);
  return body as Result;
}

/** Two steps: check the file (dry run, nothing saved), then import it. Imports are all-or-nothing. */
function ImportDialog({ kind, onClose }: { kind: Kind; onClose: () => void }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Result | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await upload(org.id, kind, file, dryRun);
      if (dryRun) {
        setPreview(result);
      } else {
        toast(t("io.done", { created: result.created, updated: result.updated }));
        void qc.invalidateQueries({ queryKey: [kind, org.id] });
        onClose();
      }
    } catch (e) {
      setPreview(null);
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const rows = error instanceof ApiError ? error.rows : [];
  return (
    <Dialog open onClose={onClose} title={t("io.import")}>
      <div className="stack">
        <p className="muted small">{kind === "customers" ? t("io.customersHint") : t("io.productsHint")}</p>
        <Field label={t("io.file")}>
          <input
            type="file"
            accept=".csv,text/csv"
            className="input"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setPreview(null);
              setError(null);
            }}
          />
        </Field>
        {preview && <div className="notice notice-info" role="status">{t("io.preview", { created: preview.created, updated: preview.updated })}</div>}
        {rows.length > 0 ? (
          <div className="notice notice-error" role="alert">
            <div className="stack-sm">
              <span>{t("io.rowErrors")}</span>
              <ul style={{ margin: 0, paddingInlineStart: "1.2em" }}>
                {rows.slice(0, 50).map((r) => (
                  <li key={r.row}>
                    <strong>{t("io.row", { row: r.row })}</strong>: <span dir="ltr">{r.errors.join("; ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : (
          error !== null && <ErrorNotice error={error} />
        )}
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button onClick={() => run(true)} disabled={!file} loading={busy && !preview}>
            {t("io.check")}
          </Button>
          <Button variant="primary" onClick={() => run(false)} disabled={!preview} loading={busy && !!preview}>
            {t("io.apply")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
