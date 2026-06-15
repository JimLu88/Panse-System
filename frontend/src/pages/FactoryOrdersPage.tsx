// 供应链「工厂下单表」(2026-06-15): 逐单 下单内容+推算成本+工厂实际+差异+支付/对账, 逐单核对。
import { useMemo, useState } from 'react';
import {
  Button, Card, Col, DatePicker, Form, Input, InputNumber, Modal, Row, Segmented,
  Select, Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  listFactoryOrders, factoryOrderAccessories, reconcileFactoryOrder,
  type FactoryOrderRow,
} from '../api/client';

const { Title, Text } = Typography;
const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `¥${Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function AccessoryPanel({ no }: { no: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['factory-order-acc', no],
    queryFn: () => factoryOrderAccessories(no),
  });
  if (isLoading) return <Text type="secondary">加载配件清单…</Text>;
  const acc = data?.accessories || [];
  if (!acc.length) return <Text type="secondary">无配件清单 (该 SKU 无 BOM, 或 SKU 编码缺失)</Text>;
  return (
    <Table
      size="small"
      rowKey={(r) => `${r.material_code}-${r.material_name}`}
      pagination={false}
      dataSource={acc}
      columns={[
        { title: '物料编码', dataIndex: 'material_code', width: 160 },
        { title: '物料名称', dataIndex: 'material_name' },
        { title: '每件用量', dataIndex: 'qty_per_product', width: 100, align: 'right' as const },
      ]}
    />
  );
}

export default function FactoryOrdersPage() {
  const qc = useQueryClient();
  const [factory, setFactory] = useState<string | undefined>();
  const [month, setMonth] = useState<string | undefined>();
  const [payStatus, setPayStatus] = useState<string | undefined>();
  const [view, setView] = useState<'all' | 'unreconciled' | 'diff'>('all');
  const [editing, setEditing] = useState<FactoryOrderRow | null>(null);
  const [form] = Form.useForm();

  const params = useMemo(() => ({
    factory,
    month,
    payment_status: payStatus,
    only_unreconciled: view === 'unreconciled',
    only_diff: view === 'diff',
  }), [factory, month, payStatus, view]);

  const { data, isLoading } = useQuery({
    queryKey: ['factory-orders', params],
    queryFn: () => listFactoryOrders(params),
  });

  const recMut = useMutation({
    mutationFn: (vals: any) =>
      reconcileFactoryOrder(editing!.factory_order_no, {
        factory_bill_amount: vals.factory_bill_amount ?? undefined,
        payment_status: vals.payment_status ?? undefined,
        payment_date: vals.payment_date ? dayjs(vals.payment_date).format('YYYY-MM-DD') : undefined,
        alipay_flow_no: vals.alipay_flow_no ?? undefined,
        remark: vals.remark ?? undefined,
      }),
    onSuccess: () => {
      message.success('已核对');
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['factory-orders'] });
    },
    onError: (e: any) => message.error(`核对失败: ${e?.response?.data?.detail || e?.message || e}`),
  });

  const openReconcile = (r: FactoryOrderRow) => {
    setEditing(r);
    form.setFieldsValue({
      factory_bill_amount: r.factory_bill_amount ?? r.expected_amount ?? undefined,
      payment_status: r.payment_status,
      payment_date: r.payment_date ? dayjs(r.payment_date) : undefined,
      alipay_flow_no: r.alipay_flow_no ?? undefined,
      remark: r.remark ?? undefined,
    });
  };

  const s = data?.summary;
  const columns = [
    { title: '工厂单号', dataIndex: 'factory_order_no', width: 150, fixed: 'left' as const },
    { title: '平台订单号', dataIndex: 'platform_order_no', width: 170, render: (v: string) => v || '—' },
    { title: '工厂', dataIndex: 'factory_name', width: 130, render: (v: string) => v || '—' },
    { title: '下单日期', dataIndex: 'order_date', width: 110, render: (v: string) => v || '—' },
    { title: '产品', dataIndex: 'product_name', ellipsis: true },
    { title: 'SKU', dataIndex: 'sku', width: 120, ellipsis: true, render: (v: string) => v || '—' },
    { title: '数量', dataIndex: 'qty', width: 64, align: 'right' as const },
    { title: '推算成本', dataIndex: 'expected_amount', width: 110, align: 'right' as const, render: money },
    { title: '工厂实际', dataIndex: 'factory_bill_amount', width: 110, align: 'right' as const, render: money },
    {
      title: '差异', dataIndex: 'diff', width: 110, align: 'right' as const,
      render: (v: number | null) =>
        v === null ? <Text type="secondary">待核对</Text>
          : Math.abs(v) < 0.01 ? <Tag color="green">一致</Tag>
            : <Text type="danger" strong>{money(v)}</Text>,
    },
    {
      title: '支付', dataIndex: 'payment_status', width: 110,
      render: (v: string, r: FactoryOrderRow) =>
        v === 'paid' ? <Tag color="blue">已付{r.payment_date ? ` ${r.payment_date}` : ''}</Tag> : <Tag>未付</Tag>,
    },
    {
      title: '对账', dataIndex: 'reconciled', width: 80,
      render: (v: boolean) => (v ? <Tag color="green">已核对</Tag> : <Tag color="orange">待核对</Tag>),
    },
    {
      title: '操作', width: 80, fixed: 'right' as const,
      render: (_: unknown, r: FactoryOrderRow) => <Button size="small" onClick={() => openReconcile(r)}>核对</Button>,
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Title level={4}>工厂下单表</Title>
      <Text type="secondary">逐单核对: 系统推算成本 ↔ 工厂实际成本(账单) ↔ 支付。点行可展开配件清单。</Text>
      {s && (
        <>
          <Row gutter={12} style={{ marginTop: 12 }}>
            <Col span={4}><Card size="small"><Statistic title="单数" value={s.count} /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="推算合计(应付)" value={s.expected_sum} precision={2} prefix="¥" /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="工厂实际合计" value={s.actual_sum} precision={2} prefix="¥" /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="差异合计" value={s.diff_sum} precision={2} prefix="¥" valueStyle={{ color: Math.abs(s.diff_sum) >= 1 ? '#cf1322' : undefined }} /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="已核对" value={s.reconciled_pct} precision={1} suffix={`% (${s.reconciled}/${s.count})`} /></Card></Col>
          </Row>
          <Row gutter={12} style={{ margin: '12px 0' }}>
            <Col span={12}><Card size="small"><Statistic title="已付(工厂)" value={s.paid_sum} precision={2} prefix="¥" suffix={` · ${s.paid_count}单`} valueStyle={{ color: '#1677ff' }} /></Card></Col>
            <Col span={12}><Card size="small"><Statistic title="未付(待付)" value={s.unpaid_sum} precision={2} prefix="¥" suffix={` · ${s.unpaid_count}单`} valueStyle={{ color: s.unpaid_sum >= 1 ? '#fa8c16' : undefined }} /></Card></Col>
          </Row>
        </>
      )}
      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented
          value={view}
          onChange={(v) => setView(v as 'all' | 'unreconciled' | 'diff')}
          options={[{ label: '全部', value: 'all' }, { label: '待核对', value: 'unreconciled' }, { label: '有差异', value: 'diff' }]}
        />
        <Select
          allowClear placeholder="按工厂" style={{ width: 180 }} value={factory}
          onChange={setFactory}
          options={(data?.factories || []).map((f) => ({ label: f, value: f }))}
        />
        <Select
          allowClear placeholder="支付状态" style={{ width: 130 }} value={payStatus}
          onChange={(v) => setPayStatus(v)}
          options={[{ label: '已付', value: 'paid' }, { label: '未付', value: 'unpaid' }]}
        />
        <DatePicker
          picker="month" placeholder="按下单月" style={{ width: 150 }}
          value={month ? dayjs(`${month}-01`) : null}
          onChange={(d) => setMonth(d ? d.format('YYYY-MM') : undefined)}
        />
      </Space>
      <Table<FactoryOrderRow>
        rowKey="id"
        loading={isLoading}
        dataSource={data?.rows || []}
        columns={columns}
        size="small"
        scroll={{ x: 1400 }}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 单` }}
        expandable={{
          expandedRowRender: (r) => <AccessoryPanel no={r.factory_order_no} />,
          rowExpandable: () => true,
        }}
      />
      <Modal
        title={`核对工厂单 ${editing?.factory_order_no || ''}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() => form.validateFields().then((v) => recMut.mutate(v))}
        confirmLoading={recMut.isPending}
        destroyOnClose
      >
        {editing && (
          <Text type="secondary">
            推算成本 {money(editing.expected_amount)} · 产品 {editing.product_name || '—'} · 数量 {editing.qty}
          </Text>
        )}
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item label="工厂实际成本(账单)" name="factory_bill_amount">
            <InputNumber style={{ width: '100%' }} min={0} precision={2} addonBefore="¥" placeholder="工厂账单实际金额" />
          </Form.Item>
          <Form.Item label="支付状态" name="payment_status">
            <Select options={[{ label: '未付', value: 'unpaid' }, { label: '已付', value: 'paid' }]} allowClear />
          </Form.Item>
          <Form.Item label="支付时间" name="payment_date">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="支付宝流水号" name="alipay_flow_no">
            <Input placeholder="付款对应的支付宝流水号" />
          </Form.Item>
          <Form.Item label="备注/差异原因" name="remark">
            <Input.TextArea rows={2} placeholder="如有差异, 记原因" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
