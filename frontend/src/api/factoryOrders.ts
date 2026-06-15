// 供应链「工厂下单表」API (2026-06-15)
import { api } from './base';

export interface FactoryOrderRow {
  id: number;
  factory_order_no: string;
  platform_order_no: string | null;
  factory_name: string | null;
  order_date: string | null;
  product_name: string | null;
  sku: string | null;
  qty: number;
  expected_amount: number | null;   // 推算成本(应付)
  factory_bill_amount: number | null; // 工厂实际(账单)
  diff: number | null;               // 差异 = 推算 − 实际
  payment_status: string;
  payment_date: string | null;
  alipay_flow_no: string | null;
  reconciled: boolean;
  remark: string | null;
}

export interface FactoryOrderSummary {
  count: number;
  expected_sum: number;
  actual_sum: number;
  diff_sum: number;
  reconciled: number;
  reconciled_pct: number;
  paid_count: number;
  unpaid_count: number;
  paid_sum: number;
  unpaid_sum: number;
}

export interface FactoryOrderList {
  rows: FactoryOrderRow[];
  summary: FactoryOrderSummary;
  factories: string[];
}

export interface FactoryOrderAccessory {
  material_code: string | null;
  material_name: string | null;
  qty_per_product: number;
}

export const listFactoryOrders = (params?: {
  factory?: string;
  payment_status?: string;
  only_unreconciled?: boolean;
  only_diff?: boolean;
  month?: string;
}) => api.get<FactoryOrderList>('/api/factory-orders', { params }).then((r) => r.data);

export const factoryOrderAccessories = (no: string) =>
  api
    .get<{ factory_order_no: string; sku_code: string | null; accessories: FactoryOrderAccessory[] }>(
      `/api/factory-orders/${encodeURIComponent(no)}/accessories`,
    )
    .then((r) => r.data);

export const reconcileFactoryOrder = (
  no: string,
  payload: {
    factory_bill_amount?: number;
    payment_status?: string;
    payment_date?: string;
    alipay_flow_no?: string;
    remark?: string;
  },
) => api.post<FactoryOrderRow>(`/api/factory-orders/${encodeURIComponent(no)}/reconcile`, payload).then((r) => r.data);
