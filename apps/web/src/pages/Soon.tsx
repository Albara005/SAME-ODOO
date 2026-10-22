import { Construction } from "lucide-react";
import { Card, PageHeader } from "../components/ui";
import { useI18n } from "../i18n/I18n";

/** Screens whose API exists but whose UI comes in a later step. */
export function SoonPage() {
  const { lang } = useI18n();
  return (
    <div className="stack">
      <PageHeader title={lang === "ar" ? "قيد البناء" : "Coming next"} />
      <Card>
        <div className="row">
          <Construction size={20} aria-hidden />
          <p>
            {lang === "ar"
              ? "هذه الشاشة قيد البناء. الوظيفة نفسها متاحة عبر الـ API."
              : "This screen is being built. The feature is already available through the API."}
          </p>
        </div>
      </Card>
    </div>
  );
}
