/**
 * 订单看板 — 顶部切「订单视图 / 配件视图」。
 *
 * 订单视图: 拖拽换列 (@dnd-kit, 桌面拖/手机长按拖), 人工拖拽即「已确定」(仅本次会话提示, 不持久化), 配件配齐徽标。
 *   拖卡片到目标列 → changeOrderStatus(confirmed=true): 改状态(允许任意方向/回拖纠错) + 安静迁移不刷异常。
 * 配件视图: 按配件汇总的全局采购清单 (原「配件备料」并入此处, 不再单开页面)。
 */
import { useState, type ReactNode } from 'react';
import { Alert, Button, Card, Col, Dropdown, Empty, Grid, Input, Row, Segmented, Space, Tag, Tooltip, Typography, message } from 'antd';
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
import AccessoryChecklistDrawer from '../components/AccessoryChecklistDrawer';
import DispositionModal, { type DispositionRequest } from '../components/DispositionModal';
import FactoryProductionView from '../components/FactoryProductionView';
import AccessoryPurchasePage from './AccessoryPurchasePage';

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
  if (acc.pending === 0) return <Tag color="green" style={{ marginInlineEnd: 0 }}>配件齐</Tag>;
  return <Tag color="red" style={{ marginInlineEnd: 0 }}>缺 {acc.pending} 项</Tag>;
}

