import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

const nav = (page: Page, name: string) =>
  page.getByRole("complementary").getByRole("navigation").getByRole("link", { name: new RegExp(`^${name}`) }).click();
// A PDF made by Chromium, so its Arabic is stored as shaped glyphs in visual order, as real PDFs often are.
const PDF = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../services/api/tests/fixtures/refund-policy-ar.pdf");

test("documents: PDF and text files become searchable; the assistant and an agent answer from them", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("docs"));
  await createOrg(page, "متجر المستندات");
  await nav(page, "المستندات");
  await page.getByLabel("رفع ملفات").setInputFiles([
    { name: "refund-policy-ar.pdf", mimeType: "application/pdf", buffer: readFileSync(PDF) },
    { name: "ساعات العمل.txt", mimeType: "text/plain", buffer: Buffer.from("نعمل من الأحد إلى الخميس، من التاسعة صباحاً حتى الخامسة مساءً.") },
  ]);
  await expect(page.getByText("رُفعت الملفات: 2")).toBeVisible();
  await expect(page.getByRole("row", { name: /refund-policy-ar\.pdf/ }).getByText("قابل للبحث")).toBeVisible();
  await expect(page.getByRole("row", { name: /ساعات العمل/ }).getByText("قابل للبحث")).toBeVisible();

  await page.getByLabel("البحث داخل المستندات").fill("إرجاع المنتج");
  const first = page.getByRole("list", { name: "المقاطع المطابقة" }).getByRole("listitem").first();
  await expect(first).toContainText("refund-policy-ar.pdf");
  await expect(first).toContainText("إرجاع المنتج خلال أربعة عشر يوماً");   // reading order, not the PDF's visual order
  await page.screenshot({ path: "test-results/screens/documents-ar.png", fullPage: true });

  // The assistant finds the passage and answers from it.
  await nav(page, "المساعد");
  await page.getByLabel("الرسالة").fill("ابحث كم يوماً لإرجاع المنتج");
  await page.getByLabel("الرسالة").press("Enter");
  await expect(page.getByText(/وجدت في «refund-policy-ar\.pdf»/)).toBeVisible();
  await expect(page).toHaveURL(/\/assistant\/[0-9a-f-]+$/);   // the reply is saved and the chat has its own address

  // An agent with the policy as its only knowledge: it can't see the opening-hours file.
  await nav(page, "الوكلاء");
  await page.getByRole("button", { name: "وكيل جديد" }).click();
  await page.getByRole("dialog").getByLabel("الاسم", { exact: true }).fill("مساعد الاسترجاع");
  await page.getByRole("dialog").getByRole("button", { name: "إنشاء" }).click();
  await page.getByLabel("التعليمات").fill("أجب عن أسئلة الاسترجاع من السياسة.");
  await page.getByRole("checkbox", { name: /refund-policy-ar\.pdf/ }).check();
  await page.getByRole("button", { name: "حفظ المسودة" }).click();
  await expect(page.getByText("حُفظت المسودة كإصدار 1.")).toBeVisible();
  await page.getByRole("tab", { name: "الاختبارات" }).click();
  await page.getByLabel("الاسم", { exact: true }).fill("يبحث في السياسة");
  await page.getByLabel("المدخل").fill("ابحث مدة إرجاع المنتج");
  await page.getByLabel(/الرد يحتوي/).fill("وجدت");
  await page.getByRole("button", { name: "إضافة اختبار" }).click();
  await page.getByRole("tab", { name: "الإصدارات" }).click();
  await expect(page.getByText(/المعرفة: refund-policy-ar\.pdf/)).toBeVisible();
  await page.getByRole("button", { name: "تشغيل الاختبارات" }).click();
  await expect(page.getByText("نجحت كل الاختبارات")).toBeVisible();
  await page.getByRole("button", { name: "نشر" }).click();
  await page.getByRole("tab", { name: "تشغيل" }).click();
  await page.getByLabel(/ماذا تريد من الوكيل/).fill("ابحث التاسعة صباحاً");
  await page.getByRole("button", { name: "تشغيل الوكيل" }).click();
  await expect(page.getByText("لم أجد ذلك في المستندات.")).toBeVisible();
});
