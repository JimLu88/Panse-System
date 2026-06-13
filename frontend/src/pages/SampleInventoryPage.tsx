/**
 * 样品库存 —— 原「营销与经营 → 样品」表, 按用户要求移到「库存」下。
 * 数据/接口不变 (GET /api/marketing/samples), 仅换了归属菜单。
 */
import { useState } from 'react';
import { Segmented, Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { Sample, listSamples } from '../api/client';
import FullColumnView from '../components/FullColumnView';

const statusColor = (v: string | null) => {
  if (!v) return 'default';
  if (v === '在用') return 'green';
  if (v === '闲置') return 'orange';
  if (v === '报废') return 'red';
  return 'default';
};

export default function SampleInventoryPage() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const { data, isLoading } = useQuery({ queryKey: ['samples'], queryFn: listSamples });

  const totalCost = (data ?? []).reduce((sum, row) => sum + (row.cost != null ? Number(row.cost) : 0), 0);
  const summaryRow = () => (
    <Table.Summary.Row>
      <Table.Summary.Cell index={0} colSpan={5}><strong>合计</strong></Table.Summary.Cell>
      <Table.Summary.Cell index={5} align="right"><strong>¥{totalCost.toFixed(2)}</strong></Table.Summary.Cell>
      <Table.Summary.Cell index={6} colSpan={4} />
    </Table.Summary.Row>
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>样品库存</Typography.Title>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[{ label: '精选视图', value: 'curated' }, { label: '全部列', value: 'full' }]}
      />
      {viewMode === 'full' && <FullColumnView entity="sample" />}
      {viewMode === 'curated' && (
        <Table<Sample>
          rowKey="id"
          loading={isLoading}
          dataSource={data}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          summary={summaryRow}
          columns={[
            { title: '样品号', dataIndex: 'sample_no', width: 110, render: (v) => <code>{v}</code> },
            { title: '产品', dataIndex: 'product_name', ellipsis: true },
            { title: 'SKU', dataIndex: 'sku', ellipsis: true },
            { title: '类型', dataIndex: 'sample_type', width: 90 },
            { title: '数量', dataIndex: 'qty', width: 60 },
            { title: '成本', dataIndex: 'cost', width: 100, align: 'right' as const, render: (v: string | null) => v ? `¥${v}` : '-' },
            { title: '制作日期', dataIndex: 'made_at', width: 110 },
            { title: '位置', dataIndex: 'location', width: 140 },
            { title: '状态', dataIndex: 'status', width: 80, render: (v: string | null) => v ? <Tag color={statusColor(v)}>{v}</Tag> : '-' },
            { title: '用途', dataIndex: 'usage', width: 100 },
          ]}
        />
      )}
    </Space>
  );
}
