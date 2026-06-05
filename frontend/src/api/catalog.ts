import { api } from './base';

// ----- 定价总表 (#3) -----
export interface PricingSku {
  id: number;
  product_code: string;
  sku: string | null;
  sku_code: string;
  size_category: string | null;
  list_price: number | null;
  daily_price: number | null;
  small_promo: number | null;
  mid_promo: number | null;
  big_promo: number | null;
  big_promo_margin: number | null;
  gross_margin_rate: number | null;
  accounting_cost: number | null;
  physical_cost: number | null;
  platform_fee_rate: number | null;
  tax: number | null;
  image_url?: string | null;
}

export const listPricingSkus = (params: {
  q?: string;
  size_category?: string;
  limit?: number;
  offset?: number;
}) =>
  api
    .get<{ total: number; items: PricingSku[] }>('/api/pricing-skus', { params })
    .then((r) => r.data);

export interface AuditLog {
  id: number;
  user_id: number | null;
  username: string | null;
  method: string;
  path: string;
  status_code: number | null;
  ip: string | null;
  request_body: Record<string, unknown> | null;
  created_at: string;
}

export const listAuditLogs = (params: {
  method?: string;
  path_prefix?: string;
  limit?: number;
} = {}) =>
  api.get<AuditLog[]>('/api/audit/logs', { params }).then((r) => r.data);

export interface Material {
  id: number;
  code: string;
  name: string;
  size_type: string | null;
  unit: string | null;
  price: string | null;
  remark: string | null;
  is_custom: boolean;
}

export interface PartInventory {
  id: number;
  warehouse: string;
  material_code: string;
  spec: string | null;
  unit: string | null;
  physical_qty: number;
  locked_qty: number;
  available_qty: number;
  remark: string | null;
}

export interface PartInventoryAddResponse {
  inventory: PartInventory;
  material_code: string;
  material_name: string;
  material_created: boolean;
}

export interface DataException {
  id: number;
  source_table: string;
  source_pk: string | null;
  exception_type: string;
  severity: string;
  description: string;
  suggestion_action: string | null;
  context: Record<string, unknown> | null;
  status: string;
  created_at: string;
}

export const listMaterials = (q?: string, isCustom?: boolean) =>
  api
    .get<Material[]>('/api/materials', {
      params: { q, is_custom: isCustom, limit: 500 },
    })
    .then((r) => r.data);

export const updateMaterial = (id: number, patch: Partial<Material>) =>
  api.patch<Material>(`/api/materials/${id}`, patch).then((r) => r.data);

export const createMaterial = (payload: {
  name: string;
  prefix?: string;
  code?: string;
  size_type?: string;
  unit?: string;
  price?: number;
  remark?: string;
}) => api.post<Material>('/api/materials', payload).then((r) => r.data);

export const getNextMaterialCode = (prefix: string) =>
  api.get<{ code: string }>('/api/materials/next-code', { params: { prefix } }).then((r) => r.data);

export const listPartInventory = () =>
  api.get<PartInventory[]>('/api/inventory/parts').then((r) => r.data);

export interface PartInventoryStats extends PartInventory {
  daily_sales: number;
  lead_time_days: number | null;
  slow_moving_days: number | null;
  safety_stock_computed: number;
  reorder_point_computed: number;
  days_of_stock: number | null;
  warning_status: string;       // ok / warning / danger / critical / excess
  auto_reorder_qty: number;
}

export const listPartInventoryWithStats = () =>
  api.get<PartInventoryStats[]>('/api/inventory/parts/with-stats').then((r) => r.data);

export const addPartInventoryRow = (payload: {
  warehouse: string;
  material_code?: string;
  material_name?: string;
  physical_qty?: number;
  locked_qty?: number;
  spec?: string;
  unit?: string;
  remark?: string;
}) =>
  api
    .post<PartInventoryAddResponse>('/api/inventory/parts', payload)
    .then((r) => r.data);

export const listExceptions = (status?: string, limit = 2000) =>
  api
    .get<DataException[]>('/api/exceptions', { params: { status, limit } })
    .then((r) => r.data);

export const resolveException = (id: number, status: 'resolved' | 'ignored') =>
  api
    .patch<DataException>(`/api/exceptions/${id}/resolve`, { status })
    .then((r) => r.data);

