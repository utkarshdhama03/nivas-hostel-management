// Playwright end-to-end tests (docs/06 Phase 1, Q78, Q87). Run: docker compose run --rm e2e
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1, // the flows share one database and one Mailpit
  timeout: 60_000,
  reporter: 'list',
  use: { baseURL: 'http://localhost:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
