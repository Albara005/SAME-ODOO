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

  // Motion: a personality, its preview, and nothing moving for people who ask for reduced motion.
  await page.getByRole("radio", { name: /حيوية/ }).check();
  await expect(page.getByText(/حُدّثت الحركة/)).toBeVisible();
  await expect(page.getByRole("radio", { name: /حيوية/ })).toBeChecked();
  await page.getByLabel("السرعة").selectOption({ label: "أبطأ" });
  await expect(page.getByText("دخول الأقسام \u2066475ms\u2069", { exact: false })).toBeVisible();
  const heading = page.frameLocator('iframe[title="معاينة الحركة"]').getByRole("heading", { name: /مخبز الوردة/ });
  await expect(heading).toBeVisible();
  const animation = () => heading.evaluate((el) => getComputedStyle(el).animationName);
  expect(await animation()).toBe("motion-rise");
  await page.screenshot({ path: "test-results/screens/creative-motion-ar.png", fullPage: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "إعادة التشغيل" }).click();
  await expect.poll(animation).toBe("none");
  await page.emulateMedia({ reducedMotion: null });

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

  const code = page.getByRole("textbox", { name: "الكود" });
  await expect(code).toContainText("<html");
  await code.fill("<!doctype html>\n<html lang=\"ar\" dir=\"rtl\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>تتبع الطلبات</title>\n</head>\n<body><h1>تتبع الطلبات</h1></body>\n</html>\n");
  await page.getByRole("button", { name: "حفظ", exact: true }).click();
  await expect(page.getByText("تغييرات غير محفوظة")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/studio-build-ar.png" });
  await page.getByRole("tab", { name: "معاينة سريعة" }).click();
  await expect(page.frameLocator('iframe[title="معاينة سريعة"]').getByRole("heading", { name: "تتبع الطلبات" })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/studio-preview-ar.png" });
  await page.getByRole("tab", { name: "الكود" }).click();

  await page.getByRole("tab", { name: "التغييرات" }).click();
  await expect(page.locator(".diff .add").filter({ hasText: "<h1>تتبع الطلبات</h1>" })).toBeVisible();

  await page.getByRole("button", { name: "حفظ إصدار" }).click();
  await page.getByRole("dialog").getByLabel(/ما الذي تغيّر/).fill("الصفحة الأولى");
  await page.getByRole("dialog").getByRole("button", { name: "حفظ إصدار" }).click();
  await expect(page.getByText(/حُفظ الإصدار 1/)).toBeVisible();

  await page.getByRole("tab", { name: "الإصدارات" }).click();
  await expect(page.getByText("الفحوص ناجحة")).toBeVisible();
  // The browser check loaded the page on a desktop and a phone screen and kept what it saw.
  const shots = page.getByRole("figure", { name: "الصفحة الرئيسية في المتصفح" });
  await expect(shots.getByRole("img", { name: "شاشة كبيرة" })).toBeVisible();
  await expect(shots.getByRole("img", { name: "هاتف" })).toBeVisible();
  expect(await shots.getByRole("img", { name: "هاتف" }).evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(390);
  await page.screenshot({ path: "test-results/screens/studio-versions-ar.png" });
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

test("App Studio: a data key lets the site read the catalog until it is switched off", async ({ page }) => {
  page.on("dialog", (d) => d.accept()); // window.confirm before switching a key off
  await signUpAndIn(page, uniqueEmail("datakey"));
  await createOrg(page, "متجر البيانات");
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const call = async (path: string, data: object) => {
    const res = await page.request.post(`/api/v1/orgs/${orgId}${path}`, { headers: { Authorization: `Bearer ${token}` }, data });
    expect(res.ok(), await res.text()).toBeTruthy();
    return res.json();
  };
  const tax = await call("/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  await call("/products", { name: "قهوة عربية", sku: "CF-1", kind: "goods", sale_price: "40", default_tax_id: tax.id });

  await nav(page, "استوديو التطبيقات");
  await page.getByRole("button", { name: "تطبيق جديد" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill("Coffee Shop");
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await page.getByRole("tab", { name: "البيانات" }).click();
  await expect(page.getByText(/لا مفاتيح بيانات بعد/)).toBeVisible();

  await page.getByRole("button", { name: "مفتاح بيانات جديد" }).click();
  await page.getByRole("dialog").getByLabel(/توفر كل منتج/).check();
  await page.getByRole("dialog").getByRole("button", { name: "إنشاء المفتاح" }).click();
  const shown = page.getByLabel("المفتاح", { exact: true });
  await expect(shown).toContainText("nvpk_");
  await expect(page.getByLabel("مثال الشيفرة")).toContainText("X-Novara-Key");
  await page.screenshot({ path: "test-results/screens/studio-data-key-ar.png", fullPage: true });
  const key = (await shown.textContent())!.trim();

  // What the published site sees
  const read = () => page.request.get("/api/v1/public/catalog", { headers: { "X-Novara-Key": key } });
  const catalog = await (await read()).json();
  expect(catalog.products).toEqual([expect.objectContaining({ name: "قهوة عربية", price: "40.00", price_with_tax: "46.00", available: false })]);
  await expect(page.getByRole("cell", { name: "التوفر" })).toBeVisible();

  await page.getByRole("button", { name: "إيقاف" }).click();
  await expect(page.getByText("موقوف", { exact: true })).toBeVisible();
  await expect(shown).toHaveCount(0);
  expect((await read()).status()).toBe(401);
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

test("App Studio: an order request from the site becomes a quotation in Sales", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("siteorder"));
  await createOrg(page, "محمصة الموقع");
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const call = async (path: string, data: object) => {
    const res = await page.request.post(`/api/v1/orgs/${orgId}${path}`, { headers: { Authorization: `Bearer ${token}` }, data });
    expect(res.ok(), await res.text()).toBeTruthy();
    return res.json();
  };
  const tax = await call("/taxes", { name: "ضريبة القيمة المضافة", rate: "15" });
  const coffee = await call("/products", { name: "قهوة عربية", sku: "CF-1", kind: "goods", sale_price: "40", default_tax_id: tax.id });
  const app = await call("/studio/projects", { name: "Roastery", slug: "roastery" });

  await page.goto(`/o/${orgId}/studio/${app.id}`);
  await page.getByRole("tab", { name: "البيانات" }).click();
  await page.getByRole("button", { name: "مفتاح بيانات جديد" }).click();
  await page.getByRole("dialog").getByLabel(/استقبال طلبات من الموقع/).check();
  await page.getByRole("dialog").getByRole("button", { name: "إنشاء المفتاح" }).click();
  await expect(page.getByLabel("مثال الشيفرة")).toContainText("/api/v1/public/orders");
  await expect(page.getByLabel("مثال الشيفرة")).toContainText("/api/v1/public/proof.js");
  const key = (await page.getByLabel("المفتاح", { exact: true }).textContent())!.trim();
  await expect(page.getByRole("row", { name: /الموقع/ }).getByText("تحقق من البشر")).toBeVisible();

  // What a visitor's browser sends from the published site: without the bot check it's refused,
  // with it (solved by the real script, in the browser) it's received.
  const order = { name: "مقهى الندى", email: "orders@nada.example.com", note: "التوصيل صباحاً", lines: [{ product_id: coffee.id, quantity: 3 }] };
  expect((await page.request.post("/api/v1/public/orders", { headers: { "X-Novara-Key": key }, data: order })).status()).toBe(403);
  const sent = await page.request.post("/api/v1/public/orders", {
    headers: { "X-Novara-Key": key }, data: { ...order, proof: await solveInBrowser(page, key) } });
  expect(sent.status()).toBe(201);
  const { reference } = await sent.json();

  await page.getByRole("complementary").getByRole("navigation").getByRole("link", { name: /^أوامر البيع/ }).click();
  await expect(page.getByText("طلبات من مواقعك (1)")).toBeVisible();
  await expect(page.getByText(reference)).toBeVisible();
  await expect(page.getByText("التوصيل صباحاً")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/site-orders-ar.png", fullPage: true });
  await page.getByRole("button", { name: "تحويل إلى عرض سعر" }).click();
  await expect(page.getByRole("heading", { name: /^SO-\d{4}-\d{5}$/ })).toBeVisible();
  await expect(page.getByText("مقهى الندى").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "قهوة عربية" })).toBeVisible();
});

/** Solve the bot check the way a published site does, with /api/v1/public/proof.js in the browser. */
async function solveInBrowser(page: Page, key: string): Promise<{ challenge: string; nonce: string }> {
  await page.addScriptTag({ url: "/api/v1/public/proof.js" });
  return page.evaluate((k) => (window as unknown as {
    novaraProof: (base: string, key: string) => Promise<{ challenge: string; nonce: string }>;
  }).novaraProof(location.origin, k), key);
}

/** A day `ahead` days from now on the business's calendar (Riyadh). */
function riyadhDay(ahead: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit" })
    .format(new Date(Date.now() + ahead * 86_400_000));
}

test("Bookings: hours and a service, a site asks, the team confirms and books someone in", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("bookings"));
  await createOrg(page, "عيادة الواحة");
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));

  await nav(page, "الحجوزات");
  await page.getByRole("tab", { name: "الخدمات والمواعيد" }).click();
  await page.getByLabel("اسم الخدمة").fill("استشارة");
  await page.getByLabel(/^السعر/).fill("200");
  await page.getByRole("button", { name: "إضافة خدمة" }).click();
  await expect(page.getByText("استشارة", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "إضافة فترة إلى الأحد" }).click();
  await page.getByRole("button", { name: "حفظ" }).click();
  await expect(page.getByText("تم الحفظ")).toBeVisible();

  // The next Sunday, then what a visitor's browser does on the published site.
  let ahead = 1;
  while (new Date(`${riyadhDay(ahead)}T12:00:00Z`).getUTCDay() !== 0) ahead++;
  const day = riyadhDay(ahead);
  const app = await (await page.request.post(`/api/v1/orgs/${orgId}/studio/projects`, {
    headers: { Authorization: `Bearer ${token}` }, data: { name: "Clinic", slug: "clinic" } })).json();
  const created = await page.request.post(`/api/v1/orgs/${orgId}/studio/projects/${app.id}/data-keys`, {
    headers: { Authorization: `Bearer ${token}` }, data: { name: "Bookings", scopes: ["catalog", "bookings"] } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const h = { "X-Novara-Key": (await created.json()).key as string };
  const { services } = await (await page.request.get("/api/v1/public/booking-services", { headers: h })).json();
  const { times } = await (await page.request.get(`/api/v1/public/free-times?service=${services[0].id}&day=${day}`, { headers: h })).json();
  expect(times[0]).toBe(`${day}T09:00:00+03:00`);
  const sent = await page.request.post("/api/v1/public/bookings", {
    headers: h, data: { service_id: services[0].id, starts_at: times[2], name: "ريم", phone: "+966500000002", note: "الزيارة الأولى",
                        proof: await solveInBrowser(page, h["X-Novara-Key"]) } });
  expect(sent.status()).toBe(201);

  await page.reload();
  await page.getByRole("tab", { name: "الطلبات (1)" }).click();
  await expect(page.getByText("الزيارة الأولى")).toBeVisible();
  await expect(page.getByText("10:00–11:00")).toBeVisible();
  await page.getByRole("button", { name: "تأكيد" }).click();
  await expect(page.getByText("لا طلبات بانتظار التأكيد.")).toBeVisible();

  // The team books someone in at a free time on the same day.
  await page.getByRole("button", { name: "حجز جديد" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/^الخدمة/).selectOption({ label: "استشارة" });
  await dialog.getByLabel(/^اليوم/).fill(day);
  await expect(dialog.getByLabel(/^الوقت/).locator("option", { hasText: "10:00" })).toHaveCount(0);   // held by Reem
  await dialog.getByLabel(/^الوقت/).selectOption({ label: "11:00" });
  await dialog.getByLabel("الاسم", { exact: true }).fill("سلمان");
  await dialog.getByRole("button", { name: "حجز", exact: true }).click();
  await expect(dialog).toHaveCount(0);

  await page.getByRole("tab", { name: "الأسبوع" }).click();
  if (ahead > 6) await page.getByRole("button", { name: "الأسبوع التالي" }).click();
  await expect(page.getByText("ريم", { exact: true })).toBeVisible();
  await expect(page.getByText("سلمان", { exact: true })).toBeVisible();
  await expect(page.getByText("من Clinic")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/bookings-ar.png", fullPage: true });
});

test("App Studio: the site on the organization's own domain, proven with a DNS record", async ({ page }) => {
  await signUpAndIn(page, uniqueEmail("domains"));
  await createOrg(page, "متجر النطاق");
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const token = await page.evaluate(() => localStorage.getItem("novara.token"));
  const app = await (await page.request.post(`/api/v1/orgs/${orgId}/studio/projects`, {
    headers: { Authorization: `Bearer ${token}` }, data: { name: "Shop", slug: "shop" } })).json();
  await page.goto(`/o/${orgId}/studio/${app.id}`);
  await page.getByRole("tab", { name: "النطاقات" }).click();
  await page.getByLabel("النطاق").fill("Shop.Example-Store.sa");
  await page.getByRole("button", { name: "إضافة نطاق" }).click();
  await expect(page.getByRole("heading", { name: "shop.example-store.sa" })).toBeVisible();
  await expect(page.getByText("بانتظار DNS")).toBeVisible();
  const txt = page.getByRole("row", { name: /^TXT/ });
  await expect(txt).toContainText("_novara-challenge.shop.example-store.sa");
  const value = (await txt.locator("td").nth(2).textContent())!.trim();
  await page.screenshot({ path: "test-results/screens/site-domains-ar.png", fullPage: true });

  await page.getByRole("button", { name: "تحقّق الآن" }).click();
  await expect(page.getByText("سجل DNS لم يظهر بعد")).toBeVisible();
  await page.request.post(`/api/e2e/dns?name=_novara-challenge.shop.example-store.sa&value=${encodeURIComponent(value)}`);
  await page.getByRole("button", { name: "تحقّق الآن" }).click();
  await expect(page.getByText("اتصل النطاق")).toBeVisible();
  await expect(page.getByText("متصل", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "https://shop.example-store.sa/" })).toBeVisible();
});
