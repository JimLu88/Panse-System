import { api } from './base';

// ----- 定价总表 (#3) -----
export interface PricingSku {
  id: number;
  product_code: string;
  sku: string | null;
  sku_code: string;
  taobao_title?: string | null;   // 淘宝宝贝标题 (订单无编码时按它匹配回编码)
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
  // 出厂/拆分成本(后端早已返回, 之前前端没暴露)
  factory_cost?: number | null;        // 工厂成本(总出厂)
  wood_cost?: number | null;           // 木作成本
  logistics_cost?: number | null;      // 物流成本
  install_cost?: number | null;        // 安装成本
  packaging_cost?: number | null;      // 包装成本
  external_parts_cost?: number | null; // 外配件成本
  // 列表接口平铺合并的配件成本(rock_slab…)、活动价(taobao_*/xhs_*/mid_*/big_*)、
  // 自定义列(cf_<id>) 都按动态 key 透传, 故用索引签名接住。
  [key: string]: number | string | null | undefined;
}

export const listPricingSkus = (params: {
  q?: string;
  size_category?: string;
  category?: string;
  limit?: number;
  offset?: number;
}) =>
  api
    .get<{ total: number; items: PricingSku[] }>('/api/pricing-skus', { params })
    .then((r) => r.data);

// 产品类目去重列表 — 产品/BOM/定价 三处「按类目筛」下拉用
export const listProductCategories = () =>
  api.get<string[]>('/api/products/categories').then((r) => r.data);

// ----- 定价表自定义列 (EAV) -----
export interface PricingCustomField {
  id: number;
  label: string;
  value_kind: 'number' | 'text';
  sort_order: number;
}
export const listPricingCustomFields = () =>
  api.get<PricingCustomField[]>('/api/pricing/custom-fields').then((r) => r.data);
export const createPricingCustomField = (payload: { label: string; value_kind?: 'number' | 'text' }) =>
  api.post<PricingCustomField>('/api/pricing/custom-fields', payload).then((r) => r.data);
export const updatePricingCustomField = (id: number, payload: { label?: string; sort_order?: number }) =>
  api.patch<PricingCustomField>(`/api/pricing/custom-fields/${id}`, payload).then((r) => r.data);
export const deletePricingCustomField = (id: number) =>
  api.delete(`/api/pricing/custom-fields/${id}`).then((r) => r.data);
export const setPricingCustomValue = (skuCode: string, fieldId: number, value: number | string | null) =>
  api.patch(`/api/pricing-skus/${encodeURIComponent(skuCode)}/custom/${fieldId}`, { value }).then((r) => r.data);

// ----- 定价公式规则 (改系数用) -----
export interface PricingFormulaRule {
  id: number;
  field_name: string;
  display_name: string | null;
  expression: string;
  description: string | null;
  enabled: boolean;
  sort_order: number;
  is_builtin: boolean;
}
export const listFormulaRules = () =>
  api.get<PricingFormulaRule[]>('/api/pricing/formula-rules').then((r) => r.data);
export const updateFormulaRule = (id: number, body: Partial<PricingFormulaRule>) =>
  api.put<PricingFormulaRule>(`/api/pricing/formula-rules/${id}`, body).then((r) => r.data);
export const recomputeAllPricing = (force = false) =>
  api.post<{ updated: number; message: string }>('/api/pricing/recompute-all', null, { params: { force } })
    .then((r) => r.data);

// ── 日均销量公式 + 大促时段配置 (成品库存页) ──
export interface ForecastConfig {
  mode: 'weighted' | 'simple';
  halflife_days: number;
  window_days: number;
  promo_periods: { name: string; start: string; end: string }[];
  promo?: {
    active: { name: string; start: string; end: string }[];
    upcoming: { name: string; start: string; end: string; days_to_start: number }[];
    prep_days: number;
  };
}

export const fetchForecastConfig = () =>
  api.get<ForecastConfig>('/api/inventory/products/forecast-config').then((r) => r.data);

export const saveForecastConfig = (cfg: Partial<ForecastConfig>) =>
  api.put<ForecastConfig>('/api/inventory/products/forecast-config', cfg).then((r) => r.data);

// ── 人工编辑历史档案 (方向2+4) ──
export interface FieldChangeRow {
  id: number;
  table_name: string;
  row_pk: string;
  row_label: string | null;
  field: string;
  field_label: string | null;
  old_value: string | null;
  new_value: string | null;
  actor: string | null;
  source: string;
  source_label: string;
  created_at: string | null;
}

