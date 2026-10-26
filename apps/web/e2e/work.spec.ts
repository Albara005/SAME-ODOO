import { expect, test, type Page } from "@playwright/test";
import { createOrg, signUpAndIn, uniqueEmail } from "./helpers";

/** Menu links only (not the organization card); a link's name may end with its count badge. */
const nav = (page: Page, name: string) =>
  page.getByRole("complementary").getByRole("navigation").getByRole("link", { name: new RegExp(`^${name}`) }).click();

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

  // A morning off the following Sunday: half a day.
  const nextSunday = new Date(`${week.start}T12:00:00Z`);
  nextSunday.setUTCDate(nextSunday.getUTCDate() + 7);
  const half = nextSunday.toISOString().slice(0, 10);
  await empPage.getByRole("button", { name: "طلب إجازة" }).click();
  dialog = empPage.getByRole("dialog");
  await dialog.getByLabel(/نوع الإجازة/).selectOption({ label: "سنوية" });
  await dialog.getByLabel("من", { exact: true }).fill(half);
  await dialog.getByLabel("إلى", { exact: true }).fill(half);
  await dialog.getByLabel("يوم كامل أو نصف يوم").selectOption({ label: "الصباح (نصف يوم)" });
  await expect(dialog.getByRole("status")).toContainText("0.5 يوم عمل");
  await dialog.getByRole("button", { name: "إرسال الطلب" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(empPage.getByRole("cell", { name: /0\.5 · صباحاً/ })).toBeVisible();
  await empPage.close();
});

