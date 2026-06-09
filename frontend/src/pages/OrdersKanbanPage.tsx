/**
 * 订单看板 — 拖拽换列 (@dnd-kit, 桌面拖/手机长按拖), 人工拖拽即「已确定」, 配件配齐徽标。
 *
 * 拖卡片到目标列 → changeOrderStatus(confirmed=true): 改状态 + 标记 kanban_confirmed。
 * 不再用「→下一档」按钮; 去掉了「快递/人工」双核对按钮(查快递在「配件」抽屉里)。
 */
import { useState, type ReactNode } from 'react';
import { Alert, Button, Card, Col, Empty, Row, Space, Tag, Tooltip, Typography, message } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  DndContext, DragOverlay, MouseSensor, TouchSensor, useSensor, useSensors,
  useDraggable, useDroppable, closestCorners, pointerWithin, MeasuringStrategy,
  type CollisionDetection, type DragEndEvent, type DragStartEvent,
} from '@dnd-kit/core';

// 用 DragOverlay 时, 原卡片不动 → 要按"指针所在的列"判落点(pointerWithin); 拖到列间空隙
// 命中不到时退回 closestCorners, 保证总能落到最近的列。
const collisionStrategy: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length > 0 ? hits : closestCorners(args);
};
import { changeOrderStatus, fetchAccessorySummary, listOrders } from '../api/client';
import type { AccessorySummary, Order } from '../api/client';
import OrderTimelineDrawer from '../components/OrderTimelineDrawer';
import AccessoryChecklistDrawer from '../components/AccessoryChecklistDrawer';

const COLUMNS: { key: string; label: string; color: string }[] = [
  { key: 'pending_payment', label: '待付款', color: 'default' },
  { key: 'paid', label: '已付款', color: 'cyan' },
  { key: 'shipped', label: '已发货', color: 'blue' },
  { key: 'aftersales', label: '售后中', color: 'orange' },
];
const COL_CAP = 25;

// 导入订单状态多为中文, 归一到看板列
function normStatus(raw: string): string {
  const s = raw || '';
  if (['pending_payment', 'paid', 'shipped', 'signed', 'aftersales', 'cancelled'].includes(s)) return s;
  if (/售后|退款|退货/.test(s)) return 'aftersales';
  if (/关闭|取消/.test(s)) return 'cancelled';
  if (/等待买家付款|待付款/.test(s)) return 'pending_payment';
  if (/成功|已签收|已收货|已完成/.test(s)) return 'signed';
  if (/已发货|等待买家确认|待收货|运输/.test(s)) return 'shipped';
  if (/已付款|等待卖家发货|待发货/.test(s)) return 'paid';
  return 'other';
}

function AccessoryTag({ acc }: { acc?: AccessorySummary }) {
  if (!acc || acc.total === 0) return <Tag style={{ marginInlineEnd: 0 }}>配件未建</Tag>;
  if (acc.pending === 0) return <Tag color="green" style={{ marginInlineEnd: 0 }}>齐 {acc.done}/{acc.total}</Tag>;
  return <Tag color="red" style={{ marginInlineEnd: 0 }}>缺 {acc.pending}/{acc.total}</Tag>;
}

function DraggableCard({
  o, acc, onTimeline, onAccessory,
}: {
  o: Order; acc?: AccessorySummary;
  onTimeline: (id: number) => void; onAccessory: (o: Order) => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: o.id });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      style={{ marginBottom: 6, cursor: 'grab', opacity: isDragging ? 0.4 : 1 }}
    >
      <Card
        size="small"
        styles={{ body: { padding: 8 } }}
        style={{ borderColor: o.kanban_confirmed ? '#52c41a' : (o.signoff_questioned ? '#faad14' : undefined) }}
      >
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Space size={4} style={{ width: '100%', justifyContent: 'space-between' }}>
            <strong style={{ fontSize: 12 }}>{o.order_no}</strong>
            {o.kanban_confirmed && <Tag color="success" style={{ marginInlineEnd: 0 }}>已确定</Tag>}
          </Space>
          <Typography.Text style={{ fontSize: 11 }} type="secondary">
            {o.customer_name || '-'} · {o.product_name || '-'} ×{o.qty}
          </Typography.Text>
          <Typography.Text style={{ fontSize: 11 }}>¥{o.paid_amount ?? '0'}</Typography.Text>
          {o.signoff_questioned && (
            <Tag color="warning" icon={<QuestionCircleOutlined />} style={{ fontSize: 11 }}>签收有疑问</Tag>
          )}
          {/* 操作区: 阻止 mousedown/touchstart 冒泡, 点按钮不会触发拖拽 */}
          <div onMouseDown={(e) => e.stopPropagation()} onTouchStart={(e) => e.stopPropagation()}>
            <Space size={4} wrap>
              <Button size="small" onClick={() => onTimeline(o.id)}>时间线</Button>
              <Tooltip title="BOM 配件采购清单: 缺哪些/已到货, 点开可补全/改状态">
                <Button size="small" onClick={() => onAccessory(o)}>配件</Button>
              </Tooltip>
              <AccessoryTag acc={acc} />
            </Space>
          </div>
        </Space>
      </Card>
    </div>
  );
}

