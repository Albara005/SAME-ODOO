import { defineConfig, devices } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// End-to-end tests run the real API (fresh SQLite database) and the production build of the app.
const python = process.env.PYTHON ?? "python3";
const db = path.join(os.tmpdir(), `novara-e2e-${Date.now()}.db`);
const storage = path.join(os.tmpdir(), `novara-e2e-storage-${Date.now()}`);

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...(process.env.PW_CHROMIUM ? { launchOptions: { executablePath: process.env.PW_CHROMIUM } } : {}),
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `${python} -m uvicorn tests.e2e_app:create_app --factory --host 127.0.0.1 --port 8000`,
      cwd: path.resolve(here, "../../services/api"),
      env: {
        NOVARA_DATABASE_URL: `sqlite:///${db}`,
        NOVARA_STORAGE_ROOT: storage,
        NOVARA_JWT_SECRET: "e2e-only-secret-not-for-production-0123456789",
        NOVARA_LIVE_INTERVAL_SECONDS: "0.5",
        // A host name, not an IP address: passkeys are bound to it (browsers refuse IPs as the relying party).
        NOVARA_APP_BASE_URL: "http://localhost:4173",
        // App Studio's browser checks use this app's own Playwright and Chromium.
        NOVARA_BROWSER_CHECKS: "on",
        NOVARA_BROWSER_SANDBOX: "false",
        NOVARA_BROWSER_NODE_PATH: path.resolve(here, "node_modules"),
      },
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npm run build && npm run preview",
      url: "http://127.0.0.1:4173",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
