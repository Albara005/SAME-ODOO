import { expect, test, type Page } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

const nav = (page: Page, name: string) => page.getByRole("complementary").getByRole("link", { name, exact: true }).click();

async function apiFor(page: Page) {
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const headers = { Authorization: `Bearer ${token}` };
  return async (method: "GET" | "POST" | "PUT", path: string, data?: object) => {
    const res = await page.request.fetch(`/api/v1/orgs/${orgId}${path}`, { method, headers, data });
    expect(res.ok(), await res.text()).toBeTruthy();
    return res.json();
  };
}

test("purchasing: vendor, order, receipt, bill, payment", async ({ page }) => {
  page.on("dialog", (d) => d.accept()); // window.confirm before posting
  await signUpAndIn(page, uniqueEmail("buyer"));
  await createOrg(page, "متجر المشتريات");
  const call = await apiFor(page);
  const tax = await call("POST", "/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  await call("POST", "/products", { name: "كرسي مكتب", sku: "CH-1", kind: "goods", sale_price: "500", default_tax_id: tax.id });

  await nav(page, "المشتريات");
  await page.getByRole("tab", { name: "الموردون" }).click();
  await page.getByRole("button", { name: "مورد جديد" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم").fill("مؤسسة الخليج للتوريد");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("cell", { name: "مؤسسة الخليج للتوريد" })).toBeVisible();

  await page.getByRole("tab", { name: "أوامر الشراء" }).click();
  await page.getByRole("link", { name: "أمر شراء جديد" }).click();
  await page.getByLabel("المورد").selectOption({ label: "مؤسسة الخليج للتوريد" });
  await page.getByLabel("المنتج 1").selectOption({ label: "كرسي مكتب (CH-1)" });
  await page.getByLabel("الكمية 1").fill("10");
  await page.getByLabel("تكلفة الوحدة 1").fill("200");
  await page.getByRole("button", { name: "حفظ الأمر" }).click();
  await expect(page.getByRole("heading", { name: /^PO-\d{4}-00001$/ })).toBeVisible();
  await page.getByRole("button", { name: "تأكيد الأمر" }).click();

  await page.getByRole("button", { name: "استلام البضاعة" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "استلام البضاعة" }).click();
  await expect(page.getByText(/تم الاستلام \(REC-/)).toBeVisible();
  await page.screenshot({ path: "test-results/screens/purchase-order-ar.png", fullPage: true });

  await page.getByRole("button", { name: "تسجيل فاتورة المورد" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel(/رقم فاتورة المورد/).fill("GS-881");
  await dialog.getByRole("button", { name: "تسجيل فاتورة المورد" }).click();
  await expect(page.getByText("هذه الفاتورة تتبع أمر الشراء")).toBeVisible();
  await page.getByRole("button", { name: "ترحيل الفاتورة" }).click();
  await expect(page.getByRole("heading", { name: /^BILL-\d{4}-00001$/ })).toBeVisible();

  await page.getByRole("button", { name: "دفع للمورد" }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/المبلغ/)).toHaveValue("2300.00");
  await dialog.getByRole("button", { name: "دفع للمورد" }).click();
  await expect(page.getByRole("main").getByText("مدفوعة", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/vendor-bill-ar.png", fullPage: true });

  // Nothing is owed any more, and the books balance.
  await nav(page, "التقارير");
  await page.getByRole("tab", { name: "الذمم الدائنة" }).click();
  await expect(page.getByText("لا مستحقات للموردين.")).toBeVisible();
  await page.getByRole("tab", { name: "الميزانية العمومية" }).click();
  await expect(page.getByText("متوازن")).toBeVisible();
  await expect(page.getByRole("cell", { name: /المخزون/ })).toBeVisible();
});

test("credit note, refund, statements, e-invoice QR and closed books", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("credit"));
  await createOrg(page, "شركة الإشعارات");
  const call = await apiFor(page);
  const tax = await call("POST", "/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  const customer = await call("POST", "/customers", { name: "مؤسسة الضياء" });
  const inv = await call("POST", "/invoices", {
    customer_id: customer.id, issue_date: "2026-04-01",
    lines: [{ description: "استشارة", quantity: "4", unit_price: "250", tax_id: tax.id }],
  });
  await call("POST", `/invoices/${inv.id}/post`);
  await call("POST", `/invoices/${inv.id}/payments`, { amount: "1150", payment_date: "2026-04-02", method: "bank" });

  // A VAT number puts the e-invoice QR code on invoices.
  await nav(page, "المؤسسة");
  await page.getByLabel(/الرقم الضريبي/).fill("300000000000003");
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();

  await nav(page, "الفواتير");
  await page.getByRole("link", { name: /^INV-2026-/ }).click();
  await expect(page.getByRole("img", { name: "رمز QR للفاتورة الإلكترونية" })).toBeVisible();
  await page.getByRole("button", { name: "إشعار دائن" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("السبب").fill("ساعة لم تُقدَّم");
  await dialog.getByLabel("الكمية المخصومة استشارة").fill("1");
  await dialog.getByRole("button", { name: "إصدار الإشعار" }).click();

  await expect(page.getByRole("heading", { name: /^CN-2026-00001$/ })).toBeVisible();
  const owed = page.getByRole("definition").filter({ hasText: "287.50" });
  await expect(owed.first()).toBeVisible();   // already paid in full: everything is owed back
  await page.screenshot({ path: "test-results/screens/credit-note-ar.png", fullPage: true });
  await page.getByRole("button", { name: "رد المبلغ" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "رد المبلغ" }).click();
  await expect(page.getByRole("button", { name: "رد المبلغ" })).toHaveCount(0);
  await expect(page.getByRole("cell", { name: /287\.50/ })).toBeVisible();

  await nav(page, "التقارير");
  await expect(page.getByRole("tab", { name: "الأرباح والخسائر", selected: true })).toBeVisible();
  await page.getByLabel(/^من/).fill("2026-01-01");
  await expect(page.getByText("إيرادات المبيعات")).toBeVisible();
  await expect(page.getByText("صافي الربح")).toBeVisible();
  await expect(page.locator(".statement-result")).toContainText("750.00");
  await page.screenshot({ path: "test-results/screens/profit-loss-ar.png", fullPage: true });

  // Closing the books refuses anything dated inside them.
  await page.getByRole("button", { name: "تغيير" }).click();
  await page.getByRole("dialog").getByLabel(/مقفلة حتى/).fill("2026-04-30");
  await page.getByRole("dialog").getByRole("button", { name: "إقفال" }).click();
  await expect(page.getByText(/الدفاتر مقفلة حتى/).first()).toBeVisible();
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const late = await page.request.post(`/api/v1/orgs/${orgId}/invoices/${inv.id}/credit-notes`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { issue_date: "2026-04-15", reason: "x", lines: [{ invoice_line_id: inv.lines[0].id, quantity: "1" }] },
  });
  expect(late.status()).toBe(409);
});
