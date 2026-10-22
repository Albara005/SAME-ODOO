import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, unwrap } from "../api/client";
import { useAuth } from "../auth/Auth";
import { BrandMark, TopActions } from "../components/Layout";
import { Button, Card, ErrorNotice, Field, Input } from "../components/ui";
import { useI18n } from "../i18n/I18n";

function AuthFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth-page">
      <div className="auth-top">
        <TopActions />
      </div>
      <div className="auth-card">
        <BrandMark />
        <Card>{children}</Card>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { t } = useI18n();
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const registered = (location.state as { registered?: boolean } | null)?.registered;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame>
      <form className="stack" onSubmit={submit} noValidate>
        <h1>{t("auth.welcome")}</h1>
        {registered && <div className="notice notice-success">{t("auth.registered")}</div>}
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
        <Button type="submit" variant="primary" loading={busy}>
          {t("auth.login")}
        </Button>
        <p className="muted small">
          {t("auth.noAccount")} <Link to="/register">{t("auth.register")}</Link>
        </p>
      </form>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unwrap(api.POST("/api/v1/auth/register", { body: { ...form, locale: lang } }));
      navigate("/login", { state: { registered: true } });
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
          {t("auth.haveAccount")} <Link to="/login">{t("auth.login")}</Link>
        </p>
      </form>
    </AuthFrame>
  );
}
