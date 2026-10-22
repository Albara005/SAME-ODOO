/**
 * Exact decimal arithmetic on BigInt, mirroring the backend's money rules (novara/money.py,
 * invoicing/service.py) so the editor can show live totals. The server's figures are final.
 */

export const CURRENCY_DIGITS: Record<string, number> = {
  SAR: 2, AED: 2, QAR: 2, EGP: 2, MAD: 2, USD: 2, EUR: 2, GBP: 2, TRY: 2,
  KWD: 3, BHD: 3, OMR: 3, JOD: 3, IQD: 3, LYD: 3, TND: 3, JPY: 0,
};

export const digitsFor = (currency: string) => CURRENCY_DIGITS[currency] ?? 2;

/** value = n / 10^scale */
type Dec = { n: bigint; scale: number };

export function parseDec(text: string): Dec | null {
  const s = text.trim();
  if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
  const negative = s.startsWith("-");
  const [i = "0", f = ""] = s.replace("-", "").split(".");
  const n = BigInt(i + f);
  return { n: negative ? -n : n, scale: f.length };
}

const pow10 = (k: number) => 10n ** BigInt(k);

/** Round a rational num/den to `digits` decimals, half away from zero (Python's ROUND_HALF_UP). */
function roundRational(num: bigint, den: bigint, digits: number): bigint {
  const scaled = num * pow10(digits);
  const negative = scaled < 0n !== den < 0n;
  const a = scaled < 0n ? -scaled : scaled;
  const b = den < 0n ? -den : den;
  const q = (a * 2n + b) / (2n * b); // round half up on the magnitude
  return negative ? -q : q;
}

export function formatUnits(units: bigint, digits: number): string {
  const negative = units < 0n;
  const s = (negative ? -units : units).toString().padStart(digits + 1, "0");
  const out = digits ? `${s.slice(0, -digits)}.${s.slice(-digits)}` : s;
  return negative ? `-${out}` : out;
}

export type LineCalc = { quantity: string; unitPrice: string; discount: string; taxKey: string | null; taxRate: string };

export type Totals = { lines: (bigint | null)[]; subtotal: bigint; tax: bigint; total: bigint; digits: number; valid: boolean };

/** Line net = round(qty x price x (1 - discount/100)); tax = round(sum of nets per rate x rate/100) per rate. */
export function computeTotals(lines: LineCalc[], currency: string): Totals {
  const digits = digitsFor(currency);
  const nets: (bigint | null)[] = [];
  const groups = new Map<string, { base: bigint; rate: Dec }>();
  let valid = true;
  for (const l of lines) {
    const q = parseDec(l.quantity);
    const p = parseDec(l.unitPrice);
    const d = parseDec(l.discount || "0");
    if (!q || !p || !d) {
      nets.push(null);
      valid = false;
      continue;
    }
    // qty*price*(100-disc)/100, as one rational number
    const num = q.n * p.n * (100n * pow10(d.scale) - d.n);
    const den = pow10(q.scale + p.scale + d.scale) * 100n;
    const net = roundRational(num, den, digits);
    nets.push(net);
    if (l.taxKey) {
      const rate = parseDec(l.taxRate || "0");
      if (!rate) continue;
      const key = `${l.taxKey}|${l.taxRate}`;
      const g = groups.get(key) ?? { base: 0n, rate };
      g.base += net;
      groups.set(key, g);
    }
  }
  const subtotal = nets.reduce<bigint>((a, b) => a + (b ?? 0n), 0n);
  let tax = 0n;
  for (const { base, rate } of groups.values()) {
    // base is in minor units: base/10^digits * rate/10^rate.scale / 100, rounded to digits
    tax += roundRational(base * rate.n, pow10(digits + rate.scale) * 100n, digits);
  }
  return { lines: nets, subtotal, tax, total: subtotal + tax, digits, valid };
}

/** True when `text` is a valid decimal greater than zero. */
export function isPositive(text: string): boolean {
  const d = parseDec(text);
  return d !== null && d.n > 0n;
}

/** True when `text` is a valid decimal equal to zero ("0", "0.00", "-0.000"). */
export function isZero(text: string): boolean {
  const d = parseDec(text);
  return d !== null && d.n === 0n;
}
