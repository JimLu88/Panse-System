/**
 * 修改档案中心 (方向4) — 所有人工编辑的全局流水。
 * 按 来源(网页/飞书) / 账号 / 业务表 / 关键词 检索; 每条含 旧值→新值 + 日期。
 * 字段级 30 份回溯在各编辑器字段旁的 ⏱ 里 (方向2)。
 */
import { useState } from 'react';
import {
  Alert, Card, Input, Segmented, Select, Space, Table, Tag, Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import { FieldChangeRow, listFieldChanges } from '../api/client';

const TABLE_LABEL: Record<string, string> = {
  pricing_skus: '定价主表', pricing_sku_costs: '定价·22配件', pricing_sku_promo: '定价·渠道',
  product_inventory: '成品库存', materials: '物料单价库',
  after_sales: '退货/售后', orders: '订单',
};

export default function AuditTrailPage() {
  const [source, setSource] = useState<string>('');
  const [table, setTable] = useState<string | undefined>(undefined);
  const [q, setQ] = useState('');

  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['field-changes', source, table, q],
    queryFn: () => listFieldChanges({
      source: source || undefined, table, q: q || undefined, limit: 500,
    }),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>修改历史</Typography.Title>
      <Alert
        type="info" showIcon
        message="只记「人」的修改 (网页编辑 / 飞书修改), 系统自动重算不入档。永久保留。"
        description="想看某个字段的完整历史: 到对应编辑弹窗里点字段旁的 ⏱ 图标 (最近 30 份)。"
      />
      <Card size="small">
        <Space wrap>
          <Segmented
            value={source}
            onChange={(v) => setSource(v as string)}
            options={[
              { label: '全部来源', value: '' },
              { label: '网页编辑', value: 'web' },
              { label: '飞书修改', value: 'feishu' },
              { label: '导入覆盖', value: 'import' },
            ]}
          />
          <Select
            allowClear placeholder="业务表" style={{ width: 160 }}
            value={table} onChange={setTable}
            options={Object.entries(TABLE_LABEL).map(([v, l]) => ({ value: v, label: l }))}
          />
          <Input.Search
            placeholder="搜 行/字段 关键词 (SKU编码/产品名/字段名)"
            value={q} onChange={(e) => setQ(e.target.value)}
            style={{ width: 300 }} allowClear
          />
          <Typography.Text type="secondary">共 {rows.length} 条 (最多显示 500)</Typography.Text>
        </Space>
      </Card>
      <Card size="small">
        <Table<FieldChangeRow>
          size="small" rowKey="id" loading={isLoading}
          dataSource={rows}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [50, 100, 200] }}
          columns={[
            { title: '时间', dataIndex: 'created_at', width: 150,
              render: (v: string | null) => v ? v.slice(0, 16).replace('T', ' ') : '—' },
            { title: '来源', dataIndex: 'source', width: 90,
              render: (v: string, r) => (
                <Tag color={{ feishu: 'purple', import: 'orange' }[v] ?? 'blue'}>{r.source_label}</Tag>
              ) },
            { title: '账号', dataIndex: 'actor', width: 100,
              render: (v: string | null) => v || <Typography.Text type="secondary">未记录</Typography.Text> },
            { title: '业务表', dataIndex: 'table_name', width: 110,
              render: (v: string) => TABLE_LABEL[v] || v },
            { title: '哪一行', width: 220, ellipsis: true,
              render: (_: any, r) => (
                <span>{r.row_label || '—'} <Typography.Text type="secondary" style={{ fontSize: 11 }}>{r.row_pk}</Typography.Text></span>
              ) },
            { title: '字段', width: 130,
              render: (_: any, r) => r.field_label || r.field },
            { title: '改动', ellipsis: true,
              render: (_: any, r) => (
                <span>
                  <Typography.Text delete type="secondary">{r.old_value ?? '空'}</Typography.Text>
                  {' → '}
                  <Typography.Text strong>{r.new_value ?? '空'}</Typography.Text>
                </span>
              ) },
          ]}
        />
      </Card>
    </Space>
  );
}
