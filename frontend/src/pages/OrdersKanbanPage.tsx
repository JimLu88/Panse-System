/**
 * 订单看板 (Phase 8 Tier 1 #4, 借鉴 Linear).
 *
 * 5 列 = 5 个订单状态. 拖拽换列 → 调 transition API.
 * 简化版用按钮"推进/取消", 不用拖拽库, 保持 0 依赖.
 */
import { useState } from 'react';
import { Alert, Button, Card, Col, Empty, Row, Space, Tag, Tooltip, Typography, message } from 'antd';
import { CheckCircleOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { changeOrderStatus, confirmOrderManual, confirmOrderTracking, listOrders } from '../api/client';
import OrderTimelineDrawer from '../components/OrderTimelineDrawer';

const COLUMNS: { key: string; label: string; color: string; next?: string }[] = [
  { key: 'pending_payment', label: '待付款', color: 'default', next: 'paid' },
  { key: 'paid', label: '已付款', color: 'cyan', next: 'shipped' },
  { key: 'shipped', label: '已发货', color: 'blue', next: 'signed' },
  { key: 'aftersales', label: '售后中', color: 'orange' },
];
const COL_CAP = 25; // 每列默认最多显示这么多张, 超出折叠

// 导入订单状态多为中文(交易成功 / 买家已付款,等待卖家发货 / 交易关闭…), 归一到看板列
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

export default function OrdersKanbanPage() {
  const qc = useQueryClient();
  const [timelineFor, setTimelineFor] = useState<number | null>(null);
  const [expandedCols, setExpandedCols] = useState<Record<string, boolean>>({});
  const { data: orders = [], isLoading } = useQuery({
    queryKey: ['orders-kanban'],
    queryFn: () => listOrders({ limit: 500 }),
    refetchInterval: 30000,
  });

  const transMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      changeOrderStatus(id, status),
    onSuccess: () => {
      message.success('已更新状态');
      qc.invalidateQueries({ queryKey: ['orders-kanban'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '失败'),
  });

  const trackingMut = useMutation({
    mutationFn: (id: number) => confirmOrderTracking(id),
    onSuccess: () => {
      message.success('快递核对完成');
      qc.invalidateQueries({ queryKey: ['orders-kanban'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '失败'),
  });

  const manualMut = useMutation({
    mutationFn: (id: number) => confirmOrderManual(id),
    onSuccess: () => {
      message.success('人工确认完成');
      qc.invalidateQueries({ queryKey: ['orders-kanban'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '失败'),
  });

  if (isLoading) return <Card loading />;

  const grouped: Record<string, any[]> = {};
  COLUMNS.forEach((c) => { grouped[c.key] = []; });
  const hidden = { signed: 0, cancelled: 0, other: 0 };
  (orders || []).forEach((o: any) => {
    const k = normStatus(o.status);
    if (grouped[k]) grouped[k].push(o);
    else if (k === 'signed') hidden.signed += 1;
    else if (k === 'cancelled') hidden.cancelled += 1;
    else hidden.other += 1;
  });
  // 每列按下单日期倒序(新→旧), 便于折叠较旧的单
  Object.values(grouped).forEach((list) =>
    list.sort((a, b) => String(b.order_date || '').localeCompare(String(a.order_date || ''))));

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
             message="订单看板视图: 一眼看到瓶颈在哪一档（只显示进行中的订单）"
             description={`点订单卡片查时间线; 点 [→ 下一档] 推进状态。已完结不展示: 已签收 ${hidden.signed} 单、已关闭 ${hidden.cancelled} 单${hidden.other ? `、其他 ${hidden.other} 单` : ''}。`} />
      <Row gutter={12}>
        {COLUMNS.map((col) => (
          <Col key={col.key} span={Math.floor(24 / COLUMNS.length)}>
            <Card size="small"
                  title={
                    <Space>
                      <Tag color={col.color}>{col.label}</Tag>
                      <Typography.Text type="secondary">
                        {grouped[col.key].length}
                      </Typography.Text>
                    </Space>
                  }
                  styles={{ body: { minHeight: 480, padding: 8 } }}>
              {grouped[col.key].length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                       description={<span style={{ color: '#bbb' }}>无</span>} />
              ) : (
                <>
                {(expandedCols[col.key] ? grouped[col.key] : grouped[col.key].slice(0, COL_CAP)).map((o: any) => (
                  <Card
                    key={o.id}
                    size="small"
                    style={{
                      marginBottom: 6,
                      borderColor: o.signoff_questioned ? '#faad14' : undefined,
                    }}
                    styles={{ body: { padding: 8 } }}
                  >
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <strong style={{ fontSize: 12 }}>{o.order_no}</strong>
                      <Typography.Text style={{ fontSize: 11 }} type="secondary">
                        {o.customer_name || '-'} · {o.product_name || '-'} ×{o.qty}
                      </Typography.Text>
                      <Typography.Text style={{ fontSize: 11 }}>
                        ¥{o.paid_amount ?? '0'}
                      </Typography.Text>
                      {o.signoff_questioned && (
                        <Tag color="warning" icon={<QuestionCircleOutlined />} style={{ fontSize: 11 }}>
                          签收有疑问
                        </Tag>
                      )}
                      <Space size={4} style={{ marginTop: 4 }} wrap>
                        <Button size="small" onClick={() => setTimelineFor(o.id)}>
                          时间线
                        </Button>
                        {col.key === 'shipped' && (
                          <>
                            <Tooltip title="确认快递单号已核对">
                              <Button
                                size="small"
                                icon={<CheckCircleOutlined />}
                                type={o.tracking_confirmed ? 'primary' : 'default'}
                                loading={trackingMut.isPending}
                                onClick={() => !o.tracking_confirmed && trackingMut.mutate(o.id)}
                              >
                                快递{o.tracking_confirmed ? '✓' : ''}
                              </Button>
                            </Tooltip>
                            <Tooltip title="人工确认已签收">
                              <Button
                                size="small"
                                icon={<CheckCircleOutlined />}
                                type={o.manual_confirmed ? 'primary' : 'default'}
                                loading={manualMut.isPending}
                                onClick={() => !o.manual_confirmed && manualMut.mutate(o.id)}
                              >
                                人工{o.manual_confirmed ? '✓' : ''}
                              </Button>
                            </Tooltip>
                          </>
                        )}
                        {col.next && col.key !== 'shipped' && (
                          <Button
                            size="small"
                            type="link"
                            loading={transMut.isPending}
                            onClick={() => transMut.mutate({ id: o.id, status: col.next! })}
                          >
                            → {COLUMNS.find((c) => c.key === col.next)?.label}
                          </Button>
                        )}
                      </Space>
                    </Space>
                  </Card>
                ))}
                {grouped[col.key].length > COL_CAP && (
                  <Button type="dashed" size="small" block style={{ marginTop: 4 }}
                          onClick={() => setExpandedCols((p) => ({ ...p, [col.key]: !p[col.key] }))}>
                    {expandedCols[col.key] ? '收起' : `还有 ${grouped[col.key].length - COL_CAP} 单 · 展开`}
                  </Button>
                )}
                </>
              )}
            </Card>
          </Col>
        ))}
      </Row>
      <OrderTimelineDrawer orderId={timelineFor} open={timelineFor !== null}
                            onClose={() => setTimelineFor(null)} />
    </Space>
  );
}
