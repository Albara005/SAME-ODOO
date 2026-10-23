import { expect, test } from "@playwright/test";
import { createOrg, emailedLink, PASSWORD, signIn, signUpAndIn, totp, uniqueEmail } from "./helpers";

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

  await page.getByRole("button", { name: "تسجيل الخروج" }).click();
  await page.getByRole("link", { name: "نسيت كلمة المرور؟" }).click();
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
  const codes = await dialog.getByRole("list", { name: "رموز الاسترداد" }).getByRole("listitem").allTextContents();
  expect(codes).toHaveLength(10);
  await dialog.getByRole("button", { name: "حفظتها" }).click();
  await expect(page.getByText("مفعّل", { exact: true })).toBeVisible();

  // The password alone is no longer enough.
  await page.getByRole("button", { name: "تسجيل الخروج" }).click();
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
  await page.getByRole("button", { name: "تسجيل الخروج" }).click();
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
  await page.getByRole("button", { name: "تسجيل الخروج" }).click();
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