test("payroll: a contribution, an employee's pay, the month's run posted and paid, and the payslip on the HR page", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("payroll"));
  await createOrg(page, "شركة الرواتب");
  const call = await apiFor(page);
  const [me] = await call("GET", "/members");
  await call("POST", "/hr/employees", { full_name: "هدى صالح", hire_date: "2025-01-01", user_id: me.user_id });

  await nav(page, "الرواتب");
  await page.getByRole("tab", { name: "الاشتراكات" }).click();
  await page.getByRole("button", { name: "اشتراك جديد" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم").fill("التأمينات الاجتماعية");
  await dialog.getByLabel("حصة الموظف").fill("9.75");
  await dialog.getByLabel("حصة صاحب العمل").fill("11.75");
  await dialog.getByLabel(/الحد الأعلى الشهري/).fill("45000");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("cell", { name: "التأمينات الاجتماعية" })).toBeVisible();

  await page.getByRole("tab", { name: "رواتب الموظفين" }).click();
  await page.getByRole("button", { name: "تعديل راتب هدى صالح" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("رقم الآيبان").fill("SA03 8000 0000 6080 1016 7519");
  await dialog.getByLabel("مبلغ البند 1").fill("10000");
  await dialog.getByRole("button", { name: "إضافة بند" }).click();
  await dialog.getByLabel("اسم البند 2").fill("بدل سكن");
  await dialog.getByLabel("مبلغ البند 2").fill("2500");
  await dialog.getByRole("row").nth(2).getByLabel("نعم").check();
  await dialog.getByLabel(/التأمينات الاجتماعية/).check();
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByRole("cell", { name: /12,500\.00/ })).toBeVisible();

  await page.getByRole("tab", { name: "مسيرات الرواتب" }).click();
  await page.getByLabel("الشهر").fill("2026-09");
  await page.getByRole("button", { name: "إعداد مسير الشهر" }).click();
  const slips = page.getByRole("table", { name: "قسائم الرواتب" });
  await expect(slips.getByRole("row", { name: /هدى صالح/ })).toContainText("11,281.25");   // 12,500 less 9.75%
  await page.getByRole("button", { name: "ترحيل إلى الدفاتر" }).click();
  await expect(page.getByText("رُحِّل المسير")).toBeVisible();
  await page.getByRole("button", { name: "صرف الرواتب" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "صرف الرواتب" }).click();
  await expect(page.getByText("سُجّل صرف الرواتب")).toBeVisible();
  await expect(page.getByRole("table", { name: "مسيرات الرواتب" }).getByText("مدفوعة")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/payroll-ar.png", fullPage: true });

  await nav(page, "الموارد البشرية");
  await page.getByRole("tab", { name: "إجازاتي" }).click();
  await page.getByRole("button", { name: /قسيمة راتب/ }).click();
  await expect(page.getByText("التأمينات الاجتماعية")).toBeVisible();
  await expect(page.getByText("11,281.25").first()).toBeVisible();
});

test("end of service: Saudi rules, the provision on each run, a resignation settled and paid", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("eos"));
  await createOrg(page, "شركة نهاية الخدمة");
  const call = await apiFor(page);
  const huda = await call("POST", "/hr/employees", { full_name: "هدى صالح", hire_date: "2024-10-01" });
  await call("PUT", `/payroll/employees/${huda.id}`, { iban: "", contribution_ids: [], items: [
    { name: "الراتب الأساسي", kind: "earning", amount: "10000", contributory: true, end_of_service: true },
    { name: "بدل سكن", kind: "earning", amount: "2500", contributory: true, end_of_service: true },
    { name: "بدل نقل", kind: "earning", amount: "500" },
  ] });

  await nav(page, "الرواتب");
  await page.getByRole("tab", { name: "نهاية الخدمة" }).click();
  await expect(page.getByText(/اختر القواعد أولاً/)).toBeVisible();
  await page.getByLabel("قواعد مكافأة نهاية الخدمة").selectOption({ label: "السعودية: نظام العمل، المادتان 84 و85" });
  await expect(page.getByText("حُفظت قواعد نهاية الخدمة")).toBeVisible();
  await expect(page.getByText(/نصف شهر عن كل سنة/)).toBeVisible();

  // Two years of service by the end of September: the provision is one month's wage (basic + housing).
  await page.getByRole("tab", { name: "مسيرات الرواتب" }).click();
  await page.getByLabel("الشهر").fill("2026-09");
  await page.getByRole("button", { name: "إعداد مسير الشهر" }).click();
  await page.getByRole("button", { name: "ترحيل إلى الدفاتر" }).click();
  await expect(page.getByText("رُحِّل المسير")).toBeVisible();
  await expect(page.getByText("التغيّر في مخصص نهاية الخدمة")).toBeVisible();
  await expect(page.locator("dd", { hasText: "12,500.00" }).first()).toBeVisible();
  const october = await call("POST", "/payroll/runs", { month: "2026-10" });
  await call("POST", `/payroll/runs/${october.id}/post`);

  await page.getByRole("tab", { name: "نهاية الخدمة" }).click();
  await page.getByRole("button", { name: "تسوية جديدة" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("الموظف").selectOption({ label: "هدى صالح" });
  await dialog.getByLabel("آخر يوم عمل").fill("2026-11-15");
  await dialog.getByLabel("سبب انتهاء الخدمة").selectOption({ label: "استقالة" });
  await dialog.getByLabel("أيام الإجازة غير المستخدمة").fill("5");
  await expect(dialog.getByText("6,512.55")).toBeVisible();          // a third of the award (resigned after 2 years) + 5 days
  await expect(dialog.getByText("4,429.22")).toBeVisible();
  await expect(dialog.getByText("1/3")).toBeVisible();
  await dialog.getByRole("button", { name: "حفظ كمسودة" }).click();

  const card = page.locator(".card", { has: page.getByRole("heading", { name: "تسوية هدى صالح" }) });
  await card.getByRole("button", { name: "ترحيل التسوية" }).click();
  await expect(page.getByText("رُحّلت التسوية")).toBeVisible();
  await card.getByRole("button", { name: "صرف المستحقات" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "صرف المستحقات" }).click();
  await expect(page.getByText("سُجّل صرف المستحقات")).toBeVisible();
  await expect(page.getByRole("table", { name: "تسويات نهاية الخدمة" }).getByText("مدفوعة")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/end-of-service-ar.png", fullPage: true });
  expect((await call("GET", `/hr/employees/${huda.id}`)).end_date).toBe("2026-11-15");
});

test("attendance: checking in and out, HR's hours on a day off, overtime approved and paid with the month", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  await signUpAndIn(page, uniqueEmail("attendance"));
  await createOrg(page, "شركة الحضور");
  const call = await apiFor(page);
  const [me] = await call("GET", "/members");
  await call("POST", "/hr/employees", { full_name: "مدير الشركة", hire_date: "2025-01-01", user_id: me.user_id });
  const huda = await call("POST", "/hr/employees", { full_name: "هدى صالح", hire_date: "2025-01-01" });
  await call("PUT", `/payroll/employees/${huda.id}`, { iban: "", contribution_ids: [], items: [
    { name: "الراتب الأساسي", kind: "earning", amount: "6000", basic: true },
    { name: "بدل سكن", kind: "earning", amount: "1500" },
  ] });

  // How overtime is paid is payroll's choice.
  await nav(page, "الرواتب");
  await page.getByRole("tab", { name: "رواتب الموظفين" }).click();
  await page.getByLabel("طريقة دفع الساعات الإضافية").selectOption({ label: "نظام العمل السعودي (المادة 107)" });
  await expect(page.getByText("حُفظت طريقة دفع الساعات الإضافية")).toBeVisible();
  await expect(page.getByText(/50% من أجر الساعة الأساسي/)).toBeVisible();

  // Checking in and out from my own page.
  await nav(page, "الموارد البشرية");
  await page.getByRole("tab", { name: "إجازاتي" }).click();
  await page.getByRole("button", { name: "تسجيل الحضور" }).click();
  await expect(page.getByText(/حاضر منذ/)).toBeVisible();
  await page.getByRole("button", { name: "تسجيل الانصراف" }).click();
  await expect(page.getByText("لم تسجّل حضورك.")).toBeVisible();

  // HR puts in Huda's Friday (the weekend): all eight hours are overtime.
  await page.getByRole("tab", { name: "الحضور والإضافي" }).click();
  await page.getByLabel("الشهر").fill("2026-09");
  const row = page.getByRole("table", { name: "الحضور والإضافي" }).getByRole("row", { name: /هدى صالح/ });
  await row.getByRole("button", { name: "السجلات" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "إضافة ساعات" }).click();
  await dialog.getByLabel("اليوم", { exact: true }).fill("2026-09-11");
  await dialog.getByLabel("الحضور", { exact: true }).fill("09:00");
  await dialog.getByLabel("الانصراف", { exact: true }).fill("17:00");
  await dialog.getByRole("button", { name: "حفظ" }).click();
  await expect(dialog.getByRole("cell", { name: "8:00" })).toBeVisible();
  await expect(dialog.getByText("أدخلتها الموارد البشرية")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(row.getByRole("cell").nth(3)).toHaveText("8:00");

  await row.getByRole("button", { name: "اعتماد", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/يُظهر الحضور/)).toBeVisible();
  await dialog.getByLabel("ملاحظة").fill("جرد المستودع");
  await dialog.getByRole("button", { name: "اعتماد", exact: true }).click();
  await expect(page.getByText("اعتُمدت الساعات الإضافية")).toBeVisible();
  await expect(row.getByRole("cell").nth(4)).toHaveText("8:00");

  // The month's run pays it: (7,500 + 3,000) / 30 / 8 = 43.75 an hour.
  await nav(page, "الرواتب");
  await page.getByLabel("الشهر").fill("2026-09");
  await page.getByRole("button", { name: "إعداد مسير الشهر" }).click();
  const slip = page.getByRole("table", { name: "قسائم الرواتب" }).getByRole("row", { name: /هدى صالح/ });
  await expect(slip).toContainText(/8:00.*350\.00/);
  await expect(slip).toContainText("7,850.00");
  await page.getByRole("button", { name: "ترحيل إلى الدفاتر" }).click();
  await expect(page.getByText("رُحِّل المسير")).toBeVisible();

  await nav(page, "الموارد البشرية");
  await page.getByRole("tab", { name: "الحضور والإضافي" }).click();
  await page.getByLabel("الشهر").fill("2026-09");
  await expect(row).toContainText("مدفوع");
  await expect(row.getByRole("button", { name: "اعتماد", exact: true })).toHaveCount(0);
  await page.screenshot({ path: "test-results/screens/attendance-ar.png", fullPage: true });
});
