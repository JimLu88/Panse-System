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
  internal_product_name?: string | null;   // 内部产品名 (产品总表回填)
  sku: string | null;
  is_custom: boolean;
  qty: number;
  status: string;
  display_status?: string | null;        // 派生展示状态: 有未完成售后→aftersales(看板/筛选用)
  has_active_aftersales?: boolean;        // 是否有未完成售后记录
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
  product?: string;       // 产品名称/编码 (含内部产品名反查)
  date_from?: string;     // 下单日期起 YYYY-MM-DD
  date_to?: string;       // 下单日期止
  status?: string;
  platform?: string;
  limit?: number;
} = {}) => api.get<Order[]>('/api/orders', { params: { limit: 100, ...params } }).then((r) => r.data);

// confirmed=true: 看板人工拖拽 → 后端标记该单为"已确定"
// opts (Plan F2): 取消带在制工厂单的订单时必须带 disposition (future|release)
export const changeOrderStatus = (
  id: number, status: string, force = false, confirmed = false,
  opts?: { disposition?: 'future' | 'release'; plannedShipDate?: string },
) =>
  api.post<Order>(`/api/orders/${id}/status`, {
    status, force, confirmed,
    disposition: opts?.disposition,
    planned_ship_date: opts?.plannedShipDate,
  }).then((r) => r.data);

// 看板配件配齐进度: { [order_id]: { total, done, pending } }
export interface AccessorySummary { total: number; done: number; pending: number }
export const fetchAccessorySummary = () =>
  api.get<Record<number, AccessorySummary>>('/api/orders/accessories/summary').then((r) => r.data);

// 按配件聚合采购视图: 每种配件全局还缺多少 + 涉及哪些订单
export interface ComponentItem {
  id: number; order_id: number; order_no: string; qty_required: string;
  status: string; purchase_no: string | null; tracking_no: string | null; self_delivered: boolean;
  product_name?: string | null; customer_name?: string | null; customer_address?: string | null;
  order_date?: string | null; ship_deadline?: string | null;
  sku?: string | null; sku_code?: string | null; size?: string | null;   // SKU名含尺寸; 展开看每单尺寸
}
export interface ComponentGroup {
  material_code: string; material_name: string | null; unit: string | null;
  to_buy_qty: string; bought_pending_qty: string; order_count: number; items: ComponentItem[];
}
export const fetchAccessoriesByComponent = (product?: string) =>
  api.get<ComponentGroup[]>('/api/orders/accessories/by-component',
    { params: product ? { product } : undefined }).then((r) => r.data);

export const bulkUpdateAccessories = (payload: {
  item_ids: number[]; status?: string; purchase_no?: string; tracking_no?: string; self_delivered?: boolean;
}) => api.post<{ updated: number }>('/api/orders/accessories/bulk-update', payload).then((r) => r.data);

// 一次性给所有进行中的订单补全配件清单(历史单批量)
export const backfillAllAccessories = () =>
  api.post<{ orders_processed: number }>('/api/orders/accessories/backfill-all').then((r) => r.data);

// 一键配齐: 把某单所有未到货配件置已到货(清缺料报警)
export const markAllAccessoriesArrived = (orderId: number) =>
  api.post(`/api/orders/${orderId}/accessories/mark-all-arrived`).then((r) => r.data);

// ----- 工厂制作单视图 (已付款待发货 = 在工厂制作中) -----
export interface FactoryCard {
  id: number;
  order_no: string;
  order_date: string | null;
  ship_deadline: string | null;        // 手动发货截止(覆盖默认)
  original_deadline: string | null;    // 客户延期前的截止(手动截止或下单+30天)
  is_customer_delayed: boolean;        // 客户主动要求顺延交期, 不等同远期挂起
  customer_delay_deadline: string | null; // 客户确认的新截止日期
  effective_deadline: string | null;   // 生效截止(手动优先, 否则下单+30天)
  days_left: number | null;            // 距截止剩余天数(负=超期)
  customer_name: string | null;
  customer_phone: string | null;
  customer_address: string | null;
  product_name: string | null;
  sku: string | null;
  sku_code: string | null;
  qty: number;
  category: string | null;
  remark: string | null;               // 客户/订单备注
  production_note: string | null;      // 工厂制作单卡片备注(红色醒目)
  is_custom: boolean;
  is_remote_ship: boolean;             // 远期单(等客户通知再发)
  status: 'remote' | 'overdue' | 'critical' | 'urgent' | 'normal';  // 紧急度分类
  factory_no: number | null;           // 工厂号(畔色N单); 只有正式开始制作的单才有
  remote_seq: number | null;           // 远期单内部序号(远期单不占工厂号)
  order_label: string;                 // 工厂下单号显示: "畔色N单" / "远期单N" / ""(未排)
  accessory: AccessorySummary | null;  // 配件配齐进度 {total,done,pending}; null=未生成配件
}
export const fetchFactoryProduction = (product?: string) =>
  api.get<FactoryCard[]>('/api/orders/factory-production',
    { params: product ? { product } : undefined }).then((r) => r.data);

export interface FactoryDispatchSettings {
  auto_enabled: boolean;
  include_images: boolean;
  direction: 'out';
  direction_label: string;
  app_token: string;
  table_id: string;
}

