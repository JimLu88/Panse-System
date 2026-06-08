import { api } from './base';
import { CsvImportReport } from './orders';

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
  generated_at: string;
}

export const getCashFlow = () =>
  api.get<CashFlowSummary>('/api/finance/cash-flow').then((r) => r.data);

export const updateCashFlowSettings = (payload: {
  shop_deposit?: string;
  total_investment?: string;
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
