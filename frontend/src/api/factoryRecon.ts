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

export const listFactoryReconItems = (params: {
  period?: string; status?: string; q?: string; limit?: number; offset?: number;
}) => api.get<FactoryReconItemList>('/api/factory-recon/items', { params }).then((r) => r.data);

export const resolveFactoryReconItem = (id: number, reason: string, resolved = true) =>
  api.post(`/api/factory-recon/items/${id}/resolve`, { reason, resolved }).then((r) => r.data);
