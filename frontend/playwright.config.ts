import { defineConfig, devices } from "@playwright/test";

const backendUrl = process.env.CINEMATCH_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: "html",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `VITE_API_URL=${backendUrl}/api VITE_DEMO_FALLBACK=false npm run dev -- --host 127.0.0.1 --port 5173`,
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
