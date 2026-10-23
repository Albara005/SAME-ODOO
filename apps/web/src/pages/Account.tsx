import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Copy, Download, KeyRound, LogOut, MailCheck, ShieldCheck } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/Auth";
import { BrandMark, SignOutButton, TopActions } from "../components/Layout";
import { Badge, Button, Card, Dialog, ErrorNotice, Field, Input, QueryView, useToast } from "../components/ui";
import { useI18n } from "../i18n/I18n";

export function AccountPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="content" style={{ maxWidth: 820 }}>
      <div className="row-between" style={{ marginBlockEnd: "var(--space-5)" }}>
        <BrandMark />
        <div className="row">
          <TopActions />
          <SignOutButton />
        </div>
      </div>
      <div className="stack">
        <div className="row-between">
          <h1>{t("account.title")}</h1>
          <Link to="/" className="row small">
            <ArrowRight size={16} className="back-arrow" aria-hidden /> {t("account.back")}
          </Link>
        </div>
        <ProfileCard user={user} />
        <PasswordCard />
        <TwoFactorCard enabled={user.mfa_enabled} />
        <SessionsCard />
      </div>
    </div>
  );
}

function ProfileCard({ user }: { user: Schemas["UserOut"] }) {
  const { t } = useI18n();
  const toast = useToast();
  const resend = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/me/verify-email")),
    onSuccess: () => toast(t("verify.resent")),
    onError: (e) => toast(e instanceof ApiError && e.status === 429 ? t("verify.wait") : t("common.error"), "error"),
  });
  return (
    <Card title={t("account.profile")}>
      <dl className="kv">
        <dt>{t("auth.fullName")}</dt>
        <dd>{user.full_name || "—"}</dd>
        <dt>{t("common.email")}</dt>
        <dd className="row">
          <bdi className="mono">{user.email}</bdi>
          {user.email_verified ? (
            <Badge tone="success">{t("account.verified")}</Badge>
          ) : (
            <>
              <Badge tone="warning">{t("account.unverified")}</Badge>
              <Button size="sm" icon={<MailCheck size={14} />} loading={resend.isPending} onClick={() => resend.mutate()}>
                {t("verify.resend")}
              </Button>
            </>
          )}
        </dd>
      </dl>
    </Card>
  );
}

