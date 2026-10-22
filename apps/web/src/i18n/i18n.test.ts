import { ar } from "./ar";
import { en } from "./en";

it("Arabic and English have exactly the same keys and placeholders", () => {
  expect(Object.keys(ar).sort()).toEqual(Object.keys(en).sort());
  for (const key of Object.keys(en) as (keyof typeof en)[]) {
    const vars = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
    expect(vars(ar[key]), key).toEqual(vars(en[key]));
    expect(ar[key].trim(), key).not.toBe("");
  }
});
