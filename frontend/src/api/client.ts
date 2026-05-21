import axios from 'axios';

export const api = axios.create({
  baseURL: '/',
  headers: { 'Content-Type': 'application/json' },
});

// 自动从 localStorage 取 token 加到所有请求
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('panse_token');
  if (token) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
  }
  return config;
});

// 401 时清掉 token (AuthProvider 监听并跳登录)
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem('panse_token');
      window.dispatchEvent(new Event('panse:unauthorized'));
    }
    return Promise.reject(err);
  },
);

// ----- Auth (Phase 6) -----
export interface MeUser {
  id: number;
  username: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
}

export const login = (username: string, password: string) =>
  api
    .post<{ token: string; user: MeUser }>('/api/auth/login', { username, password })
    .then((r) => r.data);

export const fetchMe = () => api.get<MeUser>('/api/auth/me').then((r) => r.data);

export const listAuthUsers = () =>
  api.get<MeUser[]>('/api/auth/users').then((r) => r.data);

export const createUser = (payload: {
  username: string;
  password: string;
  role: string;
  display_name?: string;
}) => api.post<MeUser>('/api/auth/users', payload).then((r) => r.data);

export const fetchRoles = () =>
  api
    .get<{ roles: string[]; descriptions: Record<string, string> }>('/api/auth/roles')
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

// ----- Reports & Optimizations (plan §12) -----
export interface HealthReport {
  period_start: string;
  period_end: string;
  exceptions: {
    total_open: number;
    by_severity: Record<string, number>;
    top_types: Record<string, number>;
  };
  reconciliation: Record<string, { total: number; ok: number; warning: number; error: number }>;
  inventory: { book_value: string; items_priced: number; items_missing_price: number };
  orders: { month_count: number; month_revenue: string };
  roi: { promotion_spend: string; order_count: number; order_revenue: string; roi: string | null };
  integrity_score: number;
  headlines: string[];
}

export const getMonthlyReport = (year: number, month: number) =>
  api
    .get<HealthReport>('/api/reports/monthly', { params: { year, month } })
    .then((r) => r.data);

export const getCurrentMonthReport = () =>
  api.get<HealthReport>('/api/reports/monthly/current').then((r) => r.data);

export interface KnowledgeRow {
  id: number;
  exception_type: string;
  context_hash: string;
  solution_text: string;
  source_description: string | null;
  model: string | null;
  usage_count: number;
  last_used_at: string | null;
  created_at: string;
}

export const listKnowledge = (limit = 50) =>
  api.get<KnowledgeRow[]>('/api/reports/knowledge', { params: { limit } }).then((r) => r.data);

export interface EscalationOut {
  exception_type: string;
  open_count: number;
  escalated_from: string;
  escalated_to: string;
  affected_ids: number[];
}

export const runEscalation = () =>
  api.post<EscalationOut[]>('/api/scanners/escalate').then((r) => r.data);

// ----- Factory Sheet (业务需求 §1) -----
export interface FactorySheetMaterial {
  material_code: string;
  material_name: string | null;
  qty_per_product: string;
  total_qty: string;
  unit: string | null;
  spec: string | null;
}

export interface FactorySheetWarning {
  code: string;
  message: string;
  severity: string;
}

export interface FactorySheet {
  order_no: string;
  sheet_title: string;
  order_date: string | null;
  ship_date: string | null;
  product_code: string | null;
  product_name: string | null;
  sku: string | null;
  sku_code: string | null;
  image_url: string | null;
  material_desc: string | null;
  dimension_desc: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  customer_address: string | null;
  qty: number;
  remark: string | null;
  materials: FactorySheetMaterial[];
  is_custom_variant: boolean;
  dimension_changes: Record<string, unknown> | null;
  warnings: FactorySheetWarning[];
}

export const getFactorySheet = (orderId: number) =>
  api.get<FactorySheet>(`/api/orders/${orderId}/factory-sheet`).then((r) => r.data);

// ----- Customization (业务需求 §2) -----
export interface CustomizationDiffLine {
  material_code: string;
  material_name: string | null;
  original_qty: string;
  new_qty: string;
  note: string | null;
  requires_new_material: boolean;
}

