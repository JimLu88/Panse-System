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
