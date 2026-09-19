import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const bundledChromium = "/home/benshin/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome";
const executablePath = process.env.BROWSER_EXECUTABLE_PATH
  || (existsSync(bundledChromium) ? bundledChromium : undefined);

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  timeout: 300_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: "http://127.0.0.1:8765",
    browserName: "chromium",
    launchOptions: executablePath ? { executablePath } : {},
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    acceptDownloads: true,
  },
  webServer: {
    command: "python3 scripts/build_web.py && python3 -m http.server --directory dist --bind 127.0.0.1 8765",
    url: "http://127.0.0.1:8765/build-manifest.json",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
