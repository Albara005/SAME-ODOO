import { describe, expect, it } from "vitest";
import { usd } from "./AiSettings";

describe("usd", () => {
  it("shows cents and keeps significant sub-cent digits", () => {
    expect(usd("50.0000")).toBe("50.00");
    expect(usd("0.0042")).toBe("0.0042");
    expect(usd("0.1000")).toBe("0.10");
    expect(usd("12")).toBe("12.00");
  });
});
