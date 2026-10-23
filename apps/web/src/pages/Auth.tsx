import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, unwrap } from "../api/client";
import { useAuth } from "../auth/Auth";
import { rememberOrg } from "../auth/Org";
import { MailCheck, Sparkle } from "lucide-react";
import { StarLattice, StarMark } from "../components/Art";
import { TopActions } from "../components/Layout";
import { Button, Card, ErrorNotice, Field, Input, Loading } from "../components/ui";
import { useI18n, useRoleLabel } from "../i18n/I18n";

/** Where to go after signing in, and the email to prefill (both set by the invitation page). */
type AuthState = { from?: string; email?: string; registered?: boolean; reset?: boolean } | null;

function AuthFrame({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="auth-split">
      <section className="auth-art">
        <StarLattice />
        <div className="brand">
          <StarMark size={34} />
          <span>{t("app.name")}</span>
        </div>
        <div>
          <h2>{t("auth.artTitle")}</h2>
          <p>{t("auth.artBody")}</p>
          <div className="auth-points">
            {(["auth.point1", "auth.point2", "auth.point3"] as const).map((k) => (
              <span key={k}>
                <Sparkle size={16} /> {t(k)}
              </span>
            ))}
          </div>
        </div>
        <span />
      </section>
      <div className="auth-form">
        <div className="auth-top">
          <TopActions />
        </div>
        <div className="auth-card">
          <Card>{children}</Card>
        </div>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { t } = useI18n();
  const { login, completeMfa } = useAuth();
  const navigate = useNavigate();
  const state = useLocation().state as AuthState;
  const [email, setEmail] = useState(state?.email ?? "");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mfaToken) {
        await completeMfa(mfaToken, code.trim());
      } else {
        const pending = await login(email, password);
        if (pending) {
          setMfaToken(pending.mfaToken);
          return;
        }
      }
      navigate(state?.from ?? "/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (mfaToken) {
    return (
      <AuthFrame>
        <form className="stack" onSubmit={submit} noValidate>
          <h1>{t("mfa.stepTitle")}</h1>
          <p className="muted">{t("mfa.stepBody")}</p>
          {error !== null && <ErrorNotice error={error} />}
          <Field label={t("mfa.code")} hint={t("mfa.codeHint")}>
            <Input
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              ltr
            />
          </Field>
          <Button type="submit" variant="primary" loading={busy}>
            {t("mfa.verify")}
          </Button>
          <button
            type="button"
            className="link-btn small"
            onClick={() => {
              setMfaToken(null);
              setCode("");
              setPassword("");
              setError(null);
            }}
          >
            {t("mfa.back")}
          </button>
        </form>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame>
      <form className="stack" onSubmit={submit} noValidate>
        <h1>{t("auth.welcome")}</h1>
        {state?.registered && <div className="notice notice-success">{t("auth.registered")}</div>}
        {state?.reset && <div className="notice notice-success">{t("reset.done")}</div>}
        {error !== null && <ErrorNotice error={error} />}
        <Field label={t("common.email")}>
          <Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required ltr />
        </Field>
        <Field label={t("auth.password")}>
          <Input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            ltr
          />
        </Field>
        <Link to="/forgot-password" className="small" state={{ email }}>
          {t("auth.forgot")}
        </Link>
        <Button type="submit" variant="primary" loading={busy}>
          {t("auth.login")}
        </Button>
        <p className="muted small">
          {t("auth.noAccount")} <Link to="/register" state={state}>{t("auth.register")}</Link>
        </p>
      </form>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const state = useLocation().state as AuthState;
  const [form, setForm] = useState({ full_name: "", email: state?.email ?? "", password: "" });
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unwrap(api.POST("/api/v1/auth/register", { body: { ...form, locale: lang } }));
      navigate("/login", { state: { registered: true, from: state?.from, email: state?.email } });
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, t("common.error")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame>
      <form className="stack" onSubmit={submit} noValidate>
        <h1>{t("auth.register")}</h1>
        {error && !Object.keys(error.fields).length && <ErrorNotice error={error} />}
        <Field label={t("auth.fullName")} error={error?.fields["full_name"]}>
          <Input autoComplete="name" value={form.full_name} onChange={set("full_name")} />
        </Field>
        <Field label={t("common.email")} error={error?.fields["email"]}>
          <Input type="email" autoComplete="email" value={form.email} onChange={set("email")} required ltr />
        </Field>
        <Field label={t("auth.password")} hint={t("auth.passwordHint")} error={error?.fields["password"]}>
          <Input type="password" autoComplete="new-password" value={form.password} onChange={set("password")} required ltr />
        </Field>
        <Button type="submit" variant="primary" loading={busy}>
          {t("auth.register")}
        </Button>
        <p className="muted small">
          {t("auth.haveAccount")} <Link to="/login" state={state}>{t("auth.login")}</Link>
        </p>
      </form>
    </AuthFrame>
  );
}

export function ForgotPasswordPage() {
  const { t } = useI18n();
  const state = useLocation().state as AuthState;
  const [email, setEmail] = useState(state?.email ?? "");
  const send = useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/auth/password-reset", { body: { email } })) });
  const badEmail = send.error instanceof ApiError && Boolean(send.error.fields["email"]);
  function submit(e: FormEvent) {
    e.preventDefault();
    send.mutate();
  }
  return (
    <AuthFrame>
      <form className="stack" onSubmit={submit} noValidate>
        <h1>{t("forgot.title")}</h1>
        {send.isSuccess ? (
          <div className="notice notice-success">
            <MailCheck size={18} aria-hidden /> {t("forgot.sent")}
          </div>
        ) : (
          <>
            <p className="muted">{t("forgot.body")}</p>
            {send.isError && !badEmail && <ErrorNotice error={send.error} />}
            <Field label={t("common.email")} error={badEmail ? t("forgot.badEmail") : undefined}>
              <Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required ltr />
            </Field>
            <Button type="submit" variant="primary" loading={send.isPending}>
              {t("forgot.send")}
            </Button>
          </>
        )}
        <Link to="/login" className="small">
          {t("forgot.backToLogin")}
        </Link>
      </form>
    </AuthFrame>
  );
}

export function ResetPasswordPage() {
  const { t } = useI18n();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const reset = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/auth/password-reset/confirm", { body: { token, password } })),
    onSuccess: async () => {
      // Every session was just revoked, this browser's included.
      if (user) await logout();
      navigate("/login", { replace: true, state: { reset: true } });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setMismatch(password !== confirm);
    if (password === confirm) reset.mutate();
  }
  const fieldError = reset.error instanceof ApiError ? reset.error.fields["password"] : undefined;
  return (
    <AuthFrame>
      <form className="stack" onSubmit={submit} noValidate>
        <h1>{t("reset.title")}</h1>
        {!token ? (
          <ErrorNotice error={new Error(t("reset.badLink"))} />
        ) : (
          <>
            {reset.isError && !fieldError && (
              <ErrorNotice error={reset.error instanceof ApiError && reset.error.status === 400 ? new Error(t("reset.badLink")) : reset.error} />
            )}
            <Field label={t("reset.new")} hint={t("auth.passwordHint")} error={fieldError}>
              <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required ltr />
            </Field>
            <Field label={t("reset.confirm")} error={mismatch ? t("reset.mismatch") : undefined}>
              <Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required ltr />
            </Field>
            <p className="muted small">{t("reset.signsOut")}</p>
            <Button type="submit" variant="primary" loading={reset.isPending}>
              {t("reset.save")}
            </Button>
          </>
        )}
        <Link to="/forgot-password" className="small">
          {t("reset.another")}
        </Link>
      </form>
    </AuthFrame>
  );
}

export function VerifyEmailPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  // A query (not an effect) so the single-use link is sent once, even when React mounts twice.
  const verify = useQuery({
    queryKey: ["verify-email", token],
    queryFn: async () => {
      const result = await unwrap(api.POST("/api/v1/auth/verify-email", { body: { token } }));
      await qc.invalidateQueries({ queryKey: ["me"] });
      return result;
    },
    enabled: token.length > 0,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  return (
    <AuthFrame>
      <div className="stack">
        <h1>{t("verify.title")}</h1>
        {!token || verify.isError ? (
          <ErrorNotice error={new Error(t("verify.badLink"))} />
        ) : verify.isPending ? (
          <Loading />
        ) : (
          <div className="notice notice-success">
            <MailCheck size={18} aria-hidden /> {t("verify.done")}
          </div>
        )}
        <Link to={user ? "/" : "/login"} className="btn btn-primary">
          {user ? t("verify.continue") : t("auth.login")}
        </Link>
      </div>
    </AuthFrame>
  );
}

export function InvitePage() {
  const { t } = useI18n();
  const roleLabel = useRoleLabel();
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const info = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => unwrap(api.GET("/api/v1/invitations/{token}", { params: { path: { token } } })),
    enabled: token.length > 0,
    retry: false,
  });
  const accept = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/invitations/{token}/accept", { params: { path: { token } } })),
    onSuccess: async (org) => {
      rememberOrg(org.id);
      await qc.invalidateQueries({ queryKey: ["orgs"] });
      await qc.invalidateQueries({ queryKey: ["me"] });
      navigate(`/o/${org.id}`, { replace: true });
    },
  });
  const here = location.pathname + location.search;

  return (
    <AuthFrame>
      <div className="stack">
        <h1>{t("invite.title")}</h1>
        {!token || info.isError ? (
          <ErrorNotice error={new Error(t("invite.badLink"))} />
        ) : info.isPending ? (
          <Loading />
        ) : (
          <>
            <p>
              {t("invite.body", { org: info.data.organization_name, role: roleLabel(info.data.role, info.data.role_name) })}
            </p>
            <p className="muted small">
              {t("invite.for")} <bdi className="mono">{info.data.email}</bdi>
            </p>
            {accept.isError && <ErrorNotice error={accept.error} />}
            {!user ? (
              <>
                <Link to="/login" state={{ from: here, email: info.data.email }} className="btn btn-primary">
                  {t("invite.signIn")}
                </Link>
                <p className="muted small">
                  {t("auth.noAccount")}{" "}
                  <Link to="/register" state={{ from: here, email: info.data.email }}>
                    {t("auth.register")}
                  </Link>
                </p>
              </>
            ) : user.email !== info.data.email ? (
              <>
                <div className="notice notice-info">{t("invite.otherAccount", { email: user.email })}</div>
                <Button onClick={() => void logout()}>{t("auth.logout")}</Button>
              </>
            ) : (
              <Button variant="primary" loading={accept.isPending} onClick={() => accept.mutate()}>
                {t("invite.accept")}
              </Button>
            )}
          </>
        )}
      </div>
    </AuthFrame>
  );
}