export interface CustomizationPreview {
  base_sku_code: string;
  proposed_custom_sku_code: string;
  dimension_changes: Record<string, unknown>;
  diff_lines: CustomizationDiffLine[];
}

export const previewCustomization = (payload: {
  base_sku_code: string;
  dimension_changes: Record<string, unknown>;
}) => api.post<CustomizationPreview>('/api/customization/preview', payload).then((r) => r.data);

export const confirmCustomization = (payload: {
  base_sku_code: string;
  dimension_changes: Record<string, unknown>;
  order_no?: string;
  note?: string;
}) =>
  api
    .post<{ custom_variant_id: number; custom_sku_code: string; cloned_bom_lines: number }>(
      '/api/customization/confirm',
      payload,
    )
    .then((r) => r.data);

// ----- Taobao IDs (业务需求 §4) -----
export const updateTaobaoIds = (
  productId: number,
  payload: { primary?: string; alternatives: string[] },
) => api.put(`/api/products/${productId}/taobao-ids`, payload).then((r) => r.data);

export const lookupByTaobaoId = (taobaoId: string) =>
  api.get<Product>(`/api/products/lookup-by-taobao-id/${taobaoId}`).then((r) => r.data);

// ----- Admin: AI Integrations (业务需求扩展) -----
export interface IntegrationConfig {
  provider: string;
  base_url: string;
  api_key_masked: string;
  api_key_set: boolean;
  model: string;
}

export interface SupportedProvider {
  value: string;
  label: string;
  model_hint: string;
  base_url_hint: string;
}

export interface Integrations {
  diagnose: IntegrationConfig;
  ocr: IntegrationConfig;
  supported_providers: SupportedProvider[];
}

export const fetchIntegrations = () =>
  api.get<Integrations>('/api/admin/integrations').then((r) => r.data);

export const updateIntegrations = (payload: {
  diagnose?: Partial<{ provider: string; base_url: string; api_key: string; model: string }>;
  ocr?: Partial<{ provider: string; base_url: string; api_key: string; model: string }>;
}) => api.put<Integrations>('/api/admin/integrations', payload).then((r) => r.data);

export const testIntegration = (kind: 'diagnose' | 'ocr') =>
  api
    .post<{ ok: boolean; provider: string; model: string; sample?: string; error?: string }>(
      '/api/admin/integrations/test',
      { kind },
    )
    .then((r) => r.data);

// ----- Suppliers / 对账模块 (业务需求) -----
export interface Supplier {
  id: number;
  name: string;
  supplier_type: string;
  contact: string | null;
  phone: string | null;
  address: string | null;
  payment_terms: string | null;
  is_active: boolean;
  remark: string | null;
}

export interface DeliveryLine {
  id: number;
  line_no: number;
  item_name: string | null;
  spec: string | null;
  unit: string | null;
  qty: number;
  unit_price: number | null;
  amount: number | null;
  matched_order_no: string | null;
  match_confidence: number | null;
  match_method: string | null;
  match_candidates: Array<{
    order_no: string;
    factory_order_no?: string | null;
    confidence: number;
    method: string;
    reason: string;
    customer_name?: string | null;
    product_code?: string | null;
    sku?: string | null;
    qty?: number | null;
  }>;
  ocr_warnings: string[];
  remark: string | null;
}

export interface DeliveryNote {
  id: number;
  supplier_id: number;
  supplier_name: string;
  note_no: string | null;
  delivery_date: string | null;
  total_amount: number | null;
  status: string;
  ocr_confidence: number | null;
  ocr_warnings: string[];
  ocr_model: string | null;
  source_file_id: number | null;
  remark: string | null;
  lines: DeliveryLine[];
}

export interface FolderListing {
  supplier_id: number;
  year: number;
  month: number;
  file_count: number;
  files: Array<{
    id: number;
    original_name: string;
    mime_type: string | null;
    size_bytes: number | null;
    delivery_note_id: number | null;
    note_no: string | null;
    uploaded_at: string;
  }>;
}

export const listSuppliers = (activeOnly = true) =>
  api.get<Supplier[]>('/api/suppliers', { params: { active_only: activeOnly } }).then((r) => r.data);