export interface FactoryDispatchSummary {
  rows: number;
  urgency_counts: Record<string, number>;
  group_counts: Record<string, number>;
  custom_count: number;
  photo_notice_count: number;
  settings: FactoryDispatchSettings;
}

export interface FactoryDispatchSyncResult {
  ok: boolean;
  rows: number;
  created: number;
  updated: number;
  direction: 'out';
  include_images: boolean;
  errors: string[];
}

export const getFactoryDispatchSettings = () =>
  api.get<FactoryDispatchSettings>('/api/orders/factory-dispatch/settings').then((r) => r.data);

export const updateFactoryDispatchSettings = (payload: {
  auto_enabled?: boolean;
  include_images?: boolean;
}) =>
  api.put<FactoryDispatchSettings>('/api/orders/factory-dispatch/settings', payload).then((r) => r.data);

export const getFactoryDispatchSummary = () =>
  api.get<FactoryDispatchSummary>('/api/orders/factory-dispatch/summary').then((r) => r.data);

export const syncFactoryDispatch = () =>
  api.post<FactoryDispatchSyncResult>(
    '/api/orders/factory-dispatch/sync',
    null,
    { timeout: 180000 },
  ).then((r) => r.data);

export const downloadFactoryDispatchWorkbook = (includeImages = true) =>
  api.get<Blob>(
    '/api/orders/factory-dispatch/export.xlsx',
    { params: { include_images: includeImages }, responseType: 'blob', timeout: 180000 },
  ).then((r) => r.data);

export const updateOrderProduction = (
  id: number,
  patch: {
    ship_deadline?: string | null;
    production_note?: string | null;
    is_remote_ship?: boolean;
    is_customer_delayed?: boolean;
    customer_delay_deadline?: string | null;
  },
) => api.patch(`/api/orders/${id}/production`, patch).then((r) => r.data);

// 工厂生产看板「重推给工厂」: 删旧下单图 → 按最新数据重生成 → 推工厂群
export const repushFactory = (id: number) =>
  api.post<{ ok: boolean; pushed: number; failed: number; factory_no: number | null; order_label: string }>(
    `/api/orders/${id}/repush-factory`).then((r) => r.data);

export interface InspectionImage {
  id: number;
  order_id: number | null;
  order_no: string | null;
  factory_no: number | null;
  factory_label: string | null;
  product_code: string | null;
  product_name: string | null;
  sku_code: string | null;
  sku: string | null;
  captured_on: string | null;
  uploaded_by: string | null;
  source: string | null;
  original_filename: string | null;
  created_at: string | null;
}

export const fetchInspectionGallery = (params: {
  date_from?: string;
  date_to?: string;
  product?: string;
  order_no?: string;
  factory_no?: number;
} = {}) => api.get<InspectionImage[]>('/api/orders/inspection-gallery', { params }).then((r) => r.data);

export const inspectionImageUrl = (fileId: number) =>
  `/api/orders/inspection-gallery/${fileId}/file`;

export const uploadInspectionImages = (
  orderId: number,
  files: File[],
  capturedOn?: string,
) => {
  const body = new FormData();
  files.forEach((file) => body.append('files', file));
  if (capturedOn) body.append('captured_on', capturedOn);
  return api.post<{ ok: boolean; uploaded: number }>(
    `/api/orders/inspection-gallery/upload/${orderId}`,
    body,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 },
  ).then((r) => r.data);
};

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

// ── 定制单核对 (推演成本; 工厂成本填入后覆盖) ──────────────────────────────────
export interface CustomReconcileRow {
  order_id: number;
  order_no: string;
  product_name: string | null;
  product_code: string | null;
  sku: string | null;
  qty: number;
  status: string;
  paid_amount: number;
  remark: string;
  actual_cost: number | null;
  projected_cost: number | null;
  method: string;
  detail: string;
  source: string;
  confidence: 'high' | 'mid' | 'low';
  is_final: boolean;
  projected_margin: number | null;
  needs_review: boolean;   // 低置信(85%兜底) → 标红待人工
}
export interface CustomReconcileResp {
  rows: CustomReconcileRow[];
  count: number;
  low_confidence_count: number;
  ai_count: number;            // 已是 AI 估算(写回)的单数
  socket_material_code: string;
  fallback_rate: number;
}

export const fetchCustomReconcile = (onlyMissing = true) =>
  api.get<CustomReconcileResp>('/api/orders/custom-reconcile', {
    params: { only_missing: onlyMissing },
  }).then((r) => r.data);

export const applyProjectedCost = (orderId: number) =>
  api.post<{ ok: boolean; order_no: string; written_theoretical_cost: number; method: string }>(
    `/api/orders/${orderId}/apply-projected-cost`,
  ).then((r) => r.data);

// 一键 AI 重算兜底(后台): 把 85% 兜底的定制单用本地 AI 重估并写回 theoretical_cost (规则算出的不动)
export const aiRecomputeCustom = () =>
  api.post<{ started: boolean; note: string }>(
    '/api/orders/custom-reconcile/ai-recompute',
  ).then((r) => r.data);

export const getReconApiUrl = () =>
  api.get<{ url: string }>('/api/orders/custom-reconcile/external-api').then((r) => r.data);

export const putReconApiUrl = (url: string) =>
  api.put<{ url: string }>('/api/orders/custom-reconcile/external-api', { url }).then((r) => r.data);
