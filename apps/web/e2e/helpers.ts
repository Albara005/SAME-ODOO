import { expect, type Page } from "@playwright/test";

export const PASSWORD = "e2e-password-123";

export function uniqueEmail(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

/** Register through the UI, sign in, and land on the organization picker. */
export async function signUpAndIn(page: Page, email: string, name = "Sara Ahmed") {
  await page.goto("/register");
  await page.getByLabel("الاسم الكامل").fill(name);
  await page.getByLabel("البريد الإلكتروني").fill(email);
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "إنشاء حساب" }).click();
  await expect(page.getByText("تم إنشاء الحساب")).toBeVisible();
  await page.getByLabel("البريد الإلكتروني").fill(email);
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "تسجيل الدخول" }).click();
  await expect(page).toHaveURL(/\/orgs$/);
}

export async function createOrg(page: Page, name: string) {
  await page.getByRole("button", { name: "مؤسسة جديدة" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill(name);
  await dialog.getByLabel(/الاسم المختصر/).fill(`org-${Date.now()}`);
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
}
