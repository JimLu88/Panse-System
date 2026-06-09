import { api } from './base';

// ----- Orders -----
export interface Order {
  id: number;
  platform: string;
  order_no: string;
  is_refill: boolean;
  order_date: string | null;
  ship_date: string | null;
  customer_name: string | null;
  product_code: string | null;
  product_name: string | null;
  sku: string | null;
  is_custom: boolean;
  qty: number;
  status: string;
  carrier: string | null;
  tracking_no: string | null;
  paid_amount: string | null;
  theoretical_cost?: string | null;
  actual_cost?: string | null;
  actual_freight?: string | null;
  cost_diff?: string | null;
  tracking_confirmed?: boolean;
  manual_confirmed?: boolean;
  signoff_questioned?: boolean;
  kanban_confirmed?: boolean;
}

export const listOrders = (params: {
  q?: string;
  status?: string;
  platform?: string;
  limit?: number;
} = {}) => api.get<Order[]>('/api/orders', { params: { limit: 100, ...params } }).then((r) => r.data);

// confirmed=true: 看板人工拖拽 → 后端标记该单为"已确定"
export const changeOrderStatus = (id: number, status: string, force = false, confirmed = false) =>
  api.post<Order>(`/api/orders/${id}/status`, { status, force, confirmed }).then((r) => r.data);

// 看板配件配齐进度: { [order_id]: { total, done, pending } }
export interface AccessorySummary { total: number; done: number; pending: number }
export const fetchAccessorySummary = () =>
  api.get<Record<number, AccessorySummary>>('/api/orders/accessories/summary').then((r) => r.data);

// 按配件聚合采购视图: 每种配件全局还缺多少 + 涉及哪些订单
export interface ComponentItem {
  id: number; order_id: number; order_no: string; qty_required: string;
  status: string; purchase_no: string | null; tracking_no: string | null; self_delivered: boolean;
}
export interface ComponentGroup {
  material_code: string; material_name: string | null; unit: string | null;
  to_buy_qty: string; bought_pending_qty: string; order_count: number; items: ComponentItem[];
}
export const fetchAccessoriesByComponent = () =>
  api.get<ComponentGroup[]>('/api/orders/accessories/by-component').then((r) => r.data);

export const bulkUpdateAccessories = (payload: {
  item_ids: number[]; status?: string; purchase_no?: string; tracking_no?: string; self_delivered?: boolean;
}) => api.post<{ updated: number }>('/api/orders/accessories/bulk-update', payload).then((r) => r.data);

export interface CsvImportReport {
  inserted: number;
  skipped_duplicate: number;
  skipped_invalid: number;
  errors: string[];
}

export const importOrdersCsv = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<CsvImportReport>('/api/orders/import-csv', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
};

// ----- Producibility -----
export interface MaterialRequirement {
  material_code: string;
  material_name: string | null;
  qty_per_product: string;
  available_stock: string;
  can_build_units: number;
  shortage_for_target: string;
}

export interface ProducibilityResult {
  sku_code: string | null;
  product_code: string | null;
  target_qty: number;
  in_stock_qty: number;
  can_build_qty: number;
  total_available_qty: number;
  bottleneck: MaterialRequirement | null;
  requirements: MaterialRequirement[];
  missing_for_target: MaterialRequirement[];
}

export const computeProducibility = (params: {
  sku_code?: string;
  product_code?: string;
  target_qty?: number;
}) =>
  api
    .get<ProducibilityResult>('/api/producibility', { params })
    .then((r) => r.data);

// ----- Scanners (Phase 3.5) -----
export interface ScannerFinding {
  source_table: string;
  source_pk: string;
  exception_type: string;
  severity: string;
  description: string;
  suggestion_action: string;
  context: Record<string, unknown>;
}

export interface ScannerResult {
  scanner: string;
  findings: ScannerFinding[];
  written: number;
  skipped_duplicate: number;
}

export const listScanners = () =>
  api.get<string[]>('/api/scanners').then((r) => r.data);

export const runAllScanners = (dryRun = false) =>
  api
    .post<Record<string, ScannerResult>>('/api/scanners/run-all', null, {
      params: { dry_run: dryRun },
    })
    .then((r) => r.data);

export interface EscalationOut {
  exception_type: string;
  open_count: number;
  escalated_from: string;
  escalated_to: string;
  affected_ids: number[];
}