export const fetchFieldHistory = (table: string, pk: string, field: string, limit = 30) =>
  api.get<{ rows: FieldChangeRow[] }>('/api/field-changes/history', {
    params: { table, pk, field, limit },
  }).then((r) => r.data.rows);

export const listFieldChanges = (params: {
  table?: string; pk?: string; actor?: string; source?: string;
  q?: string; limit?: number; offset?: number;
} = {}) =>
  api.get<{ rows: FieldChangeRow[] }>('/api/field-changes', { params }).then((r) => r.data.rows);

// 编辑器「保存并覆盖同产品全部 SKU」(价格主表/22配件/渠道系数 三段可选)
export const updatePricingByProduct = (productCode: string, body: {
  sku?: Record<string, unknown>; costs?: Record<string, unknown>; promo?: Record<string, unknown>;
}) =>
  api.patch<{ updated: number; message: string }>(
    `/api/pricing-skus/by-product/${encodeURIComponent(productCode)}`, body,
  ).then((r) => r.data);

// Plan F1: 活动报名价 vs 定价渠道价 对照 (超差记异常+critical 告警)
export const runPromoPriceCheck = () =>
  api.post<{ checked: number; mismatch_count: number; tolerance: number }>(
    '/api/pricing-skus/promo-price-check').then((r) => r.data);

// Plan L7: 定价配件成本 ↔ BOM 漂移全量检查 (标 stale + 记异常)
export const runBomSyncCheck = () =>
  api.post<{ checked: number; stale_count: number }>(
    '/api/pricing-skus/bom-sync-check').then((r) => r.data);

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
  category: string | null;
}

export interface PartInventory {
  id: number;
  warehouse: string;
  material_code: string;
  material_name: string | null;
  spec: string | null;
  unit: string | null;
  physical_qty: number;
  locked_qty: number;
  defective_qty: number;   // 待返厂/维修中 (坏件), 不计入可用
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

export const listMaterials = (q?: string, isCustom?: boolean, category?: string) =>
  api
    .get<Material[]>('/api/materials', {
      params: { q, is_custom: isCustom, category, limit: 500 },
    })
    .then((r) => r.data);

export const listMaterialCategories = () =>
  api.get<{ categories: string[] }>('/api/materials/categories').then((r) => r.data.categories);

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
  category?: string;
}) => api.post<Material>('/api/materials', payload).then((r) => r.data);

export const getNextMaterialCode = (prefix: string) =>
  api.get<{ code: string }>('/api/materials/next-code', { params: { prefix } }).then((r) => r.data);

// #5: 物料反推产品 (BOM 反查)
export interface MaterialUsedIn {
  product_code: string;
  product_name: string | null;
  qty_per_product: number;
  sku_count: number;
}
export const getMaterialUsedInProducts = (code: string) =>
  api.get<MaterialUsedIn[]>(`/api/materials/${encodeURIComponent(code)}/used-in-products`).then((r) => r.data);

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

// force: resolved=跳过"问题是否已修复"复核; ignored=确认强制忽略
export const resolveException = (id: number, status: 'resolved' | 'ignored', force = false) =>
  api
    .patch<DataException>(`/api/exceptions/${id}/resolve`, { status, force })
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
  sub_name?: string | null;
  priority?: string | null;
  size_value?: string | null;
  main_material?: string | null;
  accessory_desc?: string | null;
  accessory_remark?: string | null;
  listing_status?: string | null;
}

export const listProducts = (q?: string, params?: { category?: string; brand?: string }) =>
  api.get<Product[]>('/api/products', { params: { q, limit: 500, ...params } }).then((r) => r.data);

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

// 系数目录(中文标识 + 含义) + 每个「按 SKU 系数」的众数(全局默认) —— 定价页三色覆盖标识用
export interface CoefficientStat {
  field: string;
  label: string;
  scope: 'global' | 'per_sku';
  meaning: string;
  fixed?: number;            // 结构性系数的固定值 (如 0.4)
  mode?: number | null;      // 按SKU系数的众数 = 全局默认
  distinct?: number;         // 不同取值数
  sample?: number;
}
export const getCoefficientStats = () =>
  api
    .get<{ coefficients: CoefficientStat[] }>('/api/pricing-skus/coefficient-stats')
    .then((r) => r.data.coefficients);

// 活动价全局参数(按档): 平台立减(力度) / 88VIP佣金 / 消费券阶梯
export interface PromoParams {
  mid_platform_discount: number;
  mid_vip_commission: number;
  big_platform_discount: number;
  big_vip_commission: number;
  mid_coupon_tiers: number[][];   // [[阈值, 减额], ...]
  big_coupon_tiers: number[][];
}
export const getPromoParams = () =>
  api.get<PromoParams>('/api/pricing-skus/promo-params').then((r) => r.data);
