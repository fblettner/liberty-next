// End-to-end regression tests (frontend/e2e/) — run against a LIVE server with real data, never in
// CI: `npm run e2e` with LIBERTY_E2E_USER / LIBERTY_E2E_PASSWORD set (and LIBERTY_E2E_URL, default
// http://127.0.0.1:8000). See e2e/README.md.
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // One browser, one scenario at a time: the scenarios write to shared tables.
  workers: 1,
  fullyParallel: false,
  // Fast fail: a step that can't find its control stops within seconds, a scenario within a minute.
  timeout: 60_000,
  expect: { timeout: 5_000 },
  reporter: [['list']],
  outputDir: './e2e/.results',
  use: {
    baseURL: process.env.LIBERTY_E2E_URL ?? 'http://127.0.0.1:8000',
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 5_000,
    navigationTimeout: 10_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
