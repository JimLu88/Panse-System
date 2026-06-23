import { api } from './base';

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
  related_order_no: string | null;
  sold_at: string | null;
  remark: string | null;
}

export const listSamples = () =>
  api.get<Sample[]>('/api/marketing/samples').then((r) => r.data);

export const updateSample = (
  id: number,
  data: { status?: string; location?: string; usage?: string; remark?: string },
) => api.patch<Sample>(`/api/marketing/samples/${id}`, data).then((r) => r.data);

export interface SampleSellPayload {
  order_no: string;
  repair_fee?: number;
  transfer_freight?: number;
  supplier?: string;
}
export const sellSample = (id: number, data: SampleSellPayload) =>
  api.post(`/api/marketing/samples/${id}/sell`, data).then((r) => r.data);

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

export interface RoiMonthRow {
  period: string;
  promotion_spend: number;
  order_revenue: number;
  order_count: number;
  spend_ratio: number | null;   // 推广支出 / 正式销售额
  roi: number | null;
}
export interface RoiMonthly {
  months: RoiMonthRow[];
  total_spend: number;
  total_revenue: number;
  total_order_count: number;
  overall_spend_ratio: number | null;
}
export const getRoiMonthly = (year?: number) =>
  api.get<RoiMonthly>('/api/marketing/roi/monthly', { params: year ? { year } : {} })
    .then((r) => r.data);

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

export interface BusinessMonthRow {
  period: string;
  real_order_count: number;
  refill_order_count: number;
  real_revenue: number;
  refill_revenue: number;
  total_revenue: number;
  refill_order_ratio: number;
  refill_cost_ratio: number;
  promo_expense: number;
  promo_ratio: number;
  factory_bill: number;
  effective_cost?: number;            // 商品成本(含定制推演)
  cogs_estimated?: boolean;
  freight_expense?: number;           // 物流费
  install_upstairs_expense?: number;  // 安装上楼
  platform_deduction?: number;        // 平台扣点 (实付−实收, 统一口径)
  tax_expense?: number;
  aftersales_compensation: number;
  aftersales_count: number;
  aftersales_rate: number;
  outsourcing_expense: number;
  outsourcing_estimated?: boolean;
  fixed_costs?: number;               // 固定成本/管理费用 (房租等)
  refill_cost?: number;               // 补单(刷单)成本
  total_expense: number;
  net_profit: number;
  net_profit_rate: number;
  avg_lead_time_days: number | null;
}

export interface BusinessMonthlyResult {
  rows: BusinessMonthRow[];
  summary: BusinessMonthRow;
}

export const fetchBusinessMonthly = (fromYear = 2026, fromMonth = 1) =>
  api
    .get<BusinessMonthlyResult>('/api/reports/business-monthly', {
      params: { from_year: fromYear, from_month: fromMonth },
    })
    .then((r) => r.data);

// 逐单核对 (财务) — 某月每笔订单的完整成本拆解 + 支付宝覆盖/对账 + 问题单
export interface PerOrderRow {
  order_no: string;
  product_name: string;
  is_custom: boolean;
  order_date: string | null;
  paid_amount: number;
  refund_amount: number;
  revenue: number;
  cost_goods: number;
  cost_freight: number;
  cost_install: number;
  cost_platform: number;
  cost_tax: number;
  cost_aftersales: number;
  cost_total: number;
  net_profit: number;
  net_margin: number;
  alipay_covered: boolean;
  cost_reconciled: boolean;
  cost_estimated: boolean;
  is_loss: boolean;
  // 工厂成本核对 (预算 vs 实际): 工厂账单只含木作, 配件/打包恒为预估
  factory_bill_recorded: boolean;        // 工厂账单已入账
  predicted_wood: number | null;         // 预算木作(定价表)
  est_parts: number | null;              // 预估配件
  est_packaging: number | null;          // 预估打包
  actual_wood: number | null;            // 实际木作(工厂账单)
  wood_diff: number | null;              // 木作差额(实际−预算)
}
export interface FixedCostItem {
  name: string;
  amount: number;
  period: 'monthly' | 'yearly';
  active: boolean;
}
export interface PerOrderSubtotal {
  paid_amount: number; refund_amount: number; revenue: number;
  cost_goods: number; cost_freight: number; cost_install: number; cost_platform: number;
  cost_tax: number; cost_aftersales: number; cost_total: number; net_profit: number;
  // 工厂成本核对合计
  predicted_wood: number; est_parts: number; est_packaging: number; actual_wood: number; wood_diff: number;
  promo_expense: number; outsourcing_expense: number; outsourcing_estimated: boolean;
  fixed_costs: number; fixed_cost_items: FixedCostItem[];
  refill_count: number; refill_gmv: number; refill_cost: number;
  refill_platform: number; refill_tax: number; refill_commission: number;
  period_net_profit: number; period_net_margin: number;
}
export interface PerOrderReconcileResult {
  period: string;
  order_count: number;
  problem_count: number;
  loss_count: number;
  uncovered_count: number;
  estimated_count: number;
  rows: PerOrderRow[];
  subtotal: PerOrderSubtotal;
}
export const fetchPerOrderReconcile = (year: number, month: number) =>
  api
    .get<PerOrderReconcileResult>('/api/reports/per-order-reconcile', { params: { year, month } })
    .then((r) => r.data);