export const resolveImportConflict = (id: number, choice: 'new' | 'old') =>
  api
    .post<DataException>(`/api/exceptions/${id}/resolve-import-conflict`, { choice })
    .then((r) => r.data);

// ----- Products -----
export interface Product {
  id: number;
  code: string;
  name: string;
  brand: string | null;
  category: string | null;
  remark: string | null;
  image_url?: string | null;
  custom_scope?: string | null;
  size_detail?: string | null;
  aux_material?: string | null;
  description?: string | null;
}

export const listProducts = (q?: string) =>
  api.get<Product[]>('/api/products', { params: { q, limit: 500 } }).then((r) => r.data);

// 最近更新产品 (新产品录入「参考已有产品」聚焦时的默认下拉)
export const listRecentProducts = (limit = 10) =>
  api
    .get<Product[]>('/api/products', { params: { sort: 'recent', limit } })
    .then((r) => r.data);

// 比例参考: 大促到手价的历史分布 (会计/物理/出厂三口径)
export interface RatioHintItem {
  ratio: number;
  pct: number;
  count: number;
}
export interface RatioCaliber {
  label: string;
  cost_field: string;
  sample: number;
  used_global: boolean;
  top: RatioHintItem[];
  range: { low: number; high: number; pct: number } | null;
}
export interface RatioFieldHint {
  anchor: string;
  anchor_label: string;
  mode: 'pct' | 'multiplier';
  sample: number;
  used_global: boolean;
  top: { ratio: number; pct: number; count: number }[];
  range: { low: number; high: number; pct: number } | null;
}
export interface RatioHints {
  category: string | null;
  calibers: Record<string, RatioCaliber>;
  fields?: Record<string, RatioFieldHint>;
}
export const getRatioHints = (category?: string) =>
  api
    .get<RatioHints>('/api/pricing-skus/ratio-hints', { params: { category } })
    .then((r) => r.data);

// 通用「常见值」分布 (配件成本 / 活动价格 小灯泡)
export interface ValueHint {
  sample: number;
  used_global: boolean;
  top: { value: number; pct: number; count: number }[];
  range: { low: number; high: number; pct: number } | null;
}
export const getValueHints = (table: 'costs' | 'promo', field: string, category?: string) =>
  api
    .get<ValueHint>('/api/pricing-skus/value-hints', { params: { table, field, category } })
    .then((r) => r.data);

export const createProduct = (payload: {
  name: string;
  brand: string;
  category: string;
  category_label?: string;
  remark?: string;
  image_url?: string;
  custom_scope?: string;
  size_detail?: string;
  aux_material?: string;
  description?: string;
}) => api.post<Product>('/api/products', payload).then((r) => r.data);

export const updateProduct = (id: number, payload: {
  name?: string;
  remark?: string;
  image_url?: string | null;
  custom_scope?: string | null;
  size_detail?: string | null;
  aux_material?: string | null;
  description?: string | null;
}) => api.patch<Product>(`/api/products/${id}`, payload).then((r) => r.data);

// ----- Product Inventory (4a) -----
export interface ProductInventoryRow {
  id: number;
  warehouse: string;
  product_code: string;
  sku: string | null;
  spec: string | null;
  unit: string | null;
  physical_qty: number;
  locked_qty: number;
  safety_stock: number | null;
  lead_time_days: number | null;
  slow_moving_days: number | null;
  reorder_point: number | null;
  remark: string | null;
  // computed stats (from API)
  available_qty: number;
  daily_sales_30d: number;
  lead_time_days_computed: number | null;
  safety_stock_computed: number;
  reorder_point_computed: number;
  days_of_stock: number | null;
  warning_status: 'ok' | 'warning' | 'danger' | 'critical' | 'excess';
  auto_reorder_qty: number;
}

export const listProductInventory = (warningOnly = false) =>
  api.get<ProductInventoryRow[]>('/api/inventory/products', { params: { warning_only: warningOnly } }).then((r) => r.data);

export const refreshProductInventoryStats = () =>
  api.post<{ updated: number; message: string }>('/api/inventory/products/refresh').then((r) => r.data);

