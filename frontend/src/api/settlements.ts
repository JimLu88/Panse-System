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
