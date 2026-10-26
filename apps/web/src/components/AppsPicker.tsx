import { Check } from "lucide-react";
import { useI18n } from "../i18n/I18n";
import { APP_ITEMS, NAV, type NavItem } from "./nav";
import { Button } from "./ui";

/** The apps on the menu, as cards to tick, grouped as the menu groups them: the first-run setup and the organization's
 *  settings choose with it. */
export function AppsPicker({ value, onChange, suggested }: { value: string[]; onChange: (v: string[]) => void; suggested?: string[] }) {
  const { t } = useI18n();
  const groups = NAV.map((g) => ({ title: g.title, items: g.items.filter((i) => i.desc) })).filter((g) => g.items.length);
  const all = APP_ITEMS.map((i) => i.to);
  const toggle = (item: NavItem, on: boolean) => {
    const set = new Set(value);
    if (on) set.add(item.to);
    else set.delete(item.to);
    onChange(all.filter((a) => set.has(a)));
  };
  return (
    <div className="stack">
      <div className="row-between setup-apps-bar">
        <span className="small" role="status">{t("setup.apps.count", { n: value.length })}</span>
        <span className="row">
          {suggested && suggested.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => onChange(suggested)}>{t("setup.apps.suggested")}</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => onChange(all)}>{t("setup.apps.all")}</Button>
        </span>
      </div>
      {groups.map((g) => (
        <fieldset key={g.title} className="choice-group stack-sm">
          {g.title && <legend className="setup-group">{t(g.title)}</legend>}
          <div className="choice-grid choice-grid-sm">
            {g.items.map((i) => {
              const on = value.includes(i.to);
              return (
                <label key={i.to} className={`choice choice-sm hue-${i.hue} ${on ? "selected" : ""}`}>
                  <input type="checkbox" className="sr-only" checked={on} onChange={(e) => toggle(i, e.target.checked)} />
                  <span className="app-icon"><i.icon size={18} aria-hidden /></span>
                  <span className="choice-text">
                    <strong>{t(i.label)}</strong>
                    <span className="muted small">{t(i.desc!)}</span>
                  </span>
                  <span className="choice-check" aria-hidden><Check size={14} /></span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
