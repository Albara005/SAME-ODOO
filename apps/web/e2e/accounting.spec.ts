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
  // The vendor charged 210 a chair, not the ordered 200: the difference is booked when the bill posts.
  await page.getByRole("button", { name: "سعر المورد" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("سعر الوحدة في فاتورة المورد").fill("210");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText(/سعر الأمر/)).toBeVisible();
  await page.getByRole("button", { name: "ترحيل الفاتورة" }).click();
  await expect(page.getByRole("heading", { name: /^BILL-\d{4}-00001$/ })).toBeVisible();

  await page.getByRole("button", { name: "دفع للمورد" }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/المبلغ/)).toHaveValue("2415.00");
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
  await page.getByRole("button", { name: "حفظ", exact: true }).click();
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

test("vendor credit: goods sent back reduce the bill, the vendor pays back the rest", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("vcredit"));
  await createOrg(page, "متجر المرتجعات");
  const call = await apiFor(page);
  const tax = await call("POST", "/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  const chair = await call("POST", "/products", { name: "كرسي مكتب", sku: "CH-1", kind: "goods", sale_price: "500", default_tax_id: tax.id });
  const vendor = await call("POST", "/vendors", { name: "مؤسسة الخليج للتوريد" });
  const po = await call("POST", "/purchase-orders", {
    vendor_id: vendor.id, order_date: "2026-05-01",
    lines: [{ product_id: chair.id, quantity: "10", unit_cost: "200", tax_id: tax.id }],
  });
  await call("POST", `/purchase-orders/${po.id}/confirm`);
  await call("POST", `/purchase-orders/${po.id}/receipts`, { move_date: "2026-05-03" });
  const bill = await call("POST", `/purchase-orders/${po.id}/bill`, { bill_date: "2026-05-04", vendor_reference: "GS-881" });
  await call("POST", `/bills/${bill.id}/post`);

  const orgPath = page.url().match(/\/o\/[0-9a-f-]+/)![0];
  await page.goto(`${orgPath}/purchases/bills/${bill.id}`);
  await page.getByRole("button", { name: "إشعار دائن من المورد" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("رقم إشعار المورد").fill("GS-CN-12");
  await dialog.getByLabel("السبب").fill("تلف أثناء الشحن");
  await dialog.getByLabel("الكمية المخصومة كرسي مكتب").fill("2");
  await dialog.getByLabel("أعيد للمورد كرسي مكتب").fill("2");
  await dialog.getByRole("button", { name: "تسجيل الإشعار" }).click();

  await expect(page.getByRole("heading", { name: /^VC-2026-00001$/ })).toBeVisible();
  await expect(page.getByText(/^VRT-2026-/)).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: "460.00" }).first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/vendor-credit-ar.png", fullPage: true });
  await page.getByRole("link", { name: /^BILL-2026-/ }).click();
  await expect(page.getByText("خصمه المورد")).toBeVisible();
  await expect(page.getByRole("link", { name: "VC-2026-00001" })).toBeVisible();
  await expect(page.getByRole("button", { name: "إلغاء الفاتورة" })).toHaveCount(0);
});

