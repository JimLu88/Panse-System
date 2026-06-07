import { api } from './base';

// 中央物流追踪 — 任意带快递单号的实体 (订单/售后补发/售后返厂/工厂单/补单/配件采购) 通用。
export type ShipmentEntityType =
  | 'order'
  | 'after_sales_refill'
  | 'after_sales_return'
  | 'factory_order'
  | 'refill_record'
  | 'part_purchase';

export interface ShipmentEvent {
  time: string | null;
  context: string;
}

export interface Shipment {
  id: number;
  entity_type: string;
  entity_id: number;
  tracking_no: string;
  carrier_name: string | null;
  provider: string | null;
  mapped_status: string | null; // 运输中 / 已到货
  last_status: string | null;
  is_signed: boolean;
  active: boolean;
  events: ShipmentEvent[] | null;
  queried_at: string | null;
  last_error: string | null;
}

export const listShipments = (entityType: ShipmentEntityType, entityId: number) =>
  api
    .get<Shipment[]>('/api/shipments', { params: { entity_type: entityType, entity_id: entityId } })
    .then((r) => r.data);

export const refreshEntityShipments = (entityType: ShipmentEntityType, entityId: number) =>
  api
    .post<Shipment[]>('/api/shipments/refresh', null, {
      params: { entity_type: entityType, entity_id: entityId },
    })
    .then((r) => r.data);

// 扫全表 ensure + 刷新所有在途 (管理/手动触发)
export const syncAllShipments = () =>
  api.post<{ checked: number; signed: number; errors: number; synced?: number; skipped?: string }>(
    '/api/shipments/sync',
  ).then((r) => r.data);
