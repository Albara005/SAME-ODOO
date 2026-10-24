import { expect, test, type Page } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

const nav = (page: Page, name: string) =>
  page.getByRole("complementary").getByRole("navigation").getByRole("link", { name: new RegExp(`^${name}`) }).click();

test("assistant: the reply streams in, and a note is kept only after approval", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("notes"));
  await createOrg(page, "شركة الذاكرة");
  await nav(page, "المساعد");
  await page.getByLabel("الرسالة").fill("مرحبا بك");
  await page.getByLabel("الرسالة").press("Enter");
  await expect(page.getByText("وصلتني رسالتك: «مرحبا بك». هذا رد تجريبي للاختبارات.")).toBeVisible();

  // The first reply may still be finishing (saved, then the chat moves to the new conversation's address):
  // what's typed meanwhile is kept, and it can be sent once the button is ready.
  await page.getByLabel("الرسالة").fill("تذكّر: الفواتير ترسل إلى قسم المالية");
  await expect(page.getByRole("button", { name: "إرسال" })).toBeEnabled();
  await expect(page.getByLabel("الرسالة")).toHaveValue("تذكّر: الفواتير ترسل إلى قسم المالية");
  await page.getByLabel("الرسالة").press("Enter");
  const card = page.locator(".action-card").last();
  await expect(card).toContainText("حفظ ملاحظة");
  await expect(card).toContainText("الفواتير ترسل إلى قسم المالية");
  await page.getByRole("button", { name: "ما يتذكره المساعد" }).click();
  await expect(page.getByRole("dialog").getByText("لا شيء محفوظ بعد.")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "إغلاق" }).click();

  await card.getByRole("button", { name: "موافقة" }).click();
  await expect(card).toContainText("تمت الموافقة");
  await page.getByRole("button", { name: "ما يتذكره المساعد" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("الفواتير ترسل إلى قسم المالية")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/assistant-notes-ar.png" });
  await dialog.getByRole("button", { name: /^انسَ/ }).click();
  await expect(dialog.getByText("لا شيء محفوظ بعد.")).toBeVisible();
});

test("automation: a weekly schedule, a webhook step and the signing secret", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("sched"));
  await createOrg(page, "شركة الجدولة");
  await nav(page, "الأتمتة");
  await page.getByRole("button", { name: "إظهار السر" }).click();
  await expect(page.getByText(/^whsec_/)).toBeVisible();

  await page.getByRole("link", { name: "سير عمل جديد" }).click();
  await page.getByLabel("الاسم", { exact: true }).fill("تقرير الأسبوع");
  await page.getByLabel("عندما يحدث").selectOption({ label: "حسب جدول زمني" });
  await page.getByLabel("التكرار").selectOption({ label: "كل أسبوع" });
  await page.getByLabel("يوم", { exact: true }).selectOption({ label: "الأحد" });
  await page.getByLabel("الساعة").fill("08:30");
  await page.getByLabel("المنطقة الزمنية").selectOption("Asia/Riyadh");
  await page.getByLabel("العنوان").fill("أرسل تقرير الأسبوع");
  await page.getByRole("button", { name: "إضافة خطوة" }).click();
  await page.getByLabel("الخطوة 2", { exact: true }).selectOption({ label: "إرسال Webhook" });
  await page.getByLabel(/عنوان الاستقبال/).fill("https://hooks.example.com/novara");
  await page.screenshot({ path: "test-results/screens/workflow-schedule-ar.png", fullPage: true });
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("heading", { name: "تقرير الأسبوع" })).toBeVisible();
  await expect(page.getByText("كل يوم الأحد الساعة 08:30 (Asia/Riyadh)")).toBeVisible();
});

test("AI settings: a monthly allowance per member", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("allow"));
  await createOrg(page, "شركة الحصص");
  await nav(page, "إعدادات الذكاء الاصطناعي");
  await page.getByLabel(/حد شهري لكل عضو/).fill("5");
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel(/حد شهري لكل عضو/)).toHaveValue("5");
});

test("automation: another system starts a workflow through its webhook address; hourly schedules", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("inbound"));
  await createOrg(page, "شركة الاستقبال");
  await nav(page, "الأتمتة");
  await page.getByRole("link", { name: "سير عمل جديد" }).click();
  await page.getByLabel("الاسم", { exact: true }).fill("عميل محتمل من الموقع");
  await page.getByLabel("عندما يحدث").selectOption({ label: "استدعاء عنوانه من نظام آخر (Webhook)" });
  await expect(page.getByText(/بعد الحفظ يحصل سير العمل على عنوان سري/)).toBeVisible();
  await page.getByLabel("العنوان").fill("اتصل بـ {{data.name}}");
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("heading", { name: "عميل محتمل من الموقع" })).toBeVisible();

  const address = page.getByLabel("العنوان", { exact: true });
  await expect(address).toContainText("/api/v1/hooks/nvwh_");
  await expect(page.getByLabel("مثال")).toContainText("Idempotency-Key");
  await page.screenshot({ path: "test-results/screens/workflow-webhook-ar.png", fullPage: true });
  const url = (await address.textContent())!.trim();
  const sent = await page.request.post(url, { data: { name: "متجر هلا" }, headers: { "Idempotency-Key": "lead-1" } });
  expect(sent.status()).toBe(202);
  await page.getByRole("button", { name: "تشغيل المعلّق الآن" }).click();
  await expect(page.getByRole("cell", { name: "مكتمل" }).first()).toBeVisible();  // processed before we look at tasks
  await nav(page, "المهام");
  await expect(page.getByText("اتصل بـ متجر هلا")).toBeVisible();

  // A new address replaces the old one at once.
  page.on("dialog", (d) => d.accept());
  await page.goBack();
  await page.getByRole("button", { name: "عنوان جديد" }).click();
  await expect(page.getByText(/العنوان الجديد جاهز/)).toBeVisible();
  expect((await page.request.post(url, { data: { name: "x" } })).status()).toBe(404);

  // Requiring signed calls: unsigned ones are refused, a call signed with the secret gets in.
  await page.getByRole("button", { name: "اشتراط التوقيع" }).click();
  const secret = (await page.getByLabel("سر التوقيع").textContent())!.trim();
  expect(secret).toMatch(/^whsec_/);
  await expect(page.getByLabel("مثال")).toContainText("X-Novara-Signature: v1=$SIG");
  const fresh = (await address.textContent())!.trim();
  expect((await page.request.post(fresh, { data: { name: "x" } })).status()).toBe(401);
  const { createHmac } = await import("node:crypto");
  const body = JSON.stringify({ name: "موقّع" });
  const ts = String(Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex");
  const signed = await page.request.post(fresh, {
    data: body, headers: { "Content-Type": "application/json", "X-Novara-Timestamp": ts, "X-Novara-Signature": `v1=${signature}` } });
  expect(signed.status()).toBe(202);

  await nav(page, "الأتمتة");
  await page.getByRole("link", { name: "سير عمل جديد" }).click();
  await page.getByLabel("الاسم", { exact: true }).fill("فحص كل ساعة");
  await page.getByLabel("عندما يحدث").selectOption({ label: "حسب جدول زمني" });
  await page.getByLabel("التكرار").selectOption({ label: "كل ساعة" });
  await page.getByLabel("الدقيقة من كل ساعة").fill("15");
  await page.getByLabel("المنطقة الزمنية").selectOption("Asia/Riyadh");
  await page.getByLabel("العنوان").fill("فحص المخزون");
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("كل ساعة عند الدقيقة 15 (Asia/Riyadh)")).toBeVisible();
});