export const updateProductInventory = (id: number, patch: {
  qty?: number; locked_qty?: number; safety_stock?: number;
  lead_time_days?: number; slow_moving_days?: number; reorder_point?: number; remark?: string;
}) => api.patch<ProductInventoryRow>(`/api/inventory/products/${id}`, patch).then((r) => r.data);

export const addProductInventoryRow = (payload: {
  warehouse: string;
  product_code: string;
  sku?: string;
  spec?: string;
  unit?: string;
  physical_qty?: number;
  locked_qty?: number;
  remark?: string;
}) => api.post<ProductInventoryRow>('/api/inventory/products', payload).then((r) => r.data);

// ----- BOM -----
export interface BomLineRow {
  id: number;
  product_code: string;
  sku: string | null;
  sku_code: string | null;
  material_code: string;
  material_name: string | null;
  unit: string | null;
  qty_per_product: string;
}

export interface BomLineGroup {
  sku: string | null;
  sku_code: string | null;
  lines: BomLineRow[];
}

export const listBomForProduct = (productCode: string) =>
  api.get<BomLineGroup[]>(`/api/bom/${productCode}`).then((r) => r.data);

// ----- Match -----
export interface MatchCandidate {
  scope: string;
  code: string;
  name: string;
  score: number;
}

export const fuzzyMatch = (q: string, scope: 'product' | 'material' | 'sku', limit = 10) =>
  api.get<MatchCandidate[]>('/api/match', { params: { q, scope, limit } }).then((r) => r.data);

// ----- Quotes -----
export interface LightQuote {
  sku_code: string;
  sku: string | null;
  size_category: string | null;
  list_price: string | null;
  daily_price: string | null;
  small_promo: string | null;
  mid_promo: string | null;
  big_promo: string | null;
  big_promo_margin: string | null;
  gross_margin_rate: string | null;
}

export const lightQuote = (skuCode: string) =>
  api.get<LightQuote>(`/api/quotes/light/${encodeURIComponent(skuCode)}`).then((r) => r.data);

export interface HighQuote {
  cost: string;
  size_category: string;
  margin_rate: string;
  final_price: string;
  margin_amount: string;
}

export const highQuote = (payload: {
  cost: number | string;
  size_category: string;
  margin_rate?: number | string;
}) => api.post<HighQuote>('/api/quotes/high', payload).then((r) => r.data);

export interface DimensionQuote {
  base_cm: string;
  target_cm: string;
  cm_diff: string;
  per_cm_cost: string;
  margin_rate: string;
  delta: string;
}

export const dimensionQuote = (payload: {
  base_cm: number | string;
  target_cm: number | string;
  per_cm_cost: number | string;
  margin_rate?: number | string;
}) => api.post<DimensionQuote>('/api/quotes/dimension', payload).then((r) => r.data);

export interface MaterialSwapResult {
  from_code: string;
  to_code: string;
  qty: string;
  from_unit_price: string | null;
  to_unit_price: string | null;
  delta: string | null;
}

export const materialSwap = (payload: {
  from_code: string;
  to_code: string;
  qty?: number | string;
}) => api.post<MaterialSwapResult>('/api/quotes/material-swap', payload).then((r) => r.data);

// -- 产品匹配
export interface ProductMatchResult {
  product_code: string | null;
  product_name: string | null;
  sku_code: string | null;
  sku: string | null;
  confidence: number;
}
export const matchProduct = (product_name: string, sku?: string) =>
  api.get<ProductMatchResult>('/api/products/match', { params: { product_name, sku } }).then(r => r.data);

// -- 两级匹配度排序 (微定制人工挑选)
export interface RankedSku {
  sku_code: string | null;
  sku: string | null;
  size_category: string | null;
  confidence: number;
}
export interface RankedProduct {
  product_code: string;
  product_name: string;
  product_confidence: number;
  skus: RankedSku[];
}
export const matchProductRanked = (product_name: string, sku?: string, limit = 10) =>
  api.get<RankedProduct[]>('/api/products/match-ranked', { params: { product_name, sku, limit } })
    .then(r => r.data);

// -- 产品 SKU 列表 (展开行用)
export const listProductSkus = (product_code: string) =>
  api.get<PricingSku[]>(`/api/products/${product_code}/skus`).then(r => r.data);

