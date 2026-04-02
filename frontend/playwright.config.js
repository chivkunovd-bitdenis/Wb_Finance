// @ts-check
import { defineConfig, devices } from '@playwright/test';

/**
 * E2E против текущего фронта (по умолчанию прод).
 * Учётка: E2E_EMAIL, E2E_PASSWORD
 * База: PLAYWRIGHT_BASE_URL (например http://localhost:5173 или https://app.sellerfocus.pro)
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'https://app.sellerfocus.pro',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