export const runEscalation = () =>
  api.post<EscalationOut[]>('/api/scanners/escalate').then((r) => r.data);

// ----- Factory Sheet (业务需求 §1) -----
export interface FactorySheetMaterial {
  material_code: string;
  material_name: string | null;
  qty_per_product: string;
  total_qty: string;
  unit: string | null;
  spec: string | null;
  source?: string;          // bom | 客户备注
  note?: string | null;     // 客户备注原文
}

export interface ExtraAccessory {
  name: string;
  qty?: number;
  note?: string;
}

// 订单配件清单行 (BOM 自动 + 客户备注新增, 含物流追踪)
export interface AccessoryItem {
  id: number;
  order_id: number;
  order_no: string;
  material_code: string;
  material_name: string | null;
  qty_required: string;
  unit: string | null;
  is_factory_provided: boolean;
  source: string;           // bom | 客户备注
  status: string;           // 未采购 | 已下单 | 运输中 | 已到货 | 工厂提供
  tracking_no: string | null;
  carrier_code: string | null;
  carrier_name: string | null;
  tracking_last_status: string | null;
  tracking_updated_at: string | null;
  tracking_events: { time: string | null; context: string }[] | null;
  alert_level: string | null;   // warn | critical
  alert_reason: string | null;
  remark: string | null;
}

export const listAccessories = (orderId: number) =>
  api.get<AccessoryItem[]>(`/api/orders/${orderId}/accessories`).then((r) => r.data);

export const regenerateAccessories = (orderId: number) =>
  api.post<AccessoryItem[]>(`/api/orders/${orderId}/accessories/regenerate`).then((r) => r.data);

export const updateAccessory = (
  itemId: number,
  patch: Partial<Pick<AccessoryItem, 'status' | 'tracking_no' | 'carrier_code' | 'carrier_name' | 'remark'>>,
) => api.patch<AccessoryItem>(`/api/orders/accessories/${itemId}`, patch).then((r) => r.data);

export const refreshAccessoryTracking = (itemId: number) =>
  api.post<AccessoryItem>(`/api/orders/accessories/${itemId}/refresh-tracking`).then((r) => r.data);

export const getAccessoriesPendingSummary = () =>
  api.get<{ orders: AccessoryPendingOrder[] }>('/api/orders/accessories/pending-summary').then((r) => r.data);

export interface AccessoryPendingOrder {
  order_id: number;
  order_no: string;
  ship_date: string | null;
  product_name: string | null;
  critical_count: number;
  warn_count: number;
  missing_tracking_count: number;
  pending_items: {
    id: number;
    material_code: string;
    material_name: string | null;
    status: string;
    alert_level: string | null;
    tracking_no: string | null;
  }[];
}

export interface FactorySheetWarning {
  code: string;
  message: string;
  severity: string;
}

export interface FactorySheet {
  order_no: string;
  sheet_title: string;
  order_date: string | null;
  ship_date: string | null;
  product_code: string | null;
  product_name: string | null;
  sku: string | null;
  sku_code: string | null;
  image_url: string | null;
  material_desc: string | null;
  dimension_desc: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  customer_address: string | null;
  qty: number;
  remark: string | null;
  materials: FactorySheetMaterial[];
  is_custom_variant: boolean;
  dimension_changes: Record<string, unknown> | null;
  warnings: FactorySheetWarning[];
}

export const getFactorySheet = (orderId: number) =>
  api.get<FactorySheet>(`/api/orders/${orderId}/factory-sheet`).then((r) => r.data);

// ----- Phase 8: 订单时间轴 -----
export interface OrderEvent {
  id: number;
  order_id: number;
  kind: string;
  actor: string | null;
  summary: string;
  detail: string | null;
  context_json: Record<string, any> | null;
  created_at: string;
}

export const fetchOrderTimeline = (orderId: number) =>
  api.get<OrderEvent[]>(`/api/orders/${orderId}/timeline`).then((r) => r.data);

export const postOrderComment = (orderId: number, text: string) =>
  api.post<OrderEvent>(`/api/orders/${orderId}/comments`, { text }).then((r) => r.data);

