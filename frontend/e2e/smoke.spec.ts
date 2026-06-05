import { test, expect } from '@playwright/test';

// 关键冒烟: 构建产物能加载、SPA 起得来、未登录态正确落到登录页。
// 不依赖后端 (登录页渲染无需 API)。后续可在此基础上加"登录→导入→看全部列"的全链路。
test('app loads and shows login for unauthenticated user', async ({ page }) => {
  await page.goto('/');
  // 未登录 → 重定向到登录页, 出现密码输入框 + 登录按钮
  await expect(page.locator('input[type="password"]')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
});
