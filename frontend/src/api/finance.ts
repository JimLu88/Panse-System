import { api } from './base';
import { CsvImportReport } from './orders';

// ----- Finance -----

// 刷单(补单)单列汇总 — 所有算账页面统一展示「刷单已剔除、单列于此」(2026-06-19)
export interface RefillSummary {
  period: [string, string];
  count: number;
  gmv: number;    // 刷单流水(订单额)总额
  cost: number;   // 刷单真实成本(平台扣点+税+运费+佣金)
  note: string;
}

export const getRefillSummary = (params: { period_start?: string; period_end?: string } = {}) =>
  api.get<RefillSummary>('/api/finance/refill-summary', { params }).then((r) => r.data);

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
  q?: string;
  only_unclassified?: boolean;
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
  account_no: string | null;
  period_year: number;
  period_month: number;
  as_of_date: string | null;
  opening_balance: string;
  income: string;
  expense: string;
  closing_balance: string;
  remark: string | null;
}

export const listBalances = (params: { account_name?: string; year?: number } = {}) =>
  api.get<AccountBalanceRow[]>('/api/finance/accounts', { params }).then((r) => r.data);

export const recomputeBalance = (payload: {
  account_name: string;
  year: number;
  month: number;
  opening_balance?: string;
}) => api.post<AccountBalanceRow>('/api/finance/accounts/recompute', payload).then((r) => r.data);

// 手动录入/更新账户余额快照 (账户名+年+月 upsert); 余额多是某天手填的, as_of_date=统计日期
export interface BalanceUpsertPayload {
  account_name: string;
  account_no?: string | null;
  period_year: number;
  period_month: number;
  as_of_date?: string | null;
  opening_balance?: string;
  income?: string;
  expense?: string;
  closing_balance: string;
  remark?: string | null;
}
export const upsertBalance = (payload: BalanceUpsertPayload) =>
  api.post<AccountBalanceRow>('/api/finance/accounts', payload).then((r) => r.data);

// Plan F10: 期初余额倒推 — 最近快照 − 区间Σ流水 → target_date 当日期初
export interface DeriveOpeningResult {
  ok: boolean;
  message?: string;
  account?: string;
  target_date?: string;
  snapshot_date?: string;
  snapshot_balance?: number;
  interval_net_flow?: number;
  derived_balance?: number;
  span_days?: number;
  days_with_flows?: number;
  gap_days?: number;
  hint?: string;
}
export const deriveOpeningBalance = (account: string, targetDate: string) =>
  api.get<DeriveOpeningResult>('/api/finance/balances/derive-opening', {
    params: { account, target_date: targetDate },
  }).then((r) => r.data);

export const deleteBalance = (id: number) =>
  api.delete<{ deleted: number }>(`/api/finance/accounts/${id}`).then((r) => r.data);

// 删除整个账户的全部余额记录 (高危: 需登录密码二次确认 + 前端再输一遍账户名)
export const deleteAccountByName = (accountName: string, password: string) =>
  api
    .delete<{ deleted_account: string; deleted_rows: number }>(
      '/api/finance/accounts/by-name/all',
      { params: { account_name: accountName }, data: { password } },
    )
    .then((r) => r.data);

export const importAccountBalancesCsv = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<{ inserted: number; skipped_invalid: number; errors: string[] }>(
      '/api/finance/accounts/import-csv', fd,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    .then((r) => r.data);
};