export const setPromoParams = (body: Partial<PromoParams>) =>
  api.put<{ params: PromoParams; recomputed: number }>('/api/pricing-skus/promo-params', body)
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
  sub_name?: string | null;
  brand?: string | null;
  category?: string | null;
  priority?: string | null;
  remark?: string;
  image_url?: string | null;
  custom_scope?: string | null;
  size_detail?: string | null;
  size_value?: string | null;
  main_material?: string | null;
  aux_material?: string | null;
  accessory_desc?: string | null;
  accessory_remark?: string | null;
  listing_status?: string | null;
  description?: string | null;
}) => api.patch<Product>(`/api/products/${id}`, payload).then((r) => r.data);

export interface DeleteProductResult {
  deleted_product: string;
  deleted_bom_lines: number;
  deleted_pricing_skus: number;
  orders_referencing: number;
}
// force=true 才会删被订单引用的产品(后端默认拦截, 返回 409)。级联删它的 BOM 行 + 定价 SKU。
export const deleteProduct = (id: number, force = false) =>
  api.delete<DeleteProductResult>(`/api/products/${id}`, { params: { force } }).then((r) => r.data);

// ----- Product Inventory (4a) -----
export interface ProductInventoryRow {
  id: number | null;            // 无库存产品(虚拟行)无 id
  warehouse: string;
  product_code: string;
  product_name?: string | null;
  has_inventory?: boolean;      // false = 还没建库存行
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

export const listProductInventory = (warningOnly = false, includeAll = false) =>
  api.get<ProductInventoryRow[]>('/api/inventory/products', {
    params: { warning_only: warningOnly, include_all: includeAll },
  }).then((r) => r.data);

export const refreshProductInventoryStats = () =>
  api.post<{ updated: number; message: string }>('/api/inventory/products/refresh').then((r) => r.data);

export const updateProductInventory = (id: number, patch: {
  qty?: number; locked_qty?: number; safety_stock?: number;
  lead_time_days?: number; slow_moving_days?: number; reorder_point?: number; remark?: string;
}) => api.patch<ProductInventoryRow>(`/api/inventory/products/${id}`, patch).then((r) => r.data);

// 同产品全部 SKU 一键同步参数 (安全库存/提前期/预警线/滞销阈值; 不含数量)
export const syncProductInventoryParams = (productCode: string, patch: {
  safety_stock?: number; lead_time_days?: number;
  slow_moving_days?: number; reorder_point?: number;
}) => api.patch<{ updated: number; message: string }>(
  `/api/inventory/products/by-product/${encodeURIComponent(productCode)}`, patch,
).then((r) => r.data);

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
  product_name?: string | null;
  product_image_url?: string | null;
  product_category?: string | null;
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

// 删单条 BOM 行 (清理串料 / 错挂到别的 SKU 的料)
export const deleteBomLine = (lineId: number) =>
  api.delete(`/api/bom/lines/${lineId}`).then((r) => r.data);

// BOM 清单(扁平): 按产品编码 / 物料编码 / 类目筛
export const listBomLines = (params: { product_code?: string; product?: string; material_code?: string; category?: string; limit?: number } = {}) =>
  api.get<BomLineRow[]>('/api/bom', { params: { limit: 500, ...params } }).then((r) => r.data);

// 编辑单条 BOM 行(改 SKU 归属 / 料号 / 单耗 / 单位等)
export const updateBomLine = (id: number, patch: {
  product_code?: string; sku?: string; sku_code?: string; material_code?: string;
  material_name?: string; unit?: string; qty_per_product?: number | string;
}) => api.patch<BomLineRow>(`/api/bom/lines/${id}`, patch).then((r) => r.data);

// 行内新增 BOM 行 (图2): 选已有物料编码, 或给 new_material_name + prefix 自动建物料+编码
export const createBomLine = (payload: {
  product_code: string; sku?: string; sku_code?: string;
  material_code?: string; new_material_name?: string; material_prefix?: string;
  unit?: string; qty_per_product?: number | string;
}) => api.post<BomLineRow>('/api/bom/lines', payload).then((r) => r.data);

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
  product_code: string; sku_code: string; sku?: string; taobao_title?: string; size_category?: string;
  list_price?: number; daily_price?: number; small_promo?: number; mid_promo?: number; big_promo?: number;
  accounting_cost?: number; physical_cost?: number; platform_fee_rate?: number; tax?: number; image_url?: string;
  // 成本加成基数(改系数) + 有效期定价生效日
  base_list?: number; base_small?: number; base_mid?: number; base_big?: number;
  effective_from?: string;   // YYYY-MM-DD; 带则此日之前的订单仍按老价
}
export const createPricingSku = (payload: PricingSkuCreate) =>
  api.post<PricingSku>('/api/pricing-skus', payload).then(r => r.data);
