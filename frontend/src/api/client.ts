import axios from 'axios';

export const api = axios.create({
  baseURL: '/',
  headers: { 'Content-Type': 'application/json' },
});

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

export const listPartInventory = () =>
  api.get<PartInventory[]>('/api/inventory/parts').then((r) => r.data);

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

export const listExceptions = (status?: string) =>
  api
    .get<DataException[]>('/api/exceptions', { params: { status } })
    .then((r) => r.data);

export const resolveException = (id: number, status: 'resolved' | 'ignored') =>
  api
    .patch<DataException>(`/api/exceptions/${id}/resolve`, { status })
    .then((r) => r.data);

// ----- Products -----
export interface Product {
  id: number;
  code: string;
  name: string;
  brand: string | null;
  category: string | null;
  remark: string | null;
}

export const listProducts = (q?: string) =>
  api.get<Product[]>('/api/products', { params: { q, limit: 500 } }).then((r) => r.data);

export const createProduct = (payload: {
  name: string;
  brand: string;
  category: string;
  category_label?: string;
  remark?: string;
}) => api.post<Product>('/api/products', payload).then((r) => r.data);

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
  remark: string | null;
}

export const listProductInventory = () =>
  api.get<ProductInventoryRow[]>('/api/inventory/products').then((r) => r.data);

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

// ----- Feishu -----
export interface FeishuBinding {
  id: number;
  system_table: string;
  feishu_app_token: string;
  feishu_table_id: string;
  direction: string;
  enabled: boolean;
  field_mapping: string | null;
}

export interface FeishuStatus {
  system_table: string;
  feishu_table_id: string;
  direction: string;
  enabled: boolean;
  mapped_rows: number;
}

export const listFeishuBindings = () =>
  api.get<FeishuBinding[]>('/api/feishu/bindings').then((r) => r.data);

export const createFeishuBinding = (payload: Omit<FeishuBinding, 'id'>) =>
  api.post<FeishuBinding>('/api/feishu/bindings', payload).then((r) => r.data);

export const feishuStatus = () =>
  api.get<FeishuStatus[]>('/api/feishu/status').then((r) => r.data);

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

// ----- Orders -----
export interface Order {
  id: number;
  platform: string;
  order_no: string;
  is_refill: boolean;
  order_date: string | null;
  ship_date: string | null;
  customer_name: string | null;
  product_code: string | null;
  product_name: string | null;
  sku: string | null;
  is_custom: boolean;
  qty: number;
  status: string;
  carrier: string | null;
  tracking_no: string | null;
  paid_amount: string | null;
}

export const listOrders = (params: {
  q?: string;
  status?: string;
  platform?: string;
  limit?: number;
} = {}) => api.get<Order[]>('/api/orders', { params: { limit: 100, ...params } }).then((r) => r.data);

export const changeOrderStatus = (id: number, status: string, force = false) =>
  api.post<Order>(`/api/orders/${id}/status`, { status, force }).then((r) => r.data);

export interface CsvImportReport {
  inserted: number;
  skipped_duplicate: number;
  skipped_invalid: number;
  errors: string[];
}

export const importOrdersCsv = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<CsvImportReport>('/api/orders/import-csv', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
};

// ----- Producibility -----
export interface MaterialRequirement {
  material_code: string;
  material_name: string | null;
  qty_per_product: string;
  available_stock: string;
  can_build_units: number;
  shortage_for_target: string;
}

export interface ProducibilityResult {
  sku_code: string | null;
  product_code: string | null;
  target_qty: number;
  in_stock_qty: number;
  can_build_qty: number;
  total_available_qty: number;
  bottleneck: MaterialRequirement | null;
  requirements: MaterialRequirement[];
  missing_for_target: MaterialRequirement[];
}

export const computeProducibility = (params: {
  sku_code?: string;
  product_code?: string;
  target_qty?: number;
}) =>
  api
    .get<ProducibilityResult>('/api/producibility', { params })
    .then((r) => r.data);

// ----- Finance -----
export interface AlipayFlow {
  id: number;
  account: string;
  transaction_no: string;
  transaction_time: string | null;
  transaction_type: string | null;
  counterparty: string | null;
  amount: string;
  related_order_no: string | null;
  balance: string | null;
  reconciliation_status: string;
  reconciliation_type: string | null;
  remark: string | null;
}

export const listAlipayFlows = (params: {
  account?: string;
  recon_type?: string;
  limit?: number;
} = {}) =>
  api.get<AlipayFlow[]>('/api/finance/alipay-flows', { params: { limit: 100, ...params } })
    .then((r) => r.data);

