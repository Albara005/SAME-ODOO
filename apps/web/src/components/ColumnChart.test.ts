import { describe, expect, it } from "vitest";
import { niceTicks } from "./ColumnChart";

describe("niceTicks", () => {
  it("steps on clean numbers and covers the maximum", () => {
    expect(niceTicks(4000)).toEqual([0, 1000, 2000, 3000, 4000]);
    expect(niceTicks(4600)).toEqual([0, 2000, 4000, 6000]);
    expect(niceTicks(9)).toEqual([0, 2.5, 5, 7.5, 10]);
    expect(niceTicks(0)).toEqual([0]);
  });
});
