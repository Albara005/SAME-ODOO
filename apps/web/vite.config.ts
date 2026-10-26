import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the API runs on :8000 and is proxied, so the app and API share an origin.
const api = process.env.NOVARA_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": api, "/sites": api } },
  preview: { port: 4173, proxy: { "/api": api, "/sites": api } },
  build: {
    sourcemap: true,
    rollupOptions: {
      output: {
        // The framework changes far less often than the app: its own chunk stays cached across releases.
        manualChunks: { framework: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"] },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
