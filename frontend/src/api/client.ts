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

// Phase 13: 401 时先尝试用 refresh_token 续, 失败才跳登录
let refreshing: Promise<string | null> | null = null;

async function tryRefresh(): Promise<string | null> {
  const rt = localStorage.getItem('panse_refresh_token');
  if (!rt) return null;
  try {
    const r = await axios.post<{ access_token: string }>(
      '/api/auth/refresh', { refresh_token: rt },
    );
    localStorage.setItem('panse_token', r.data.access_token);
    return r.data.access_token;
  } catch {
    localStorage.removeItem('panse_token');
    localStorage.removeItem('panse_refresh_token');
    return null;
  }
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const config = err?.config;
    if (err?.response?.status === 401 && config && !config._retried) {
      config._retried = true;
      // 同一时刻只发一次 refresh 请求
      if (!refreshing) refreshing = tryRefresh();
      const newToken = await refreshing;
      refreshing = null;
      if (newToken) {
        config.headers.Authorization = `Bearer ${newToken}`;
        return api.request(config);
      }
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
    .post<{
      token: string;
      access_token?: string;
      refresh_token?: string;
      user: MeUser;
    }>('/api/auth/login', { username, password })
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

export const updateUser = (
  id: number,
  payload: { username?: string; display_name?: string; role?: string; is_active?: boolean },
) => api.patch<MeUser>(`/api/auth/users/${id}`, payload).then((r) => r.data);

export const adminResetPassword = (id: number, newPassword: string) =>
  api.post(`/api/auth/users/${id}/password`, { new_password: newPassword }).then((r) => r.data);

export const changeMyPassword = (oldPassword: string, newPassword: string) =>
  api
    .post('/api/auth/me/password', { old_password: oldPassword, new_password: newPassword })
    .then((r) => r.data);

export const fetchRoles = () =>
  api
    .get<{ roles: string[]; descriptions: Record<string, string> }>('/api/auth/roles')
    .then((r) => r.data);

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

export const listExceptions = (status?: string, limit = 2000) =>
  api
    .get<DataException[]>('/api/exceptions', { params: { status, limit } })
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
  image_url?: string | null;
  custom_scope?: string | null;
  size_detail?: string | null;
  aux_material?: string | null;
  description?: string | null;
}

export const listProducts = (q?: string) =>
  api.get<Product[]>('/api/products', { params: { q, limit: 500 } }).then((r) => r.data);

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

export const updateFeishuBinding = (
  id: number,
  payload: Partial<Omit<FeishuBinding, 'id' | 'system_table'>>,
) => api.patch<FeishuBinding>(`/api/feishu/bindings/${id}`, payload).then((r) => r.data);

export const deleteFeishuBinding = (id: number) =>
  api.delete(`/api/feishu/bindings/${id}`).then((r) => r.data);

export const feishuStatus = () =>
  api.get<FeishuStatus[]>('/api/feishu/status').then((r) => r.data);

export const feishuSupportedTables = () =>
  api.get<{ tables: string[] }>('/api/feishu/supported-tables').then((r) => r.data);

export interface FeishuCredentials {
  app_id: string;
  app_secret_masked: string;
  configured: boolean;
  verification_token_set?: boolean;
  encrypt_key_set?: boolean;
}

export const getFeishuCredentials = () =>
  api.get<FeishuCredentials>('/api/feishu/credentials').then((r) => r.data);

export const putFeishuCredentials = (payload: {
  app_id?: string; app_secret?: string;
  verification_token?: string; encrypt_key?: string;
}) =>
  api.put<FeishuCredentials>('/api/feishu/credentials', payload).then((r) => r.data);

export const testFeishuConnection = () =>
  api.post<{ ok: boolean; error?: string }>('/api/feishu/test').then((r) => r.data);

export interface FeishuSyncResult {
  system_table: string;
  pushed: number;
  pulled: number;
  created_feishu: number;
  created_system: number;
  conflicts: number;
  errors: string[];
}

export const triggerFeishuSync = (system_table?: string) =>
  api
    .post<{ results: FeishuSyncResult[] }>('/api/feishu/sync', { system_table }, { timeout: 120000 })
    .then((r) => r.data);

export interface FeishuConflict {
  id: number;
  system_table: string;
  source_pk: string | null;
  description: string;
  context: {
    diffs?: Array<{ field: string; system: any; feishu: any }>;
    system_updated_at?: string | null;
    feishu_updated_at?: any;
  } | null;
  created_at: string | null;
}

export const listFeishuConflicts = () =>
  api.get<FeishuConflict[]>('/api/feishu/conflicts').then((r) => r.data);

export const resolveFeishuConflict = (id: number, keep: 'system' | 'feishu') =>
  api.post(`/api/feishu/conflicts/${id}/resolve`, { keep }).then((r) => r.data);

// 字段级合并裁决: {字段: 'system'|'feishu'}
export const resolveFeishuConflictFields = (id: number, field_choices: Record<string, 'system' | 'feishu'>) =>
  api.post(`/api/feishu/conflicts/${id}/resolve`, { field_choices }).then((r) => r.data);

// Wiki 节点 token → Bitable App Token
export const resolveFeishuWiki = (wiki_token: string) =>
  api.get<{ app_token: string }>('/api/feishu/resolve-wiki', { params: { wiki_token } }).then((r) => r.data);

// 查询飞书多维表字段列表
export const getFeishuTableFields = (app_token: string, table_id: string) =>
  api.get<{ fields: Array<{ field_name: string; type: number }> }>(
    '/api/feishu/table-fields', { params: { app_token, table_id } }
  ).then((r) => r.data);

// 一键导入预设绑定 (23 表)
export const setupFeishuPreset = (wiki_token: string, enabled = false, overwrite = false) =>
  api.post<{app_token:string; created:number; skipped:number; updated:number; items:Array<{system_table:string;label:string;feishu_table_id:string;action:string}>}>(
    '/api/feishu/setup-preset', { wiki_token, enabled, overwrite }).then(r => r.data);

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
  theoretical_cost?: string | null;
  actual_cost?: string | null;
  actual_freight?: string | null;
  cost_diff?: string | null;
  tracking_confirmed?: boolean;
  manual_confirmed?: boolean;
  signoff_questioned?: boolean;
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
  unresolved_count: number;
  diffs: ReconciliationDiff[];
}

