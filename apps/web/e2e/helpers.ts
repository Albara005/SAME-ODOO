import { expect, type Page } from "@playwright/test";

export const PASSWORD = "e2e-password-123";

export function uniqueEmail(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

/** Register through the UI, sign in, and land on the organization picker (on `origin`, by default the base URL). */
export async function signUpAndIn(page: Page, email: string, name = "Sara Ahmed", origin = "") {
  await page.goto(`${origin}/register`);
  await page.getByLabel("الاسم الكامل").fill(name);
  await page.getByLabel("البريد الإلكتروني").fill(email);
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "إنشاء حساب" }).click();
  await expect(page.getByText("تم إنشاء الحساب")).toBeVisible();
  await page.getByLabel("البريد الإلكتروني").fill(email);
  await page.getByLabel("كلمة المرور").fill(PASSWORD);
  await page.getByRole("button", { name: "تسجيل الدخول" }).click();
  await expect(page).toHaveURL(/\/orgs$/);
}

export async function createOrg(page: Page, name: string) {
  await page.getByRole("button", { name: "مؤسسة جديدة" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("الاسم", { exact: true }).fill(name);
  await dialog.getByLabel(/الاسم المختصر/).fill(`org-${Date.now()}`);
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  // A new organization starts at the first-run setup (e2e/setup.spec.ts); skipping it keeps every app on the menu.
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+\/setup$/);
  await page.getByRole("button", { name: "تخطي الإعداد" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]+$/);
}

/** The emailed link to `path` (e.g. "/reset-password"), read from the test server's mailbox. */
export async function emailedLink(page: Page, to: string, path: string): Promise<string> {
  let link: string | undefined;
  await expect
    .poll(async () => {
      const res = await page.request.get(`/api/e2e/mail?to=${encodeURIComponent(to)}`);
      const messages = (await res.json()) as { text: string }[];
      const match = messages.reverse().map((m) => m.text.match(new RegExp(`${path}\\?token=[\\w-]+`))).find(Boolean);
      link = match?.[0];
      return link;
    })
    .toBeTruthy();
  return link!;
}

/** RFC 6238 code (SHA-1, 6 digits, 30 s) for a base32 secret, `offset` seconds from now. */
export async function totp(secret: string, offset = 0): Promise<string> {
  const { createHmac } = await import("node:crypto");
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/[\s=]/g, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor((Date.now() / 1000 + offset) / 30)));
  const mac = createHmac("sha1", key).update(counter).digest();
  const at = mac[mac.length - 1]! & 0x0f;
  return String((mac.readUInt32BE(at) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

export async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.getByLabel("البريد الإلكتروني").fill(email);
  await page.getByLabel("كلمة المرور").fill(password);
  await page.getByRole("button", { name: "تسجيل الدخول" }).click();
}

/** Sign out: from the account menu in the app, or the button on pages outside an organization. */
export async function signOut(page: Page) {
  const direct = page.getByRole("button", { name: "تسجيل الخروج" });
  const menu = page.getByRole("button", { name: /^الحساب:/ });
  await expect(direct.or(menu).first()).toBeVisible();
  if (await direct.isVisible()) return direct.click();
  await menu.click();
  await page.getByRole("menuitem", { name: "تسجيل الخروج" }).click();
}