export const createSupplier = (payload: Partial<Supplier>) =>
  api.post<Supplier>('/api/suppliers', payload).then((r) => r.data);

export const patchSupplier = (id: number, payload: Partial<Supplier>) =>
  api.patch<Supplier>(`/api/suppliers/${id}`, payload).then((r) => r.data);

export const listDeliveryNotes = (
  supplierId: number,
  params: { year?: number; month?: number; status?: string } = {},
) =>
  api
    .get<DeliveryNote[]>(`/api/suppliers/${supplierId}/delivery-notes`, { params })
    .then((r) => r.data);

export const getDeliveryNote = (id: number) =>
  api.get<DeliveryNote>(`/api/delivery-notes/${id}`).then((r) => r.data);

export const uploadDeliveryNote = (
  supplierId: number,
  file: File,
  onDate?: string,
) => {
  const form = new FormData();
  form.append('file', file);
  if (onDate) form.append('on_date', onDate);
  return api
    .post<DeliveryNote>(`/api/suppliers/${supplierId}/delivery-notes`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000, // OCR 可能要 60-120s
    })
    .then((r) => r.data);
};

export const updateDeliveryNote = (
  id: number,
  payload: {
    status?: string;
    note_no?: string;
    delivery_date?: string;
    total_amount?: number;
    remark?: string;
    alipay_flow_no?: string;
  },
) => api.patch<DeliveryNote>(`/api/delivery-notes/${id}`, payload).then((r) => r.data);

export const patchLineMatch = (
  noteId: number,
  lineId: number,
  payload: { matched_order_no?: string | null; match_confidence?: number },
) =>
  api
    .patch<DeliveryLine>(`/api/delivery-notes/${noteId}/lines/${lineId}/match`, payload)
    .then((r) => r.data);

export const rematchNote = (id: number) =>
  api.post<DeliveryNote>(`/api/delivery-notes/${id}/rematch`).then((r) => r.data);

export const listSupplierFolder = (supplierId: number, year: number, month: number) =>
  api
    .get<FolderListing>(`/api/suppliers/${supplierId}/folders/${year}/${month}`)
    .then((r) => r.data);

export const sourceImageUrl = (noteId: number) =>
  `/api/delivery-notes/${noteId}/source-image`;

export const deliveryFileRawUrl = (fileId: number) =>
  `/api/delivery-files/${fileId}/raw`;

export const statementXlsxUrl = (supplierId: number, year: number, month: number) =>
  `/api/suppliers/${supplierId}/statements/${year}/${month}.xlsx`;

export const statementHtmlUrl = (supplierId: number, year: number, month: number) =>
  `/api/suppliers/${supplierId}/statements/${year}/${month}.html`;

// ----- 支付宝自动对账 (业务需求 2) -----
export interface PaymentMatch {
  flow_id: number;
  flow_no: string;
  flow_amount: number;
  flow_time: string | null;
  counterparty: string | null;
  supplier_id: number | null;
  supplier_name: string | null;
  matched_note_ids: number[];
  matched_note_nos: string[];
  decision: 'exact' | 'combo' | 'needs_review' | 'no_supplier' | 'no_candidates' | 'skipped';
  reason: string;
}

export interface ReconcileSummary {
  scanned: number;
  matched_count: number;
  needs_review: number;
  no_supplier: number;
  no_candidates: number;
  skipped: number;
  matches: PaymentMatch[];
}

export const reconcilePayments = (payload: {
  account?: string;
  since_days?: number;
  dry_run?: boolean;
}) =>
  api
    .post<ReconcileSummary>('/api/suppliers/reconcile-payments', payload)
    .then((r) => r.data);

export const applyManualPaymentMatch = (flow_id: number, note_ids: number[]) =>
  api
    .post<PaymentMatch>('/api/suppliers/reconcile-payments/manual', { flow_id, note_ids })
    .then((r) => r.data);

// ----- Excel 通用 importer (业务需求) -----
export interface EntityField {
  name: string;
  type: string;
  required: boolean;
  desc: string;
  aliases: string[];
}

export interface EntityType {
  value: string;
  label: string;
  description: string;
  fields: EntityField[];
}

