/**
 * 订单配件清单抽屉 — 列出该单所有配件 (BOM 自动 + 客户备注新增),
 * 跟踪采购/物流状态, 可填快递单号并实时刷新物流轨迹。
 */
import { useState } from 'react';
import {
  Alert, Button, Drawer, Input, Popover, Select, Space, Table, Tag, Timeline,
  Tooltip, Typography, message,
} from 'antd';
import { ReloadOutlined, TruckOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listAccessories, updateAccessory, refreshAccessoryTracking, regenerateAccessories,
  type AccessoryItem,
} from '../api/orders';

const STATUS_OPTIONS = ['未采购', '已下单', '运输中', '已到货', '工厂提供'];
const STATUS_COLOR: Record<string, string> = {
  未采购: 'default', 已下单: 'blue', 运输中: 'gold', 已到货: 'green', 工厂提供: 'cyan',
};

export default function AccessoryChecklistDrawer({
  orderId, orderNo, open, onClose,
}: { orderId: number | null; orderNo?: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [trackingEdit, setTrackingEdit] = useState<Record<number, string>>({});

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['accessories', orderId],
    queryFn: () => listAccessories(orderId!),
    enabled: orderId !== null && open,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['accessories', orderId] });

  const patchMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<AccessoryItem> }) =>
      updateAccessory(id, patch),
    onSuccess: () => { invalidate(); },
  });

  const refreshMut = useMutation({
    mutationFn: (id: number) => refreshAccessoryTracking(id),
    onSuccess: (row) => {
      if (row.alert_reason && row.status !== '已到货') {
        message.warning(row.alert_reason);  // 多为「物流未配置」之类提示
      } else {
        message.success(`物流已刷新：${row.tracking_last_status ?? row.status}`);
      }
      invalidate();
    },
    onError: () => message.error('物流刷新失败'),
  });

  const regenMut = useMutation({
    mutationFn: () => regenerateAccessories(orderId!),
    onSuccess: () => { message.success('已按 BOM 补全配件'); invalidate(); },
  });

  const purchaseItems = items.filter((i) => !i.is_factory_provided);
  const factoryItems = items.filter((i) => i.is_factory_provided);
  const pendingCount = purchaseItems.filter((i) => i.status !== '已到货').length;
  const criticalCount = items.filter((i) => i.alert_level === 'critical').length;

  const columns = [
    {
      title: '配件', dataIndex: 'material_name', ellipsis: true,
      render: (v: string, r: AccessoryItem) => (
        <Space size={4}>
          <span>{v ?? r.material_code}</span>
          {r.source === '客户备注' && <Tag color="orange">客户加配</Tag>}
          {r.alert_level === 'critical' && <Tag color="red">急</Tag>}
          {r.alert_level === 'warn' && <Tag color="gold">催</Tag>}
        </Space>
      ),
    },
    { title: '编码', dataIndex: 'material_code', width: 100, render: (v: string) => <code>{v}</code> },
    { title: '需求量', dataIndex: 'qty_required', width: 80, align: 'right' as const,
      render: (v: string, r: AccessoryItem) => `${v}${r.unit ?? ''}` },
    {
      title: '状态', dataIndex: 'status', width: 120,
      render: (v: string, r: AccessoryItem) =>
        r.is_factory_provided ? (
          <Tag color="cyan">工厂提供</Tag>
        ) : (
          <Select
            size="small" value={v} style={{ width: 100 }}
            options={STATUS_OPTIONS.filter((s) => s !== '工厂提供').map((s) => ({ value: s, label: s }))}
            onChange={(s) => patchMut.mutate({ id: r.id, patch: { status: s } })}
          />
        ),
    },
    {
      title: '快递单号 / 物流', width: 280,
      render: (_: unknown, r: AccessoryItem) => {
        if (r.is_factory_provided) return <span style={{ color: '#aaa' }}>—</span>;
        const events = r.tracking_events ?? [];
        return (
          <Space>
            <Input
              size="small" style={{ width: 130 }} placeholder="快递单号"
              value={trackingEdit[r.id] ?? r.tracking_no ?? ''}
              onChange={(e) => setTrackingEdit({ ...trackingEdit, [r.id]: e.target.value })}
              onBlur={(e) => {
                const v = e.target.value.trim();
                if (v && v !== (r.tracking_no ?? '')) {
                  patchMut.mutate({ id: r.id, patch: { tracking_no: v } });
                }
              }}
            />
            <Tooltip title="实时刷新物流">
              <Button
                size="small" icon={<ReloadOutlined />}
                loading={refreshMut.isPending && refreshMut.variables === r.id}
                disabled={!r.tracking_no}
                onClick={() => refreshMut.mutate(r.id)}
              />
            </Tooltip>
            {events.length > 0 && (
              <Popover
                title={`${r.carrier_name ?? '物流'} 轨迹`}
                content={
                  <Timeline
                    style={{ maxWidth: 360, maxHeight: 320, overflow: 'auto' }}
                    items={events.map((ev, i) => ({
                      color: i === 0 ? 'green' : 'gray',
                      children: (
                        <span style={{ fontSize: 12 }}>
                          <div style={{ color: '#999' }}>{ev.time}</div>
                          {ev.context}
                        </span>
                      ),
                    }))}
                  />
                }
              >
                <Button size="small" type="link" icon={<TruckOutlined />}>
                  {r.tracking_last_status ? `${r.tracking_last_status.slice(0, 10)}…` : '查看'}
                </Button>
              </Popover>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <Drawer
      title={`配件清单 · ${orderNo ?? `#${orderId}`}`}
      open={open}
      onClose={onClose}
      width={760}
      destroyOnClose
      extra={
        <Button size="small" onClick={() => regenMut.mutate()} loading={regenMut.isPending}>
          按 BOM 补全
        </Button>
      }
    >
      {criticalCount > 0 && (
        <Alert
          type="error" showIcon style={{ marginBottom: 12 }}
          message={`${criticalCount} 项配件临近发货仍未到货，请尽快处理`}
        />
      )}
      <Space style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">
          需采购 {purchaseItems.length} 项 · 待到货 <b>{pendingCount}</b> · 工厂提供 {factoryItems.length} 项
        </Typography.Text>
      </Space>
      <Table<AccessoryItem>
        rowKey="id"
        loading={isLoading}
        dataSource={items}
        columns={columns}
        pagination={false}
        size="small"
        rowClassName={(r) => (r.source === '客户备注' ? 'extra-accessory-row' : '')}
        locale={{ emptyText: '该 SKU 暂无 BOM 配件，点「按 BOM 补全」或先录入 BOM' }}
      />
      <style>{`.extra-accessory-row td { background: #fff7e6; }`}</style>
    </Drawer>
  );
}
