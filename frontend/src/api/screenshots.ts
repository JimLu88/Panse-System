import { api } from './base';

// ----- 截图自动化 (Phase 3, 业务需求 1/6) -----
export interface QianniuOrderParsed {
  order_no: string;
  platform?: string;
  order_date?: string;
  pay_time?: string;
  customer_name?: string;
  customer_phone?: string;
  customer_address?: string;
  product_name?: string;
  sku?: string;
  qty?: number;
  unit_price?: number;
  discount?: number;
  paid_amount?: number;
  platform_fee?: number;
  freight?: number;
  remark?: string;
  confidence?: number;
  warnings?: string[];
  product_code?: string | null;
  sku_code?: string | null;
  // 客户备注里识别的新增配件 (OCR 带出), 每项 {name, qty?, note?}
  extra_accessories?: { name: string; qty?: number; note?: string }[];
}

export interface QianniuParseResp {
  image_b64: string;
  mime: string;
  orders: QianniuOrderParsed[];
  ocr_warnings: string[];
}

export const parseQianniuScreenshot = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<QianniuParseResp>('/api/screenshots/qianniu-orders/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const commitQianniuOrders = (orders: QianniuOrderParsed[]) =>
  api
    .post<{ inserted: number; skipped_existing: string[]; conflicts?: string[] }>(
      '/api/screenshots/qianniu-orders/commit',
      { orders },  // 含 extra_accessories, 后端入库时自动生成配件清单
    )
    .then((r) => r.data);

// 千牛截图解析后, 对单条订单直接生成下单图 (无需先入库)
import type { FactorySheet } from './orders';
export const previewQianniuFactorySheet = (order: QianniuOrderParsed) =>
  api
    .post<FactorySheet>('/api/screenshots/qianniu-orders/factory-sheet', {
      order_no: order.order_no,
      order_date: order.order_date,
      product_code: order.product_code,
      product_name: order.product_name,
      sku: order.sku,
      sku_code: order.sku_code,
      qty: order.qty ?? 1,
      customer_name: order.customer_name,
      customer_phone: order.customer_phone,
      customer_address: order.customer_address,
      remark: order.remark,
      extra_accessories: order.extra_accessories,
    })
    .then((r) => r.data);

export interface PurchaseLineParsed {
  material_name?: string;
  material_code?: string;
  spec?: string;
  qty?: number;
  unit?: string;
  unit_price?: number;
  amount?: number;
}

export interface PurchaseParsed {
  supplier_name?: string;
  purchase_date?: string;
  purchase_no?: string;
  tracking_no?: string;
  carrier?: string;
  freight?: number;
  total_amount?: number;
  remark?: string;
  lines: PurchaseLineParsed[];
  warnings?: string[];
}

export interface PurchaseParseResp {
  image_b64: string;
  mime: string;
  purchase: PurchaseParsed;
}

export const parsePurchaseScreenshot = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<PurchaseParseResp>('/api/screenshots/purchase/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const commitPurchaseScreenshot = (payload: {
  supplier?: string;
  purchase_date?: string;
  purchase_no?: string;
  tracking_no?: string;
  carrier?: string;
  freight?: number;
  total_amount?: number;
  remark?: string;
  lines: PurchaseLineParsed[];
}) =>
  api
    .post<{ inserted: number; purchase_no: string; has_tracking: boolean }>(
      '/api/screenshots/purchase/commit',
      payload,
    )
    .then((r) => r.data);

// ----- 工厂对账单截图 (Task 3) -----
export interface FactoryReconRowParsed {
  factory_name: string;
  period_start?: string | null;
  period_end?: string | null;
  order_amount?: number | null;
  bill_amount?: number | null;
  paid_amount?: number | null;
  alipay_flow_no?: string | null;
  remark?: string | null;
  warnings?: string[];
}
export interface FactoryReconParseResp {
  image_b64: string;
  mime: string;
  rows: FactoryReconRowParsed[];
  ocr_warnings: string[];
}
export const parseFactoryReconScreenshot = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<FactoryReconParseResp>('/api/screenshots/factory-recon/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};
export interface FactoryReconExcelResp {
  rows: FactoryReconRowParsed[];
  warnings: string[];
}
export const parseFactoryReconExcel = (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<FactoryReconExcelResp>('/api/screenshots/factory-recon/parse-excel', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000,
    })
    .then((r) => r.data);
};
export const commitFactoryReconScreenshot = (rows: FactoryReconRowParsed[]) =>
  api
    .post<{ inserted: number; skipped: string[] }>(
      '/api/screenshots/factory-recon/commit',
      { rows },
    )
    .then((r) => r.data);

// ----- 打包费手写账单 (用户 2026-06-21, C) -----
export interface PackingRowParsed {
  row_date?: string | null;
  customer_name?: string | null;
  order_no?: string | null;
  product?: string | null;
  packing_fee?: number | null;
  excluded?: boolean;
  exclude_reason?: string | null;
  note?: string | null;
  confidence?: number | null;
  warnings?: string[];
}
export interface PackingParseResp {
  image_b64: string;
  mime: string;
  rows: PackingRowParsed[];
  declared_total?: number | null;
  ocr_warnings: string[];
}
export const parsePackingBill = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<PackingParseResp>('/api/screenshots/packing-bill/parse', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};
export interface PackingCommitResp {
  inserted: number;
  skipped: number;
  matched: number;
  excluded: number;
  rows_total: number;
  payable_total: number;
  excluded_total: number;
  excluded_rows: number;
  unmatched_rows: number;
}
export const commitPackingBill = (payload: {
  bill_month?: string;
  declared_total?: number;
  source_image?: string;
  rows: PackingRowParsed[];
}) =>
  api
    .post<PackingCommitResp>('/api/screenshots/packing-bill/commit', payload)
    .then((r) => r.data);

export interface PackingBillRow {
  id: number;
  bill_month?: string | null;
  row_date?: string | null;
  customer_name?: string | null;
  order_no?: string | null;
  matched_order_no?: string | null;
  match_method?: string | null;
  match_note?: string | null;
  product?: string | null;
  packing_fee?: number | null;
  excluded: boolean;
  exclude_reason?: string | null;
  confidence?: number | null;
  note?: string | null;
}
export const listPackingBills = (billMonth?: string) =>
  api
    .get<PackingBillRow[]>('/api/finance/packing-bills', { params: { bill_month: billMonth } })
    .then((r) => r.data);
export const packingSummary = (billMonth?: string) =>
  api
    .get<Omit<PackingCommitResp, 'inserted' | 'skipped' | 'matched' | 'excluded'>>(
      '/api/finance/packing-bills/summary', { params: { bill_month: billMonth } })
    .then((r) => r.data);
export const rematchPackingBills = (loose = true) =>
  api
    .post<{ matched: number; multi: number; none: number }>(
      '/api/finance/packing-bills/rematch', null, { params: { loose } })
    .then((r) => r.data);
