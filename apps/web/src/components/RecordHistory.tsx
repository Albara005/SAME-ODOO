import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { api, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import type { MessageKey } from "../i18n/en";
import { initialsOf, locale } from "../lib/format";

type Kind = "invoice" | "sales_order" | "purchase_order" | "vendor_bill" | "customer" | "employee" | "pay_run";

/** "3 hours ago", or the date and time for anything older than a week. */
function when(iso: string, lang: "ar" | "en"): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((then - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale(lang), { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 7 * 86400) return rtf.format(Math.round(seconds / 86400), "day");
  return new Intl.DateTimeFormat(locale(lang), { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

/** What happened to a record and who did it, newest first (the record's own trail in the audit log). `version` is
 *  anything that changes when the record does (its status...), so the history follows it. */
export function RecordHistory({ kind, id, version = "" }: { kind: Kind; id: string; version?: string }) {
  const { t, lang } = useI18n();
  const { org } = useOrg();
  const history = useQuery({
    queryKey: ["history", org.id, kind, id, version],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/history/{target_type}/{target_id}", {
      params: { path: { org_id: org.id, target_type: kind, target_id: id } },
    })),
  });
  if (!history.data?.length) return null;
  const label = (action: string) => {
    const key = `audit.a.${action}` as MessageKey;
    return t(key) === key ? action : t(key);
  };
  return (
    <section className="card card-tight history no-print" aria-labelledby={`history-${id}`}>
      <h2 id={`history-${id}`} className="history-title"><History size={16} aria-hidden /> {t("history.title")}</h2>
      <ol className="timeline">
        {history.data.map((h) => (
          <li key={h.id}>
            <span className="avatar avatar-sm" aria-hidden>{initialsOf(h.actor_name ?? "•")}</span>
            <span className="timeline-body">
              <span><strong dir="auto">{h.actor_name ?? t("history.system")}</strong> · {label(h.action)}</span>
              <time className="muted small" dateTime={h.created_at}>{when(h.created_at, lang)}</time>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