export const updatePricingSku = (id: number, payload: Partial<PricingSkuCreate>) =>
  api.patch<PricingSku>(`/api/pricing-skus/${id}`, payload).then(r => r.data);

// -- 上传淘宝商品导出 xlsx → 回填定价表 taobao_title + 无编码订单按标题对回编码
export interface ImportTaobaoTitlesResult {
  parsed_rows: number;
  filled_by_sku_code: number;
  filled_by_product_code: number;
  distinct_titles: number;
  unmatched_titles: string[];
  orders_code_backfilled: number;
}
export const importTaobaoTitles = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post<ImportTaobaoTitlesResult>('/api/pricing-skus/import-taobao-titles', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data);
};
export const recomputePricingSku = (id: number) =>
  api.post<PricingSku>(`/api/pricing-skus/${id}/recompute`).then(r => r.data);

// -- 工厂调价历史 (有效期定价): 列出定价版本区间, 每行=该[period_start,period_end)区间的旧值
export interface PriceVersion {
  id: number; sku_code: string; sku: string | null; product_code: string | null;
  period_start: string | null; period_end: string | null;
  physical_cost: number | null; factory_cost: number | null;
  list_price: number | null; daily_price: number | null;
  small_promo: number | null; mid_promo: number | null; big_promo: number | null;
  note: string | null; created_by: string | null; created_at: string | null;
}
export const listPriceVersions = (params?: { sku_code?: string; product_code?: string; limit?: number }) =>
  api.get<PriceVersion[]>('/api/pricing/version-history', { params }).then(r => r.data);

// -- 淘宝批量操作模板下载
export interface TaobaoTemplate {
  key: string;
  label: string;
  desc: string;
}
export const listPricingTemplates = () =>
  api.get<TaobaoTemplate[]>('/api/pricing-skus/templates').then(r => r.data);
// 定价图册 (带图导出): 返回 Excel(.xlsx) blob — 一SKU一行, 首列产品图(同编码多SKU合并),
// 全字段 + 中文表头 + 分类色带 (用户 2026-07-01: 要 Excel 不要 HTML)
export const downloadPricingCatalog = () =>
  api.get('/api/pricing/catalog.xlsx', { responseType: 'blob' }).then((r) => r.data);
export const downloadPricingTemplate = (key: string) =>
  api
    .get(`/api/pricing-skus/templates/${encodeURIComponent(key)}/download`, {
      responseType: 'blob',
    })
    .then(r => r.data as Blob);

// 改价台 (2026-07-02): 改店铺实收价(小/中/大促价) → 后端倒推店铺宝系数
export interface ShopPriceRow {
  id: number;
  product_code: string;
  product_name?: string | null;
  sku?: string | null;
  size_info?: string | null;
  image?: string | null;
  daily_price?: number | null;
  small_promo?: number | null;
  mid_promo?: number | null;
  big_promo?: number | null;
  shop_promo_rate?: number | null;
  mid_shop_rate?: number | null;
  big_shop_rate?: number | null;
}
export const fetchShopPriceBoard = (q?: string) =>
  api.get<ShopPriceRow[]>('/api/pricing-skus/shop-price-board', { params: q ? { q } : {} })
    .then(r => r.data);
