/**
 * 月度对账中心 API (用户 2026-06-27, 方向三)。
 * 统一所有月结(配件/打包/运费)对账总览 + 一键导出全部月结账单。
 * 口径: 供应商应付(AP)核对, 不参与产品成本分摊。
 */
import { api } from './base';

export interface SettlementRow {
  period: string;
  estimate: number;
  actual: number | null;
  variance: number | null;
  variance_pct: number | null;
  order_count: number | null;
}

export interface SettlementGroup {
  key: string;
  label: string;
  rows: SettlementRow[];
  total_estimate: number;
  total_actual: number;
  total_variance: number;
  total_variance_pct: number | null;
}

export interface SettlementDomain {
  key: string;
  label: string;
  settle_hint: string;
  groups: SettlementGroup[];
}

export interface MonthlySettlementCenter {
  domains: SettlementDomain[];
  caliber: string;
  ship_date_basis: boolean;
}

export const fetchMonthlySettlementCenter = () =>
  api.get<MonthlySettlementCenter>('/api/monthly-settlement/center').then((r) => r.data);

// 通过带鉴权的 axios 实例取 blob 再触发下载 (window.open 直链会丢 Authorization 头 → 401)。
export async function downloadMonthlySettlementAll() {
  const resp = await api.get('/api/monthly-settlement/export', { responseType: 'blob' });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '月度对账_全部月结.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// ── 打包导清单(当月发货单 + 每单预估/实际打包费, 给打包供应商核对) ──────────────
export interface PackingChecklistOrder {
  order_no: string;
  order_date: string | null;
  ship_date: string | null;
  customer_name: string | null;
  product_name: string | null;
  sku: string | null;
  est_packing: number;
  actual_packing: number | null;
}

export interface PackingChecklist {
  year_month: string;
  order_count: number;
  total_est_packing: number;
  total_actual_packing: number;
  orders: PackingChecklistOrder[];
}

export const fetchPackingChecklist = (yearMonth: string) =>
  api
    .get<PackingChecklist>('/api/monthly-settlement/packing-checklist',
      { params: { year_month: yearMonth } })
    .then((r) => r.data);

export async function downloadPackingChecklistXlsx(yearMonth: string) {
  const resp = await api.get('/api/monthly-settlement/packing-checklist.xlsx', {
    params: { year_month: yearMonth }, responseType: 'blob',
  });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `打包对账清单_${yearMonth}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
