import { expect, test } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

test("mobile: the menu opens as a drawer", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("mobile"));
  await createOrg(page, "Mobile Co");
  const nav = page.getByRole("complementary");
  await expect(nav.getByRole("link", { name: "العملاء" })).not.toBeInViewport();
  await page.getByRole("button", { name: "القائمة" }).click();
  await expect(nav.getByRole("link", { name: "العملاء" })).toBeInViewport();
  await page.screenshot({ path: "test-results/screens/mobile-menu-ar.png" });
  await nav.getByRole("link", { name: "العملاء" }).click();
  await expect(page.getByRole("heading", { name: "العملاء" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "العملاء" })).not.toBeInViewport();
});