export const updateShopPrice = (
  id: number,
  patch: { small_promo?: number | null; mid_promo?: number | null; big_promo?: number | null },
) => api.patch<ShopPriceRow>(`/api/pricing-skus/${id}/shop-price`, patch).then(r => r.data);

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
  shop: string | null;
  matched: boolean;
}
export interface TaobaoImportResult {
  inserted: number;
  updated: number;
  matched: number;
  total: number;
  warnings: string[];
}
export const importTaobaoExport = (file: File, shop?: string) => {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<TaobaoImportResult>('/api/taobao-listings/import', form, {
      params: shop ? { shop } : undefined,
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then(r => r.data);
};
export const backfillOrdersFromListings = () =>
  api
    .post<{ scanned: number; updated: number }>('/api/taobao-listings/backfill-orders')
    .then(r => r.data);
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

// -- 坏件/返厂维修 (方案B): 标记坏件 → 待返厂; 处理 → 回良品/报废/退款
export const markPartDefective = (
  id: number,
  payload: { qty: number; reason?: string; remark?: string },
) => api.post<PartInventory>(`/api/inventory/parts/${id}/defect`, payload).then(r => r.data);

export const resolvePartDefective = (
  id: number,
  payload: {
    qty: number;
    disposition: 'repaired' | 'scrapped' | 'returned';
    amount?: number;
    supplier?: string;
    related_purchase_no?: string;
    tracking_no?: string;
    reason?: string;
    remark?: string;
  },
) => api.post<PartInventory>(`/api/inventory/parts/${id}/defect/resolve`, payload).then(r => r.data);

// -- 配件返厂/退货 财务台账 (方案C): 退款应收/维修费/报废损失 + 供应商对账
export interface PartReturn {
  id: number;
  material_code: string;
  material_name: string | null;
  warehouse: string;
  qty: number;
  disposition: string;        // returned / repaired / scrapped
  amount_kind: string;        // refund / repair_fee / scrap_loss
  amount: number | null;
  reason: string | null;
  supplier: string | null;
  related_purchase_no: string | null;
  alipay_flow_no: string | null;
  tracking_no: string | null;
  status: string;             // open / settled
  actor: string | null;
  processed_at: string | null;
  remark: string | null;
}

export interface PartReturnSummary {
  pending_refund: number;
  received_refund: number;
  repair_fee_total: number;
  scrap_loss_total: number;
  open_count: number;
  total_count: number;
}

export const listPartReturns = (status?: string) =>
  api.get<PartReturn[]>('/api/part-returns', { params: status ? { status } : {} }).then(r => r.data);

export const partReturnSummary = () =>
  api.get<PartReturnSummary>('/api/part-returns/summary').then(r => r.data);

export const settlePartReturn = (
  id: number,
  payload: { alipay_flow_no?: string; remark?: string },
) => api.post<PartReturn>(`/api/part-returns/${id}/settle`, payload).then(r => r.data);

export interface RefundCandidate {
  transaction_no: string;
  account: string;
  transaction_time: string | null;
  counterparty: string | null;
  amount: number;
  score: number;
  reason: string;
}

export const refundCandidates = (returnId: number) =>
  api.get<RefundCandidate[]>(`/api/part-returns/${returnId}/refund-candidates`).then(r => r.data);

export const autoReconcileReturns = () =>
  api.post<{ matched: number; details: unknown[] }>('/api/part-returns/auto-reconcile').then(r => r.data);
// updateProductInventory defined above (with full patch type)

// -- 异常工作台
export const fixException = (id: number, fields: Record<string, unknown>) =>
  api.post(`/api/exceptions/${id}/fix`, { fields }).then(r => r.data);
export const runDataQuality = () =>
  api.post<Record<string, number>>('/api/exceptions/run-data-quality').then(r => r.data);
export const recheckAllExceptions = () =>
  api.post<{ closed: number; by_type: Record<string, number> }>('/api/exceptions/recheck-all').then(r => r.data);
export const refreshExceptions = () =>
  api.post<{ open_before: number; open_now: number; new_found: number; closed: number; closed_by_type: Record<string, number> }>(
    '/api/exceptions/refresh',
  ).then(r => r.data);
// #6 工厂账单挂已取消单 → 改挂同客户其它有效单
export interface FactoryDeadOrderCandidate {
  order_no: string;
  status: string;
  order_date: string | null;
  product_name: string | null;
  sku_code: string | null;
  paid_amount: number;
  has_actual_cost: boolean;
  match_pct: number;
}
export interface FactoryDeadOrderCandidates {
  cancelled_order_no: string;
  customer_name: string | null;
  bill_total: number;
  bill_detail: string;
  candidates: FactoryDeadOrderCandidate[];
  note: string | null;
}
export const factoryDeadOrderCandidates = (orderNo: string) =>
  api
    .get<FactoryDeadOrderCandidates>(
      `/api/factory-recon/dead-order/${encodeURIComponent(orderNo)}/rematch-candidates`,
    )
    .then(r => r.data);
export const factoryDeadOrderRematch = (orderNo: string, newOrderNo: string) =>
  api
    .post<{ moved_bills: number; moved_amount: number; new_order_no: string; new_actual_cost: number; closed_exceptions: number }>(
      `/api/factory-recon/dead-order/${encodeURIComponent(orderNo)}/rematch`,
      { new_order_no: newOrderNo },
    )
    .then(r => r.data);

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