// -- 定价录入/编辑
export interface PricingSkuCreate {
  product_code: string; sku_code: string; sku?: string; size_category?: string;
  list_price?: number; daily_price?: number; small_promo?: number; mid_promo?: number; big_promo?: number;
  accounting_cost?: number; physical_cost?: number; platform_fee_rate?: number; tax?: number; image_url?: string;
}
export const createPricingSku = (payload: PricingSkuCreate) =>
  api.post<PricingSku>('/api/pricing-skus', payload).then(r => r.data);
export const updatePricingSku = (id: number, payload: Partial<PricingSkuCreate>) =>
  api.patch<PricingSku>(`/api/pricing-skus/${id}`, payload).then(r => r.data);
export const recomputePricingSku = (id: number) =>
  api.post<PricingSku>(`/api/pricing-skus/${id}/recompute`).then(r => r.data);

// -- 淘宝批量操作模板下载
export interface TaobaoTemplate {
  key: string;
  label: string;
  desc: string;
}
export const listPricingTemplates = () =>
  api.get<TaobaoTemplate[]>('/api/pricing-skus/templates').then(r => r.data);
export const downloadPricingTemplate = (key: string) =>
  api
    .get(`/api/pricing-skus/templates/${encodeURIComponent(key)}/download`, {
      responseType: 'blob',
    })
    .then(r => r.data as Blob);

