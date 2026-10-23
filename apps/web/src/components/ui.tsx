import { Loader2, X } from "lucide-react";
import {
  cloneElement,
  createContext,
  forwardRef,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { ApiError } from "../api/client";
import { useI18n } from "../i18n/I18n";

// ---------------------------------------------------------------- buttons

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "md" | "sm";
  loading?: boolean;
  icon?: ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading = false, icon, children, className = "", disabled, type = "button", ...rest },
  ref,
) {
  const cls = ["btn", variant !== "secondary" ? `btn-${variant}` : "", size === "sm" ? "btn-sm" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button ref={ref} type={type} className={cls} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <Loader2 size={16} className="spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export function IconButton({ label, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button type="button" className="btn btn-ghost icon-btn" aria-label={label} title={label} {...rest}>
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- form fields

type FieldProps = {
  label: string;
  error?: string;
  hint?: string;
  optional?: boolean;
  children: ReactElement;
};

/** Wires label, hint and error to the control with ids and aria attributes. */
export function Field({ label, error, hint, optional, children }: FieldProps) {
  const id = useId();
  const { t } = useI18n();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy,
      })
    : children;
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {optional && <span className="muted small"> ({t("common.optional")})</span>}
      </label>
      {control}
      {hint && !error && (
        <span id={`${id}-hint`} className="hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={`${id}-error`} className="error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { ltr?: boolean }>(function Input(
  { className = "", ltr, ...rest },
  ref,
) {
  return <input ref={ref} className={`input ${ltr ? "ltr" : ""} ${className}`} {...rest} />;
});

export function Select({ className = "", children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`select ${className}`} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className = "", ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`textarea ${className}`} {...rest} />;
}

export function Checkbox({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  const id = useId();
  return (
    <div className="check">
      <input id={id} type="checkbox" {...rest} />
      <label htmlFor={id}>{label}</label>
    </div>
  );
}

// ---------------------------------------------------------------- layout pieces

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

export function Card({ children, title, actions, tight }: { children: ReactNode; title?: string; actions?: ReactNode; tight?: boolean }) {
  return (
    <section className={`card ${tight ? "card-tight" : ""}`}>
      {(title || actions) && (
        <div className="row-between" style={{ marginBlockEnd: "var(--space-4)" }}>
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "primary";

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone !== "neutral" ? `badge-${tone}` : ""}`}>{children}</span>;
}

export function Loading() {
  const { t } = useI18n();
  return (
    <div className="loading" role="status">
      <Loader2 size={18} className="spin" aria-hidden />
      {t("common.loading")}
    </div>
  );
}

export function Empty({ children }: { children?: ReactNode }) {
  const { t } = useI18n();
  return <div className="empty">{children ?? t("common.none")}</div>;
}

/** An error whose message is already written for the reader (and translated): ErrorNotice shows it as is. */
export class Notice extends Error {}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  const message =
    error instanceof Notice
      ? error.message
      : error instanceof ApiError
        ? error.status === 403
          ? t("common.notAllowed")
          : error.message
        : t("common.error");
  return (
    <div className="notice notice-error" role="alert">
      <span className="grow">{message}</span>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      )}
    </div>
  );
}

/** A query's loading/error/success states in one place. */
export function QueryView<T>({
  query,
  children,
}: {
  query: { isPending: boolean; isError: boolean; error: unknown; data: T | undefined; refetch: () => unknown };
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <Loading />;
  if (query.isError) return <ErrorNotice error={query.error} onRetry={() => query.refetch()} />;
  return <>{children(query.data as T)}</>;
}

// ---------------------------------------------------------------- dialog

export function Dialog({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { t } = useI18n();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      if (typeof el.showModal === "function") el.showModal();
      else el.setAttribute("open", "");
    } else if (!open && el.open) {
      if (typeof el.close === "function") el.close();
      else el.removeAttribute("open");
    }
  }, [open]);
  return (
    <dialog ref={ref} className={`dialog ${wide ? "dialog-wide" : ""}`} onClose={onClose} onCancel={onClose} aria-labelledby={titleId}>
      {open && (
        <>
          <div className="dialog-head">
            <h2 id={titleId}>{title}</h2>
            <IconButton label={t("common.close")} onClick={onClose}>
              <X size={18} />
            </IconButton>
          </div>
          <div className="dialog-body">{children}</div>
        </>
      )}
    </dialog>
  );
}

// ---------------------------------------------------------------- toasts

type Toast = { id: number; text: string; tone: "info" | "error" };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all, { id, text, tone }]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((x) => (
          <div key={x.id} className={`toast ${x.tone === "error" ? "toast-error" : ""}`} role={x.tone === "error" ? "alert" : "status"}>
            {x.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

/** Error message for a toast from anything a mutation can throw. */
export function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}
