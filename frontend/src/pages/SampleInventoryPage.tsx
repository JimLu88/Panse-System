/**
 * 样品库存 —— 原「营销与经营 → 样品」表, 按用户要求移到「库存」下。
 * 数据/接口不变 (GET /api/marketing/samples)。新增「样品售出」: 从样品库卖出 →
 * 标记已售+关联订单, 修复费/转运费各记一笔配件采购(不动订单表)。
 */
import { useState } from 'react';
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sample, listSamples, sellSample } from '../api/client';
import FullColumnView from '../components/FullColumnView';

const statusColor = (v: string | null) => {
  if (!v) return 'default';
  if (v === '在用') return 'green';
  if (v === '闲置') return 'orange';
  if (v === '报废' || v === '报损') return 'red';
  if (v === '已售') return 'blue';
  return 'default';
};

export default function SampleInventoryPage() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [sellTarget, setSellTarget] = useState<Sample | null>(null);
  const [form] = Form.useForm();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['samples'], queryFn: listSamples });

  const sellMut = useMutation({
    mutationFn: (v: {
      id: number;
      order_no: string;
      repair_fee?: number;
      transfer_freight?: number;
      supplier?: string;
    }) => sellSample(v.id, v),
    onSuccess: (r: any) => {
      const n = (r?.purchases ?? []).length;
      message.success(
        `样品已标记售出, 关联订单 ${r?.related_order_no}; 已生成 ${n} 笔配件采购(修复费/转运费)`,
      );
      setSellTarget(null);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['samples'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '售出失败'),
  });

  const openSell = (s: Sample) => {
    setSellTarget(s);
    form.resetFields();
  };
  const submitSell = (vals: {
    order_no: string;
    repair_fee?: number;
    transfer_freight?: number;
    supplier?: string;
  }) => {
    if (!sellTarget) return;
    sellMut.mutate({ id: sellTarget.id, ...vals });
  };

  const totalCost = (data ?? []).reduce((sum, row) => sum + (row.cost != null ? Number(row.cost) : 0), 0);
  const summaryRow = () => (
    <Table.Summary.Row>
      <Table.Summary.Cell index={0} colSpan={5}>
        <strong>合计</strong>
      </Table.Summary.Cell>
      <Table.Summary.Cell index={5} align="right">
        <strong>¥{totalCost.toFixed(2)}</strong>
      </Table.Summary.Cell>
      <Table.Summary.Cell index={6} colSpan={6} />
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
            { title: '成本', dataIndex: 'cost', width: 100, align: 'right' as const, render: (v: string | null) => (v ? `¥${v}` : '-') },
            { title: '制作日期', dataIndex: 'made_at', width: 110 },
            { title: '位置', dataIndex: 'location', width: 140 },
            { title: '状态', dataIndex: 'status', width: 80, render: (v: string | null) => (v ? <Tag color={statusColor(v)}>{v}</Tag> : '-') },
            { title: '关联订单', dataIndex: 'related_order_no', width: 130, render: (v: string | null) => (v ? <code>{v}</code> : '-') },
            { title: '用途', dataIndex: 'usage', width: 100 },
            {
              title: '操作',
              key: 'op',
              width: 70,
              render: (_: unknown, s: Sample) =>
                s.status !== '已售' ? (
                  <Button size="small" type="link" onClick={() => openSell(s)}>售出</Button>
                ) : null,
            },
          ]}
        />
      )}

      <Modal
        title={`样品售出 ${sellTarget?.sample_no ?? ''}`}
        open={!!sellTarget}
        onOk={() => form.submit()}
        onCancel={() => setSellTarget(null)}
        okText="确认售出"
        confirmLoading={sellMut.isPending}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          从样品库卖出 → 走"杭州→江西修复→发客户"。填修复费/转运费会各记一笔配件采购(关联该订单, 不动订单表);
          该订单不进工厂对账单。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={submitSell}>
          <Form.Item name="order_no" label="关联订单号" rules={[{ required: true, message: '填订单号' }]}>
            <Input placeholder="如 TB1234567890" />
          </Form.Item>
          <Space size="middle" wrap>
            <Form.Item name="repair_fee" label="修复费(江西)">
              <InputNumber min={0} addonAfter="元" style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="transfer_freight" label="转运费(杭州→江西)">
              <InputNumber min={0} addonAfter="元" style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item name="supplier" label="修复方/承运(可选)">
            <Input placeholder="江西修复方名称" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
