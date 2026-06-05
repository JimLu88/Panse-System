import { defineConfig, devices } from '@playwright/test';

// 端到端冒烟 (优化 #8): 构建后用 vite preview 起静态站, 浏览器跑关键页渲染。
// 仅前端, 不依赖后端 (未登录态登录页可独立渲染)。
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run preview -- --port 4173',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