export const runReconciliation = (
  rule?: string,
  period?: { period_start?: string; period_end?: string },
) => {
  const params = period?.period_start || period?.period_end
    ? { period_start: period.period_start, period_end: period.period_end }
    : undefined;
  if (rule) {
    return api.get<ReconciliationResult>(`/api/finance/reconciliation/${rule}`, { params }).then((r) => r.data);
  }
  return api.get<Record<string, ReconciliationResult>>('/api/finance/reconciliation', { params }).then((r) => r.data);
};

export interface SmartMatchResult {
  total_scanned: number;
  tagged: Record<string, number>;
  untouched: number;
}
export const rerunSmartMatch = (account?: string) =>
  api.post<SmartMatchResult>('/api/finance/smart-match/rerun', null, {
    params: account ? { account } : undefined,
  }).then((r) => r.data);

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
  remark: string | null;
}

export const listSamples = () =>
  api.get<Sample[]>('/api/marketing/samples').then((r) => r.data);

export const updateSample = (
  id: number,
  data: { status?: string; location?: string; usage?: string; remark?: string },
) => api.patch<Sample>(`/api/marketing/samples/${id}`, data).then((r) => r.data);

export interface WoodLoss {
  id: number;
  purchase_date: string | null;
  wood_type: string | null;
  spec: string | null;
  unit: string | null;
  inbound_qty: string | null;
  used_qty: string | null;
  loss_qty: string | null;
  loss_rate_pct: string | null;
  related_product_qty: string | null;
  reason: string | null;
  disposition: string | null;
  remark: string | null;
}

export const listWoodLoss = () =>
  api.get<WoodLoss[]>('/api/marketing/wood-loss').then((r) => r.data);

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
  alipay_counterparty_keywords?: string[] | null;
  alipay_account?: string | null;
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

export const cancelImportJob = (id: number) =>
  api.post<ImportJob>(`/api/importer/jobs/${id}/cancel`).then((r) => r.data);

// ----- 智能 Excel 导入 (Phase 14) -----
export interface SheetAnalysis {
  sheet_name: string;
  total_rows: number;
  header_row: number;
  columns: string[];
  sample_rows: any[][];
  suggested_entity: string | null;
  entity_label: string | null;
  confidence: number;
  mapping: Record<string, string>;
  skipped_columns: string[];
  quality: 'good' | 'needs_review' | 'messy';
  quality_score: number;
  issues: Array<{ row_offset: number; column: string; value: any; problem: string; fix: string }>;
  notes: string[];
}

export interface SmartAnalysisResp {
  file_b64: string;
  sheets: SheetAnalysis[];
}

export const smartAnalyzeExcel = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<SmartAnalysisResp>('/api/importer/smart-analyze', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000,
    })
    .then((r) => r.data);
};

export interface ImportConflict {
  source_table: string;
  source_pk: string | null;
  diffs: Array<{ field: string; old: any; new: any }>;
}

export interface SmartCommitReport {
  sheet_name: string;
  entity_type?: string;
  total_rows?: number;
  inserted_parents?: number;
  inserted_children?: number;
  skipped_rows?: number;
  errors?: string[];
  warnings?: string[];
  conflicts?: ImportConflict[];
  skipped?: boolean;
  reason?: string;
  error?: string;
}

