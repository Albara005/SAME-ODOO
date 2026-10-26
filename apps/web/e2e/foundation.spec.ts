import { expect, test } from "@playwright/test";
import { createOrg, PASSWORD, signOut, signUpAndIn, uniqueEmail } from "./helpers";

test("sign up, create an organization, manage customers and products in Arabic", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("owner"));
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await createOrg(page, "شركة النخبة");
  await expect(page.getByRole("heading", { level: 1, name: /^(صباح|مساء) الخير/ })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/home-ar.png", fullPage: true });

  // Customers
  await page.getByRole("complementary").getByRole("link", { name: "العملاء", exact: true }).click();
  await page.getByRole("button", { name: "عميل جديد" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("مؤسسة الأفق");
  await dialog.getByLabel(/الرقم الضريبي/).fill("300000000000003");
  await dialog.getByLabel(/المدينة/).fill("جدة");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("cell", { name: "مؤسسة الأفق", exact: true })).toBeVisible();
  await page.getByRole("searchbox").fill("3000000");
  await expect(page.getByRole("cell", { name: "مؤسسة الأفق", exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/customers-ar.png", fullPage: true });

  // Validation errors from the API appear next to the field
  await page.getByRole("button", { name: "عميل جديد" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("Bad");
  // Passes the browser's own check but not the server's (no dot in the domain).
  await dialog.getByLabel(/البريد الإلكتروني/).fill("a@b");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "إلغاء" }).click();

  // Taxes and products
  await page.getByRole("complementary").getByRole("link", { name: "المنتجات", exact: true }).click();
  await page.getByPlaceholder("ضريبة جديدة").fill("ضريبة القيمة المضافة");
  await page.getByPlaceholder("النسبة %").fill("15");
  await page.getByRole("button", { name: "إضافة" }).click();
  await expect(page.getByText("ضريبة القيمة المضافة", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "منتج جديد" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("كرسي مكتب");
  await dialog.getByLabel(/رمز المنتج/).fill("CH-1");
  await dialog.getByLabel(/سعر البيع/).fill("499.99");
  await dialog.getByLabel("الضريبة الافتراضية").selectOption({ label: "ضريبة القيمة المضافة (15%)" });
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("cell", { name: /499\.99/ })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/products-ar.png", fullPage: true });

  // Switch to English: the whole layout flips to LTR
  await page.getByRole("button", { name: "English" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.getByRole("heading", { name: "Products & services" })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/products-en.png", fullPage: true });

  // Audit log shows what happened
  await page.getByRole("complementary").getByRole("link", { name: "Audit log", exact: true }).click();
  await expect(page.getByText("customer.created").first()).toBeVisible();
  await expect(page.getByText("product.created").first()).toBeVisible();
});

test("a member only sees what their role allows", async ({ page, browser }) => {
  const owner = uniqueEmail("owner2");
  const viewer = uniqueEmail("viewer");
  // The viewer needs an account before being added.
  const viewerPage = await browser.newPage();
  await signUpAndIn(viewerPage, viewer, "Viewer");
  await viewerPage.close();

  await signUpAndIn(page, owner);
  await createOrg(page, "Globex");
  await page.getByRole("complementary").getByRole("link", { name: "الأعضاء", exact: true }).click();
  await page.getByRole("button", { name: "إضافة عضو" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("البريد الإلكتروني").fill(viewer);
  await dialog.getByLabel("الدور").selectOption({ label: "مشاهد" });
  await dialog.getByRole("button", { name: "إضافة" }).click();
  await expect(page.getByRole("cell", { name: viewer, exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/members-ar.png", fullPage: true });

  // Sign in as the viewer: no business or admin menus.
  await signOut(page);
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("البريد الإلكتروني").fill(viewer);
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "تسجيل الدخول" }).click();
  // A single organization: the app goes straight to it.
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1, name: /^(صباح|مساء) الخير/ })).toBeVisible();
  const nav = page.getByRole("complementary");
  await expect(nav.getByRole("link", { name: "العملاء" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "الأعضاء" })).toHaveCount(0);
  // Even typing the URL shows a permission message, because the server refuses the data.
  await page.goto(page.url() + "/customers");
  await expect(page.getByText("ليست لديك صلاحية لعرض هذا.")).toBeVisible();
});

test("wrong password shows the server's message", async ({ page }) => {
  await page.goto("/login");
  await page.screenshot({ path: "test-results/screens/login-ar.png" });
  await page.getByLabel("البريد الإلكتروني").fill("nobody@example.com");
  await page.getByLabel("كلمة المرور").fill("wrong-password-1");
  await page.getByRole("button", { name: "تسجيل الدخول" }).click();
  await expect(page.getByRole("alert")).toContainText("invalid email or password");
});
