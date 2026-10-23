import { formatAmount, formatDate, formatMoney, sumAmounts } from "./format";

describe("formatAmount", () => {
  it("groups large amounts exactly, without float rounding", () => {
    expect(formatAmount("12345678901234567.89", "en")).toBe("12,345,678,901,234,567.89");
    expect(formatAmount("0.10", "en")).toBe("0.10");
    expect(formatAmount("-1150.00", "en")).toBe("-1,150.00");
  });
  it("keeps Latin digits in Arabic", () => {
    expect(formatAmount("1150.005", "ar")).toMatch(/^1[,٬]150[.٫]005$/);
  });
  it("leaves non-numbers untouched", () => {
    expect(formatAmount("n/a", "en")).toBe("n/a");
  });
});

describe("formatMoney", () => {
  it("uses Arabic currency labels in Arabic", () => {
    expect(formatMoney("10.00", "SAR", "ar")).toContain("ر.س");
    expect(formatMoney("10.00", "SAR", "en")).toBe("10.00 SAR");
  });
});

describe("formatDate", () => {
  it("never shifts a calendar date across timezones and uses the Gregorian calendar", () => {
    expect(formatDate("2026-03-01", "en")).toBe("1 Mar 2026");
    expect(formatDate("2026-03-01", "ar")).toContain("2026");
  });
});

describe("sumAmounts", () => {
  it("adds money exactly", () => {
    expect(sumAmounts(["0.10", "0.20"])).toBe("0.30");
    expect(sumAmounts(["1000.005", "-0.010"])).toBe("999.995");
    expect(sumAmounts([])).toBe("0");
    expect(sumAmounts(["-5.00", "2.50"])).toBe("-2.50");
  });
});
