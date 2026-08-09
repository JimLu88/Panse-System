import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const quoteConfig = {
  factory_profit_rate: 0.25,
  panse_profit_rate: 0.15,
  safety_rate: 1.05,
  platform_fee_rate: 0.05,
  tax_rate: 0,
  style_labor_ratio: 0.3,
  style_remove_credit: 0.85,
  paint_table_base: 250,
  paint_sideboard_base: 350,
  paint_fixed_ratio: 0.8,
  competitor_coupon_rate: 0.08,
  projection_type: 'front',
  projection_rate: 900,
  packing: [100, 200, 400],
  freight: [100, 100, 150],
  install: [50, 100, 150],
  labor: { 餐桌: [300, 400, 500] },
  size_rules: { 餐桌: [2, 1.4] },
  size_sanity_factor: 1.6,
  prices: {},
};

async function openQuotePage(page: Page) {
  await page.addInitScript(() => window.localStorage.setItem('panse_token', 'e2e-token'));
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );
  await page.route('**/api/auth/me', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ id: 1, username: 'admin', display_name: '测试管理员', role: 'admin', is_active: true }),
  }));
  await page.route('**/api/customization/quote-config', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(quoteConfig),
  }));
  await page.route('**/api/customization/v2/part-options*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ parts: [], materials: [], woods: ['榉木'] }),
  }));
  await page.route('**/api/customization/v2/classify', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      customization_type: '普通定制',
      base_product_code: 'P-OLD',
      base_product_name: '实木餐桌',
      confidence: 0.67,
      reasoning: '规则匹配',
      target_length_m: 2,
      target_width_cm: 90,
      target_material: '榉木',
      add_parts: [], remove_parts: [],
      candidates: [
        { product_code: 'P-OLD', product_name: '实木餐桌', confidence: 0.67 },
        { product_code: 'P-SLAB', product_name: '榉木岩板餐桌', confidence: 0.66 },
      ],
      sku_candidates: [
        { sku_code: 'OLD-200', sku_name: '实木餐桌-200cm', price: 3010, confidence: 0.33 },
      ],
    }),
  }));
  await page.route('**/api/customization/v2/sku-candidates?*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      product_code: 'P-SLAB',
      items: [
        { sku_code: 'SLAB-180-W', sku_name: '榉木餐桌-1.8米-白色岩板', price: 3270, confidence: 0.8 },
        { sku_code: 'SLAB-200-B', sku_name: '榉木餐桌-2.0米-黑色岩板', price: 3510, confidence: 0.7 },
      ],
    }),
  }));
  await page.route('**/api/customization/v2/quote-both', async (route) => {
    const body = route.request().postDataJSON() as { base_product_code: string };
    const isSlab = body.base_product_code === 'P-SLAB';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        spec: {
          final_price: isSlab ? 3652.19 : 3276.47,
          anchor: isSlab ? 3300 : 2950,
          anchor_method: '面积定价', material_delta: 0, size_delta: 0, addremove_delta: 0,
          base_product_name: isSlab ? '榉木岩板餐桌' : '实木餐桌', category: '餐厅-餐桌',
          subtotal_before_safety: isSlab ? 3478.28 : 3120.45,
          safety_delta: isSlab ? 173.91 : 156.02,
          paint_surcharge: 0,
          pricing_parameters: { factory_profit_rate: 0.25, panse_profit_rate: 0.15, safety_rate: 1.05 },
          specification: {
            target_length_m: 2, target_width_cm: 90, target_height_cm: null,
            target_material: '榉木', price_tier: 'big', standard_width_cm: 85, standard_height_cm: 75,
          },
          breakdown: [
            { label: '底面积定价(长×宽)', amount: isSlab ? 3478.28 : 3120.45, note: '2m×90cm' },
            { label: '安全系数 ×1.05', amount: isSlab ? 173.91 : 156.02, note: '非油漆小计×(1.05−1)' },
          ],
          parts_detail: [], comparison: null,
        },
        custom: null,
        custom_boards: [],
      }),
    });
  });

  await page.goto('/custom-quote-v2');
  await expect(page.getByRole('heading', { name: '定制报价 · 智能算价' })).toBeVisible();
}

test('switching product refreshes SKU candidates and shows the full calculation', async ({ page }) => {
  await openQuotePage(page);
  await page.getByPlaceholder('例如: 蜂蜜餐桌 改 1.5 米 黑胡桃 / 客户要全新异形旋转吧台...').fill('榉木岩板餐桌2米，宽度90');
  await page.getByRole('button', { name: '判定并算价' }).click();
  const skuRow = page.getByText('当前产品 SKU（切换产品后实时更新）:').locator('..').locator('..');
  await skuRow.locator('.ant-select').click();
  await expect(page.getByText('实木餐桌-200cm')).toBeVisible();
  await page.keyboard.press('Escape');

  const productRow = page.getByText('匹配产品(不一定准, 选错可改后自动重算):').locator('..').locator('..');
  await productRow.locator('.ant-select').click();
  await page.getByText(/榉木岩板餐桌/).last().click();

  await skuRow.locator('.ant-select').click();
  await expect(page.getByText('榉木餐桌-1.8米-白色岩板')).toBeVisible();
  await expect(page.getByText('实木餐桌-200cm')).toHaveCount(0);
  await expect(page.getByText('计算规格明细')).toBeVisible();
  await expect(page.getByText('具体加减项（逐笔公式与金额）')).toBeVisible();
  await expect(page.getByText('安全系数 ×1.05')).toBeVisible();
});

test('quote parameters have one entry in the top-right dialog', async ({ page }) => {
  await openQuotePage(page);
  await expect(page.getByText('报价系数(可调 · 改完保存→重算生效)')).toHaveCount(0);
  await page.getByRole('button', { name: '报价参数设置' }).click();
  await expect(page.getByText('利润、安全与增减项规则')).toBeVisible();
  await expect(page.getByText('打包 / 运费 / 安装（小、中、大）')).toBeVisible();
});

test('price tier menu keeps promo tiers and adds buyer-price tiers', async ({ page }) => {
  await openQuotePage(page);
  await page.getByText('报价档·大促', { exact: true }).click();
  await expect(page.getByText('报价档·中促', { exact: true })).toBeVisible();
  await expect(page.getByText('大促到手价', { exact: true })).toBeVisible();
  await expect(page.getByText('中促到手价', { exact: true })).toBeVisible();
  await expect(page.getByText('报价档·小促', { exact: true })).toHaveCount(0);
  await expect(page.getByText('报价档·日常', { exact: true })).toHaveCount(0);
});