function DroppableColumn({
  col, count, children,
}: { col: { key: string; label: string; color: string }; count: number; children: ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: col.key });
  return (
    <Card
      size="small"
      title={
        <Space>
          <Tag color={col.color}>{col.label}</Tag>
          <Typography.Text type="secondary">{count}</Typography.Text>
        </Space>
      }
      styles={{ body: { minHeight: 480, padding: 8, background: isOver ? '#e6f4ff' : undefined, transition: 'background .15s' } }}
    >
      <div ref={setNodeRef} style={{ minHeight: 464 }}>{children}</div>
    </Card>
  );
}

export default function OrdersKanbanPage() {
  const qc = useQueryClient();
  const [timelineFor, setTimelineFor] = useState<number | null>(null);
  const [accessoryFor, setAccessoryFor] = useState<{ id: number; order_no: string } | null>(null);
  const [expandedCols, setExpandedCols] = useState<Record<string, boolean>>({});
  const [activeId, setActiveId] = useState<number | null>(null);

  const { data: orders = [], isLoading } = useQuery({
    queryKey: ['orders-kanban'],
    queryFn: () => listOrders({ limit: 500 }),
    refetchInterval: 30000,
  });
  const { data: accSummary = {} } = useQuery({
    queryKey: ['orders-kanban-acc'],
    queryFn: fetchAccessorySummary,
    refetchInterval: 60000,
  });

  const transMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      changeOrderStatus(id, status, false, true),   // confirmed=true → 标记已确定
    onSuccess: () => {
      message.success('已确定并更新状态');
      qc.invalidateQueries({ queryKey: ['orders-kanban'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '失败'),
  });

  // 桌面: 移动 6px 起拖(点击不误触); 手机: 长按 200ms 起拖(点击/滚动不误触)
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 6 } }),
  );

  if (isLoading) return <Card loading />;

  const grouped: Record<string, Order[]> = {};
  COLUMNS.forEach((c) => { grouped[c.key] = []; });
  const hidden = { signed: 0, cancelled: 0, other: 0 };
  (orders || []).forEach((o) => {
    const k = normStatus(o.status);
    if (grouped[k]) grouped[k].push(o);
    else if (k === 'signed') hidden.signed += 1;
    else if (k === 'cancelled') hidden.cancelled += 1;
    else hidden.other += 1;
  });
  Object.values(grouped).forEach((list) =>
    list.sort((a, b) => String(b.order_date || '').localeCompare(String(a.order_date || ''))));

  const activeOrder = activeId != null ? orders.find((o) => o.id === activeId) : null;

  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null);
    const over = e.over?.id as string | undefined;
    if (!over) return;
    const o = orders.find((x) => x.id === e.active.id);
    if (!o || normStatus(o.status) === over) return;   // 没移动 / 同列 → 不动
    transMut.mutate({ id: o.id, status: over });
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={collisionStrategy}
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      onDragStart={(e: DragStartEvent) => setActiveId(e.active.id as number)}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Alert
          type="info" showIcon
          message="订单看板: 拖动卡片到目标档即更新状态(并标记「已确定」)。只显示进行中的订单。"
          description={`[时间线]看进度 · [配件]看BOM采购清单(缺多少/已到货)。手机端长按卡片再拖。已完结不展示: 已签收 ${hidden.signed} 单、已关闭 ${hidden.cancelled} 单${hidden.other ? `、其他 ${hidden.other} 单` : ''}。`}
        />
        <Row gutter={12}>
          {COLUMNS.map((col) => {
            const list = grouped[col.key];
            const shown = expandedCols[col.key] ? list : list.slice(0, COL_CAP);
            return (
              <Col key={col.key} span={Math.floor(24 / COLUMNS.length)}>
                <DroppableColumn col={col} count={list.length}>
                  {list.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ color: '#bbb' }}>无</span>} />
                  ) : (
                    <>
                      {shown.map((o) => (
                        <DraggableCard
                          key={o.id} o={o} acc={accSummary[o.id]}
                          onTimeline={setTimelineFor}
                          onAccessory={(ord) => setAccessoryFor({ id: ord.id, order_no: ord.order_no })}
                        />
                      ))}
                      {list.length > COL_CAP && (
                        <Button type="dashed" size="small" block style={{ marginTop: 4 }}
                                onClick={() => setExpandedCols((p) => ({ ...p, [col.key]: !p[col.key] }))}>
                          {expandedCols[col.key] ? '收起' : `还有 ${list.length - COL_CAP} 单 · 展开`}
                        </Button>
                      )}
                    </>
                  )}
                </DroppableColumn>
              </Col>
            );
          })}
        </Row>
      </Space>

      <DragOverlay>
        {activeOrder ? (
          <Card size="small" styles={{ body: { padding: 8 } }}
                style={{ width: 240, boxShadow: '0 6px 20px rgba(0,0,0,.18)' }}>
            <strong style={{ fontSize: 12 }}>{activeOrder.order_no}</strong>
            <div style={{ fontSize: 11, color: '#888' }}>{activeOrder.product_name || '-'} ×{activeOrder.qty}</div>
          </Card>
        ) : null}
      </DragOverlay>

      <OrderTimelineDrawer orderId={timelineFor} open={timelineFor !== null} onClose={() => setTimelineFor(null)} />
      <AccessoryChecklistDrawer
        orderId={accessoryFor?.id ?? null}
        orderNo={accessoryFor?.order_no}
        open={accessoryFor !== null}
        onClose={() => setAccessoryFor(null)}
      />
    </DndContext>
  );
}
