import { visibleNav } from "./nav";

const labels = (perms: string[]) => visibleNav((p) => perms.includes(p)).flatMap((g) => g.items.map((i) => i.label));

it("hides what the role can't use", () => {
  const viewer = labels(["files.read"]);
  expect(viewer).toContain("nav.home");
  expect(viewer).not.toContain("nav.invoices");
  expect(viewer).not.toContain("nav.members");
  const sales = labels(["customers.read", "invoices.read", "sales.read", "ai.use", "members.read"]);
  expect(sales).toEqual(expect.arrayContaining(["nav.customers", "nav.invoices", "nav.sales", "nav.reports", "nav.assistant"]));
  expect(sales).not.toContain("nav.audit");
});

it("drops empty groups", () => {
  const groups = visibleNav(() => false);
  expect(groups.every((g) => g.items.length > 0)).toBe(true);
});