export interface PostImportResult {
  logic_issues: number;
  analysis: string | null;
  ai_used: boolean;
}

export const smartCommitExcel = (payload: {
  file_b64: string;
  plan: Array<{
    sheet_name: string;
    entity_type: string;
    mapping: Record<string, string>;
    header_row: number;
    dry_run?: boolean;
    on_conflict?: 'ask' | 'overwrite' | 'keep';
    sheet_account?: string | null;
  }>;
}) =>
  api
    .post<{ reports: SmartCommitReport[]; post_import: PostImportResult }>(
      '/api/importer/smart-commit',
      payload,
      { timeout: 300000 },
    )
    .then((r) => r.data);

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

// ----- 告警 / 通知中心 (Phase 1B) -----
export interface AlertItem {
  id: number;
  kind: string;
  severity: 'info' | 'warn' | 'critical';
  title: string;
  body: string | null;
  dedupe_key: string | null;
  related_url: string | null;
  context_json: Record<string, any> | null;
  sticky: boolean;
  resolved_at: string | null;
  resolved_by: string | null;
  auto_resolve_until: string | null;
  notified_at: string | null;
  created_at: string;
}

export interface AlertSummary {
  info: number;
  warn: number;
  critical: number;
}

export const fetchActiveAlerts = (params: { severity?: string; kind?: string; limit?: number } = {}) =>
  api.get<AlertItem[]>('/api/alerts/active', { params }).then((r) => r.data);

export const fetchAlertSummary = () =>
  api.get<AlertSummary>('/api/alerts/summary').then((r) => r.data);

export const dismissAlert = (id: number) =>
  api.post(`/api/alerts/${id}/dismiss`).then((r) => r.data);

export const fetchAlertHistory = (limit = 100, kind?: string) =>
  api.get<AlertItem[]>('/api/alerts/history', { params: { limit, kind } })
    .then((r) => r.data);

// ----- 定时任务 (Phase 1A, 业务需求 18 自动任务清单) -----
export interface SchedulerJob {
  job_id: string;
  label: string;
  kind: string;
  schedule: Record<string, any>;
  next_run_at: string | null;
}