export interface ReconciliationDiff {
  key: string;
  expected: string | null;
  actual: string | null;
  diff: string | null;
  severity: 'ok' | 'warning' | 'error' | 'not_available';
  message: string;
  related_records?: string[];  // 涉及的明细单号(支付宝流水号/工厂单号/订单号), 供核对
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

// 人工做平: 永久豁免某条对账差异 (带原因, 进修改档案)
export const writeoffReconciliationDiff = (rule: string, key: string, reason: string) =>
  api.post('/api/finance/reconciliation/writeoff', { rule, key, reason }).then((r) => r.data);

export interface WriteoffsOut {
  keys: Record<string, string[]>;
  totals: Record<string, number>;
  grand_total: number;
  count: number;
  synced_at?: string | null;   // 异常池最近同步时间
}

// 工厂别名映射 (货款对账两侧名称归一; 系统内自助维护)
export const getFactoryAliases = () =>
  api.get<{ aliases: Record<string, string> }>(
    '/api/finance/reconciliation/factory-aliases',
  ).then((r) => r.data.aliases);

export const saveFactoryAliases = (aliases: Record<string, string>) =>
  api.put('/api/finance/reconciliation/factory-aliases', { aliases }).then((r) => r.data);

export const listReconciliationWriteoffs = () =>
  api.get<WriteoffsOut>('/api/finance/reconciliation/writeoffs').then((r) => r.data);

// 对账每日快照 (近 N 天) — 趋势图数据源, 由每日 23:30 调度写入
export interface ReconSnapshotRow {
  snap_date: string;
  rule: string;
  ok: number;
  warning: number;
  error: number;
  total_diff_abs: number;
}
export const listReconSnapshots = (days = 30) =>
  api.get<{ rows: ReconSnapshotRow[] }>(
    '/api/finance/reconciliation/snapshots', { params: { days } },
  ).then((r) => r.data.rows);

// 经营支出自动配流水: 缺流水号的 日常/外包/品牌 记录按金额+日期窗口配支付宝支出
export interface ExpenseMatchOut {
  matched: Record<string, number>;
  ambiguous: number;
  unmatched: number;
  details: string[];
}
export const matchExpenseFlows = () =>
  api.post<ExpenseMatchOut>('/api/finance/reconciliation/match-expense-flows')
    .then((r) => r.data);

export interface SmartMatchResult {
  total_scanned: number;
  tagged: Record<string, number>;
  untouched: number;
}
export const rerunSmartMatch = (account?: string) =>
  api.post<SmartMatchResult>('/api/finance/smart-match/rerun', null, {
    params: account ? { account } : undefined,
  }).then((r) => r.data);

export interface AlipayRouteResult {
  aftersales_created: number;
  promotion_filled: number;
  daily_filled: number;
  outsourcing_filled: number;
  purchases_created: number;
  factory_flipped: number;
}
export const routeAlipayFlows = (rerunClassify = true) =>
  api.post<AlipayRouteResult>('/api/finance/alipay-flows/route', null, {
    params: { rerun_classify: rerunClassify },
  }).then((r) => r.data);

// 手动跑一遍全自动对账流水线 (归类/退款识别/工厂匹配/核销/配流水/成本/对账/写异常)
export const runRealtimeSync = () =>
  api.post<Record<string, unknown>>('/api/finance/realtime-sync').then((r) => r.data);

export const detectRefunds = () =>
  api.post<{ pairs_found: number; message: string }>('/api/finance/alipay-flows/detect-refunds')
    .then((r) => r.data);

export const matchFactoryAlipay = (factoryName?: string) =>
  api.post<{ matched_periods: number; message: string }>(
    '/api/finance/factory-reconciliation/match-alipay',
    null,
    { params: factoryName ? { factory_name: factoryName } : undefined },
  ).then((r) => r.data);

export const rebuildFactoryReconciliation = (factoryName?: string) =>
  api.post<{ periods: number; created: number; updated: number }>(
    '/api/finance/factory-reconciliation/rebuild',
    null,
    { params: factoryName ? { factory_name: factoryName } : undefined },
  ).then((r) => r.data);

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
  latest_score?: number | null;
  latest_rank?: number | null;
  score_period?: string | null;
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

// #3: 从支付宝流水挖候选 + 批量建供应商
export interface AlipaySupplierCandidate {
  counterparty: string;
  payment_count: number;
  total_paid: number;
}
export const getAlipaySupplierCandidates = (minCount = 2) =>
  api
    .get<{ candidates: AlipaySupplierCandidate[]; total: number }>('/api/suppliers/alipay-candidates', { params: { min_count: minCount } })
    .then((r) => r.data);
export const autoCreateSuppliers = (counterparties: string[], supplier_type = 'other') =>
  api
    .post<{ created: string[]; count: number }>('/api/suppliers/auto-create', { counterparties, supplier_type })
    .then((r) => r.data);
// 从配件采购记录(真实供应商源)挖候选 — 结构同支付宝候选, 前端复用同一个建档弹窗
export const getPurchaseSupplierCandidates = (minCount = 1) =>
  api
    .get<{ candidates: AlipaySupplierCandidate[]; total: number }>('/api/suppliers/purchase-candidates', { params: { min_count: minCount } })
    .then((r) => r.data);

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
// Excel/CSV 直接导入采购记录 (列名映射, 不走 OCR)
export const importPurchasesTable = (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<{ inserted: number; skipped_duplicate: number; skipped_invalid: number;
            unmapped_columns: string[]; message: string }>(
      '/api/purchases/import-table', form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    .then((r) => r.data);
};

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

// ----- 剩余流水（可用资金）测算 -----
export interface CashFlowLine {
  key: string;
  label: string;
  amount: string;
  manual: boolean;
  source: string;
}
export interface CashFlowFreshness {
  source: string;
  as_of: string | null;
  days_ago: number | null;
  status: 'fresh' | 'aging' | 'stale' | 'unknown';
}
export interface CashFlowInvestment {
  total_investment: string;
  total_profit: string;
  recovered: boolean | null;
  recovery_rate: number | null;
  remaining: string;
  profit_detail: {
    order_count: number;
    revenue: string;
    cost: string;
    expense: string;
    net_profit: string;
    orders_missing_cost: number;
  };
}
export interface CashFlowSummary {
  total: string;
  total_additions: string;
  total_subtractions: string;
  additions: CashFlowLine[];
  subtractions: CashFlowLine[];
  investment?: CashFlowInvestment | null;
  other_account_balance: string;
  freshness: CashFlowFreshness[];
  manual?: { shop_deposit: string; total_investment: string; factory_settlement_days: number };
  generated_at: string;
}

export const getCashFlow = () =>
  api.get<CashFlowSummary>('/api/finance/cash-flow').then((r) => r.data);

export const updateCashFlowSettings = (payload: {
  shop_deposit?: string;
  total_investment?: string;
  factory_settlement_days?: number;
}) =>
  api.put<CashFlowSummary>('/api/finance/cash-flow/settings', payload).then((r) => r.data);

// 工厂欠款对账回填 (消除虚高): 有流水号/付款日 或 关联订单已签收且超结算周期 → 标已付
export interface FactoryPaymentBackfillResult {
  scanned: number;
  by_evidence: number;
  by_settled: number;
  still_unpaid: number;
  settlement_days: number;
  dry_run: boolean;
}
export const backfillFactoryPayment = (params: {
  settlement_days?: number; apply_settled_inference?: boolean; dry_run?: boolean;
} = {}) =>
  api.post<FactoryPaymentBackfillResult>('/api/finance/factory-payment/backfill', null, { params })
    .then((r) => r.data);

// 反推活跃单理论成本 (让"工厂未开账单预估"不再缺成本)
export interface RecomputeCostResult { updated: number; skipped_no_bom: number; total: number; }
export const recomputeOrderCosts = (only_missing = true) =>
  api.post<RecomputeCostResult>('/api/orders/recompute-costs', null, { params: { only_missing } })
    .then((r) => r.data);

// 财务系数设置 (会计成本费率; 用户拍板 2026-06-17) — 全系统利润口径
export interface FinCoefficients {
  fin_platform_handling_rate: string;     // 平台手续费率 (0.006 = 0.6%)
  fin_platform_activity_rate: string;     // 平台活动抽成率 (0.02 = 2%)
  fin_platform_activity_since: string;    // 活动抽成生效日 YYYY-MM-DD
  fin_platform_activity_until: string;    // 活动抽成截止日 YYYY-MM-DD (只 5-6 月有)
  fin_tax_rate: string;                   // 税率 (0.02 = 2%)
  fin_outsourcing_monthly?: string;       // 人员外包预估 (元/月, 5月起无实际录入时用)
  fin_outsourcing_est_since?: string;     // 人员外包预估生效起始月 YYYY-MM-DD
}
export const getFinancialCoefficients = () =>
  api.get<FinCoefficients>('/api/finance/financial-coefficients').then((r) => r.data);
export const putFinancialCoefficients = (
  payload: Partial<FinCoefficients> & { password: string },
) => api.put('/api/finance/financial-coefficients', payload).then((r) => r.data);