export const importAlipayCsv = (file: File, account: string) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<CsvImportReport>('/api/finance/alipay-flows/import-csv', fd, {
      params: { account },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
};

export interface AccountBalanceRow {
  id: number;
  account_name: string;
  period_year: number;
  period_month: number;
  opening_balance: string;
  income: string;
  expense: string;
  closing_balance: string;
}

export const listBalances = (params: { account_name?: string; year?: number } = {}) =>
  api.get<AccountBalanceRow[]>('/api/finance/accounts', { params }).then((r) => r.data);

export const recomputeBalance = (payload: {
  account_name: string;
  year: number;
  month: number;
  opening_balance?: string;
}) => api.post<AccountBalanceRow>('/api/finance/accounts/recompute', payload).then((r) => r.data);

export interface ReconciliationDiff {
  key: string;
  expected: string | null;
  actual: string | null;
  diff: string | null;
  severity: 'ok' | 'warning' | 'error' | 'not_available';
  message: string;
}

export interface ReconciliationResult {
  rule: string;
  total_diffs: number;
  ok_count: number;
  warning_count: number;
  error_count: number;
  diffs: ReconciliationDiff[];
}

export const runReconciliation = (rule?: string) => {
  if (rule) {
    return api.get<ReconciliationResult>(`/api/finance/reconciliation/${rule}`).then((r) => r.data);
  }
  return api.get<Record<string, ReconciliationResult>>('/api/finance/reconciliation').then((r) => r.data);
};

// ----- Scanners (Phase 3.5) -----
export interface ScannerFinding {
  source_table: string;
  source_pk: string;
  exception_type: string;
  severity: string;
  description: string;
  suggestion_action: string;
  context: Record<string, unknown>;
}

export interface ScannerResult {
  scanner: string;
  findings: ScannerFinding[];
  written: number;
  skipped_duplicate: number;
}

export const listScanners = () =>
  api.get<string[]>('/api/scanners').then((r) => r.data);

export const runAllScanners = (dryRun = false) =>
  api
    .post<Record<string, ScannerResult>>('/api/scanners/run-all', null, {
      params: { dry_run: dryRun },
    })
    .then((r) => r.data);

// ----- AI Assistant -----
export interface AiStatus {
  configured: boolean;
  model: string;
}

export interface AiDiagnoseResult {
  log_id: number;
  exception_id: number;
  text: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  error: string | null;
}

export interface AiChatResult {
  log_id: number;
  text: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  error: string | null;
}

export const aiStatus = () => api.get<AiStatus>('/api/ai/status').then((r) => r.data);

export const aiDiagnose = (exceptionId: number) =>
  api.post<AiDiagnoseResult>(`/api/ai/diagnose/${exceptionId}`).then((r) => r.data);

export const aiChat = (message: string, sessionId?: string) =>
  api
    .post<AiChatResult>('/api/ai/chat', { message, session_id: sessionId })
    .then((r) => r.data);

// ----- Marketing (Phase 5) -----
export interface Sample {
  id: number;
  sample_no: string;
  product_code: string | null;
  product_name: string | null;
  sku: string | null;
  sample_type: string | null;
  qty: number;
  made_at: string | null;
  cost: string | null;
  location: string | null;
  status: string | null;
  usage: string | null;
}

export const listSamples = () =>
  api.get<Sample[]>('/api/marketing/samples').then((r) => r.data);

export interface BrandMarketing {
  id: number;
  project_name: string;
  project_type: string | null;
  partner: string | null;
  start_date: string | null;
  end_date: string | null;
  budget: string | null;
  actual_spend: string | null;
  status: string | null;
}

export const listBrandMarketing = () =>
  api.get<BrandMarketing[]>('/api/marketing/brand').then((r) => r.data);

export const createBrandMarketing = (payload: Partial<BrandMarketing> & { project_name: string }) =>
  api.post<BrandMarketing>('/api/marketing/brand', payload).then((r) => r.data);

export interface PromotionFlow {
  id: number;
  transaction_date: string | null;
  flow_type: string | null;
  amount: string;
  balance_after: string | null;
  remark: string | null;
}

export const listPromotionFlows = () =>
  api.get<PromotionFlow[]>('/api/marketing/promotion').then((r) => r.data);

export interface OutsourcingExpense {
  id: number;
  payee: string;
  amount: string;
  project: string | null;
  cost_category: string | null;
  payment_date: string | null;
}

export const listOutsourcing = () =>
  api.get<OutsourcingExpense[]>('/api/marketing/outsourcing').then((r) => r.data);

export interface AfterSalesRow {
  id: number;
  platform_order_no: string;
  reason: string | null;
  in_platform_total: string | null;
  out_platform_total: string | null;
  refill_sku: string | null;
  status: string | null;
  customer_satisfaction: string | null;
  processed_at: string | null;
}

export const listAfterSales = () =>
  api.get<AfterSalesRow[]>('/api/marketing/after-sales').then((r) => r.data);

export interface RoiResult {
  period_start: string | null;
  period_end: string | null;
  promotion_spend: string;
  promotion_recharge: string;
  order_count: number;
  order_revenue: string;
  avg_order_value: string;
  roi: string | null;
}

export const getRoi = (params: { period_start?: string; period_end?: string } = {}) =>
  api.get<RoiResult>('/api/marketing/roi', { params }).then((r) => r.data);