function PasswordCard() {
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = useState({ current_password: "", new_password: "" });
  const change = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/me/password", { body: form })),
    onSuccess: () => {
      setForm({ current_password: "", new_password: "" });
      void qc.invalidateQueries({ queryKey: ["sessions"] });
      toast(t("account.passwordChanged"));
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    change.mutate();
  }
  const err = change.error instanceof ApiError ? change.error : null;
  return (
    <Card title={t("account.password")}>
      <form className="stack" onSubmit={submit} noValidate>
        {err && !err.fields["new_password"] && (
          <ErrorNotice error={err.status === 403 ? new Error(t("account.wrongPassword")) : err} />
        )}
        <div className="grid-2">
          <Field label={t("account.currentPassword")}>
            <Input
              type="password"
              autoComplete="current-password"
              value={form.current_password}
              onChange={(e) => setForm({ ...form, current_password: e.target.value })}
              required
              ltr
            />
          </Field>
          <Field label={t("reset.new")} hint={t("auth.passwordHint")} error={err?.fields["new_password"]}>
            <Input
              type="password"
              autoComplete="new-password"
              value={form.new_password}
              onChange={(e) => setForm({ ...form, new_password: e.target.value })}
              required
              ltr
            />
          </Field>
        </div>
        <div className="row-between">
          <span className="muted small">{t("account.passwordNote")}</span>
          <Button type="submit" variant="primary" loading={change.isPending}>
            {t("account.changePassword")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------- two-factor sign-in

function TwoFactorCard({ enabled }: { enabled: boolean }) {
  const { t } = useI18n();
  const [dialog, setDialog] = useState<"setup" | "disable" | "codes" | null>(null);
  const remaining = useQuery({
    queryKey: ["recovery-codes"],
    queryFn: () => unwrap(api.GET("/api/v1/me/mfa/recovery-codes/remaining")) as Promise<{ remaining: number }>,
    enabled,
  });
  const left = remaining.data?.remaining;
  return (
    <Card
      title={t("mfa.title")}
      actions={enabled ? <Badge tone="success">{t("mfa.on")}</Badge> : <Badge>{t("mfa.off")}</Badge>}
    >
      <div className="stack">
        <p className="muted">{t("mfa.about")}</p>
        {enabled && left !== undefined && (
          <p className={left <= 2 ? "notice notice-info small" : "small"}>{t("mfa.remaining", { n: left })}</p>
        )}
        <div className="row">
          {enabled ? (
            <>
              <Button icon={<KeyRound size={16} />} onClick={() => setDialog("codes")}>
                {t("mfa.newCodes")}
              </Button>
              <Button variant="danger" onClick={() => setDialog("disable")}>
                {t("mfa.disable")}
              </Button>
            </>
          ) : (
            <Button variant="primary" icon={<ShieldCheck size={16} />} onClick={() => setDialog("setup")}>
              {t("mfa.enable")}
            </Button>
          )}
        </div>
      </div>
      {dialog === "setup" && <SetupDialog onClose={() => setDialog(null)} />}
      {(dialog === "disable" || dialog === "codes") && <ConfirmDialog kind={dialog} onClose={() => setDialog(null)} />}
    </Card>
  );
}

function useQrImage(text: string | undefined) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!text) return;
    let current = true;
    void QRCode.toString(text, { type: "svg", margin: 1, errorCorrectionLevel: "M" }).then((svg) => {
      if (current) setSrc(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`);
    });
    return () => {
      current = false;
    };
  }, [text]);
  return src;
}

function SetupDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const start = useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/me/mfa/setup", { body: { password } })) });
  const enable = useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/me/mfa/enable", { body: { code: code.trim() } })) });
  const qr = useQrImage(start.data?.otpauth_uri);
  const finish = () => {
    void qc.invalidateQueries({ queryKey: ["me"] });
    void qc.invalidateQueries({ queryKey: ["recovery-codes"] });
    onClose();
  };

  if (enable.data) {
    return (
      <Dialog open onClose={finish} title={t("mfa.codesTitle")}>
        <RecoveryCodes codes={enable.data.recovery_codes} onDone={finish} />
      </Dialog>
    );
  }
  return (
    <Dialog open onClose={onClose} title={t("mfa.enable")}>
      {!start.data ? (
        <form className="stack" onSubmit={(e) => (e.preventDefault(), start.mutate())}>
          <p className="muted">{t("mfa.confirmPassword")}</p>
          {start.isError && <ErrorNotice error={wrongPassword(start.error, t("account.wrongPassword"))} />}
          <Field label={t("auth.password")}>
            <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required ltr />
          </Field>
          <div className="form-actions">
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" loading={start.isPending}>
              {t("common.next")}
            </Button>
          </div>
        </form>
      ) : (
        <form className="stack" onSubmit={(e) => (e.preventDefault(), enable.mutate())}>
          <p>{t("mfa.scan")}</p>
          <div className="mfa-qr">
            {qr ? <img src={qr} alt={t("mfa.qrAlt")} width={184} height={184} /> : <span className="muted">…</span>}
          </div>
          <details>
            <summary className="small">{t("mfa.manual")}</summary>
            <code className="mfa-secret" dir="ltr">{start.data.secret.match(/.{1,4}/g)?.join(" ")}</code>
          </details>
          {enable.isError && <ErrorNotice error={enable.error} />}
          <Field label={t("mfa.code")}>
            <Input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required ltr />
          </Field>
          <div className="form-actions">
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" loading={enable.isPending}>
              {t("mfa.turnOn")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

/** Turning two-factor sign-in off, or replacing the recovery codes: both need the password and a code. */
function ConfirmDialog({ kind, onClose }: { kind: "disable" | "codes"; onClose: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ password: "", code: "" });
  const run = useMutation({
    mutationFn: async () => {
      const body = { password: form.password, code: form.code.trim() };
      if (kind === "disable") {
        await unwrap(api.POST("/api/v1/me/mfa/disable", { body }));
        return null;
      }
      return (await unwrap(api.POST("/api/v1/me/mfa/recovery-codes", { body }))).recovery_codes;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["recovery-codes"] });
      if (kind === "disable") {
        void qc.invalidateQueries({ queryKey: ["me"] });
        toast(t("mfa.disabled"));
        onClose();
      }
    },
  });
  if (run.data) {
    return (
      <Dialog open onClose={onClose} title={t("mfa.codesTitle")}>
        <RecoveryCodes codes={run.data} onDone={onClose} />
      </Dialog>
    );
  }
  return (
    <Dialog open onClose={onClose} title={kind === "disable" ? t("mfa.disable") : t("mfa.newCodes")}>
      <form className="stack" onSubmit={(e) => (e.preventDefault(), run.mutate())}>
        <p className="muted">{kind === "disable" ? t("mfa.disableBody") : t("mfa.newCodesBody")}</p>
        {run.isError && <ErrorNotice error={wrongPassword(run.error, t("mfa.wrongPasswordOrCode"))} />}
        <Field label={t("auth.password")}>
          <Input
            type="password"
            autoComplete="current-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
            ltr
          />
        </Field>
        <Field label={t("mfa.code")} hint={t("mfa.codeHint")}>
          <Input
            inputMode="numeric"
            autoComplete="one-time-code"
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value })}
            required
            ltr
          />
        </Field>
        <div className="form-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant={kind === "disable" ? "danger" : "primary"} loading={run.isPending}>
            {kind === "disable" ? t("mfa.disable") : t("mfa.newCodes")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function wrongPassword(error: unknown, message: string): unknown {
  return error instanceof ApiError && error.status === 403 ? new Error(message) : error;
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const text = codes.join("\n");
  function save() {
    const url = URL.createObjectURL(new Blob([`NOVARA\n\n${text}\n`], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "novara-recovery-codes.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <div className="stack">
      <p>{t("mfa.codesBody")}</p>
      <ol className="recovery-codes" dir="ltr" aria-label={t("mfa.codesTitle")}>
        {codes.map((c) => (
          <li key={c} className="mono">
            {c}
          </li>
        ))}
      </ol>
      <div className="row">
        <Button
          icon={<Copy size={16} />}
          onClick={() => void navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")), () => undefined)}
        >
          {t("common.copy")}
        </Button>
        <Button icon={<Download size={16} />} onClick={save}>
          {t("mfa.download")}
        </Button>
      </div>
      <div className="form-actions">
        <Button variant="primary" onClick={onDone}>
          {t("mfa.saved")}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- sessions

/** "Chrome on Windows" from a user-agent string; the raw string is in the tooltip. */
export function describeAgent(ua: string): string {
  const browser =
    /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Firefox\//.test(ua) ? "Firefox"
      : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "";
  const os =
    /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS"
      : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return [browser, os].filter(Boolean).join(" · ") || ua.slice(0, 40) || "—";
}

function SessionsCard() {
  const { t, date } = useI18n();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => unwrap(api.GET("/api/v1/me/sessions")) });
  const revoke = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/me/sessions/{session_id}", { params: { path: { session_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
  return (
    <Card title={t("account.sessions")}>
      {revoke.isError && <ErrorNotice error={revoke.error} />}
      <QueryView query={sessions}>
        {(list) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("account.device")}</th>
                  <th>{t("account.address")}</th>
                  <th>{t("account.since")}</th>
                  <th className="end">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((s) => (
                  <tr key={s.id}>
                    <td title={s.user_agent}>{describeAgent(s.user_agent)}</td>
                    <td className="mono">{s.ip_address || "—"}</td>
                    <td className="small">{date(s.created_at)}</td>
                    <td className="end">
                      {s.current ? (
                        <Badge tone="primary">{t("account.thisDevice")}</Badge>
                      ) : (
                        <Button size="sm" icon={<LogOut size={14} />} onClick={() => revoke.mutate(s.id)}>
                          {t("account.signOutDevice")}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
    </Card>
  );
}
