import { expect, test } from "@playwright/test";
import { signUpAndIn, uniqueEmail } from "./helpers";

test("installable: a manifest, a service worker, an offline page, and push on the account page", async ({ page, context }) => {
  await context.grantPermissions(["notifications"]);
  await signUpAndIn(page, uniqueEmail("pwa"));

  const href = await page.locator('link[rel="manifest"]').getAttribute("href");
  const manifest = await (await page.request.get(href!)).json();
  expect(manifest).toMatchObject({ lang: "ar", dir: "rtl", display: "standalone", start_url: "/orgs" });
  for (const icon of manifest.icons as { src: string }[]) {
    expect((await page.request.get(icon.src)).ok()).toBeTruthy();
  }

  // The worker takes control with the offline page already cached. (Playwright's offline mode doesn't reach a
  // service worker's own requests, so the fallback itself can't be exercised here.)
  await expect.poll(() => page.evaluate(async () => (await navigator.serviceWorker.ready).active?.scriptURL)).toContain("/sw.js");
  await page.reload();
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  const offline = await page.evaluate(async () => (await caches.match("/offline.html"))?.text());
  expect(offline).toContain("لا يوجد اتصال بالإنترنت");

  await page.goto("/account");
  const card = page.locator(".card", { has: page.getByRole("heading", { name: "الإشعارات على هذا الجهاز" }) });
  await expect(card.getByRole("button", { name: "تفعيل على هذا الجهاز" })).toBeEnabled();
  const key = await page.request.get("/api/v1/push/key");
  expect((await key.json()).public_key).toMatch(/^[A-Za-z0-9_-]{87}$/);
});