// ----- 工厂订单自动派生 (Phase 2) -----
export const generateFactoryOrder = (orderId: number) =>
  api.post<{
    factory_order_id: number;
    factory_order_no: string;
    locked_lines: any[];
    shortages: any[];
    alerts_created: number[];
  }>(`/api/orders/${orderId}/generate-factory-order`).then((r) => r.data);

export const createFutureOrder = (payload: {
  base_order_no: string;
  activate_at: string;
  product_code?: string;
  sku?: string;
  qty?: number;
  customer_name?: string;
  remark?: string;
}) => api.post<{ id: number; order_no: string; activate_at: string }>(
  '/api/orders/future', payload,
).then((r) => r.data);

export const voidFactoryOrder = (factoryOrderId: number, reason: string) =>
  api.post<{ id: number; factory_order_no: string; voided_at: string; voided_reason: string }>(
    `/api/orders/factory-orders/${factoryOrderId}/void`,
    { reason },
  ).then((r) => r.data);

// -- 订单双核对签收
export const confirmOrderTracking = (orderId: number) =>
  api.post(`/api/orders/${orderId}/confirm-tracking`).then(r => r.data);
export const confirmOrderManual = (orderId: number) =>
  api.post(`/api/orders/${orderId}/confirm-manual`).then(r => r.data);

// -- 订单理论成本反推 (按 BOM × 物料单价)
export interface OrderCostLine {
  material_code: string;
  material_name: string | null;
  qty_per_product: string;
  unit_price: string | null;
  line_cost: string | null;
  missing_price: boolean;
}
export interface OrderCostBreakdown {
  order_no: string;
  sku_code: string | null;
  qty: number;
  unit_cost: string;
  total_cost: string;
  resolved: boolean;
  missing_price_count: number;
  note: string | null;
  lines: OrderCostLine[];
}
export const getOrderCostBreakdown = (id: number) =>
  api.get<OrderCostBreakdown>(`/api/orders/${id}/cost-breakdown`).then(r => r.data);
export const recomputeOrderCost = (id: number) =>
  api.post<OrderCostBreakdown>(`/api/orders/${id}/recompute-cost`).then(r => r.data);
export const recomputeAllOrderCosts = (only_missing = true) =>
  api.post<{ updated: number; skipped_no_bom: number; total: number }>(
    `/api/orders/recompute-costs?only_missing=${only_missing}`,
  ).then(r => r.data);

// 规范化订单状态: 中文/遗留状态(等待买家付款/交易成功/confirmed…) → 枚举, 修看板推进+统计纳入
export const normalizeOrderStatuses = () =>
  api.post<{ scanned: number; fixed: number; by_map: Record<string, number> }>(
    '/api/orders/normalize-statuses',
  ).then(r => r.data);

// ----------------------------- 订单细节自动生成 ----------------------------- //
export interface GenerateOrderDetailsResult {
  orders_scanned: number;
  orders_matched: number;
  details_created: number;
  details_skipped: number;
  orders_no_bom: string[];
  orders_no_bom_count: number;
  orders_no_product: number;
}
export const generateOrderDetails = (orderNos?: string[], onlyMissing = true) =>
  api
    .post<GenerateOrderDetailsResult>('/api/orders/generate-order-details', {
      order_nos: orderNos ?? null,
      only_missing: onlyMissing,
    })
    .then((r) => r.data);

export const printShippingLabel = (orderId: number, carrier?: string) =>
  api.post<{ tracking_no: string; carrier: string; label_url: string }>(
    `/api/orders/${orderId}/print-label`, null, { params: { carrier } },
  ).then((r) => r.data);

export const backfillWarehouse = () =>
  api.post<{ updated: number; message: string }>('/api/orders/backfill-warehouse')
    .then((r) => r.data);

export const markCustomSku = () =>
  api.post<{ updated: number; message: string }>('/api/orders/mark-custom-sku')
    .then((r) => r.data);

export const rederiveRefillFlags = (recomputeCost = true) =>
  api.post<{ scanned: number; flagged: number; unflagged: number }>(
    '/api/orders/rederive-refill-flags',
    null,
    { params: { recompute_cost: recomputeCost } },
  ).then((r) => r.data);

export const backfillCompensation = () =>
  api.post<{ aftersales_scanned: number; orders_updated: number; total_compensation: string }>(
    '/api/orders/backfill-compensation',
  ).then((r) => r.data);