// -- 淘宝商品导出对应表 (Task 5)
export interface TaobaoListing {
  id: number;
  taobao_item_id: string;
  taobao_sku_id: string | null;
  title: string | null;
  merchant_code: string | null;
  sku_spec: string | null;
  category_name: string | null;
  list_price: string | null;
  sku_price: string | null;
  stock: number | null;
  sku_code: string | null;
  product_code: string | null;
  matched: boolean;
}
export interface TaobaoImportResult {
  inserted: number;
  updated: number;
  matched: number;
  total: number;
  warnings: string[];
}
export const importTaobaoExport = (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<TaobaoImportResult>('/api/taobao-listings/import', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then(r => r.data);
};
export const listTaobaoListings = (params: {
  q?: string;
  matched?: boolean;
  limit?: number;
  offset?: number;
}) =>
  api
    .get<{ total: number; matched: number; items: TaobaoListing[] }>('/api/taobao-listings', { params })
    .then(r => r.data);
export const updateTaobaoListing = (
  id: number,
  patch: { sku_code?: string; product_code?: string },
) => api.patch<TaobaoListing>(`/api/taobao-listings/${id}`, patch).then(r => r.data);

// -- 新产品综合输入 (Task 4)
export interface ComposeBomLine {
  material_code: string;
  material_name?: string | null;
  unit?: string | null;
  qty_per_product?: string | number;
  size_type?: string | null;
  remark?: string | null;
}
export interface ComposePricingSku {
  sku_code?: string;
  is_custom?: boolean;
  sku?: string | null;
  size_category?: string | null;
  list_price?: string | number | null;
  daily_price?: string | number | null;
  small_promo?: string | number | null;
  mid_promo?: string | number | null;
  big_promo?: string | number | null;
  accounting_cost?: string | number | null;
  physical_cost?: string | number | null;
}
export interface ComposeProductPayload {
  name: string;
  brand: string;
  category: string;
  category_label?: string;
  remark?: string;
  taobao_id?: string;
  bom_lines: ComposeBomLine[];
  pricing_skus: ComposePricingSku[];
}
export interface ProductReference {
  product: { code: string; name: string; brand: string | null; category: string | null; remark: string | null };
  bom_lines: ComposeBomLine[];
  pricing_skus: ComposePricingSku[];
}
export const loadProductReference = (productCode: string) =>
  api.get<ProductReference>(`/api/product-composer/reference/${encodeURIComponent(productCode)}`).then(r => r.data);
export const composeProduct = (payload: ComposeProductPayload) =>
  api
    .post<{ product_code: string; bom_lines: number; pricing_skus: number }>('/api/product-composer', payload)
    .then(r => r.data);

// -- 库存可编辑 (盘库/纠错: 物理库存 + 锁定库存 + 备注)
export const updatePartInventory = (
  id: number,
  payload: { physical_qty?: number; locked_qty?: number; remark?: string },
) => api.patch(`/api/inventory/parts/${id}`, payload).then(r => r.data);
// updateProductInventory defined above (with full patch type)

// -- 异常工作台
export const fixException = (id: number, fields: Record<string, unknown>) =>
  api.post(`/api/exceptions/${id}/fix`, { fields }).then(r => r.data);
export const runDataQuality = () =>
  api.post<Record<string, number>>('/api/exceptions/run-data-quality').then(r => r.data);
export const getExceptionCounts = () =>
  api.get<Record<string, number>>('/api/exceptions/counts-by-type').then(r => r.data);
export const getOpenExceptionCount = () =>
  api.get<{ count: number }>('/api/exceptions/open-count').then(r => r.data);
export interface ExceptionSummary {
  total: number;
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
}
export const getExceptionsSummary = (status = 'open') =>
  api.get<ExceptionSummary>('/api/exceptions/summary', { params: { status } }).then(r => r.data);

export const autofillGenerate = (dry_run = false) =>
  api.post<{ factory_orders: { created: number; skipped: number; dry_run: boolean } }>(
    `/api/exceptions/autofill/generate?dry_run=${dry_run}`
  ).then(r => r.data);

// ----- Taobao IDs (业务需求 §4) -----
export const updateTaobaoIds = (
  productId: number,
  payload: { primary?: string; alternatives: string[] },
) => api.put(`/api/products/${productId}/taobao-ids`, payload).then((r) => r.data);

export const lookupByTaobaoId = (taobaoId: string) =>
  api.get<Product>(`/api/products/lookup-by-taobao-id/${taobaoId}`).then((r) => r.data);

// 配件成本
export interface PricingSkuCosts {
  id?: number; sku_code: string;
  rock_slab?: number|null; drawer_rail?: number|null; led_strip?: number|null;
  glass?: number|null; electric_rail?: number|null; packing_sheet?: number|null;
  iron_pin?: number|null; connector?: number|null; aluminum_rail?: number|null;
  plastic_rail?: number|null; mini_handle?: number|null; nail_free_glue?: number|null;
  engraving?: number|null; acrylic_strip?: number|null; embedded_sleeve?: number|null;
  cable_mgmt?: number|null; back_panel?: number|null; stainless_trim?: number|null;
  leg?: number|null; soft_pack?: number|null; bed_board?: number|null;
  other_cost?: number|null; other_desc?: string|null; parts_remark?: string|null;
}
export const getSkuCosts = (skuCode: string) =>
  api.get<PricingSkuCosts>(`/api/pricing-skus/${encodeURIComponent(skuCode)}/costs`).then(r => r.data);
export const upsertSkuCosts = (skuCode: string, payload: Partial<PricingSkuCosts>) =>
  api.patch<PricingSkuCosts>(`/api/pricing-skus/${encodeURIComponent(skuCode)}/costs`, payload).then(r => r.data);

// 活动价
export interface PricingSkuPromo {
  id?: number; sku_code: string;
  taobao_item_id?: string|null; taobao_sku_id?: string|null;
  taobao_activity_price?: number|null;
  shop_promo_rate?: number|null; shop_internal_promo?: number|null; shop_internal_final?: number|null;
  mid_shop_rate?: number|null; mid_buyer_price?: number|null; mid_shop_receipt?: number|null; mid_vip_final?: number|null;
  big_shop_rate?: number|null; big_buyer_price?: number|null; big_shop_receipt?: number|null; big_vip_final?: number|null;
  xhs_item_id?: string|null; xhs_sku_name?: string|null; xhs_sku_id?: string|null;
  xhs_list_price?: number|null; xhs_activity_price?: number|null; xhs_promo_discount?: number|null; xhs_promo_price?: number|null;
}
export const getSkuPromo = (skuCode: string) =>
  api.get<PricingSkuPromo>(`/api/pricing-skus/${encodeURIComponent(skuCode)}/promo`).then(r => r.data);
export const upsertSkuPromo = (skuCode: string, payload: Partial<PricingSkuPromo>) =>
  api.patch<PricingSkuPromo>(`/api/pricing-skus/${encodeURIComponent(skuCode)}/promo`, payload).then(r => r.data);
