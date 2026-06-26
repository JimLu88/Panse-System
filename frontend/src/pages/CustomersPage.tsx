/**
 * 客户 CRM 列表 (Phase 9 Tier 2 #5).
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Input, Modal, Select, Segmented, Space, Switch, Table, Tag, Typography, message,
} from 'antd';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';
import ResponsiveTable from '../components/ResponsiveTable';
import { StatusCard, type StatusTone } from '../components/MobileCards';
import { ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CustomerItem,
  fetchCustomerOrders,
  fetchCustomers,
  triggerCustomerAggregate,
} from '../api/client';

const TIER_COLOR: Record<string, string> = {
  bronze: 'default', silver: 'cyan', gold: 'gold', platinum: 'purple',
};

const TIER_LABEL: Record<string, string> = {
  bronze: '青铜', silver: '白银', gold: '黄金', platinum: '铂金',
};

const TIER_TONE: Record<string, StatusTone> = {
  bronze: 'info', silver: 'ship', gold: 'wait', platinum: 'done',
};


export default function CustomersPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [tier, setTier] = useState<string | undefined>(undefined);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  // Plan C1: 重算口径开关 — 含/不含历史导入订单
  const [includeHistorical, setIncludeHistorical] = useState(true);

  const { data: customers = [], isLoading } = useQuery({
    queryKey: ['customers', q, tier],
    queryFn: () => fetchCustomers({ q: q || undefined, tier, limit: 500 }),
  });

  const aggMut = useMutation({
    mutationFn: () => triggerCustomerAggregate(includeHistorical),
    onSuccess: () => {
      message.success('客户聚合已重算');
      qc.invalidateQueries({ queryKey: ['customers'] });
    },
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="客户聚合 + LTV 分级"
        description="按 phone+name 自动合并历史订单. 平铂金客户优先服务, 复购预警."
      />
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="customer" />}
      {viewMode === 'curated' && (
      <Card
        size="small"
        title="客户列表"
        extra={
          <Space>
            <Input.Search placeholder="搜客户名 / 电话" value={q}
                          onChange={(e) => setQ(e.target.value)}
                          style={{ width: 220 }} allowClear />
            <Select placeholder="分级"
                    allowClear value={tier} onChange={setTier}
                    style={{ width: 120 }}
                    options={Object.entries(TIER_LABEL).map(([v, l]) => ({ value: v, label: l }))} />
            <Switch checkedChildren="含历史客户" unCheckedChildren="不含历史"
                    checked={includeHistorical} onChange={setIncludeHistorical} />
            <Button icon={<ReloadOutlined />} loading={aggMut.isPending}
                    onClick={() => aggMut.mutate()}>
              重算聚合
            </Button>
          </Space>
        }
      >
        <ResponsiveTable<CustomerItem>
          data={customers}
          rowKey={(r) => r.id}
          loading={isLoading}
          emptyText="暂无客户"
          renderCard={(r) => (
            <StatusCard
              title={r.name || `客户#${r.id}`}
              status={TIER_LABEL[r.tier] ?? r.tier ?? '—'}
              tone={TIER_TONE[r.tier] ?? 'info'}
              fields={[
                { label: '电话', value: r.phone || '—' },
                { label: '订单', value: `${r.total_orders ?? 0} 单` },
                { label: '最后下单', value: r.last_order_at ? new Date(r.last_order_at).toLocaleDateString('zh-CN') : '—' },
              ]}
              amount={`¥${Number(r.total_revenue ?? 0).toLocaleString()}`}
              actions={[{ label: '历史订单', primary: true, onClick: () => setDetailId(r.id) }]}
            />
          )}
          desktop={
        <PresetTable<CustomerItem>
          tableKey="customer"
          size="small" loading={isLoading} rowKey="id"
          dataSource={customers}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '客户', dataIndex: 'name', width: 120 },
            { title: '电话', dataIndex: 'phone', width: 130 },
            { title: '分级', dataIndex: 'tier', width: 80,
              render: (v: string) => <Tag color={TIER_COLOR[v]}>{TIER_LABEL[v]}</Tag>,
            },
            { title: '订单数', dataIndex: 'total_orders', width: 80, align: 'right' },
            { title: '累计消费', dataIndex: 'total_revenue', width: 120, align: 'right',
              render: (v: number) => `¥${v.toFixed(2)}` },
            { title: '售后数', dataIndex: 'total_returns', width: 80, align: 'right',
              render: (v: number) => v > 0 ? <Tag color="orange">{v}</Tag> : '-' },
            { title: '最后下单', dataIndex: 'last_order_at', width: 120,
              render: (v: string | null) => v ? new Date(v).toLocaleDateString('zh-CN') : '-' },
            { title: '购买产品', dataIndex: 'products', width: 240,
              render: (v: string[]) => {
                const arr = v ?? [];
                if (!arr.length) return <Typography.Text type="secondary">—</Typography.Text>;
                return (
                  <span>
                    {arr.slice(0, 3).map((p, i) => <Tag key={i} style={{ marginBottom: 2 }}>{p}</Tag>)}
                    {arr.length > 3 ? <Tag>+{arr.length - 3}</Tag> : null}
                  </span>
                );
              },
            },
            { title: '标签', dataIndex: 'tags',
              render: (v: string[]) => (v ?? []).map((t, i) => <Tag key={i}>{t}</Tag>) },
            { title: '操作', fixed: 'right', width: 90,
              render: (_: any, r: CustomerItem) => (
                <Button size="small" onClick={() => setDetailId(r.id)}>历史订单</Button>
              ),
            },
          ]}
        />
        }
        />
      </Card>
      )}
      <CustomerOrdersModal customerId={detailId} onClose={() => setDetailId(null)} />
    </Space>
  );
}

function CustomerOrdersModal({ customerId, onClose }: {
  customerId: number | null; onClose: () => void;
}) {
  const { data: orders = [] } = useQuery({
    queryKey: ['customer-orders', customerId],
    queryFn: () => fetchCustomerOrders(customerId!),
    enabled: customerId !== null,
  });
  return (
    <Modal title={`客户 #${customerId} 历史订单`} open={customerId !== null}
           onCancel={onClose} footer={null} width={900}>
      <Table size="small" rowKey="id"
             dataSource={orders}
             pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
             columns={[
               { title: '订单号', dataIndex: 'order_no', width: 180 },
               { title: '日期', dataIndex: 'order_date', width: 120 },
               { title: '产品', dataIndex: 'product_name' },
               { title: '数量', dataIndex: 'qty', width: 70 },
               { title: '实付', dataIndex: 'paid_amount', width: 120,
                 render: (v: number) => `¥${(v ?? 0).toFixed(2)}` },
               { title: '状态', dataIndex: 'status', width: 100,
                 render: (v: string) => <Tag>{v}</Tag> },
             ]} />
    </Modal>
  );
}
