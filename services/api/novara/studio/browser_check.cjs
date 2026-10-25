"use strict";
/* Loads a version's pages in headless Chromium, on a desktop and a phone screen, and reports what a visitor would hit.
 *
 * Every request is answered from the version's own files. Nothing reaches the network: requests to other sites are
 * refused in the browser, and as a second wall Chromium gets an unreachable proxy and a resolver that finds no host.
 *
 * stdin:  {"files": {path: base64}, "pages": [path], "screenshot": path, "sandbox": bool}
 * stdout: {"results": [{check, status, message, path}], "screenshots": {desktop, mobile}: base64 PNG, "sandboxed": bool}
 */
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  process.stdout.write(JSON.stringify({ error: "Playwright isn't installed on the server (npm install playwright, then npx playwright install chromium)" }));
  process.exit(1);
}

const ORIGIN = "https://site.check.invalid";
const VIEWPORTS = [["desktop", { width: 1280, height: 800 }], ["mobile", { width: 390, height: 844 }]];
const LOAD_TIMEOUT = 10_000;
const SETTLE = 400;
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8", ".woff2": "font/woff2",
};

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

const extension = (path) => (path.match(/\.[^./]+$/) || [""])[0].toLowerCase();
const short = (text, n = 240) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

async function main() {
  const input = JSON.parse(await readStdin());
  const files = new Map(Object.entries(input.files).map(([p, b]) => [p, Buffer.from(b, "base64")]));
  const results = [];
  const add = (check, status, message, path = "") => results.push({ check, status, message: short(String(message), 300), path });
  const missing = new Set();
  const external = new Set();
  let apiCalls = 0;

  const launch = chromium.launch({
    chromiumSandbox: Boolean(input.sandbox),
    args: [
      "--proxy-server=http://127.0.0.1:9",
      "--proxy-bypass-list=<-loopback>",
      "--host-resolver-rules=MAP * ~NOTFOUND",
      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
      "--disable-background-networking",
    ],
  });
  const browser = await launch.catch((e) => {
    if (!input.sandbox) throw e;
    throw new Error("Chromium couldn't start with its sandbox (the server may run as root or without user namespaces)");
  });
  const screenshots = {};
  try {
    const context = await browser.newContext({ serviceWorkers: "block", acceptDownloads: false, locale: "ar-SA" });
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== ORIGIN) {
        external.add(url.host || url.protocol);
        return route.abort("blockedbyclient");
      }
      let path = decodeURIComponent(url.pathname).replace(/^\/+/, "");
      if (path === "" || path.endsWith("/")) path += "index.html";
      if (path.startsWith("api/")) {                 // live data from NOVARA isn't loaded in the check
        apiCalls += 1;
        return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail": "not available during checks"}' });
      }
      const body = files.get(path);
      if (body === undefined) {
        missing.add(path);
        return route.fulfill({ status: 404, contentType: "text/plain", body: "not found" });
      }
      return route.fulfill({ status: 200, body, headers: { "content-type": TYPES[extension(path)] || "application/octet-stream" } });
    });

    for (const path of input.pages) {
      const found = new Map();       // [check, status, message] as JSON -> the screens it happened on
      const note = (check, status, message, label) => {
        const key = JSON.stringify([check, status, short(message)]);
        found.set(key, [...new Set([...(found.get(key) || []), label])]);
      };
      const before = apiCalls;
      for (const [label, viewport] of VIEWPORTS) {
        const page = await context.newPage();
        await page.setViewportSize(viewport);
        page.on("pageerror", (e) => note("browser_errors", "fail", e.message || String(e), label));
        page.on("console", (m) => {
          if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) note("browser_console", "warn", m.text(), label);
        });
        page.on("dialog", (d) => d.dismiss().catch(() => undefined));
        page.on("popup", (p) => p.close().catch(() => undefined));
        try {
          await page.goto(`${ORIGIN}/${path.split("/").map(encodeURIComponent).join("/")}`, { waitUntil: "load", timeout: LOAD_TIMEOUT });
          await page.waitForTimeout(SETTLE);
          const seen = await page.evaluate((origin) => {
            const body = document.body;
            return {
              text: body ? body.innerText.trim().length : 0,
              media: document.querySelectorAll("img, svg, canvas, video, picture").length,
              overflow: document.documentElement.scrollWidth - window.innerWidth,
              broken: [...document.images].filter((i) => i.complete && i.naturalWidth === 0 && i.currentSrc.startsWith(origin))
                .map((i) => i.getAttribute("src")).slice(0, 3),
            };
          }, ORIGIN);
          if (seen.text === 0 && seen.media === 0) note("browser_render", "fail", "the page is blank", label);
          if (label === "mobile" && seen.overflow > 1) {
            note("browser_layout", "warn", `wider than a phone screen by ${seen.overflow}px: visitors must scroll sideways`, label);
          }
          for (const src of seen.broken) note("browser_render", "warn", `image doesn't display: ${short(src, 120)}`, label);
          if (path === input.screenshot) screenshots[label] = (await page.screenshot({ type: "png" })).toString("base64");
        } catch (e) {
          note("browser_render", "fail", `didn't finish loading within ${LOAD_TIMEOUT / 1000} s`, label);
        }
        await page.close();
      }
      // An error after a request for live data may only be the missing data: a warning, not a failure.
      const liveData = apiCalls > before;
      for (const [key, screens] of found) {
        const [check, status, message] = JSON.parse(key);
        const downgrade = check === "browser_errors" && liveData;
        add(check, downgrade ? "warn" : status,
          `${message} (${screens.join(", ")})${downgrade ? "; live data isn't loaded during checks, which may be why" : ""}`, path);
      }
    }
  } finally {
    await browser.close();
  }
  for (const path of [...missing].sort()) add("browser_requests", "fail", `requested a file that doesn't exist: ${path}`);
  for (const host of [...external].sort()) add("browser_requests", "warn", `loads from another site, not checked: ${host}`);
  return { results, screenshots, sandboxed: Boolean(input.sandbox) };
}

main().then(
  (out) => process.stdout.write(JSON.stringify(out)),
  (e) => {
    process.stdout.write(JSON.stringify({ error: short(String((e && e.message) || e).split("\n")[0], 400) }));
    process.exitCode = 1;
  },
);
