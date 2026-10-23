import { expect, test, type Page } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

/** Menu links only (not the organization card); a link's name may end with its count badge. */
const nav = (page: Page, name: string) =>
  page.getByRole("complementary").getByRole("navigation").getByRole("link", { name: new RegExp(`^${name}`) }).click();

async function apiFor(page: Page) {
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const headers = { Authorization: `Bearer ${token}` };
  return async (method: "GET" | "POST", path: string, data?: object) => {
    const res = await page.request.fetch(`/api/v1/orgs/${orgId}${path}`, { method, headers, data });
    expect(res.ok(), await res.text()).toBeTruthy();
    return res.json();
  };
}

test("assistant proposes, the user approves, the customer exists", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("ai"));
  await createOrg(page, "شركة المساعد");
  await nav(page, "المساعد");
  await expect(page.getByRole("heading", { name: "كيف أساعدك اليوم؟" })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/assistant-welcome-ar.png" });

  await page.getByLabel("الرسالة").fill("أضف عميل شركة الريادة");
  await page.getByLabel("الرسالة").press("Enter");
  await expect(page.getByText("جهّزت الطلب")).toBeVisible();
  const card = page.locator(".action-card");
  await expect(card).toContainText("إنشاء عميل");
  await expect(card).toContainText("شركة الريادة");
  await expect(card).toContainText("بانتظار موافقتك");
  await page.screenshot({ path: "test-results/screens/assistant-ar.png" });
  await card.getByRole("button", { name: "موافقة" }).click();
  await expect(card).toContainText("تمت الموافقة");

  // The conversation is kept, and the customer really exists
  await expect(page.getByRole("button", { name: /أضف عميل شركة الريادة/ })).toBeVisible();
  await nav(page, "العملاء");
  await expect(page.getByRole("cell", { name: "شركة الريادة", exact: true })).toBeVisible();
});

test("workflow approval, tasks and notifications", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("flow"));
  await createOrg(page, "شركة الأتمتة");
  const call = await apiFor(page);

  await call("POST", "/workflows", {
    name: "متابعة الفواتير الكبيرة",
    trigger: "invoice.posted",
    actions: [
      { type: "create_task", title: "اتصل بالعميل بخصوص {{number}}", assignee: "actor", due_in_days: 3 },
      { type: "require_approval", permission: "payments.write", message: "هل نرسل تذكيراً للفاتورة {{number}}؟" },
      { type: "notify", to: "actor", title: "تمت الموافقة على متابعة {{number}}" },
    ],
  });
  const customer = await call("POST", "/customers", { name: "مؤسسة النور" });
  const invoice = await call("POST", "/invoices", {
    customer_id: customer.id, issue_date: "2026-10-01", lines: [{ description: "استشارة", quantity: "1", unit_price: "1000" }],
  });
  await call("POST", `/invoices/${invoice.id}/post`);
  await call("POST", "/automation/process");
  await page.reload();

  // The menu shows what is waiting
  const sidebar = page.getByRole("complementary");
  await expect(sidebar.getByRole("link", { name: /^الموافقات/ })).toContainText("1");

  await nav(page, "الموافقات");
  await expect(page.getByText("متابعة الفواتير الكبيرة")).toBeVisible();
  await expect(page.getByText(/هل نرسل تذكيراً للفاتورة INV-2026-/)).toBeVisible();
  await page.screenshot({ path: "test-results/screens/approvals-ar.png" });
  await page.getByRole("button", { name: "موافقة" }).click();
  await expect(page.getByText("لا شيء بانتظارك.")).toBeVisible();

  await nav(page, "الإشعارات");
  // The page's own list (a live toast for the same notification may be on screen too).
  await expect(page.getByRole("main").getByText(/تمت الموافقة على متابعة INV-2026-/)).toBeVisible();
  await page.getByRole("button", { name: "تحديد كمقروء" }).click();
  await expect(page.getByRole("button", { name: "تحديد كمقروء" })).toHaveCount(0);

  await nav(page, "المهام");
  const task = page.getByRole("checkbox", { name: /اتصل بالعميل بخصوص INV-2026-/ });
  await expect(task).toBeVisible();
  await page.screenshot({ path: "test-results/screens/tasks-ar.png" });
  await task.check();
  await expect(page.getByText("لا شيء متبقٍ. أحسنت!")).toBeVisible();
  await page.getByRole("tab", { name: "منجزة" }).click();
  await expect(page.getByRole("checkbox", { name: /اتصل بالعميل/ })).toBeChecked();
});

test("AI settings: switch off, budget and model per task", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("aiset"));
  await createOrg(page, "شركة الإعدادات");
  await nav(page, "إعدادات الذكاء الاصطناعي");
  await page.getByLabel("الميزانية (دولار شهرياً)").fill("50");
  await page.getByLabel("النموذج assistant", { exact: true }).selectOption("claude-haiku-5-5");
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("النموذج assistant", { exact: true })).toHaveValue("claude-haiku-5-5");
  await page.screenshot({ path: "test-results/screens/ai-settings-ar.png", fullPage: true });

  await page.getByLabel(/ميزات الذكاء الاصطناعي مفعّلة/).uncheck();
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();
  await nav(page, "المساعد");
  await page.getByLabel("الرسالة").fill("مرحبا");
  await page.getByRole("button", { name: "إرسال" }).click();
  await expect(page.getByRole("alert")).toContainText("المساعد الذكي متوقف");
});

