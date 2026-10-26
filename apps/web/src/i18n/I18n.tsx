import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { formatAmount, formatDate, formatMoney, type Lang } from "../lib/format";
import { ar } from "./ar";
import { en, type MessageKey } from "./en";

const dictionaries: Record<Lang, Record<MessageKey, string>> = { ar, en };
const LANG_KEY = "novara.lang";

type I18n = {
  lang: Lang;
  dir: "rtl" | "ltr";
  setLang: (lang: Lang) => void;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
  money: (value: string, currency: string) => string;
  amount: (value: string) => string;
  date: (iso: string | null | undefined) => string;
};

const Ctx = createContext<I18n | null>(null);

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === "ar" || saved === "en") return saved;
  } catch {
    /* ignore */
  }
  return "ar";
}

export function I18nProvider({ children, lang: fixed }: { children: ReactNode; lang?: Lang }) {
  const [lang, setLangState] = useState<Lang>(fixed ?? initialLang);
  const dir = lang === "ar" ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = dir;
  }, [lang, dir]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(LANG_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<I18n>(() => {
    const dict = dictionaries[lang];
    return {
      lang,
      dir,
      setLang,
      t: (key, vars) => {
        let text = dict[key] ?? key;
        for (const [k, v] of Object.entries(vars ?? {})) text = text.replaceAll(`{${k}}`, String(v));
        return text;
      },
      money: (value, currency) => formatMoney(value, currency, lang),
      amount: (value) => formatAmount(value, lang),
      date: (iso) => formatDate(iso, lang),
    };
  }, [lang, dir, setLang]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

const SYSTEM_ROLES = ["owner", "admin", "accountant", "sales", "warehouse", "developer", "member", "viewer"];

/** System roles have translated names; custom roles show the name their organization gave them. */
export function useRoleLabel() {
  const { t } = useI18n();
  return (key: string, name?: string) => (SYSTEM_ROLES.includes(key) ? t(`role.${key}` as MessageKey) : (name ?? key));
}
