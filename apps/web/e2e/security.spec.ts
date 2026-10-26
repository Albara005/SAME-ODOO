import { expect, test } from "@playwright/test";
import { createOrg, emailedLink, PASSWORD, signIn, signOut, signUpAndIn, totp, uniqueEmail } from "./helpers";

test("confirm the email, forget the password, choose a new one", async ({ page }) => {
  const email = uniqueEmail("reset");
  await signUpAndIn(page, email);
  await createOrg(page, "شركة الاستعادة");
  const banner = page.locator(".verify-banner");
  await expect(banner).toContainText("أكّد بريدك");

  await page.goto(await emailedLink(page, email, "/verify-email"));
  await expect(page.getByText("تم تأكيد بريدك")).toBeVisible();
  await page.getByRole("link", { name: "متابعة" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
  await expect(banner).toHaveCount(0);

  await signOut(page);
  await page.getByRole("link", { name: "نسيت كلمة المرور؟" }).click();
  // The login page has an email field too: wait for the new page before filling.
  await expect(page.getByRole("heading", { name: "استعادة كلمة المرور" })).toBeVisible();
  await page.getByLabel("البريد الإلكتروني").fill(email);
  await page.getByRole("button", { name: "أرسل الرابط" }).click();
  await expect(page.getByText("الرابط في طريقه إليك")).toBeVisible();

  await page.goto(await emailedLink(page, email, "/reset-password"));
  await page.getByLabel("كلمة المرور الجديدة", { exact: true }).fill("a-brand-new-password");
  await page.getByLabel("أعد كتابة كلمة المرور الجديدة").fill("a-brand-new-passwordX");
  await page.getByRole("button", { name: "احفظ كلمة المرور" }).click();
  await expect(page.getByText("كلمتا المرور غير متطابقتين.")).toBeVisible();
  await page.getByLabel("أعد كتابة كلمة المرور الجديدة").fill("a-brand-new-password");
  await page.getByRole("button", { name: "احفظ كلمة المرور" }).click();
  await expect(page.getByText("تم تغيير كلمة المرور")).toBeVisible();

  await signIn(page, email, PASSWORD);
  await expect(page.getByRole("alert")).toContainText("invalid email or password");
  await signIn(page, email, "a-brand-new-password");
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
});

test("two-step sign-in: set up, sign in with a code or a recovery code, turn off", async ({ page }) => {
  const email = uniqueEmail("mfa");
  await signUpAndIn(page, email);
  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "حسابي" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "هذا الجهاز" })).toBeVisible();

  await page.getByRole("button", { name: "تفعيل التحقق بخطوتين" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("كلمة المرور").fill(PASSWORD);
  await dialog.getByRole("button", { name: "التالي" }).click();
  await expect(dialog.getByRole("img", { name: "رمز QR لتطبيق المصادقة" })).toBeVisible();
  await dialog.getByText("لا يمكنك المسح؟").click();
  const secret = (await dialog.locator(".mfa-secret").textContent())!;
  await page.screenshot({ path: "test-results/screens/mfa-setup-ar.png" });
  await dialog.getByLabel("الرمز").fill(await totp(secret));
  await dialog.getByRole("button", { name: "تفعيل", exact: true }).click();
  // allTextContents doesn't wait: wait for the codes to be on screen first.
  const codeItems = dialog.getByRole("list", { name: "رموز الاسترداد" }).getByRole("listitem");
  await expect(codeItems).toHaveCount(10);
  const codes = await codeItems.allTextContents();
  await dialog.getByRole("button", { name: "حفظتها" }).click();
  await expect(page.getByText("مفعّل", { exact: true })).toBeVisible();

  // The password alone is no longer enough.
  await signOut(page);
  await page.goto("/login");
  await signIn(page, email);
  await expect(page.getByRole("heading", { name: "التحقق بخطوتين" })).toBeVisible();
  await page.getByLabel("الرمز").fill("000000");
  await page.getByRole("button", { name: "تحقّق وادخل" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  // The code used to switch it on can't be used again; the next one works.
  await page.getByLabel("الرمز").fill(await totp(secret, 30));
  await page.getByRole("button", { name: "تحقّق وادخل" }).click();
  await expect(page).toHaveURL(/\/orgs$/);

  // A recovery code works once, and turns it off together with the password.
  await page.goto("/account");
  await signOut(page);
  await page.goto("/login");
  await signIn(page, email);
  await page.getByLabel("الرمز").fill(codes[0]!);
  await page.getByRole("button", { name: "تحقّق وادخل" }).click();
  await expect(page).toHaveURL(/\/orgs$/);
  await page.goto("/account");
  await expect(page.getByText("متبقٍ 9 من رموز الاسترداد")).toBeVisible();
  await page.getByRole("button", { name: "إيقاف التحقق" }).click();
  await dialog.getByLabel("كلمة المرور").fill(PASSWORD);
  await dialog.getByLabel("الرمز").fill(codes[1]!);
  await dialog.getByRole("button", { name: "إيقاف التحقق" }).click();
  await expect(page.getByText("غير مفعّل", { exact: true })).toBeVisible();
});

test("invite by email: someone without an account joins with the invited role", async ({ page }) => {
  const newcomer = uniqueEmail("invited");
  await signUpAndIn(page, uniqueEmail("inviter"));
  await createOrg(page, "شركة الدعوات");
  await page.getByRole("complementary").getByRole("link", { name: "الأعضاء", exact: true }).click();
  await page.getByRole("button", { name: "دعوة بالبريد" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("البريد الإلكتروني").fill(newcomer);
  await dialog.getByLabel("الدور").selectOption({ label: "مشاهد" });
  await dialog.getByRole("button", { name: "أرسل الدعوة" }).click();
  await expect(page.getByText(`أُرسلت الدعوة إلى ${newcomer}`)).toBeVisible();
  await expect(page.getByRole("cell", { name: newcomer, exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/invitations-ar.png", fullPage: true });

  const link = await emailedLink(page, newcomer, "/invite");
  await signOut(page);
  await page.goto(link);
  await expect(page.getByText("انضم إلى شركة الدعوات بدور مشاهد.")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/invite-ar.png" });
  await page.getByRole("link", { name: "إنشاء حساب" }).click();
  await expect(page.getByLabel("البريد الإلكتروني")).toHaveValue(newcomer);
  await page.getByLabel("الاسم الكامل").fill("Omar");
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "إنشاء حساب" }).click();
  await expect(page.getByText("تم إنشاء الحساب")).toBeVisible();
  await expect(page.getByLabel("البريد الإلكتروني")).toHaveValue(newcomer);
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "تسجيل الدخول" }).click();

  // Back on the invitation, now signed in as the invited email.
  await page.getByRole("button", { name: "اقبل وانضم" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
  await expect(page.getByRole("complementary")).toContainText("شركة الدعوات");
  await expect(page.getByRole("complementary").getByRole("link", { name: "العملاء" })).toHaveCount(0);
  await expect(page.locator(".verify-banner")).toHaveCount(0); // the invitation email proved the address
});

test("an organization requires two-step sign-in: members set it up before they get in", async ({ page, browser }) => {
  const member = uniqueEmail("strict-member");
  const memberPage = await browser.newPage();
  await signUpAndIn(memberPage, member, "Omar Ali");

  await signUpAndIn(page, uniqueEmail("strict-owner"));
  await createOrg(page, "شركة الأمان");
  const orgId = page.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const headers = { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem("novara.token"))}` };
  expect((await page.request.post(`/api/v1/orgs/${orgId}/members`, { headers, data: { email: member, role: "sales" } })).ok()).toBeTruthy();

  // The owner can't require it before having it.
  await page.getByRole("complementary").getByRole("link", { name: "المؤسسة", exact: true }).click();
  const requireBox = page.getByLabel("إلزام كل الأعضاء بالتحقق بخطوتين");
  await expect(requireBox).toBeDisabled();
  await expect(page.getByText("يلزم أن يكون دخولك أنت بخطوتين أولاً (تطبيق مصادقة أو مفتاح مرور):")).toBeVisible();
  const setup = await (await page.request.post("/api/v1/me/mfa/setup", { headers, data: { password: PASSWORD } })).json();
  expect((await page.request.post("/api/v1/me/mfa/enable", { headers, data: { code: await totp(setup.secret) } })).ok()).toBeTruthy();
  await page.reload();
  await requireBox.click();
  await expect(page.getByText("أصبح التحقق بخطوتين إلزامياً")).toBeVisible();
  await expect(requireBox).toBeChecked();
  await page.getByRole("complementary").getByRole("link", { name: "الأعضاء", exact: true }).click();
  await expect(page.getByRole("row", { name: new RegExp(member) }).getByText("بلا تحقق بخطوتين")).toBeVisible();

  // The member is asked to set it up, and gets in once it's on.
  await memberPage.goto(`/o/${orgId}`);
  await expect(memberPage.getByRole("heading", { name: "يلزم التحقق بخطوتين" })).toBeVisible();
  await expect(memberPage.getByText(/تطلب شركة الأمان/)).toBeVisible();
  await memberPage.screenshot({ path: "test-results/screens/mfa-required-ar.png" });
  await memberPage.getByRole("link", { name: "التفعيل من حسابي" }).click();
  await memberPage.getByRole("button", { name: "تفعيل التحقق بخطوتين" }).click();
  const dialog = memberPage.getByRole("dialog");
  await dialog.getByLabel("كلمة المرور").fill(PASSWORD);
  await dialog.getByRole("button", { name: "التالي" }).click();
  await dialog.getByText("لا يمكنك المسح؟").click();
  const secret = (await dialog.locator(".mfa-secret").textContent())!;
  await dialog.getByLabel("الرمز").fill(await totp(secret));
  await dialog.getByRole("button", { name: "تفعيل", exact: true }).click();
  const codeItems = dialog.getByRole("list", { name: "رموز الاسترداد" }).getByRole("listitem");
  await expect(codeItems).toHaveCount(10);
  const codes = await codeItems.allTextContents();
  await dialog.getByRole("button", { name: "حفظتها" }).click();

  // While the organization requires it, it can't be switched off.
  await memberPage.getByRole("button", { name: "إيقاف التحقق" }).click();
  await dialog.getByLabel("كلمة المرور").fill(PASSWORD);
  await dialog.getByLabel("الرمز").fill(codes[0]!);
  await dialog.getByRole("button", { name: "إيقاف التحقق" }).click();
  await expect(dialog.getByText(/شركة الأمان تلزم أعضاءها بالتحقق بخطوتين/)).toBeVisible();
  await dialog.getByRole("button", { name: "إلغاء" }).click();

  await memberPage.goto(`/o/${orgId}`);
  await expect(memberPage.getByRole("heading", { level: 1, name: /^(صباح|مساء) الخير/ })).toBeVisible();
  await memberPage.close();
});

test("passkeys: added on the account page, then used to sign in without the password", async ({ page }) => {
  // Chromium's built-in software authenticator stands in for a phone or laptop with a fingerprint reader.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true,
    automaticPresenceSimulation: true } });
  const app = "http://localhost:4173";  // passkeys belong to a host name, not an IP address
  await signUpAndIn(page, uniqueEmail("passkey"), "Sara Ahmed", app);

  await page.goto(`${app}/account`);
  await page.getByRole("button", { name: "إضافة مفتاح مرور" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/^الاسم/).fill("حاسوب العمل");
  await dialog.getByLabel("كلمة المرور").fill(PASSWORD);
  await dialog.getByRole("button", { name: "إنشاء المفتاح" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("حاسوب العمل", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/screens/passkeys-ar.png", fullPage: true });

  await signOut(page);
  await expect(page).toHaveURL(/\/login/);
  await page.getByRole("button", { name: "الدخول بمفتاح مرور" }).click();
  await expect(page).toHaveURL(/\/orgs$/);
  await page.goto(`${app}/account`);
  await expect(page.getByText(/آخر استخدام/)).toBeVisible();
});

test("a passkey meets the organization's two-step requirement; a password-only sign-in can't change sign-in methods", async ({ page, browser }) => {
  const owner = await browser.newPage();
  await signUpAndIn(owner, uniqueEmail("keys-owner"));
  await createOrg(owner, "شركة المفاتيح");
  const orgId = owner.url().match(/\/o\/([0-9a-f-]+)/)![1]!;
  const headers = { Authorization: `Bearer ${await owner.evaluate(() => localStorage.getItem("novara.token"))}` };
  const setup = await (await owner.request.post("/api/v1/me/mfa/setup", { headers, data: { password: PASSWORD } })).json();
  expect((await owner.request.post("/api/v1/me/mfa/enable", { headers, data: { code: await totp(setup.secret) } })).ok()).toBeTruthy();
  expect((await owner.request.patch(`/api/v1/orgs/${orgId}`, { headers, data: { require_mfa: true } })).ok()).toBeTruthy();

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true,
    automaticPresenceSimulation: true } });
  const app = "http://localhost:4173";
  const member = uniqueEmail("keys-member");
  await signUpAndIn(page, member, "Omar Ali", app);
  expect((await owner.request.post(`/api/v1/orgs/${orgId}/members`, { headers, data: { email: member, role: "sales" } })).ok()).toBeTruthy();
  await page.goto(`${app}/account`);
  await page.getByRole("button", { name: "إضافة مفتاح مرور" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("كلمة المرور").fill(PASSWORD);
  await dialog.getByRole("button", { name: "إنشاء المفتاح" }).click();
  await expect(dialog).toHaveCount(0);

  // Signed in again with the password only: the account page won't change sign-in methods...
  await signOut(page);
  await signIn(page, member);
  await expect(page.getByRole("heading", { name: "يلزم التحقق بخطوتين" })).toBeVisible();   // its only organization
  await page.goto(`${app}/account`);
  await expect(page.getByText(/دخلت بكلمة المرور وحدها/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "إضافة مفتاح مرور" })).toBeDisabled();
  await expect(page.getByRole("button", { name: /^حذف / })).toBeDisabled();

  // ...and the organization asks for the passkey, which opens it.
  await page.goto(`${app}/o/${orgId}`);
  await expect(page.getByRole("heading", { name: "يلزم التحقق بخطوتين" })).toBeVisible();
  await expect(page.getByText(/ادخل مجدداً بمفتاح المرور للمتابعة/)).toBeVisible();
  await page.screenshot({ path: "test-results/screens/mfa-required-passkey-ar.png" });
  await page.getByRole("button", { name: "الدخول بمفتاح مرور" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^(صباح|مساء) الخير/ })).toBeVisible();

  await owner.goto(`/o/${orgId}/settings/members`);
  await expect(owner.getByRole("row", { name: new RegExp(member) }).getByText("مفتاح مرور", { exact: true })).toBeVisible();
  await owner.close();
});
