import { expect, test, type Page } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

const nav = (page: Page, name: string) =>
  page.getByRole("complementary").getByRole("navigation").getByRole("link", { name: new RegExp(`^${name}`) }).click();

test("Creative Studio: brand kit, live palette, logo, recolour, apply to an app", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("brand"));
  await createOrg(page, "مخبز الوردة");

  // An app to apply the brand to
  await nav(page, "استوديو التطبيقات");
  await page.getByRole("button", { name: "تطبيق جديد" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("متجر الوردة");
  await dialog.getByLabel(/اسم العنوان/).fill("rose-shop");
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page.getByRole("heading", { name: "متجر الوردة" })).toBeVisible();

  await nav(page, "الاستوديو الإبداعي");
  await page.getByRole("button", { name: "هوية جديدة" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("مخبز الوردة");
  await dialog.getByLabel(/الشعار النصي/).fill("منذ 1985");
  await dialog.getByPlaceholder("#2747b5").first().fill("#b0305e");
  await expect(dialog.getByText("اجتاز كل فحوص التباين")).toBeVisible();
  await expect(dialog.getByLabel("فاتح")).toBeVisible();
  await expect(dialog.getByLabel("داكن")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/creative-new-ar.png" });
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page.getByRole("heading", { name: "مخبز الوردة" })).toBeVisible();

  await page.getByRole("button", { name: "شعار جديد" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("الرمز", { exact: true }).selectOption("arch");
  await dialog.getByRole("button", { name: "ارسم الشعار" }).click();
  await expect(page.getByText("الشعار الرئيسي")).toBeVisible();
  await expect(page.getByRole("img", { name: "مخبز الوردة", exact: true })).toBeVisible();

  await page.getByPlaceholder("#2747b5").first().fill("#0f766e");
  await page.getByRole("button", { name: "اعتماد الألوان" }).click();
  await expect(page.getByText("تم تحديث الألوان")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/creative-kit-ar.png", fullPage: true });

  await page.getByRole("button", { name: "استخدامها في تطبيق" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("التطبيق").selectOption({ label: "متجر الوردة" });
  await dialog.getByRole("button", { name: "تطبيق", exact: true }).click();
  await expect(page.getByText(/طُبّقت الهوية/)).toBeVisible();
});

test("App Studio: edit, review the diff, version, preview, approved production", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("apps"));
  await createOrg(page, "شركة التطبيقات");
  await nav(page, "استوديو التطبيقات");
  await page.getByRole("button", { name: "تطبيق جديد" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("Order Tracker");
  await expect(dialog.getByLabel(/اسم العنوان/)).toHaveValue("order-tracker");
  await dialog.getByRole("button", { name: "إنشاء" }).click();

  const code = page.getByLabel("الكود");
  await expect(code).toHaveValue(/<html/);
  await code.fill("<!doctype html>\n<html lang=\"ar\" dir=\"rtl\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>تتبع الطلبات</title>\n</head>\n<body><h1>تتبع الطلبات</h1></body>\n</html>\n");
  await page.getByRole("button", { name: "حفظ", exact: true }).click();
  await expect(page.getByText("تغييرات غير محفوظة")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/studio-build-ar.png" });

  await page.getByRole("tab", { name: "التغييرات" }).click();
  await expect(page.locator(".diff .add").filter({ hasText: "<h1>تتبع الطلبات</h1>" })).toBeVisible();

  await page.getByRole("button", { name: "حفظ إصدار" }).click();
  await page.getByRole("dialog").getByLabel(/ما الذي تغيّر/).fill("الصفحة الأولى");
  await page.getByRole("dialog").getByRole("button", { name: "حفظ إصدار" }).click();
  await expect(page.getByText(/حُفظ الإصدار 1/)).toBeVisible();

  await page.getByRole("tab", { name: "الإصدارات" }).click();
  await expect(page.getByText("الفحوص ناجحة")).toBeVisible();
  await page.getByRole("button", { name: "معاينة" }).click();
  await expect(page.getByText(/المعاينة تعمل الآن/)).toBeVisible();
  await page.getByRole("button", { name: "الإنتاج" }).click();
  await expect(page.getByText(/طُلب النشر للإنتاج/)).toBeVisible();

  await page.getByRole("tab", { name: "النشر" }).click();
  await expect(page.getByRole("link", { name: "فتح" })).toHaveCount(1);
  await page.getByRole("button", { name: "موافقة" }).click();
  await expect(page.getByRole("link", { name: "فتح" })).toHaveCount(2);
  await page.screenshot({ path: "test-results/screens/studio-deploy-ar.png" });

  // The published site serves the saved version
  const href = await page.getByRole("link", { name: "فتح" }).first().getAttribute("href");
  const site = await page.request.get(href!);
  expect(await site.text()).toContain("تتبع الطلبات");
});

test("Agents: build, test, publish, run", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("agent"));
  await createOrg(page, "شركة الوكلاء");
  await nav(page, "الوكلاء");
  await page.getByRole("button", { name: "وكيل جديد" }).click();
  await page.getByRole("dialog").getByLabel("الاسم", { exact: true }).fill("مساعد التحصيل");
  await page.getByRole("dialog").getByRole("button", { name: "إنشاء" }).click();

  await page.getByLabel("التعليمات").fill("تابع الفواتير المتأخرة بلباقة.");
  await page.getByRole("checkbox", { name: /receivables_aging/ }).check();
  await page.getByRole("button", { name: "حفظ المسودة" }).click();
  await expect(page.getByText("حُفظت المسودة كإصدار 1.")).toBeVisible();

  await page.getByRole("tab", { name: "الاختبارات" }).click();
  await page.getByLabel("الاسم", { exact: true }).fill("يرد بأدب");
  await page.getByLabel("المدخل").fill("من المتأخرون؟");
  await page.getByLabel(/الرد يحتوي/).fill("تجريبي");
  await page.getByRole("button", { name: "إضافة اختبار" }).click();
  await expect(page.getByText("يرد بأدب")).toBeVisible();

  await page.getByRole("tab", { name: "الإصدارات" }).click();
  await page.getByRole("button", { name: "تشغيل الاختبارات" }).click();
  await expect(page.getByText("نجحت كل الاختبارات")).toBeVisible();
  await page.getByRole("button", { name: "نشر" }).click();
  await expect(page.getByText("الإصدار 1 يعمل الآن.")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/agent-versions-ar.png" });

  await page.getByRole("tab", { name: "تشغيل" }).click();
  await page.getByLabel(/ماذا تريد من الوكيل/).fill("لخّص الذمم");
  await page.getByRole("button", { name: "تشغيل الوكيل" }).click();
  await expect(page.getByText(/هذا رد تجريبي/)).toBeVisible();
});

test("Automation: build a workflow in the UI and see it run", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("wf"));
  await createOrg(page, "شركة السير");
  await nav(page, "الأتمتة");
  await page.getByRole("link", { name: "سير عمل جديد" }).click();
  await page.getByLabel("الاسم", { exact: true }).fill("شكر العملاء الجدد");
  await page.getByLabel("عندما يحدث").selectOption({ label: "إنشاء عميل" });
  await page.getByLabel("العنوان").fill("اتصل بالعميل الجديد");
  await page.getByRole("button", { name: "إضافة خطوة" }).click();
  await page.getByLabel("العنوان").nth(1).fill("عميل جديد أُضيف");
  await page.screenshot({ path: "test-results/screens/workflow-builder-ar.png", fullPage: true });
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("heading", { name: "شكر العملاء الجدد" })).toBeVisible();

  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  await page.request.post(`/api/v1/orgs/${orgId}/customers`, { headers: { Authorization: `Bearer ${token}` }, data: { name: "مؤسسة الفجر" } });
  await page.getByRole("button", { name: "تشغيل المعلّق الآن" }).click();
  await expect(page.getByText("مكتمل")).toBeVisible();
  await nav(page, "المهام");
  await expect(page.getByRole("checkbox", { name: /اتصل بالعميل الجديد/ })).toBeVisible();
});

test("Hosting: quote, request, approve the exact price", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("host"));
  await createOrg(page, "شركة الاستضافة");
  await nav(page, "الاستضافة");
  await expect(page.getByText("$150.00")).toBeVisible();
  await page.getByRole("button", { name: "إرسال الطلب" }).click();
  await expect(page.getByRole("cell", { name: "بانتظار الموافقة" })).toBeVisible();
  await page.getByRole("button", { name: "الموافقة على السعر" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "الموافقة على السعر" })).toBeDisabled();
  await dialog.getByLabel(/اكتب 150.00 للتأكيد/).fill("150.00");
  await dialog.getByRole("button", { name: "الموافقة على السعر" }).click();
  await expect(page.getByRole("cell", { name: "تمت الموافقة" })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/hosting-ar.png", fullPage: true });
});
