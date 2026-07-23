import { expect, test } from '@playwright/test';

const PRODUCT_CODE = 'PPS24210070901';
const PRODUCT_FOLDER = `${PRODUCT_CODE} 测试餐桌`;

test('图库界面可把根目录现有图片整理到新文件夹', async ({ page }) => {
  let movePayload: { folder: string; paths: string[]; target_group: string } | undefined;
  let treeReads = 0;

  await page.addInitScript(() => localStorage.setItem('panse_token', 'e2e-token'));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

    if (pathname === '/api/auth/me') {
      await json({ id: 1, username: 'admin', display_name: '测试管理员', role: 'admin', is_active: true });
    } else if (pathname === '/api/products/categories') {
      await json([]);
    } else if (pathname === '/api/products') {
      await json([{ id: 1, code: PRODUCT_CODE, name: '测试餐桌', brand: 'PS', category: '餐厅-餐桌' }]);
    } else if (pathname === `/api/gallery/by-product/${PRODUCT_CODE}`) {
      await json({ folders: [PRODUCT_FOLDER] });
    } else if (pathname === '/api/gallery/tree') {
      treeReads += 1;
      await json({
        groups: treeReads === 1 ? [
          {
            group: '(根目录)',
            images: [`${PRODUCT_FOLDER}/正面.jpg`, `${PRODUCT_FOLDER}/侧面.jpg`],
          },
          { group: '主图', images: [`${PRODUCT_FOLDER}/主图/封面.jpg`] },
        ] : [
          { group: '主图', images: [`${PRODUCT_FOLDER}/主图/封面.jpg`] },
          {
            group: '安装细节图',
            images: [`${PRODUCT_FOLDER}/安装细节图/正面.jpg`, `${PRODUCT_FOLDER}/安装细节图/侧面.jpg`],
          },
        ],
      });
    } else if (pathname === '/api/gallery/move') {
      movePayload = request.postDataJSON();
      await json({ moved: 2, conflicts: 0, missing: 0, invalid: 0, skipped_same: 0, failed: 0 });
    } else if (pathname === '/api/gallery/file') {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#d6e4ff"/></svg>',
      });
    } else if (pathname === '/api/alerts/active') {
      await json([]);
    } else if (pathname === '/api/exceptions/open-count') {
      await json({ count: 0 });
    } else if (pathname === '/api/version') {
      await json({ commit: 'e2e', branch: 'test', deployed_at: '', source: 'test' });
    } else {
      await json({});
    }
  });

  await page.goto('/products');
  await expect(page.getByText(PRODUCT_CODE, { exact: true })).toBeVisible();
  await page.locator('.ant-table-row').first().locator('button').filter({ hasText: /图\s*库/ }).click();
  const modal = page.getByRole('dialog', { name: `产品图库 — ${PRODUCT_CODE}` });
  await expect(modal).toBeVisible();

  await modal.getByRole('button', { name: '整理已上传图片' }).click();
  await modal.getByRole('button', { name: '全选本组' }).first().click();
  await expect(modal.getByText('已选 2 张')).toBeVisible();
  await modal.locator('input[role="combobox"]').last().fill('安装细节图');
  await modal.getByRole('button', { name: '移动到此文件夹' }).click();

  await expect.poll(() => movePayload).toEqual({
    folder: PRODUCT_FOLDER,
    paths: [`${PRODUCT_FOLDER}/正面.jpg`, `${PRODUCT_FOLDER}/侧面.jpg`],
    target_group: '安装细节图',
  });
  await expect(page.getByText('已移动 2 张到「安装细节图」')).toBeVisible();
  await expect(modal.getByRole('heading', { name: /安装细节图/ })).toBeVisible();
  await page.screenshot({ path: 'test-results/gallery-organize.png', fullPage: true });
});