// 通过带鉴权的 axios 实例取 blob 再触发下载 (window.open 直链会丢 Authorization 头 → 401「需要登录」)
export async function downloadPerOrderReconcile(year: number, month: number) {
  const resp = await api.get('/api/reports/per-order-reconcile/export', {
    params: { year, month },
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `逐单核对_${year}-${String(month).padStart(2, '0')}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// 自定义固定成本/管理费用项 (房租/水电/软件…) — 用户可自增删
export const getFixedCostItems = () =>
  api.get<{ items: FixedCostItem[]; monthly_total: number }>('/api/finance/fixed-cost-items')
    .then((r) => r.data);
export const putFixedCostItems = (items: FixedCostItem[]) =>
  api.put<{ items: FixedCostItem[]; monthly_total: number }>('/api/finance/fixed-cost-items', { items })
    .then((r) => r.data);

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

// ----- Customization (业务需求 §2) -----
export interface CustomizationDiffLine {
  material_code: string;
  material_name: string | null;
  original_qty: string;
  new_qty: string;
  note: string | null;
  requires_new_material: boolean;
}

export interface StockCheckItem {
  material_code: string;
  material_name: string | null;
  need: number;
  available?: number;
  shortage?: number;
}

export interface StockCheck {
  in_stock: StockCheckItem[];
  need_purchase: StockCheckItem[];
  need_new_material: StockCheckItem[];
  has_shortage: boolean;
}

export interface CustomizationPreview {
  base_sku_code: string;
  proposed_custom_sku_code: string;
  dimension_changes: Record<string, unknown>;
  diff_lines: CustomizationDiffLine[];
  stock_check?: StockCheck | null;   // Plan F5 库存预检
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
  acknowledge_shortage?: boolean;   // Plan F5: 缺料弹窗确认后置 true
}) =>
  api
    .post<{ custom_variant_id: number; custom_sku_code: string; cloned_bom_lines: number }>(
      '/api/customization/confirm',
      payload,
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
  bottom_products_by_profit?: Array<Record<string, any>>;
}

// period: 7d/30d/month/year/last_month/YYYY-MM (按月下拉, 2026-06-17)
export const fetchSalesSummary = (period: string, platform?: string, brand?: string) =>
  api.get<SalesSummary>('/api/reports/sales/summary', {
    params: { period, ...(platform ? { platform } : {}), ...(brand ? { brand } : {}) },
  }).then((r) => r.data);

export const fetchSalesBreakdown = (period: string, brand?: string) =>
  api.get<{ period_start: string; period_end: string; rows: Array<Record<string, any>> }>(
    '/api/reports/sales/breakdown', { params: { period, ...(brand ? { brand } : {}) } },
  ).then((r) => r.data);

// Plan F6: 经营状况分析 (收支占比 + 净利)
export interface OperatingAnalysis {
  period: string;
  period_start: string;
  period_end: string;
  revenue: number;
  expense_items: Array<{ name: string; amount: number; pct: number }>;
  total_expense: number;
  net_profit: number;
  net_profit_rate: number;
}

export const fetchOperatingAnalysis = (period: string) =>
  api.get<OperatingAnalysis>('/api/reports/operating-analysis', { params: { period } })
    .then((r) => r.data);

// Plan L6: 两口径资金差额下钻 — 每个科目的构成明细 TopN
export const fetchAssetDrilldown = () =>
  api.get<Record<string, any[]>>('/api/reports/assets/diff-drilldown').then((r) => r.data);

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
  products?: string[];
}

export const fetchCustomers = (params: { q?: string; tier?: string; limit?: number } = {}) =>
  api.get<CustomerItem[]>('/api/customers', { params }).then((r) => r.data);

export const fetchCustomer = (id: number) =>
  api.get<CustomerItem>(`/api/customers/${id}`).then((r) => r.data);

export const fetchCustomerOrders = (id: number) =>
  api.get<any[]>(`/api/customers/${id}/orders`).then((r) => r.data);

export const triggerCustomerAggregate = (includeHistorical = true) =>
  api.post('/api/customers/aggregate', null, {
    params: { include_historical: includeHistorical },
  }).then((r) => r.data);

// ----- 运营待办台账 (SOP 每日/每周/每月清单) -----
export interface OpsTask {
  key: string;
  title: string;
  detail: string;
  route?: string | null;
  done: boolean;
  done_at: string | null;
  auto?: boolean;   // 系统按数据自动判完成(导入类), 不用手动勾
  overdue?: boolean;   // 设了超时阈值且本周期未完成 → 已超时(系统发报警)
}
export interface OpsGroup {
  freq: string;
  label: string;
  period_key: string;
  done_count: number;
  total: number;
  tasks: OpsTask[];
}
export interface PlatformLogin {
  platform: string;
  need_scan: boolean;
  message: string;
  scan_url?: string | null;
  last_ok?: string | null;
}
export interface OpsChecklist {
  groups: OpsGroup[];
  today: string;
  login_status?: PlatformLogin[];
}
export const fetchOpsChecklist = () =>
  api.get<OpsChecklist>('/api/ops-checklist').then((r) => r.data);
export const toggleOpsTask = (task_key: string, done: boolean) =>
  api.post<OpsChecklist>('/api/ops-checklist/toggle', { task_key, done }).then((r) => r.data);

// ----- 智能定价 + 异常诊断 (Phase 10) -----
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
  customer_name: string | null;
  product_name: string | null;
  product_code: string | null;
  sku_code: string | null;
  status: string | null;
  reason: string | null;
  refill_tracking_no: string | null;
  return_tracking_no: string | null;
  second_inbound_confirmed: string | null;
  processed_at: string | null;
  remark: string | null;
  in_platform_total: string | null;
  out_platform_total: string | null;
  total_cost: string | null;
}

export const updateAfterSales = (id: number, patch: {
  return_tracking_no?: string | null;
  refill_tracking_no?: string | null;
  remark?: string | null;
}) => api.patch<AfterSalesItem>(`/api/aftersales/${id}`, patch).then((r) => r.data);

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

// 拆 BOM 历史 + 回撤 (用户需求 2026-06-11: 误操作可补救)
export interface DisassemblyLogRow {
  id: number; product_code: string; sku_code: string | null; qty: number;
  parts: { material_code: string; qty: number }[];
  actor: string | null; created_at: string | null;
  undone_at: string | null; undone_by: string | null;
}

export const listDisassemblyLogs = () =>
  api.get<DisassemblyLogRow[]>('/api/aftersales/disassembly-logs').then((r) => r.data);

export const undoDisassembly = (logId: number) =>
  api.post(`/api/aftersales/disassembly-logs/${logId}/undo`).then((r) => r.data);

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
    refill_excluded_30d?: number;   // 注释用: 有补单 ¥X 未计入
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
export const getDashboard = (params?: { start?: string; end?: string }) =>
  api.get<DashboardData>('/api/dashboard', { params }).then(r => r.data);

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
export const addCompetitor = (payload: {
  store?: string; category?: string; product?: string; sku_name?: string;
  wood?: string; link?: string; daily_price?: number; latest_price?: number;
}) => api.post<CompetitorRow>('/api/customization/competitors', payload).then(r => r.data);

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
