import { expect, test, type Page } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

/** Seed what the foundation test already covers through the UI: a customer, a 15% tax, a stocked product. */
async function seed(page: Page) {
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const headers = { Authorization: `Bearer ${token}` };
  const base = `/api/v1/orgs/${orgId}`;
  const post = async (path: string, data: object) => {
    const res = await page.request.post(base + path, { headers, data });
    expect(res.ok(), await res.text()).toBeTruthy();
    return res.json();
  };
  await post("/customers", { name: "مؤسسة الأفق" });
  const tax = await post("/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  await post("/products", { name: "كرسي مكتب", sku: "CH-1", kind: "goods", sale_price: "500", default_tax_id: tax.id });
  return orgId;
}

const nav = (page: Page, name: string) => page.getByRole("complementary").getByRole("link", { name, exact: true }).click();

test("full sales cycle: stock, quotation, delivery, invoice, payment, reports", async ({ page }) => {
  page.on("dialog", (d) => d.accept()); // window.confirm before posting
  await signUpAndIn(page, uniqueEmail("seller"));
  await createOrg(page, "متجر الأثاث");
  await seed(page);

  // Receive 10 chairs at 300.00 each
  await nav(page, "المخزون");
  await page.getByRole("button", { name: "استلام مخزون" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("المنتج 1").selectOption({ label: "كرسي مكتب (CH-1)" });
  await dialog.getByLabel("الكمية").fill("10");
  await dialog.getByLabel(/تكلفة الوحدة/).fill("300");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("cell", { name: "كرسي مكتب" })).toBeVisible();
  await expect(page.getByText("مطابق للدفاتر")).toBeVisible();

  // Quotation for 8 chairs; the editor estimates 4,000 + 15% = 4,600.00 before saving
  await nav(page, "أوامر البيع");
  await page.getByRole("link", { name: "عرض سعر جديد" }).click();
  await page.getByLabel("العميل", { exact: true }).selectOption({ label: "مؤسسة الأفق" });
  await page.getByLabel("المنتج 1").selectOption({ label: "كرسي مكتب (CH-1)" });
  await page.getByLabel("الكمية 1").fill("8");
  await expect(page.getByText(/4,600\.00/).first()).toBeVisible();
  await page.getByRole("button", { name: "حفظ عرض السعر" }).click();
  await expect(page.getByRole("heading", { name: /^SO-\d{4}-/ })).toBeVisible();

  await page.getByRole("button", { name: "تأكيد الأمر" }).click();
  await expect(page.getByText("مؤكد").first()).toBeVisible();

  await page.getByRole("button", { name: "تسليم" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "تسليم" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("مسلّم").first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/sales-order-ar.png", fullPage: true });

  // Invoice the delivery, post it, then pay it in full
  await page.getByRole("button", { name: "إنشاء فاتورة" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "إنشاء فاتورة" }).click();
  await expect(page).toHaveURL(/\/invoices\/[0-9a-f-]+$/);
  await expect(page.getByText("أُنشئت من أمر بيع")).toBeVisible();
  await expect(page.getByRole("link", { name: /^SO-\d{4}-/ })).toBeVisible();
  await page.getByRole("button", { name: "ترحيل الفاتورة" }).click();
  await expect(page.getByRole("heading", { name: /^INV-\d{4}-/ })).toBeVisible();
  await page.getByRole("button", { name: "تسجيل دفعة" }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/المبلغ/)).toHaveValue("4600.00");
  await dialog.getByRole("button", { name: "تسجيل دفعة" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("مدفوعة").first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/invoice-ar.png", fullPage: true });

  // Stock went down by 8 and still matches the ledger
  await nav(page, "المخزون");
  // On hand and available are both 2: the order is fully delivered, so nothing is reserved.
  await expect(page.getByRole("row", { name: /كرسي مكتب/ }).getByRole("cell", { name: "2", exact: true })).toHaveCount(2);
  await expect(page.getByText("مطابق للدفاتر")).toBeVisible();

  // Reports: sales chart, balanced trial balance
  await nav(page, "التقارير");
  await page.getByRole("tab", { name: "المبيعات" }).click();
  const chart = page.getByRole("group", { name: "حسب الشهر" });
  await expect(chart).toBeVisible();
  await chart.getByRole("img").first().hover();
  await expect(page.locator(".chart-tip")).toContainText("4,000.00");
  await expect(page.getByText(/4,000\.00/).first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/reports-sales-ar.png", fullPage: true });
  await page.getByRole("tab", { name: "ميزان المراجعة" }).click();
  await expect(page.getByText("متوازن", { exact: true })).toBeVisible();

  // The same screens in the dark theme
  await nav(page, "الرئيسية");
  await page.screenshot({ path: "test-results/screens/home-ar.png", fullPage: true });
  await page.getByRole("button", { name: "تبديل الوضع الفاتح/الداكن" }).click();
  await page.screenshot({ path: "test-results/screens/home-dark-ar.png", fullPage: true });
  await nav(page, "أوامر البيع");
  await page.getByRole("link", { name: /^SO-\d{4}-/ }).first().click();
  await page.screenshot({ path: "test-results/screens/sales-order-dark-ar.png", fullPage: true });
});

test("CSV import checks the file first and reports bad rows", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("importer"));
  await createOrg(page, "شركة الاستيراد");
  await nav(page, "العملاء");

  const upload = async (csv: string) => {
    await page.getByLabel("ملف CSV").setInputFiles({ name: "customers.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    await page.getByRole("button", { name: "فحص الملف" }).click();
  };

  await page.getByRole("button", { name: "استيراد CSV" }).click();
  await upload("name,email\nشركة أ,a@example.com\n,b@example.com\n");
  await expect(page.getByRole("dialog").getByText("الصف 3")).toBeVisible();

  await upload("name,email\nشركة أ,a@example.com\nشركة ب,b@example.com\n");
  await expect(page.getByText("2 جديد، 0 محدَّث")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "استيراد", exact: true }).click();
  await expect(page.getByRole("cell", { name: /^شركة ب/ })).toBeVisible();     // with the email under the name
});
