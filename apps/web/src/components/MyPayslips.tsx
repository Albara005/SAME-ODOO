import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { useOrg } from "../auth/Org";
import { useI18n } from "../i18n/I18n";
import { hm } from "../lib/format";
import { Card } from "./ui";

/** The signed-in member's own payslips, once their month is posted. Nothing shows until there is one. */
export function MyPayslips() {
  const { t, money } = useI18n();
  const { org } = useOrg();
  const [open, setOpen] = useState<string | null>(null);
  const slips = useQuery({
    queryKey: ["payroll", org.id, "mine"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/payroll/my-payslips", { params: { path: { org_id: org.id } } })),
  });
  if (!slips.data?.length) return null;
  return (
    <Card title={t("payroll.mySlips")}>
      <ul className="stack-sm" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {slips.data.map((s) => (
          <li key={s.id}>
            <div className="row-between">
              <button type="button" className="link-btn" aria-expanded={open === s.id} onClick={() => setOpen(open === s.id ? null : s.id)}>
                {t("payroll.slipOf", { month: s.month })}
              </button>
              <strong className="num">{money(s.net, s.currency)}</strong>
            </div>
            {open === s.id && (
              <dl className="kv small">
                {s.lines.map((l, i) => (
                  <div key={i} style={{ display: "contents" }}>
                    <dt>{l.overtime_minutes ? t("payroll.overtimeLine", { h: hm(l.overtime_minutes) }) : l.name}</dt>
                    <dd className="num">{l.kind === "earning" ? "" : "−"}{money(l.amount, s.currency)}</dd>
                  </div>
                ))}
                <dt><strong>{t("payroll.net")}</strong></dt>
                <dd className="num"><strong>{money(s.net, s.currency)}</strong></dd>
                {s.end_of_service_accrued !== "0.00" && (
                  <><dt className="muted">{t("payroll.eosAccrued")}</dt><dd className="num muted">{money(s.end_of_service_accrued, s.currency)}</dd></>
                )}
              </dl>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
