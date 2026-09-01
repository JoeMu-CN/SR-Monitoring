import {defineConfig} from '@playwright/test';

const externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  reporter: 'list',
  use: {
    baseURL: externalBaseUrl ?? 'http://127.0.0.1:4173',
    trace: 'on-first-retry',
  },
  webServer: externalBaseUrl === undefined ? {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: false,
    timeout: 120_000,
  } : undefined,
});