export interface SheetPreview {
  sheet_name: string;
  row_count: number;
  column_names: string[];
  sample_rows: any[][];
  suggested_entity: string | null;
  suggested_mapping: Record<string, string>;
  notes: string[];
}

export interface ImporterPreviewResp {
  file_b64: string;
  sheets: SheetPreview[];
}

export interface ImportReport {
  entity_type: string;
  sheet_name: string;
  total_rows: number;
  inserted_parents: number;
  inserted_children: number;
  skipped_rows: number;
  matched_lines: number;
  auto_created_suppliers: string[];
  errors: string[];
  warnings: string[];
}

export const fetchEntityTypes = () =>
  api.get<EntityType[]>('/api/importer/entity-types').then((r) => r.data);

export const previewImporter = (file: File, entityType?: string) => {
  const form = new FormData();
  form.append('file', file);
  const params = entityType ? { entity_type: entityType } : {};
  return api
    .post<ImporterPreviewResp>('/api/importer/preview', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params,
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const commitImporter = (payload: {
  file_b64: string;
  sheet_name: string;
  entity_type: string;
  mapping: Record<string, string>;
  auto_create_suppliers?: boolean;
  auto_match_orders?: boolean;
  dry_run?: boolean;
}) => api.post<ImportReport>('/api/importer/commit', payload).then((r) => r.data);

// ----- 系统监控 / 看门狗 (业务需求) -----
export interface HealthCheck {
  name: string;
  status: 'ok' | 'warn' | 'fail';
  detail: string;
  duration_ms: number;
}

export interface SystemStatus {
  uptime_sec: number;
  process_started_at: string;
  version_sha: string;
  python_version: string;
  db_ok: boolean;
  db_latency_ms: number | null;
  pending_migrations: number;
  disk_total_gb: number;
  disk_free_gb: number;
  disk_used_pct: number;
  mem_total_mb: number;
  mem_available_mb: number;
  mem_used_pct: number;
  storage_used_mb: number;
  recent_checks: HealthCheck[];
}

export interface HealthLog {
  id: number;
  check_name: string;
  status: string;
  detail: string | null;
  duration_ms: number | null;
  created_at: string;
}

export const fetchSystemStatus = () =>
  api.get<SystemStatus>('/api/admin/system-status').then((r) => r.data);

export const fetchHealthLogs = (limit = 100, check_name?: string) =>
  api
    .get<HealthLog[]>('/api/admin/system-health-logs', {
      params: { limit, ...(check_name ? { check_name } : {}) },
    })
    .then((r) => r.data);

export const restartApi = () =>
  api.post('/api/admin/restart-api', { confirm: 'RESTART' }).then((r) => r.data);

// ----- 异步导入作业 (业务需求 6) -----
export interface ImportJob {
  id: number;
  user_id: number | null;
  entity_type: string;
  sheet_name: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
  total_rows: number;
  processed_rows: number;
  progress_pct: number;
  error: string | null;
  report: any | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export const commitImporterAsync = (payload: {
  file_b64: string;
  sheet_name: string;
  entity_type: string;
  mapping: Record<string, string>;
  auto_create_suppliers?: boolean;
  auto_match_orders?: boolean;
}) =>
  api
    .post<{ job_id: number; status: string; sheet_name: string; entity_type: string }>(
      '/api/importer/commit-async',
      payload,
      { timeout: 180000 },
    )
    .then((r) => r.data);

export const fetchImportJob = (id: number) =>
  api.get<ImportJob>(`/api/importer/jobs/${id}`).then((r) => r.data);

export const fetchImportJobs = (limit = 50) =>
  api.get<ImportJob[]>('/api/importer/jobs', { params: { limit } }).then((r) => r.data);

// ----- 重启事件 (业务需求 5) -----
export interface SystemEvent {
  id: number;
  kind: string;
  actor: string | null;
  detail: string | null;
  snapshot_json: any | null;
  created_at: string;
}

export const fetchSystemEvents = (limit = 50) =>
  api
    .get<SystemEvent[]>('/api/admin/system-events', { params: { limit } })
    .then((r) => r.data);
