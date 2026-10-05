import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', testMatch: '**/*.e2e.spec.ts', fullyParallel: true,
  outputDir: '/tmp/yield-ui-playwright',
  use: { baseURL: 'http://127.0.0.1:5187', headless: true, channel: process.env.PLAYWRIGHT_CHANNEL },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port 5187', url: 'http://127.0.0.1:5187', reuseExistingServer: false },
});
