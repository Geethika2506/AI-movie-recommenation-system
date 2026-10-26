import { defineConfig, devices } from "@playwright/test";

const backendUrl = process.env.CINEMATCH_BACKEND_URL ?? "http://127.0.0.1:8000";
const frontendPort = process.env.CINEMATCH_FRONTEND_PORT ?? "5173";
const frontendUrl = `http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: "html",
  use: {
    baseURL: frontendUrl,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `VITE_API_URL=${backendUrl}/api VITE_DEMO_FALLBACK=false npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
    url: frontendUrl,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
