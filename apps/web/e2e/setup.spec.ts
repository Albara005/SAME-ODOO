import { expect, test, type Page } from "@playwright/test";
import { signUpAndIn, uniqueEmail } from "./helpers";

const menu = (page: Page) => page.getByRole("complementary");

test("first run: say what the business does, get its apps, VAT, sample data and a store taking orders", async ({ page, context }) => {
  await signUpAndIn(page, uniqueEmail("setup"));
  await page.getByRole("button", { name: "مؤسسة جديدة" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("متجر الريحان");
  await dialog.getByLabel(/الاسم المختصر/).fill(`rayhan-${Date.now()}`);
  await dialog.getByRole("button", { name: "إنشاء" }).click();

  // The organization asks before anything else.
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+\/setup$/);
  await expect(page.getByRole("heading", { name: "لنجهّز متجر الريحان للعمل" })).toBeVisible();
  const next = page.getByRole("button", { name: "التالي" });
  await expect(next).toBeDisabled();
  await page.locator("label.choice", { hasText: "متجر تجزئة" }).click();
  await expect(page.getByRole("radio", { name: /متجر تجزئة/ })).toBeChecked();
  await next.click();

  // The apps a shop needs are suggested; HR is added on top.
  await expect(page.getByText("التطبيقات المختارة: 10")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /^المخزون/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /^الرواتب/ })).not.toBeChecked();
  await page.locator("label.choice", { hasText: "الموارد البشرية" }).click();
  await expect(page.getByText("التطبيقات المختارة: 11")).toBeVisible();
  await next.click();

  await expect(page.getByLabel("البلد")).toHaveValue("SA");
  await expect(page.getByText(/ضريبة القيمة المضافة بنسبة/)).toContainText("15%");
  await page.getByLabel(/الرقم الضريبي/).fill("300000000000003");
  await expect(page.getByRole("checkbox", { name: "أضف بيانات تجريبية للاستكشاف" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "أنشئ متجري الإلكتروني" })).toBeChecked();
  await expect(page.getByLabel("اسم المتجر")).toHaveValue("متجر الريحان");
  await page.screenshot({ path: "test-results/screens/setup-details-ar.png", fullPage: true });
  await page.getByRole("button", { name: "جهّز مؤسستي" }).click();

  await expect(page.getByRole("heading", { name: "متجر الريحان جاهزة للعمل" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "متجرك الإلكتروني يعمل الآن" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "منتجات تجريبية" })).toContainText("4");
  await page.screenshot({ path: "test-results/screens/setup-done-ar.png", fullPage: true });

  // A visitor orders from the store.
  const [store] = await Promise.all([context.waitForEvent("page"), page.getByRole("link", { name: "افتح المتجر" }).click()]);
  await expect(store.getByRole("heading", { name: "قميص قطني" })).toBeVisible();
  await expect(store.getByText(/90\.85/)).toBeVisible();                       // 79 + 15% VAT
  await store.getByRole("button", { name: "أضف قميص قطني إلى السلة" }).click();
  await store.getByRole("button", { name: "أضف قميص قطني إلى السلة" }).click();
  await store.getByRole("button", { name: /^السلة/ }).click();
  await store.getByLabel("الاسم").fill("نورة");
  await store.getByLabel("الجوال").fill("+966500000009");
  await store.getByRole("button", { name: "إرسال الطلب" }).click();
  await expect(store.getByText(/وصلنا طلبك رقم WEB-/)).toBeVisible({ timeout: 30_000 });
  await store.close();

  // The menu holds what was chosen, the order waits in Sales.
  await page.getByRole("link", { name: "ابدأ العمل" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
  await expect(menu(page).getByRole("link", { name: "الموارد البشرية", exact: true })).toBeVisible();
  await expect(menu(page).getByRole("link", { name: "الرواتب", exact: true })).toHaveCount(0);
  await menu(page).getByRole("link", { name: "أوامر البيع", exact: true }).click();
  await expect(page.getByText("طلبات من مواقعك (1)")).toBeVisible();
  await expect(page.getByText("نورة")).toBeVisible();

  // Apps can be turned on later; the wizard doesn't come back.
  await menu(page).getByRole("link", { name: "المؤسسة", exact: true }).click();
  const apps = page.locator("section.card").filter({ has: page.getByRole("heading", { name: "التطبيقات في القائمة" }) });
  await apps.locator("label.choice", { hasText: "الرواتب" }).click();
  await apps.getByRole("button", { name: "حفظ التطبيقات" }).click();
  await expect(page.getByText("تم تحديث القائمة")).toBeVisible();
  await expect(menu(page).getByRole("link", { name: "الرواتب", exact: true })).toBeVisible();
  await menu(page).getByRole("link", { name: "الرئيسية", exact: true }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
});
