import { computeTotals, formatUnits, parseDec, subtractDec, sumDec } from "./decimal";

const line = (quantity: string, unitPrice: string, taxRate = "0", discount = "0", taxKey: string | null = taxRate !== "0" ? "vat" : null) =>
  ({ quantity, unitPrice, discount, taxKey, taxRate });

describe("computeTotals mirrors the backend", () => {
  it("rounds tax once per rate, not per line (0.30 at 15% -> 0.05)", () => {
    const t = computeTotals([line("1", "0.10", "15"), line("1", "0.10", "15"), line("1", "0.10", "15")], "SAR");
    expect([formatUnits(t.subtotal, 2), formatUnits(t.tax, 2), formatUnits(t.total, 2)]).toEqual(["0.30", "0.05", "0.35"]);
  });
  it("handles 3-decimal currencies", () => {
    const t = computeTotals([line("3", "1.333", "5")], "KWD");
    expect([formatUnits(t.subtotal, 3), formatUnits(t.tax, 3), formatUnits(t.total, 3)]).toEqual(["3.999", "0.200", "4.199"]);
  });
  it("applies discounts and rounds half up", () => {
    const t = computeTotals([line("2", "250", "0", "10")], "SAR");
    expect(formatUnits(t.subtotal, 2)).toBe("450.00");
    expect(formatUnits(computeTotals([line("1", "10.005")], "SAR").subtotal, 2)).toBe("10.01");
  });
  it("matches the backend's full sales-cycle invoice", () => {
    const t = computeTotals([line("8", "500", "15"), line("1", "100", "15")], "SAR");
    expect(formatUnits(t.total, 2)).toBe("4715.00");
  });
  it("marks unparseable lines", () => {
    const t = computeTotals([line("x", "1")], "SAR");
    expect(t.valid).toBe(false);
    expect(t.lines[0]).toBeNull();
  });
});

it("parses and formats", () => {
  expect(parseDec("-1.50")).toEqual({ n: -150n, scale: 2 });
  expect(formatUnits(-5n, 2)).toBe("-0.05");
});

it("subtracts quantities exactly", () => {
  expect(subtractDec("10", "2.5")).toBe("7.5");
  expect(subtractDec("3.0000", "3")).toBe("0");
  expect(subtractDec("1", "1.25")).toBe("-0.25");
  expect(subtractDec("5", "x")).toBe("0");
});

describe("sumDec", () => {
  it("adds exactly, ignoring blanks", () => {
    expect(sumDec(["0.1", "0.2", "", "abc"], 2)).toBe("0.30");
    expect(sumDec(["1200", "33.335"], 2)).toBe("1233.34");
    expect(sumDec(["-5", "2.5"], 3)).toBe("-2.500");
    expect(sumDec([], 2)).toBe("0.00");
  });
});
