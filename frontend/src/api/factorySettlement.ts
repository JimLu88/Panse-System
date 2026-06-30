import { api } from './base';

// ----- 木作工厂月结销账 (用户 2026-07-01) -----

export interface FsMonth {
  month: string;
  billed: string;
  paid: string;
  unpaid: string;
  order_count: number;
  paid_count: number;
  status: string; // paid / unpaid / partial
}

export interface FsBreakdown {
  supplier: string;
  months: FsMonth[];
  total_billed: string;
  total_paid: string;
  total_unpaid: string;
}

export interface FsPayment {
  id: number;
  supplier: string;
  settlement_month: string;
  trigger: string;
  alipay_flow_no: string | null;
  paid_amount: string | null;
  flipped_count: number;
  created_by: string | null;
  note: string | null;
  reversed_at: string | null;
  created_at: string | null;
}

export interface FsAlias {
  id: number;
  supplier: string;
  alias: string;
  note: string | null;
}

export interface FsOverview {
  breakdown: FsBreakdown;
  payments: FsPayment[];
  aliases: FsAlias[];
}

export const getFsOverview = (supplier?: string) =>
  api.get<FsOverview>('/api/factory-settlement/overview', {
    params: supplier ? { supplier } : {},
  }).then((r) => r.data);

export const fsSettle = (payload: {
  month: string; supplier?: string; paid_amount?: number | string; flow_no?: string; note?: string;
}) => api.post('/api/factory-settlement/settle', payload).then((r) => r.data);

export const fsReverse = (paymentId: number) =>
  api.post(`/api/factory-settlement/reverse/${paymentId}`).then((r) => r.data);

export const fsAddAlias = (payload: { alias: string; supplier?: string; note?: string }) =>
  api.post<FsAlias>('/api/factory-settlement/aliases', payload).then((r) => r.data);

export const fsDeleteAlias = (id: number) =>
  api.delete(`/api/factory-settlement/aliases/${id}`).then((r) => r.data);
