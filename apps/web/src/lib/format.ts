export type Lang = "ar" | "en";

/** Arabic UI with Latin digits and the Gregorian calendar - what invoices and tax documents use. */
export function locale(lang: Lang): string {
  return lang === "ar" ? "ar-SA-u-nu-latn-ca-gregory" : "en-GB";
}

const AR_CURRENCY: Record<string, string> = {
  SAR: "ر.س", AED: "د.إ", KWD: "د.ك", BHD: "د.ب", OMR: "ر.ع", QAR: "ر.ق", EGP: "ج.م", JOD: "د.أ",
};

/**
 * Format a decimal string from the API without ever converting it to a float:
 * the integer part is grouped with BigInt, the fraction is kept digit for digit.
 */
export function formatAmount(value: string, lang: Lang): string {
  const trimmed = value.trim();
  if (!/^-?\d+(\.\d+)?$/.test(trimmed)) return value;
  const negative = trimmed.startsWith("-");
  const [intPart = "0", frac] = trimmed.replace("-", "").split(".");
  const nf = new Intl.NumberFormat(locale(lang));
  const decimal = nf.formatToParts(1.5).find((p) => p.type === "decimal")?.value ?? ".";
  const grouped = nf.format(BigInt(intPart));
  return `${negative ? "-" : ""}${grouped}${frac !== undefined ? decimal + frac : ""}`;
}

export function formatMoney(value: string, currency: string, lang: Lang): string {
  const label = lang === "ar" ? (AR_CURRENCY[currency] ?? currency) : currency;
  return `${formatAmount(value, lang)} ${label}`;
}

export function formatDate(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "—";
  // Plain dates (YYYY-MM-DD) are calendar dates, not instants: format them in UTC so no timezone shifts the day.
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso);
  const d = new Date(isDateOnly ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale(lang), {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(isDateOnly ? { timeZone: "UTC" } : { hour: "2-digit", minute: "2-digit" }),
  }).format(d);
}

/** Minutes as hours and minutes: 510 -> "8:30". */
export function hm(minutes: number): string {
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Exact sum of decimal strings (BigInt), e.g. ["0.10", "0.20"] -> "0.30" - never 0.30000000000000004. */
export function sumAmounts(values: string[]): string {
  const scale = Math.max(0, ...values.map((v) => (v.split(".")[1] ?? "").length));
  const total = values.reduce((acc, v) => {
    const negative = v.trim().startsWith("-");
    const [i = "0", f = ""] = v.trim().replace("-", "").split(".");
    const units = BigInt(i + f.padEnd(scale, "0"));
    return acc + (negative ? -units : units);
  }, 0n);
  const negative = total < 0n;
  const digits = (negative ? -total : total).toString().padStart(scale + 1, "0");
  const out = scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits;
  return negative ? `-${out}` : out;
}

export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** The fiscal year (first and last day, YYYY-MM-DD) containing `day`, for a year starting in `startMonth`. */
export function fiscalYear(startMonth: number, day: string): { start: string; end: string } {
  const [y, m] = day.split("-").map(Number) as [number, number];
  const year = m >= startMonth ? y : y - 1;
  const start = `${year}-${String(startMonth).padStart(2, "0")}-01`;
  const next = new Date(Date.UTC(year + 1, startMonth - 1, 1) - 86_400_000);
  return { start, end: next.toISOString().slice(0, 10) };
}

/** Two letters for a name's avatar ("مؤسسة الأفق" -> "مأ"), the article "ال" skipped. */
export const initialsOf = (name: string) =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w.replace(/^ال/, "")[0] ?? "").join("").toUpperCase() || "•";
