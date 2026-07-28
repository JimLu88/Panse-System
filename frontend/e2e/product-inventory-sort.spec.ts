import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const inventoryRows = [
  {
    id: 1,
    warehouse: '江西仓库',
    product_code: 'PPS-TABLE',
    product_name: '榉木餐桌',
    sku: '榉木餐桌-1.6米',
    spec: null,
    unit: '张',
    physical_qty: 0,
    locked_qty: 0,
    safety_stock: null,
    lead_time_days: null,
    slow_moving_days: 60,
    reorder_point: null,
    remark: null,
    available_qty: 0,
    daily_sales_30d: 0.17,
    sales_qty_30d: 5,
    sales_amount_30d: 10000,
    forecast_daily: 0.2,
    lead_time_days_computed: 30,
    safety_stock_computed: 1,
    reorder_point_computed: 6,
    days_of_stock: 0,
    warning_status: 'danger',
    auto_reorder_qty: 6,
    forecast_30d: 6,
    target_stock: 6,
    restock_policy: '30天滚动备货',
  },
  {
    id: 2,
    warehouse: '江西仓库',
    product_code: 'PPS-TABLE',
    product_name: '榉木餐桌',
    sku: '榉木餐桌-1.4米',
    spec: null,
    unit: '张',
    physical_qty: 0,
    locked_qty: 0,
    safety_stock: null,
    lead_time_days: null,
    slow_moving_days: 60,
    reorder_point: null,
    remark: null,
    available_qty: 0,
    daily_sales_30d: 0.1,
    sales_qty_30d: 3,
    sales_amount_30d: 6000,
    forecast_daily: 0.1,
    lead_time_days_computed: 30,
    safety_stock_computed: 1,
    reorder_point_computed: 3,
    days_of_stock: 0,
    warning_status: 'danger',
    auto_reorder_qty: 3,
    forecast_30d: 3,
    target_stock: 3,
    restock_policy: '30天滚动备货',
  },
  {
    id: 3,
    warehouse: '江西仓库',
    product_code: 'PPS-CABINET',
    product_name: '餐边柜',
    sku: '餐边柜-1.2米',
    spec: null,
    unit: '个',
    physical_qty: 0,
    locked_qty: 0,
    safety_stock: null,
    lead_time_days: null,
    slow_moving_days: 60,
    reorder_point: null,
    remark: null,
    available_qty: 0,
    daily_sales_30d: 0.23,
    sales_qty_30d: 7,
    sales_amount_30d: 14000,
    forecast_daily: 0.23,
    lead_time_days_computed: 30,
    safety_stock_computed: 1,
    reorder_point_computed: 7,
    days_of_stock: 0,
    warning_status: 'danger',
    auto_reorder_qty: 7,
    forecast_30d: 7,
    target_stock: 7,
    restock_policy: '30天滚动备货',
  },
];

async function openInventory(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('panse_token', 'e2e-token');
  });
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 1,
        username: 'admin',
        display_name: '测试管理员',
        role: 'admin',
        is_active: true,
      }),
    }),
  );
  await page.route('**/api/inventory/products?*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(inventoryRows),
    }),
  );
  await page.route('**/api/inventory/products/forecast-config', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mode: 'weighted',
        halflife_days: 15,
        window_days: 90,
        promo_periods: [],
        enable_semi_finished: false,
        promo: { active: [], upcoming: [], prep_days: 30 },
      }),
    }),
  );
  await page.goto('/product-inventory');
  await expect(page.getByRole('heading', { name: '成品库存' })).toBeVisible();
}

async function visibleProductNames(page: Page) {
  return page.locator('.ant-table-tbody > tr > td:first-child strong').allTextContents();
}

test('inventory defaults to product total sales and keeps SKU names together', async ({ page }) => {
  await openInventory(page);

  await expect(page.locator('.ant-select[aria-label="库存排序方式"]')).toContainText('产品总销量');
  await expect.poll(() => visibleProductNames(page)).toEqual([
    '榉木餐桌-1.4米',
    '榉木餐桌-1.6米',
    '餐边柜-1.2米',
  ]);
});

test('inventory sort mode is adjustable and persisted', async ({ page }) => {
  await openInventory(page);

  await page.locator('.ant-select[aria-label="库存排序方式"]').click();
  await page.getByText('单 SKU 销量', { exact: true }).click();
  await expect.poll(() => visibleProductNames(page)).toEqual([
    '餐边柜-1.2米',
    '榉木餐桌-1.6米',
    '榉木餐桌-1.4米',
  ]);

  await page.reload();
  await expect(page.locator('.ant-select[aria-label="库存排序方式"]')).toContainText('单 SKU 销量');
});