export interface SchedulerRun {
  id: number;
  job_id: string;
  job_label: string;
  status: string;
  duration_ms: number | null;
  error: string | null;
  result_summary: Record<string, any> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export const fetchSchedulerJobs = () =>
  api.get<SchedulerJob[]>('/api/scheduler/jobs').then((r) => r.data);

export const fetchSchedulerRuns = (limit = 100, job_id?: string) =>
  api.get<SchedulerRun[]>('/api/scheduler/runs', { params: { limit, job_id } })
    .then((r) => r.data);

export const triggerSchedulerJob = (job_id: string) =>
  api.post(`/api/scheduler/jobs/${job_id}/trigger`).then((r) => r.data);

// ----- 截图自动化 (Phase 3, 业务需求 1/6) -----
export interface QianniuOrderParsed {
  order_no: string;
  platform?: string;
  order_date?: string;
  pay_time?: string;
  customer_name?: string;
  customer_phone?: string;
  customer_address?: string;
  product_name?: string;
  sku?: string;
  qty?: number;
  unit_price?: number;
  discount?: number;
  paid_amount?: number;
  platform_fee?: number;
  freight?: number;
  remark?: string;
  confidence?: number;
  warnings?: string[];
  product_code?: string | null;
  sku_code?: string | null;
}

export interface QianniuParseResp {
  image_b64: string;
  mime: string;
  orders: QianniuOrderParsed[];
  ocr_warnings: string[];
}

export const parseQianniuScreenshot = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<QianniuParseResp>('/api/screenshots/qianniu-orders/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const commitQianniuOrders = (orders: QianniuOrderParsed[]) =>
  api
    .post<{ inserted: number; skipped_existing: string[] }>(
      '/api/screenshots/qianniu-orders/commit',
      { orders },
    )
    .then((r) => r.data);

export interface PurchaseLineParsed {
  material_name?: string;
  material_code?: string;
  spec?: string;
  qty?: number;
  unit?: string;
  unit_price?: number;
  amount?: number;
}

export interface PurchaseParsed {
  supplier_name?: string;
  purchase_date?: string;
  purchase_no?: string;
  tracking_no?: string;
  carrier?: string;
  freight?: number;
  total_amount?: number;
  remark?: string;
  lines: PurchaseLineParsed[];
  warnings?: string[];
}

export interface PurchaseParseResp {
  image_b64: string;
  mime: string;
  purchase: PurchaseParsed;
}

export const parsePurchaseScreenshot = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<PurchaseParseResp>('/api/screenshots/purchase/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const commitPurchaseScreenshot = (payload: {
  supplier?: string;
  purchase_date?: string;
  purchase_no?: string;
  tracking_no?: string;
  carrier?: string;
  freight?: number;
  total_amount?: number;
  remark?: string;
  lines: PurchaseLineParsed[];
}) =>
  api
    .post<{ inserted: number; purchase_no: string; has_tracking: boolean }>(
      '/api/screenshots/purchase/commit',
      payload,
    )
    .then((r) => r.data);

// ----- 工厂对账单截图 (Task 3) -----
export interface FactoryReconRowParsed {
  factory_name: string;
  period_start?: string | null;
  period_end?: string | null;
  order_amount?: number | null;
  bill_amount?: number | null;
  paid_amount?: number | null;
  alipay_flow_no?: string | null;
  remark?: string | null;
  warnings?: string[];
}
export interface FactoryReconParseResp {
  image_b64: string;
  mime: string;
  rows: FactoryReconRowParsed[];
  ocr_warnings: string[];
}
export const parseFactoryReconScreenshot = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<FactoryReconParseResp>('/api/screenshots/factory-recon/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};
export const commitFactoryReconScreenshot = (rows: FactoryReconRowParsed[]) =>
  api
    .post<{ inserted: number; skipped: string[] }>(
      '/api/screenshots/factory-recon/commit',
      { rows },
    )
    .then((r) => r.data);

// ----- 销售报表 / 资产 / 预测 (Phase 4) -----
export interface SalesSummary {
  period_start: string;
  period_end: string;
  order_count: number;
  revenue: number;
  cost: number;
  gross_profit: number;
  net_profit: number;
  top_products_by_profit: Array<Record<string, any>>;
  top_products_by_profit_rate: Array<Record<string, any>>;
}

export const fetchSalesSummary = (period: '7d' | '30d' | 'month' | 'year', platform?: string) =>
  api.get<SalesSummary>('/api/reports/sales/summary', {
    params: { period, ...(platform ? { platform } : {}) },
  }).then((r) => r.data);

export const fetchSalesBreakdown = (period: '7d' | '30d' | 'month' | 'year') =>
  api.get<{ period_start: string; period_end: string; rows: Array<Record<string, any>> }>(
    '/api/reports/sales/breakdown', { params: { period } },
  ).then((r) => r.data);

export const fetchForecast30d = () =>
  api.get<{ forecast: Array<any> }>('/api/reports/forecast/30d').then((r) => r.data);

export const fetchStockAdvice = () =>
  api.get<{ products: any[]; materials: any[] }>('/api/reports/stock-advice')
    .then((r) => r.data);

export const fetchSlowMoving = (params: { long_no_sale_days?: number; overstock_ratio?: number } = {}) =>
  api.get<{ long_idle: any[]; overstock: any[]; thresholds: any }>(
    '/api/reports/slow-moving', { params },
  ).then((r) => r.data);

export interface AssetSummary {
  total: number;
  categories: Array<{ name: string; amount: number; detail: any[] }>;
  formula_a: number;
  formula_b: number;
  diff: number;
}

export const fetchAssets = () =>
  api.get<AssetSummary>('/api/reports/assets').then((r) => r.data);

export const fetchUnmatchedFlows = (days = 7) =>
  api.get<{ days: number; rows: any[] }>('/api/reports/unmatched-flows',
    { params: { days } }).then((r) => r.data);

// ----- Phase 8 Tier 1: 订单时间轴 + AI 简报 + 会计期间 + 供应商评分 -----
export interface OrderEvent {
  id: number;
  order_id: number;
  kind: string;
  actor: string | null;
  summary: string;
  detail: string | null;
  context_json: Record<string, any> | null;
  created_at: string;
}

export const fetchOrderTimeline = (orderId: number) =>
  api.get<OrderEvent[]>(`/api/orders/${orderId}/timeline`).then((r) => r.data);

export const postOrderComment = (orderId: number, text: string) =>
  api.post<OrderEvent>(`/api/orders/${orderId}/comments`, { text }).then((r) => r.data);

export interface DailyBriefing {
  id: number;
  for_date: string;
  content: string;
  highlights_json: any[] | null;
  model: string | null;
  generated_at: string | null;
}

export const fetchTodayBriefing = () =>
  api.get<DailyBriefing | null>('/api/briefings/today').then((r) => r.data);

export const fetchRecentBriefings = (limit = 14) =>
  api.get<DailyBriefing[]>('/api/briefings/recent', { params: { limit } }).then((r) => r.data);

export const triggerBriefing = (for_date?: string) =>
  api.post('/api/briefings/generate-now', null, { params: { for_date } }).then((r) => r.data);

export interface AccountingPeriod {
  id: number;
  year: number;
  month: number;
  status: 'open' | 'closed' | 'locked';
  closed_at: string | null;
  closed_by: string | null;
  remark: string | null;
}

export const fetchAccountingPeriods = () =>
  api.get<AccountingPeriod[]>('/api/accounting/periods').then((r) => r.data);

export const closeAccountingPeriod = (year: number, month: number) =>
  api.post<AccountingPeriod>('/api/accounting/periods/close', { year, month }).then((r) => r.data);

export const reopenAccountingPeriod = (year: number, month: number) =>
  api.post<AccountingPeriod>('/api/accounting/periods/reopen', { year, month }).then((r) => r.data);

export const lockAccountingPeriod = (year: number, month: number) =>
  api.post<AccountingPeriod>('/api/accounting/periods/lock', { year, month }).then((r) => r.data);

export interface SupplierScore {
  supplier_id: number;
  year: number;
  month: number;
  on_time_rate: number | null;
  return_rate: number | null;
  price_variance_pct: number | null;
  total_orders: number;
  total_amount: number | null;
  score: number | null;
  rank: number | null;
  detail_json: Record<string, any> | null;
}

export const fetchSupplierScores = (year: number, month: number) =>
  api.get<SupplierScore[]>(`/api/supplier-scores/${year}/${month}`).then((r) => r.data);

export const computeSupplierScores = (year: number, month: number) =>
  api.post(`/api/supplier-scores/compute/${year}/${month}`).then((r) => r.data);

// ----- 数据水位线 (Phase 7) -----
export const fetchDataBaseline = () =>
  api.get<{ baseline: string | null }>('/api/admin/data-baseline').then((r) => r.data);

export const setDataBaseline = (baseline: string) =>
  api.put('/api/admin/data-baseline', { baseline }).then((r) => r.data);

// ----- 客户 CRM (Phase 9) -----
export interface CustomerItem {
  id: number;
  name: string;
  phone: string | null;
  address: string | null;
  tier: 'bronze' | 'silver' | 'gold' | 'platinum';
  first_order_at: string | null;
  last_order_at: string | null;
  total_orders: number;
  total_revenue: number;
  total_returns: number;
  tags: string[];
  note: string | null;
}

export const fetchCustomers = (params: { q?: string; tier?: string; limit?: number } = {}) =>
  api.get<CustomerItem[]>('/api/customers', { params }).then((r) => r.data);

export const fetchCustomer = (id: number) =>
  api.get<CustomerItem>(`/api/customers/${id}`).then((r) => r.data);

export const fetchCustomerOrders = (id: number) =>
  api.get<any[]>(`/api/customers/${id}/orders`).then((r) => r.data);

export const triggerCustomerAggregate = () =>
  api.post('/api/customers/aggregate').then((r) => r.data);

// ----- 智能定价 + 异常诊断 + 物流面单 (Phase 10) -----
export interface PriceSuggestion {
  sku_code: string | null;
  product_code: string;
  cost: number;
  historical_avg_price: number;
  target_margin: number;
  suggested_price: number;
  inventory_pressure: number;
  notes: string[];
}

export const fetchPriceSuggestion = (params: {
  product_code: string; sku_code?: string; target_margin?: number;
}) => api.get<PriceSuggestion>('/api/smart-pricing/suggest', { params }).then((r) => r.data);

export const diagnoseException = (id: number) =>
  api.get<{ analysis: string; suggested_actions: any[]; severity_recommended: string }>(
    `/api/exceptions/${id}/diagnose`,
  ).then((r) => r.data);

export const printShippingLabel = (orderId: number, carrier?: string) =>
  api.post<{ tracking_no: string; carrier: string; label_url: string }>(
    `/api/orders/${orderId}/print-label`, null, { params: { carrier } },
  ).then((r) => r.data);

// ----- 全局搜索 (Tier 3 #14) -----
export interface SearchHit {
  kind: string;
  id: number;
  title: string;
  subtitle: string | null;
  url: string;
}

export const globalSearch = (q: string, limit = 50) =>
  api.get<SearchHit[]>('/api/search', { params: { q, limit } }).then((r) => r.data);

// ----- 售后 / 退货 (Phase 5) -----
export interface AfterSalesItem {
  id: number;
  platform_order_no: string;
  status: string | null;
  reason: string | null;
  refill_tracking_no: string | null;
  second_inbound_confirmed: string | null;
  processed_at: string | null;
  remark: string | null;
}

export const fetchAfterSales = (status?: string, limit = 100) =>
  api.get<AfterSalesItem[]>('/api/aftersales', { params: { status, limit } })
    .then((r) => r.data);

export const createReturn = (payload: { order_no: string; reason: string; tracking_no?: string }) =>
  api.post<AfterSalesItem>('/api/aftersales', payload).then((r) => r.data);

export const markReturnReceived = (id: number) =>
  api.post<AfterSalesItem>(`/api/aftersales/${id}/mark-received`).then((r) => r.data);

export const confirmReturnInbound = (id: number, payload: {
  product_code: string; sku_code?: string; qty: number;
}) =>
  api.post<AfterSalesItem>(`/api/aftersales/${id}/confirm-inbound`, payload)
    .then((r) => r.data);

export const markReturnDamaged = (id: number, reason: string) =>
  api.post<AfterSalesItem>(`/api/aftersales/${id}/mark-damaged`, { reason })
    .then((r) => r.data);

export const disassembleProduct = (payload: {
  product_code: string; sku_code?: string; qty: number;
}) =>
  api.post<{ product_remaining: number; parts_added: any[] }>(
    '/api/aftersales/disassemble-product', payload,
  ).then((r) => r.data);

// ----- 工厂订单自动派生 (Phase 2) -----
export const generateFactoryOrder = (orderId: number) =>
  api.post<{
    factory_order_id: number;
    factory_order_no: string;
    locked_lines: any[];
    shortages: any[];
    alerts_created: number[];
  }>(`/api/orders/${orderId}/generate-factory-order`).then((r) => r.data);

export const createFutureOrder = (payload: {
  base_order_no: string;
  activate_at: string;
  product_code?: string;
  sku?: string;
  qty?: number;
  customer_name?: string;
  remark?: string;
}) => api.post<{ id: number; order_no: string; activate_at: string }>(
  '/api/orders/future', payload,
).then((r) => r.data);

export const voidFactoryOrder = (factoryOrderId: number, reason: string) =>
  api.post<{ id: number; factory_order_no: string; voided_at: string; voided_reason: string }>(
    `/api/orders/factory-orders/${factoryOrderId}/void`,
    { reason },
  ).then((r) => r.data);

// ----- 通知配置 (业务需求扩展: 看门狗触发时推 Slack/微信/钉钉/飞书) -----
export interface NotifyProvider {
  value: string;
  label: string;
}

export interface NotifyConfig {
  provider: string;
  webhook_masked: string;
  webhook_set: boolean;
  supported_providers: NotifyProvider[];
}

export const fetchNotifyConfig = () =>
  api.get<NotifyConfig>('/api/admin/notify-config').then((r) => r.data);

export const updateNotifyConfig = (payload: {
  provider?: string;
  webhook?: string;
}) => api.put<NotifyConfig>('/api/admin/notify-config', payload).then((r) => r.data);

export const testNotifyConfig = () =>
  api
    .post<{ ok: boolean; detail: string }>('/api/admin/notify-config/test')
    .then((r) => r.data);

// ===== Phase 13: 产品主数据中心 / 异常工作台 / Dashboard =====

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
  sku_code: string;
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
export const updateProductInventory = (
  id: number,
  payload: { qty?: number; locked_qty?: number; remark?: string },
) => api.patch(`/api/inventory/products/${id}`, payload).then(r => r.data);

// -- 订单理论成本反推 (按 BOM × 物料单价)
export interface OrderCostLine {
  material_code: string;
  material_name: string | null;
  qty_per_product: string;
  unit_price: string | null;
  line_cost: string | null;
  missing_price: boolean;
}
export interface OrderCostBreakdown {
  order_no: string;
  sku_code: string | null;
  qty: number;
  unit_cost: string;
  total_cost: string;
  resolved: boolean;
  missing_price_count: number;
  note: string | null;
  lines: OrderCostLine[];
}
export const getOrderCostBreakdown = (id: number) =>
  api.get<OrderCostBreakdown>(`/api/orders/${id}/cost-breakdown`).then(r => r.data);
export const recomputeOrderCost = (id: number) =>
  api.post<OrderCostBreakdown>(`/api/orders/${id}/recompute-cost`).then(r => r.data);
export const recomputeAllOrderCosts = (only_missing = true) =>
  api.post<{ updated: number; skipped_no_bom: number; total: number }>(
    `/api/orders/recompute-costs?only_missing=${only_missing}`,
  ).then(r => r.data);

// -- 异常工作台
export const fixException = (id: number, fields: Record<string, unknown>) =>
  api.post(`/api/exceptions/${id}/fix`, { fields }).then(r => r.data);
export const runDataQuality = () =>
  api.post<Record<string, number>>('/api/exceptions/run-data-quality').then(r => r.data);
export const getExceptionCounts = () =>
  api.get<Record<string, number>>('/api/exceptions/counts-by-type').then(r => r.data);
export const getOpenExceptionCount = () =>
  api.get<{ count: number }>('/api/exceptions/open-count').then(r => r.data);

// -- 运行日志 (内存环形缓冲, 用于界面排查)
export interface LogLine {
  ts: string;
  level: string;
  logger: string;
  msg: string;
}
export const getRecentLogs = (params?: {
  limit?: number; level?: string; contains?: string; logger_prefix?: string;
}) =>
  api.get<{ logs: LogLine[] }>('/api/logs/recent', { params }).then(r => r.data.logs);

// -- AI 对账走查
export interface ReconcileWalkthroughResult {
  issues: Array<{
    id?: number; type: string; description: string; ai_analysis?: string; suggestion?: string; source: string;
  }>;
  ai_used: boolean;
  total: number;
}
export const reconcileWalkthrough = () =>
  api.post<ReconcileWalkthroughResult>('/api/ai/reconcile-walkthrough').then(r => r.data);

// -- Dashboard
export interface DashboardData {
  orders: {
    status_counts: Record<string, number>;
    trend_30d: Array<{ date: string; count: number; revenue: number }>;
    total_30d: number;
    revenue_30d: number;
    count_7d: number;
  };
  inventory: {
    part_total: number; part_negative: number;
    part_below_safety: number; part_oversold: number;
    product_total: number; product_low_stock: number;
  };
  finance: {
    alipay_income_30d: number; order_revenue_30d: number;
    theoretical_cost_30d: number; actual_cost_30d: number;
    gross_profit_30d: number; gross_margin_rate: number;
    reconciliation_unresolved: number;
    aftersales_count: number; aftersales_cost: number;
  };
  health: { open_exceptions: number; health_score: number };
}
export const getDashboard = () =>
  api.get<DashboardData>('/api/dashboard').then(r => r.data);

// -- 订单双核对签收
export const confirmOrderTracking = (orderId: number) =>
  api.post(`/api/orders/${orderId}/confirm-tracking`).then(r => r.data);
export const confirmOrderManual = (orderId: number) =>
  api.post(`/api/orders/${orderId}/confirm-manual`).then(r => r.data);

// -- 微定制 AI 报价
export interface AiQuoteBreakdown {
  label: string;
  amount: number;
  note: string;
}
export interface AiQuoteResult {
  base_product: string | null;
  base_sku: string | null;
  base_size: string | null;
  changes: string[];
  est_price: number | null;
  breakdown: AiQuoteBreakdown[];
  ai_used: boolean;
  model: string | null;
  error: string | null;
}
export const aiCustomizationQuote = (file: File): Promise<AiQuoteResult> => {
  const fd = new FormData();
  fd.append('image', file);
  return api.post<AiQuoteResult>('/api/customization/ai-quote', fd).then(r => r.data);
};

export const autofillGenerate = (dry_run = false) =>
  api.post<{ factory_orders: { created: number; skipped: number; dry_run: boolean } }>(
    `/api/exceptions/autofill/generate?dry_run=${dry_run}`
  ).then(r => r.data);

// ===== 全定制报价参数 (后台可调) =====
export interface QuoteConfig {
  factory_profit_rate: number;
  panse_profit_rate: number;
  safety_rate: number;
  competitor_coupon_rate: number;
  projection_type: string;          // front=正面 / top=俯视
  projection_rate: number;
  packing: number[];                // [小,中,大]
  labor: Record<string, number[]>;  // 品类 → [小,中,大]
  size_rules: Record<string, number[]>;  // 品类 → [大阈值,中阈值]
  prices: Record<string, number>;   // 材料 → 单价
}
export const getQuoteConfig = () =>
  api.get<QuoteConfig>('/api/customization/quote-config').then(r => r.data);
export const updateQuoteConfig = (patch: Partial<QuoteConfig>) =>
  api.put<QuoteConfig>('/api/customization/quote-config', patch).then(r => r.data);

// ===== 全定制: 板单实时报价 + AI 抽板 =====
export interface QuoteBoard {
  part: string; material: string;
  length_cm: number; width_cm: number; qty: number;
  unit?: string; is_accessory?: boolean; is_drawer_rail?: boolean;
}
export interface BoardQuoteResult {
  wood_cost: number; labor_fee: number; factory_in_cost: number; factory_profit: number;
  factory_wood_total: number; accessory_total: number; drawer_rail_total: number;
  packing_fee: number; freight: number; install_fee: number;
  panse_cost: number; final_quote: number; factory_quote_compare: number;
  factory_quote_conservative: number; safety_rate: number;
  projection_estimate: number | null; projection_area_m2: number | null;
  factory_quote: number | null; factory_diff: number | null; size_class: string;
  wood_lines: { part: string; material: string; cost: number }[];
  accessory_lines: { part: string; material: string; cost: number }[];
}
export const boardQuote = (payload: {
  product_type: string; length_m: number;
  overall_width_m?: number; overall_height_m?: number;
  boards: QuoteBoard[]; factory_quote?: number;
}) => api.post<BoardQuoteResult>('/api/customization/board-quote', payload).then(r => r.data);

export interface ExtractBoardsResult {
  ai_used: boolean; model?: string; product_type: string | null;
  overall: { length_mm?: number; width_mm?: number; height_mm?: number };
  boards: QuoteBoard[]; error: string | null;
}
export const extractBoards = (file: File) => {
  const fd = new FormData(); fd.append('file', file);
  return api.post<ExtractBoardsResult>('/api/customization/extract-boards', fd).then(r => r.data);
};

// ===== 竞品 Top-10 =====
export interface CompetitorRow {
  id: number;
  store: string | null; category: string | null; product: string | null;
  link: string | null; wood: string | null; sku_name: string | null;
  daily_price: number | null;          // 我表价(叠券前)
  latest_price: number | null;         // 最新价(抓取/手动, 叠券前)
  fetch_status: string | null;
  latest_fetched_at: string | null;
  coupon_cut: number;                  // 通用券减额
  after_coupon: number | null;         // 券后价
  confidence: number;
}
export const competitorsTop = (q: string, limit = 10) =>
  api.get<CompetitorRow[]>('/api/customization/competitors', { params: { q, limit } }).then(r => r.data);
export const refreshCompetitor = (id: number) =>
  api.post<CompetitorRow>(`/api/customization/competitors/${id}/refresh`).then(r => r.data);
export const setCompetitorPrice = (id: number, latest_price: number) =>
  api.patch<CompetitorRow>(`/api/customization/competitors/${id}`, { latest_price }).then(r => r.data);

// ----------------------------- 版本信息 ----------------------------- //
export interface VersionInfo {
  commit: string;            // 短哈希, 如 6aaf8ad
  commit_full: string;
  commit_date: string;       // commit 作者时间
  commit_message: string;
  branch: string;
  deployed_at: string;       // 看门狗 build 这版代码的时间 (容器里唯一可靠的"部署时间")
  source: string;            // build_file | runtime_git | unknown
}
export const getVersion = () =>
  api.get<VersionInfo>('/api/version').then((r) => r.data);

// ----------------------------- 订单细节自动生成 ----------------------------- //
export interface GenerateOrderDetailsResult {
  orders_scanned: number;
  orders_matched: number;
  details_created: number;
  details_skipped: number;
  orders_no_bom: string[];
  orders_no_bom_count: number;
  orders_no_product: number;
}
export const generateOrderDetails = (orderNos?: string[], onlyMissing = true) =>
  api
    .post<GenerateOrderDetailsResult>('/api/orders/generate-order-details', {
      order_nos: orderNos ?? null,
      only_missing: onlyMissing,
    })
    .then((r) => r.data);

// ----------------------------- 配件采购 (OCR + 发票留存) ----------------------------- //
export interface PurchaseLine {
  item_name: string;
  spec: string;
  unit: string;
  qty: number;
  unit_price: number | null;
  amount: number | null;
}
export interface PurchaseRow {
  id: number;
  purchase_no: string;
  supplier: string | null;
  purchase_date: string | null;
  material_code: string | null;
  material_name: string | null;
  spec: string | null;
  qty: number;
  unit_price: number | null;
  amount: number | null;
  tracking_no: string | null;
  freight: number | null;
  total_amount: number | null;
  payment_status: string;
  source_file_id: number | null;
  ocr_warnings: string[];
  ocr_model: string | null;
}
export interface PurchaseOcrResult {
  file_id: number;
  supplier: string | null;
  purchase_date: string | null;
  tracking_no: string | null;
  freight: number | null;
  total_amount: number | null;
  confidence: number;
  warnings: string[];
  lines: PurchaseLine[];
  created_purchase_ids: number[];
}
export interface PurchaseFileRow {
  id: number;
  year: number;
  month: number;
  original_name: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  uploaded_by: string | null;
}
export const uploadPurchaseOcr = (file: File, autoCommit = true) => {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<PurchaseOcrResult>('/api/purchases/upload-ocr', form, {
      params: { auto_commit: autoCommit },
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000, // OCR 可能要 60-120s
    })
    .then((r) => r.data);
};
export const listPurchases = (supplier?: string, limit = 200) =>
  api
    .get<PurchaseRow[]>('/api/purchases', { params: { supplier, limit } })
    .then((r) => r.data);
export const listPurchaseFiles = (year?: number, month?: number) =>
  api
    .get<PurchaseFileRow[]>('/api/purchases/files', { params: { year, month } })
    .then((r) => r.data);
export const purchaseSourceImageUrl = (purchaseId: number) =>
  `/api/purchases/${purchaseId}/source-image`;
export const purchaseFileImageUrl = (fileId: number) =>
  `/api/purchases/files/${fileId}/image`;

// ---- 校验导出 ----
export const validateExportExcel = async (file: File): Promise<Blob> => {
  const form = new FormData();
  form.append('file', file);
  const resp = await api.post('/api/importer/validate-export', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    responseType: 'blob',
    timeout: 120000,
  });
  return resp.data as Blob;
};