function DraggableCard({
  o, acc, showAccessory, confirmed, onAccessory,
}: {
  o: Order; acc?: AccessorySummary; showAccessory: boolean; confirmed: boolean;
  onAccessory: (o: Order) => void;
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
        style={{ borderColor: confirmed ? '#52c41a' : (o.signoff_questioned ? '#faad14' : undefined) }}
      >
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Space size={4} style={{ width: '100%', justifyContent: 'space-between' }}>
            <strong style={{ fontSize: 12 }}>{o.order_no}</strong>
            {confirmed && <Tag color="success" style={{ marginInlineEnd: 0 }}>已确定</Tag>}
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
              <Tooltip title="BOM 配件采购清单: 缺哪些 / 已到货, 点开可补全 / 改状态">
                <Button size="small" onClick={() => onAccessory(o)}>配件</Button>
              </Tooltip>
              {showAccessory && <AccessoryTag acc={acc} />}
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

function OrdersBoard() {
  const qc = useQueryClient();
  const [accessoryFor, setAccessoryFor] = useState<{ id: number; order_no: string } | null>(null);
  const [expandedCols, setExpandedCols] = useState<Record<string, boolean>>({});
  const [activeId, setActiveId] = useState<number | null>(null);
  const [q, setQ] = useState('');   // 按 订单号 / 产品名 / 客户名 过滤(用户要求: 订单视图加搜索)
  // 「已确定」只在本次会话内提示(拖完即时反馈), 不读后端 kanban_confirmed → 下次登录不再显示。
  const [justConfirmed, setJustConfirmed] = useState<Set<number>>(new Set());
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;   // <768px: 4列看板会裂成一字一行 → 改「状态分段 + 整宽卡片竖列」
  const [mobStatus, setMobStatus] = useState('paid');   // 手机端当前查看的状态列(默认已付款)

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

  const [dispReq, setDispReq] = useState<DispositionRequest | null>(null);
  const transMut = useMutation({
    mutationFn: ({ id, status, opts }: { id: number; status: string;
                  opts?: { disposition?: 'future' | 'release'; plannedShipDate?: string } }) =>
      changeOrderStatus(id, status, false, true, opts),   // confirmed=true → 允许任意方向 + 安静迁移
    onSuccess: (_data, vars) => {
      setDispReq(null);
      setJustConfirmed((prev) => new Set(prev).add(vars.id));   // 本次会话内显示「已确定」
      message.success('已确定并更新状态');
      qc.invalidateQueries({ queryKey: ['orders-kanban'] });
    },
    onError: (e: any, vars) => {
      const detail = e?.response?.data?.detail;
      // Plan F2: 取消带在制工厂单 → 强制二选一弹窗
      if (e?.response?.status === 422 && detail?.need_disposition) {
        setDispReq({ orderId: vars.id, status: vars.status, factoryOrders: detail.factory_orders || [] });
        return;
      }
      message.error(typeof detail === 'string' ? detail : '失败');
    },
  });

  // 桌面: 移动 6px 起拖(点击不误触); 手机: 长按 200ms 起拖(点击/滚动不误触)
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 6 } }),
  );

  if (isLoading) return <Card loading />;

  const kw = q.trim().toLowerCase();
  const visible = kw
    ? (orders || []).filter((o) =>
        [o.order_no, o.product_name, o.customer_name]
          .some((v) => String(v || '').toLowerCase().includes(kw)))
    : (orders || []);

  const grouped: Record<string, Order[]> = {};
  COLUMNS.forEach((c) => { grouped[c.key] = []; });
  const hidden = { signed: 0, cancelled: 0, other: 0 };
  visible.forEach((o) => {
    // 有未完成售后 → 归"售后中"列 (派生, 不依赖底层 status; 与订单视图口径一致)
    const k = o.has_active_aftersales ? 'aftersales' : normStatus(o.status);
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

  // ── 手机端 (<768px): 4列看板会被压成一字一行 → 改「状态分段 + 整宽卡片竖列」, 换状态用卡上「移到…」下拉 ──
  if (isMobile) {
    const cur = COLUMNS.find((c) => c.key === mobStatus) || COLUMNS[1];
    const list = grouped[mobStatus] || [];
    return (
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Input.Search allowClear placeholder="搜索 订单号 / 产品名 / 客户名" value={q} onChange={(e) => setQ(e.target.value)} />
        <Segmented block value={mobStatus} onChange={(v) => setMobStatus(v as string)}
          options={COLUMNS.map((c) => ({ label: `${c.label} ${grouped[c.key]?.length ?? 0}`, value: c.key }))} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          点卡片右下「移到…」改状态(可任意方向)。已完结不展示: 已签收 {hidden.signed} · 已关闭 {hidden.cancelled}{hidden.other ? ` · 其他 ${hidden.other}` : ''}。
        </Typography.Text>
        {list.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无" style={{ padding: '28px 0' }} />
        ) : (
          list.map((o) => {
            const moveItems = COLUMNS.filter((c) => c.key !== mobStatus).map((c) => ({
              key: c.key, label: `移到「${c.label}」`, onClick: () => transMut.mutate({ id: o.id, status: c.key }),
            }));
            return (
              <Card key={o.id} size="small" styles={{ body: { padding: 11 } }}
                style={{ borderColor: justConfirmed.has(o.id) ? '#52c41a' : (o.signoff_questioned ? '#faad14' : undefined) }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <strong style={{ fontSize: 13 }}>{o.order_no}</strong>
                  {justConfirmed.has(o.id) && <Tag color="success" style={{ marginInlineEnd: 0 }}>已确定</Tag>}
                  <Tag color={cur.color} style={{ marginInlineEnd: 0, marginLeft: 'auto' }}>{cur.label}</Tag>
                </div>
                <div style={{ fontSize: 12.5, color: '#5f6368', marginTop: 5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {(o.customer_name || '-')} · {(o.product_name || '-')} ×{o.qty}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                  <span style={{ fontSize: 16, fontWeight: 800, fontVariantNumeric: 'tabular-nums' }}>¥{o.paid_amount ?? '0'}</span>
                  {o.signoff_questioned && <Tag color="warning" icon={<QuestionCircleOutlined />} style={{ marginInlineEnd: 0 }}>签收疑问</Tag>}
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                    <Button size="small" onClick={() => setAccessoryFor({ id: o.id, order_no: o.order_no })}>配件</Button>
                    {mobStatus !== 'pending_payment' && <AccessoryTag acc={accSummary[o.id]} />}
                    <Dropdown menu={{ items: moveItems }} trigger={['click']}>
                      <Button size="small" type="primary" ghost>移到 ▾</Button>
                    </Dropdown>
                  </span>
                </div>
              </Card>
            );
          })
        )}
        <DispositionModal
          req={dispReq} loading={transMut.isPending} onCancel={() => setDispReq(null)}
          onSubmit={(d) => dispReq && transMut.mutate({ id: dispReq.orderId, status: dispReq.status, opts: { disposition: d.disposition, plannedShipDate: d.plannedShipDate } })}
        />
        <AccessoryChecklistDrawer
          orderId={accessoryFor?.id ?? null} orderNo={accessoryFor?.order_no}
          open={accessoryFor !== null} onClose={() => setAccessoryFor(null)}
        />
      </Space>
    );
  }

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
          message="订单看板: 拖动卡片到目标档即更新状态(可任意方向, 拖错了拖回去也行)。只显示进行中的订单。"
          description={`[配件]看 BOM 采购清单(缺多少/已到货)。手机端长按卡片再拖。已完结不展示: 已签收 ${hidden.signed} 单、已关闭 ${hidden.cancelled} 单${hidden.other ? `、其他 ${hidden.other} 单` : ''}。`}
        />
        <Input.Search
          allowClear
          placeholder="搜索 订单号 / 产品名 / 客户名"
          style={{ maxWidth: 360 }}
          value={q}
          onChange={(e) => setQ(e.target.value)}
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
                          confirmed={justConfirmed.has(o.id)}
                          showAccessory={col.key !== 'pending_payment'}   /* 待付款不显示缺料(没付款没必要配) */
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

      <DispositionModal
        req={dispReq}
        loading={transMut.isPending}
        onCancel={() => setDispReq(null)}
        onSubmit={(d) => dispReq && transMut.mutate({
          id: dispReq.orderId, status: dispReq.status,
          opts: { disposition: d.disposition, plannedShipDate: d.plannedShipDate },
        })}
      />

      <DragOverlay>
        {activeOrder ? (
          <Card size="small" styles={{ body: { padding: 8 } }}
                style={{ width: 240, boxShadow: '0 6px 20px rgba(0,0,0,.18)' }}>
            <strong style={{ fontSize: 12 }}>{activeOrder.order_no}</strong>
            <div style={{ fontSize: 11, color: '#888' }}>{activeOrder.product_name || '-'} ×{activeOrder.qty}</div>
          </Card>
        ) : null}
      </DragOverlay>

      <AccessoryChecklistDrawer
        orderId={accessoryFor?.id ?? null}
        orderNo={accessoryFor?.order_no}
        open={accessoryFor !== null}
        onClose={() => setAccessoryFor(null)}
      />
    </DndContext>
  );
}

export default function OrdersKanbanPage() {
  const [topView, setTopView] = useState<'orders' | 'factory' | 'accessory'>('orders');
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Segmented
        value={topView}
        onChange={(v) => setTopView(v as 'orders' | 'factory' | 'accessory')}
        options={[
          { label: '订单视图', value: 'orders' },
          { label: '工厂制作单', value: 'factory' },
          { label: '配件视图', value: 'accessory' },
        ]}
      />
      {topView === 'orders' ? <OrdersBoard /> : topView === 'factory' ? <FactoryProductionView /> : <AccessoryPurchasePage />}
    </Space>
  );
}
