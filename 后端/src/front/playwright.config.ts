import { defineConfig } from '@playwright/test'

// Playwright loads configuration again in workers; inherit one run ID so reports and
// screenshots stay together without overwriting evidence from earlier runs.
const runId = process.env.DEMO_VERIFICATION_RUN ?? Date.now().toString()
process.env.DEMO_VERIFICATION_RUN = runId
const artifactRoot = `../../data/verification/browser-${runId}`

/** Real-service acceptance only: start API/Vite explicitly before invoking. */
export default defineConfig({
  testDir: './tests',
  timeout: 600_000,
  workers: 1,
  retries: 0,
  outputDir: `${artifactRoot}/artifacts`,
  reporter: [['list'], ['json', { outputFile: `${artifactRoot}/results.json` }]],
  use: {
    baseURL: process.env.DEMO_UI_URL ?? 'http://127.0.0.1:5173',
    headless: true,
    viewport: { width: 1440, height: 1050 },
    launchOptions: { executablePath: process.env.DEMO_BROWSER_PATH ?? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
