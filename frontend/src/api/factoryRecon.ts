import { api } from './base';

// ---- 工厂逐单对账 (factory-side per-order reconciliation) ----
export interface FactoryReconImportResult {
  inserted: number;
  skipped_invalid: number;
  skipped_duplicate: number;
  backfilled_cost: number;
  sheets: Array<{ sheet: string; inserted: number }>;
  unmapped_columns: string[];
  errors: string[];
  archived_file_id?: number;
  duplicate_upload?: boolean;
}

export interface FactoryReconMonth {
  period: string;
  items_total: number;
  items_resolved: number;
  items_open: number;
  billed: number;   // 应付 (Σ结算价)
  paid: number;     // 实付 (factory_payment)
  diff: number;     // 实付 - 应付
  status: 'balanced' | 'explained' | 'diff';
}

export interface FactoryReconSummary {
  total_items: number;
  total_billed: number;
  total_paid: number;
  total_diff: number;
  resolved_items: number;
  months: FactoryReconMonth[];
}

export interface FactoryReconItem {
  id: number;
  source_sheet: string | null;
  doc_no: string | null;
  order_no: string | null;
  extra_order_no1: string | null;
  extra_order_no2: string | null;
  detail: string | null;
  qty: number;
  settle_price: number;
  customer_info: string | null;
  order_date: string | null;
  ship_date: string | null;
  remark: string | null;
  resolved: boolean;
  settle_reason: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface FactoryReconItemList {
  total: number;
  rows: FactoryReconItem[];
}

export const importFactoryRecon = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post<FactoryReconImportResult>('/api/factory-recon/import', fd).then((r) => r.data);
};

export const fetchFactoryReconSummary = () =>
  api.get<FactoryReconSummary>('/api/factory-recon/summary').then((r) => r.data);

// 工厂对账单未导入时的逐单预估 (我方下单数据, 应付=账单额/理论成本)
export interface FactoryReconPreviewRow {
  factory_order_no: string;
  platform_order_no: string | null;
  internal_order_no: string | null;
  factory_name: string | null;
  payable: number | null;
  payable_source: string;
  order_date: string | null;
}
export interface FactoryReconPreview {
  total: number; total_payable: number; note: string; rows: FactoryReconPreviewRow[];
}
export const fetchFactoryReconPreview = () =>
  api.get<FactoryReconPreview>('/api/factory-recon/preview-from-orders').then((r) => r.data);

export const listFactoryReconItems = (params: {
  period?: string; status?: string; q?: string; limit?: number; offset?: number;
}) => api.get<FactoryReconItemList>('/api/factory-recon/items', { params }).then((r) => r.data);

export const resolveFactoryReconItem = (id: number, reason: string, resolved = true) =>
  api.post(`/api/factory-recon/items/${id}/resolve`, { reason, resolved }).then((r) => r.data);

// Plan L5: 差异处置闭环 — 确认归因 / 拆分归因子行
export const RESOLUTION_KINDS = ['漏单', '价差', '运费', '补偿', '其他'] as const;

export const confirmFactoryReconItem = (id: number, resolutionKind: string) =>
  api.post(`/api/factory-recon/items/${id}/confirm`, { resolution_kind: resolutionKind })
    .then((r) => r.data);

export const splitFactoryReconItem = (
  id: number,
  parts: Array<{ amount: string; resolution_kind: string; remark?: string }>,
) => api.post(`/api/factory-recon/items/${id}/split`, { parts }).then((r) => r.data);

// 工厂实收(actual_cost) vs 我方预测(pricing factory_cost) 按月对比饼图
export interface FactoryCostCompareMonth {
  month: string;
  predicted: number;
  actual: number;
  diff: number;
  diff_pct: number;
  n_actual: number;
  n_total: number;
  coverage_pct: number;
}
export interface FactoryCostCompare {
  months: FactoryCostCompareMonth[];
  totals: { predicted: number; actual: number; diff: number; diff_pct: number };
}
export const fetchFactoryCostComparison = () =>
  api.get<FactoryCostCompare>('/api/factory-recon/cost-comparison').then((r) => r.data);