test("live: a new notification arrives without reloading", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("live"));
  await createOrg(page, "شركة اللحظة");
  const call = await apiFor(page);
  await call("POST", "/workflows", {
    name: "ترحيب", trigger: "customer.created",
    actions: [{ type: "notify", to: "owner", title: "عميل جديد: {{name}}" }],
  });
  const sidebar = page.getByRole("complementary");
  await expect(sidebar.getByRole("link", { name: /^الإشعارات/ })).not.toContainText(/\d/);

  await call("POST", "/customers", { name: "مؤسسة الضحى" });
  await call("POST", "/automation/process");
  await expect(page.getByText("إشعار جديد: عميل جديد: مؤسسة الضحى")).toBeVisible();
  await expect(sidebar.getByRole("link", { name: /^الإشعارات/ })).toContainText("1");
});

/** A Sunday-to-Saturday week at least two weeks ahead, inside one calendar year. */
function leaveWeek() {
  const start = new Date();
  start.setUTCDate(start.getUTCDate() + 14);
  while (start.getUTCDay() !== 0) start.setUTCDate(start.getUTCDate() + 1);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  if (end.getUTCFullYear() !== start.getUTCFullYear()) {
    start.setUTCDate(start.getUTCDate() + 7);
    end.setUTCDate(end.getUTCDate() + 7);
  }
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end), year: String(start.getUTCFullYear()) };
}

test("leave: HR sets up a department, the employee asks, the manager approves", async ({ page, browser }) => {
  const week = leaveWeek();
  const employee = uniqueEmail("huda");
  const empPage = await browser.newPage();
  await signUpAndIn(empPage, employee, "Huda Saleh");

  await signUpAndIn(page, uniqueEmail("hr-owner"));
  await createOrg(page, "استوديو النخلة");
  const call = await apiFor(page);
  await call("POST", "/members", { email: employee, role: "member" });

  await nav(page, "الموارد البشرية");
  await page.getByRole("tab", { name: "الإعدادات" }).click();
  await page.getByLabel("اسم القسم").fill("التصميم");
  await page.getByLabel(/^المدير/).selectOption({ label: "Sara Ahmed" });
  await page.getByRole("button", { name: "إضافة قسم" }).click();
  await expect(page.getByText("التصميم", { exact: true })).toBeVisible();
  await page.getByLabel("اسم النوع").fill("سنوية");
  await page.getByLabel(/الأيام في السنة/).fill("21");
  await page.getByRole("button", { name: "إضافة نوع" }).click();
  await expect(page.getByText("سنوية", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "الموظفون" }).click();
  await page.getByRole("button", { name: "إضافة موظف" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("هدى صالح");
  await dialog.getByLabel(/المسمى الوظيفي/).fill("مصممة");
  await dialog.getByLabel(/^القسم/).selectOption({ label: "التصميم" });
  await dialog.getByLabel(/حساب العضو/).selectOption({ label: "Huda Saleh" });
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(page.getByRole("cell", { name: /^هدى صالح/ })).toBeVisible();

  // The employee asks for Sunday to Saturday: Friday and Saturday are the weekend.
  await empPage.goto("/orgs");
  await empPage.getByRole("link", { name: /استوديو النخلة/ }).click();
  await nav(empPage, "الموارد البشرية");
  await empPage.getByLabel("السنة").selectOption(week.year);
  await expect(empPage.getByText("المتبقي 21 من 21 يوم")).toBeVisible();
  await empPage.getByRole("button", { name: "طلب إجازة" }).click();
  dialog = empPage.getByRole("dialog");
  await dialog.getByLabel(/نوع الإجازة/).selectOption({ label: "سنوية" });
  await dialog.getByLabel("من", { exact: true }).fill(week.start);
  await dialog.getByLabel("إلى", { exact: true }).fill(week.end);
  await expect(dialog.getByRole("status")).toContainText("5 يوم عمل");
  await dialog.getByLabel(/السبب/).fill("سفر عائلي");
  await dialog.getByRole("button", { name: "إرسال الطلب" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(empPage.getByText("بانتظار القرار", { exact: true })).toBeVisible();
  await expect(empPage.getByText("5 يوم بانتظار القرار")).toBeVisible();

  // The department's manager decides it.
  await page.getByRole("tab", { name: "الطلبات" }).click();
  await expect(page.getByRole("cell", { name: /^هدى صالح/ })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/hr-ar.png", fullPage: true });
  await page.getByRole("button", { name: "موافقة" }).click();
  await expect(page.getByText("لا توجد طلبات هنا.")).toBeVisible();

  await empPage.reload();
  await empPage.getByLabel("السنة").selectOption(week.year);
  await expect(empPage.getByText("موافق عليها", { exact: true })).toBeVisible();
  await expect(empPage.getByText("المتبقي 16 من 21 يوم")).toBeVisible();
  await empPage.close();
});
