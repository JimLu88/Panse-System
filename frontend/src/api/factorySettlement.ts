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

export const fsScanAlipay = () =>
  api.post('/api/factory-settlement/scan-alipay').then((r) => r.data);

export interface FsMissingOrder {
  order_no: string;
  product_name: string | null;
  sku: string | null;
  qty: number;
  ship_date: string | null;
  ship_month: string;
  order_date: string | null;
  paid_amount: string;
  customer_name: string | null;
}

export interface FsMissing {
  supplier: string;
  up_to_month: string | null;
  count: number;
  total_paid: string;
  orders: FsMissingOrder[];
}

export const getFsMissing = (upToMonth?: string) =>
  api.get<FsMissing>('/api/factory-settlement/missing-orders', {
    params: upToMonth ? { up_to_month: upToMonth } : {},
  }).then((r) => r.data);

export const downloadFsMissing = async (upToMonth?: string) => {
  const res = await api.get('/api/factory-settlement/missing-orders.xlsx', {
    params: upToMonth ? { up_to_month: upToMonth } : {},
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `工厂漏单_${upToMonth || 'all'}.xlsx`;
  a.click();
  window.URL.revokeObjectURL(url);
};

// 月结明细导出: 月度汇总 + 逐单明细(每张工厂账单金额 + 已付金额), 看清应付/已付怎么来的
export const downloadFsDetail = async (supplier?: string) => {
  const res = await api.get('/api/factory-settlement/detail.xlsx', {
    params: supplier ? { supplier } : {},
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '工厂月结明细.xlsx';
  a.click();
  window.URL.revokeObjectURL(url);
};
