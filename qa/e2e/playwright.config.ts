// Playwright config for the Sahaiy demo-gate E2E suite (G1–G10).
// Run: cd qa/e2e && npx playwright test
import { defineConfig, devices } from '@playwright/test';

const BASE = process.env.E2E_BASE_URL || 'https://sahaiy.vercel.app';

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: 0, // flaky = broken; never retry into green (qa-role-kit policy)
  reporter: [['list'], ['html', { outputFolder: '../evidence/e2e-html', open: 'never' }]],
  use: {
    baseURL: BASE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