test("accounting: a manual entry, a new account, closing the year and reversing", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("ledger"));
  await createOrg(page, "شركة الدفاتر");
  const call = await apiFor(page);
  const customer = await call("POST", "/customers", { name: "مؤسسة الضياء" });
  const inv = await call("POST", "/invoices", {
    customer_id: customer.id, issue_date: "2026-02-01",
    lines: [{ description: "استشارة", quantity: "4", unit_price: "250" }],
  });
  await call("POST", `/invoices/${inv.id}/post`);

  // This business's fiscal year runs April to March.
  const orgPath = page.url().match(/\/o\/[0-9a-f-]+/)![0];
  await page.goto(`${orgPath}/settings/organization`);
  await page.getByLabel("تبدأ السنة المالية في").selectOption({ label: "أبريل" });
  await page.getByRole("button", { name: "حفظ", exact: true }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();

  await nav(page, "المحاسبة");
  await page.getByRole("button", { name: "قيد يدوي" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("التاريخ").fill("2026-03-15");
  await dialog.getByLabel("البيان").fill("إيجار مارس");
  await dialog.getByLabel("الحساب 1").selectOption({ label: "6000 · مصروفات عامة" });
  await dialog.getByLabel("مدين 1").fill("300");
  await dialog.getByLabel("الحساب 2").selectOption({ label: "1200 · البنك" });
  await expect(dialog.getByRole("button", { name: "ترحيل القيد" })).toBeDisabled();
  await dialog.getByLabel("دائن 2").fill("300");
  await expect(dialog.getByText("متوازن")).toBeVisible();
  await expect(dialog.getByLabel("الحساب 1").locator("option", { hasText: "ذمم العملاء" })).toHaveCount(0);
  await page.screenshot({ path: "test-results/screens/manual-entry-ar.png" });
  await dialog.getByRole("button", { name: "ترحيل القيد" }).click();
  await expect(page.getByText(/رُحِّل القيد JE-2026-/)).toBeVisible();
  const entry = page.locator(".card").filter({ hasText: "إيجار مارس" });
  await expect(entry.getByText("يدوي", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "دليل الحسابات" }).click();
  await page.getByRole("button", { name: "حساب جديد" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("الرمز").fill("6200");
  await dialog.getByLabel("الاسم").fill("إيجار المكتب");
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page.getByRole("cell", { name: "إيجار المكتب" })).toBeVisible();
  await expect(page.getByRole("row", { name: /1300/ }).getByText("تتبع المستندات")).toBeVisible();

  await page.getByRole("tab", { name: "إقفال السنة" }).click();
  await expect(page.getByText("لم تُقفل سنة بعد")).toBeVisible();
  await page.getByRole("button", { name: "إقفال السنة" }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/آخر يوم في السنة/)).toHaveValue(/-03-31$/);     // the next fiscal year end
  await dialog.getByLabel(/آخر يوم في السنة/).fill("2026-03-31");
  await dialog.getByRole("button", { name: "إقفال السنة" }).click();
  await expect(page.getByText(/أُقفلت السنة حتى/)).toBeVisible();
  await expect(page.getByText(/الدفاتر مقفلة حتى/).first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/year-end-ar.png", fullPage: true });

  await page.getByRole("tab", { name: "دفتر اليومية" }).click();
  await page.getByLabel("المصدر").selectOption({ label: "إقفال السنة" });
  await expect(page.getByText("الأرباح المحتجزة")).toBeVisible();
  await page.getByLabel("المصدر").selectOption({ label: "يدوي" });
  await entry.getByRole("button", { name: "عكس" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "عكس" }).click();
  await expect(page.getByText(/عُكس JE-2026-/)).toBeVisible();
  await expect(entry.getByText("معكوس", { exact: true })).toBeVisible();
});

test("projects: a task, hours logged, and the billable hours invoiced", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("projects"));
  await createOrg(page, "استوديو التصميم");
  const call = await apiFor(page);
  const tax = await call("POST", "/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  await call("POST", "/customers", { name: "متاجر هلا" });
  void tax;

  await nav(page, "المشاريع");
  await page.getByRole("button", { name: "مشروع جديد" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("موقع متاجر هلا");
  await dialog.getByLabel(/العميل/).selectOption({ label: "متاجر هلا" });
  await dialog.getByLabel(/سعر الساعة/).fill("250");
  await dialog.getByLabel(/الضريبة/).selectOption({ label: "ضريبة القيمة المضافة" });
  await dialog.getByLabel(/الميزانية/).fill("40");
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page.getByRole("heading", { name: "موقع متاجر هلا" })).toBeVisible();

  await page.getByRole("tab", { name: "المهام" }).click();
  await page.getByRole("button", { name: "إضافة مهمة" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("العنوان").fill("الصفحة الرئيسية");
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page.getByRole("cell", { name: "الصفحة الرئيسية" })).toBeVisible();

  for (const [hours, task] of [["3", "الصفحة الرئيسية"], ["1.5", null]] as const) {
    await page.getByRole("button", { name: "تسجيل ساعات" }).first().click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel(/^الساعات/).fill(hours);
    if (task) await dialog.getByLabel(/المهام/).selectOption({ label: task });
    await dialog.getByRole("button", { name: "تسجيل ساعات" }).click();
    await expect(dialog).toHaveCount(0);
  }
  await page.getByRole("tab", { name: "الساعات" }).click();
  await expect(page.getByRole("cell", { name: "4.5" }).or(page.getByText("4.5").first())).toBeVisible();
  await page.screenshot({ path: "test-results/screens/project-ar.png", fullPage: true });

  await page.getByRole("button", { name: "فوترة الساعات" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "إنشاء مسودة الفاتورة" }).click();
  await expect(page.getByRole("cell", { name: "موقع متاجر هلا: الصفحة الرئيسية" })).toBeVisible();
  await expect(page.getByText("1,293.75").first()).toBeVisible();   // 4.5 h x 250 = 1,125 + 15%
});

test("monthly closing: close last month with its drafts in view, then reopen it with a reason", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("months"));
  await createOrg(page, "شركة الأشهر");
  const call = await apiFor(page);
  const now = new Date();
  const last = new Date(Date.UTC(now.getFullYear(), now.getMonth() - 1, 10));
  const day = last.toISOString().slice(0, 10);
  const customer = await call("POST", "/customers", { name: "مؤسسة الضحى" });
  const lines = [{ description: "استشارة", quantity: "1", unit_price: "750" }];
  const invoice = await call("POST", "/invoices", { customer_id: customer.id, issue_date: day, lines });
  await call("POST", `/invoices/${invoice.id}/post`);
  await call("POST", "/invoices", { customer_id: customer.id, issue_date: day, lines });   // stays a draft

  await nav(page, "المحاسبة");
  await page.getByRole("tab", { name: "الإقفال الشهري" }).click();
  await page.getByLabel("السنة المالية").selectOption(`${last.getUTCFullYear()}-01-01`);
  const monthName = new Intl.DateTimeFormat("ar-SA-u-nu-latn-ca-gregory", { month: "long", year: "numeric", timeZone: "UTC" }).format(last);
  const row = page.getByRole("row", { name: new RegExp(monthName) });
  await expect(row).toContainText("750");
  await expect(row.getByText("1 فاتورة، 0 فاتورة مورد")).toBeVisible();
  page.once("dialog", (d) => { expect(d.message()).toContain("ما زالت 1 مسودة"); void d.accept(); });
  await row.getByRole("button", { name: "إقفال حتى هنا" }).click();
  await expect(page.getByText("أُقفل الشهر")).toBeVisible();
  await expect(row.getByText("مقفل", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/monthly-close-ar.png", fullPage: true });

  await row.getByRole("button", { name: "إعادة فتح" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("سبب إعادة الفتح").fill("فاتورة مورد متأخرة عن هذا الشهر");
  await dialog.getByRole("button", { name: "إعادة فتح" }).click();
  await expect(page.getByText("أُعيد فتح الشهر")).toBeVisible();
  await expect(row.getByText("مفتوح", { exact: true })).toBeVisible();
  await expect(row.getByText("فاتورة مورد متأخرة عن هذا الشهر")).toBeVisible();
});

test("online payment: the customer pays through a link and the invoice is paid when the provider confirms", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("paylink"));
  await createOrg(page, "شركة الدفع");
  const call = await apiFor(page);

  await nav(page, "المؤسسة");
  const provider = page.getByLabel("مزوّد الدفع");
  await provider.selectOption({ label: "Stripe" });
  await expect(page.getByLabel("عنوان الـ Webhook")).toHaveValue(/\/api\/v1\/public\/payments\/stripe\/nvpay_/);
  await provider.selectOption({ label: "ميسّر (مدى، Apple Pay، البطاقات)" });
  await expect(page.getByLabel("عنوان الـ Webhook")).toHaveValue(/\/api\/v1\/public\/payments\/moyasar\/nvpay_/);
  await expect(page.getByLabel(/الرمز السري للـ Webhook/)).toBeVisible();
  await provider.selectOption({ label: "هايبر باي (مدى، فيزا، ماستركارد)" });
  await expect(page.getByLabel("عنوان الـ Webhook")).toHaveValue(/\/api\/v1\/public\/payments\/hyperpay\/nvpay_/);
  await page.getByLabel("رمز الوصول").fill("OGFjN2E0YzlkZDk5ZTRkMTAxZGRhZjRlNDY4MTAzZmF8");
  await page.getByLabel("معرّف الكيان لمدى").fill("8ac7a4c9dd99e4d101ddaf4e46810400");
  await page.getByRole("button", { name: "حفظ إعدادات الدفع" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();
  await expect(page.getByText(/محفوظ؛.*OGFjN2…/)).toBeVisible();
  await provider.selectOption({ label: "PayTabs (مدى، فيزا، ماستركارد)" });
  await page.getByLabel("رقم الملف (Profile ID)").fill("98765");
  await page.getByLabel("منطقة الحساب").selectOption({ label: "السعودية" });
  await page.getByLabel(/مفتاح الخادم/).fill("SJN999999999-JDMMMMMMM-6KKKKKKK");
  await page.getByRole("button", { name: "حفظ إعدادات الدفع" }).click();
  await expect(page.getByText(/محفوظ؛.*SJN9…KKKK/)).toBeVisible();
  await expect(page.getByText("تم الحفظ")).toHaveCount(0);        // its toast gone before the next save's
  await provider.selectOption({ label: "مزوّد تجريبي (للتطوير فقط)" });
  await page.getByRole("button", { name: "حفظ إعدادات الدفع" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();

  const customer = await call("POST", "/customers", { name: "مؤسسة الواحة" });
  const inv = await call("POST", "/invoices", {
    customer_id: customer.id, issue_date: "2026-04-01",
    lines: [{ description: "تصميم شعار", quantity: "1", unit_price: "1200" }],
  });
  await call("POST", `/invoices/${inv.id}/post`);

  await nav(page, "الفواتير");
  await page.getByRole("link", { name: /^INV-2026-/ }).click();
  await page.getByRole("button", { name: "إنشاء رابط دفع" }).click();
  const links = page.getByRole("table", { name: "الدفع الإلكتروني" });
  await expect(links.getByText("مفتوحة")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/payment-link-ar.png", fullPage: true });

  const [checkout] = await Promise.all([page.waitForEvent("popup"), links.getByRole("link", { name: "فتح" }).click()]);
  await expect(checkout.getByText("1200.00 SAR")).toBeVisible();
  await checkout.getByRole("button", { name: /ادفع \(تجريبي\)/ }).click();
  await expect(checkout.getByRole("heading", { name: "شكراً لك، وصلتنا دفعتك" })).toBeVisible();
  await checkout.close();

  // The open link is checked every few seconds; the invoice turns paid without reloading.
  await expect(links.getByText("مدفوعة")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("cell", { name: "إلكترونياً (رابط دفع)" })).toBeVisible();
  await expect(page.getByRole("button", { name: "إنشاء رابط دفع" })).toHaveCount(0);
});

test("e-invoicing: onboard the device with ZATCA, then a posted invoice is signed and reported", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("zatca"));
  await createOrg(page, "شركة الفوترة");

  await nav(page, "المؤسسة");
  await page.getByLabel(/الرقم الضريبي/).fill("399999999900003");
  await page.getByRole("button", { name: "حفظ", exact: true }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();
  await page.getByLabel("رقم السجل التجاري").fill("1010010000");
  await page.getByLabel("الشارع").fill("الأمير سلطان");
  await page.getByLabel("رقم المبنى").fill("2322");
  await page.getByLabel("الحي").fill("المربع");
  await page.getByLabel("المدينة").fill("الرياض");
  await page.getByLabel("الرمز البريدي").fill("23333");
  await page.getByLabel(/رمز التحقق لمرة واحدة/).fill("123345");
  await page.getByRole("button", { name: "ربط الجهاز" }).click();
  await expect(page.getByText("رُبط الجهاز", { exact: true })).toBeVisible();
  await expect(page.getByText(/رُبط الجهاز في بيئة المطوّرين التجريبية/)).toBeVisible();
  await page.screenshot({ path: "test-results/screens/einvoicing-ar.png", fullPage: true });

  const call = await apiFor(page);
  const tax = await call("POST", "/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  const customer = await call("POST", "/customers", { name: "عميل نقدي" });
  const inv = await call("POST", "/invoices", {
    customer_id: customer.id, issue_date: new Date().toISOString().slice(0, 10),
    lines: [{ description: "قهوة عربية", quantity: "2", unit_price: "50", tax_id: tax.id }],
  });
  await nav(page, "الفواتير");
  await page.getByRole("link", { name: /مسودة|INV-/ }).first().click();
  await page.getByRole("button", { name: "ترحيل الفاتورة" }).click();
  await expect(page.getByText("أُبلغت الهيئة")).toBeVisible();
  await expect(page.getByText(/فاتورة ضريبية مبسطة \(إبلاغ\)/)).toBeVisible();
  await expect(page.getByRole("img", { name: "رمز QR للفاتورة الإلكترونية" })).toBeVisible();
  expect(inv.id).toBeTruthy();
});

test("multi-currency: a dollar invoice is booked in riyals and its payment shows the exchange difference", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("fx"));
  await createOrg(page, "شركة العملات");
  const call = await apiFor(page);
  await call("POST", "/customers", { name: "Gulf Trading LLC" });
  const orgPath = page.url().match(/\/o\/[0-9a-f-]+/)![0];
  await page.goto(`${orgPath}/invoices/new`);
  await page.getByLabel("العميل").selectOption({ label: "Gulf Trading LLC" });
  await page.getByLabel("العملة").selectOption("USD");
  await page.getByLabel(/سعر الصرف/).fill("3.75");
  await page.getByLabel("الوصف 1").fill("Consulting");
  await page.getByLabel("الكمية 1").fill("10");
  await page.getByLabel("سعر الوحدة 1").fill("100");
  await page.getByRole("button", { name: "حفظ كمسودة" }).click();
  await page.getByRole("button", { name: "ترحيل الفاتورة" }).click();
  await expect(page.getByText(/الإجمالي بـ/)).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: "3,750.00" }).first()).toBeVisible();

  await page.getByRole("button", { name: "تسجيل دفعة" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/سعر الصرف يوم الدفع/)).toHaveValue("3.75");
  await dialog.getByLabel(/سعر الصرف يوم الدفع/).fill("3.80");
  await dialog.getByRole("button", { name: "تسجيل دفعة" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("مدفوعة").first()).toBeVisible();
  await expect(page.getByText(/فرق صرف/)).toContainText("50.00");                  // 1,000 x (3.80 - 3.75)
  await page.screenshot({ path: "test-results/screens/invoice-usd-ar.png", fullPage: true });
});

test("debit note: more is charged on a posted invoice, numbered DN- and linked both ways", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("debit"));
  await createOrg(page, "شركة الإشعارات");
  const call = await apiFor(page);
  const customer = await call("POST", "/customers", { name: "مؤسسة الواحة" });
  const inv = await call("POST", "/invoices", {
    customer_id: customer.id, issue_date: "2026-04-01",
    lines: [{ description: "تصميم شعار", quantity: "1", unit_price: "1200" }],
  });
  const posted = await call("POST", `/invoices/${inv.id}/post`);
  const orgPath = page.url().match(/\/o\/[0-9a-f-]+/)![0];

  await page.goto(`${orgPath}/invoices/${inv.id}`);
  await page.getByRole("link", { name: "إشعار مدين" }).click();
  await expect(page.getByRole("heading", { name: `إشعار مدين على ⁦${posted.number}⁩` })).toBeVisible();
  await expect(page.getByLabel("العميل")).toBeDisabled();
  await page.getByLabel("سبب المبلغ الإضافي").fill("مراجعة إضافية لم تُذكر في الفاتورة");
  await page.getByLabel("الوصف 1").fill("مراجعة إضافية");
  await page.getByLabel("الكمية 1").fill("1");
  await page.getByLabel("سعر الوحدة 1").fill("300");
  await page.getByRole("button", { name: "حفظ كمسودة" }).click();
  await expect(page.getByText("يعدّل الفاتورة")).toBeVisible();
  await page.getByRole("button", { name: "ترحيل الفاتورة" }).click();
  await expect(page.getByRole("heading", { name: /^DN-2026-00001/ })).toBeVisible();
  await expect(page.getByText("إشعار مدين").first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/debit-note-ar.png", fullPage: true });

  await page.getByRole("link", { name: posted.number }).click();
  await expect(page.getByRole("heading", { name: "الإشعارات المدينة" })).toBeVisible();
  await expect(page.getByRole("link", { name: "DN-2026-00001" })).toBeVisible();
});

test("currencies: a stored rate is suggested on invoices, and what is owed in dollars is revalued at the month's end", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("reval"));
  await createOrg(page, "شركة التقييم");
  const call = await apiFor(page);
  const customer = await call("POST", "/customers", { name: "Gulf Trading LLC" });
  const inv = await call("POST", "/invoices", { customer_id: customer.id, issue_date: "2026-04-01", currency: "USD", exchange_rate: "3.75",
    lines: [{ description: "Consulting", quantity: "10", unit_price: "100" }] });
  const posted = await call("POST", `/invoices/${inv.id}/post`);                          // 1,000 USD = 3,750.00
  const orgPath = page.url().match(/\/o\/[0-9a-f-]+/)![0];

  await nav(page, "المحاسبة");
  await page.getByRole("tab", { name: "العملات" }).click();
  await page.getByLabel("العملة").selectOption("USD");
  await page.getByLabel("اليوم", { exact: true }).fill("2026-04-30");
  await page.getByLabel("السعر", { exact: true }).fill("3.76");
  await page.getByRole("button", { name: "حفظ السعر" }).click();
  await expect(page.getByRole("table", { name: "أسعار الصرف" }).getByRole("cell", { name: "3.76" })).toBeVisible();

  await page.getByLabel("في يوم").fill("2026-04-30");
  const preview = page.getByRole("table", { name: "إعادة تقييم ما يستحق على العملاء بعملات أخرى" });
  await expect(preview.getByRole("link", { name: posted.number })).toBeVisible();
  await expect(preview.getByRole("row", { name: new RegExp(posted.number) })).toContainText("3,760.00");
  await expect(page.locator("p", { hasText: "صافي الفرق غير المحقق" })).toContainText("10.00");
  await page.getByRole("button", { name: "قيد إعادة التقييم" }).click();
  await expect(page.getByText("قُيّدت إعادة التقييم").first()).toBeVisible();
  const history = page.getByRole("table", { name: "عمليات إعادة التقييم" });
  await expect(history.getByText("USD 3.76")).toBeVisible();
  await expect(page.getByRole("button", { name: "قيد إعادة التقييم" })).toHaveCount(0);   // once per day
  await page.screenshot({ path: "test-results/screens/fx-revaluation-ar.png", fullPage: true });
  await history.getByRole("button", { name: "إلغاء" }).click();
  await expect(history.getByText("ملغاة")).toBeVisible();

  // The stored rate is suggested on a new invoice in dollars dated after it.
  await page.goto(`${orgPath}/invoices/new`);
  await page.getByLabel("تاريخ الإصدار").fill("2026-05-02");
  await page.getByLabel("العملة").selectOption("USD");
  await expect(page.getByLabel(/سعر الصرف/)).toHaveValue("3.76");
  await expect(page.getByText(/مقترح من سعر يوم/)).toBeVisible();
});

test("exchange rates: fetched from the European Central Bank for the chosen currencies; a rate set by hand stays", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("ecb"));
  await createOrg(page, "شركة الأسعار");
  const call = await apiFor(page);
  await call("POST", "/exchange-rates", { currency: "EUR", day: "2026-04-30", rate: "4.10" });

  await nav(page, "المحاسبة");
  await page.getByRole("tab", { name: "العملات" }).click();
  const card = page.locator(".card", { has: page.getByRole("heading", { name: "الأسعار من البنك المركزي الأوروبي" }) });
  await card.getByLabel("اجلب الأسعار كل يوم عمل").check();
  for (const c of ["USD", "EUR", "GBP"]) await card.getByLabel(c, { exact: true }).check();
  await card.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();
  await card.getByRole("button", { name: "اجلب الآن" }).click();
  await expect(page.getByText(/لـ 2 عملات/)).toBeVisible();                    // the euro rate was set by hand

  // Riyals per euro = 1.08 x 3.75 (the peg) = 4.05; a pound is 4.05 / 0.86.
  const rates = page.getByRole("table", { name: "أسعار الصرف" });
  await expect(rates.getByRole("row", { name: /GBP/ })).toContainText("4.709302");
  await expect(rates.getByRole("row", { name: /GBP/ })).toContainText("البنك الأوروبي");
  await expect(rates.getByRole("row", { name: /USD/ })).toContainText("3.75");
  await expect(rates.getByRole("row", { name: /EUR/ })).toContainText("4.1");
  await expect(rates.getByRole("row", { name: /EUR/ })).not.toContainText("البنك الأوروبي");
  await expect(card).toContainText("آخر أسعار محفوظة");
  await page.screenshot({ path: "test-results/screens/fx-feed-ar.png", fullPage: true });
});
