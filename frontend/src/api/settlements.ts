import { api } from './base';

export interface SettlementRow {
  id: number;
  source: string;
  pay_no: string;
  order_no: string | null;
  settle_time: string | null;
  entry_type: string | null;
  income: number;
  expense: number;
  description: string | null;
}

export interface SettlementSummary {
  count: number;
  orders: number;
  income: number;
  expense: number;
  net: number;
  by_source: Record<string, number>;
}

export interface ImportResult {
  inserted?: number;
  updated?: number;
  source?: string;
  error?: string;
}

export const importSettlementBill = (file: File, source = 'wechat') => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<ImportResult>(`/api/settlements/import?source=${source}`, fd)
    .then((r) => r.data);
};

export const fetchSettlementSummary = () =>
  api.get<SettlementSummary>('/api/settlements/summary').then((r) => r.data);

export const listSettlements = (limit = 100) =>
  api.get<SettlementRow[]>('/api/settlements', { params: { limit } }).then((r) => r.data);

// ---- 逐笔对账 (per-order reconciliation) ----
export interface ReconRow {
  order_no: string;
  order_date: string | null;
  shop: string | null;
  product_name: string | null;
  customer_name: string | null;
  is_custom: boolean;
  payable: number | null;       // 买家应付
  paid: number | null;          // 买家实付
  subsidy: number | null;       // 平台优惠券补贴 (应付-实付)
  received: number | null;      // 店铺实收
  tax: number | null;           // 补贴税 2%
  platform_fee: number | null;  // 软件服务费
  expected_net: number | null;  // 理论应到账
  arrived: number | null;       // 实际到账
  wechat_net: number | null;
  alipay_net: number | null;
  channels: string[];
  diff: number | null;          // 到账 - 理论
  status: 'matched' | 'diff' | 'pending';
  theoretical_cost: number | null;
  actual_cost: number | null;
  cost_diff: number | null;
  refund_amount: number | null;
}

export interface ReconSummary {
  orders: number;
  payable_sum: number;
  paid_sum: number;
  received_sum: number;
  tax_sum: number;
  platform_fee_sum: number;
  subsidy_sum: number;
  arrived_sum: number;
  matched: number;
  diff: number;
  pending: number;
  evidence_orders: number;
  wechat_orders: number;
  alipay_orders: number;
  coverage_pct: number;
  tax_rate: number;
}

export interface ReconListResult {
  total: number;
  rows: ReconRow[];
}

export const fetchReconSummary = () =>
  api.get<ReconSummary>('/api/settlements/reconciliation/summary').then((r) => r.data);

export const listReconciliation = (params: {
  limit?: number; offset?: number; status?: string; channel?: string; q?: string;
}) =>
  api.get<ReconListResult>('/api/settlements/reconciliation', { params }).then((r) => r.data);

// ---- 对账诊断 (reconciliation diagnostics) ----
export interface ReconDiagnostics {
  balance_check: {
    checked: number; unbalanced: number;
    rows: Array<{ account_name: string; period: string; opening: number; income: number; expense: number; closing: number; expected_closing: number; diff: number }>;
  };
  orphan_flows: {
    total_flows: number; orphan_count: number; orphan_income: number; orphan_expense: number;
    by_account: Record<string, number>;
    samples: Array<{ account: string; transaction_no: string; transaction_time: string | null; transaction_type: string | null; amount: number; counterparty: string | null; remark: string }>;
  };
  coverage: {
    accounts: Array<{ account: string; total: number; with_order: number; matched: number; unclassified: number; no_date: number; matched_pct: number }>;
  };
}

export const fetchReconDiagnostics = () =>
  api.get<ReconDiagnostics>('/api/settlements/reconciliation/diagnostics').then((r) => r.data);

// 导出所有"没对上"的支付宝流水(含流水号+原因)。直链会丢 Authorization 头 → 401, 故走带鉴权 axios 取 blob。
export async function downloadProblemFlows() {
  const resp = await api.get('/api/settlements/reconciliation/problem-flows.xlsx', { responseType: 'blob' });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '问题流水.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// ---- 代付台账 (prepay ledger: 补单佣金/补单快递/售后 实际打款) ----
export interface PrepayRow {
  id: number; category: string; pay_no: string | null; order_no: string | null;
  pay_date: string | null; amount: number; payee: string | null; remark: string | null;
}
export interface PrepaySummary {
  total: number; by_category: Record<string, { count: number; amount: number }>;
}
export interface PrepayImportResult {
  inserted: number; skipped_invalid: number; skipped_duplicate: number;
  unmapped_columns: string[]; errors: string[];
  archived_file_id?: number; duplicate_upload?: boolean;
}
export const importPrepay = (file: File, category: string) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post<PrepayImportResult>(`/api/settlements/prepay/import?category=${category}`, fd).then((r) => r.data);
};
export const fetchPrepaySummary = () =>
  api.get<PrepaySummary>('/api/settlements/prepay/summary').then((r) => r.data);
export const listPrepay = (category?: string) =>
  api.get<PrepayRow[]>('/api/settlements/prepay', { params: { category, limit: 500 } }).then((r) => r.data);

// ---- 对账/利润 口径配置 (容差+税费率, 全局+按店铺) ----
export interface ReconConfig {
  defaults: Record<string, number>;
  by_shop: Record<string, Record<string, number>>;
}
export const fetchReconConfig = () =>
  api.get<ReconConfig>('/api/settlements/recon-config').then((r) => r.data);
export const updateReconConfig = (
  defaults: Record<string, number> | null,
  by_shop: Record<string, Record<string, number>> | null,
) => api.put<ReconConfig>('/api/settlements/recon-config', { defaults, by_shop }).then((r) => r.data);

// ---- 到账覆盖缺口诊断 (按月该补哪批流水/账单) ----
export interface ReconGapMonth {
  period: string;
  orders: number;
  evidence: number;
  pending: number;
  pending_amount: number;
  wechat: number;
  alipay: number;
  coverage_pct: number;
}
export interface ReconGap {
  total_orders: number;
  evidence_orders: number;
  pending_orders: number;
  coverage_pct: number;
  pending_amount: number;
  months: ReconGapMonth[];
  worst_months: string[];
}
export const fetchReconGap = () =>
  api.get<ReconGap>('/api/settlements/reconciliation/gap').then((r) => r.data);

// Plan L1: 某月待补订单清单 + 缺什么证据 + 行动指引
export interface ReconGapDetail {
  period: string;
  pending_count: number;
  rows: Array<{
    order_no: string;
    order_date: string | null;
    shop: string | null;
    customer_name: string | null;
    product_name: string | null;
    expected_net: number | null;
    missing: string[];
  }>;
  actions: string[];
}
export const fetchReconGapDetail = (period: string) =>
  api.get<ReconGapDetail>(`/api/settlements/reconciliation/gap/${period}`).then((r) => r.data);
