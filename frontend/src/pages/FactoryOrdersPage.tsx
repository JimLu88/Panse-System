// 供应链「工厂下单表」(2026-06-15): 逐单 下单内容+推算成本+工厂实际+差异+支付/对账, 逐单核对。
import { useMemo, useState } from 'react';
import {
  Button, Card, Col, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Row, Segmented,
  Select, Space, Statistic, Table, Tag, Typography, Upload, message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  listFactoryOrders, factoryOrderAccessories, reconcileFactoryOrder, syncFactoryOrdersFromOrders,
  importFactoryBill, type FactoryOrderMonthlySummary, type FactoryOrderRow,
} from '../api/client';
import ResponsiveTable from '../components/ResponsiveTable';
import { StatusCard } from '../components/MobileCards';

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
  const [q, setQ] = useState('');   // 产品名/SKU/产品编码 模糊搜索
  const [editing, setEditing] = useState<FactoryOrderRow | null>(null);
  const [form] = Form.useForm();
  const formCostType = Form.useWatch('factory_cost_type', form);

  const params = useMemo(() => ({
    factory,
    month,
    payment_status: payStatus,
    only_unreconciled: view === 'unreconciled',
    only_diff: view === 'diff',
    product_search: q.trim() || undefined,
  }), [factory, month, payStatus, view, q]);

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
        unpaid_reason_note: vals.unpaid_reason_note ?? undefined,
        factory_cost_type: vals.factory_cost_type ?? undefined,
        related_primary_order_no: vals.related_primary_order_no ?? undefined,
      }),
    onSuccess: () => {
      message.success('已核对');
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['factory-orders'] });
    },
    onError: (e: any) => message.error(`核对失败: ${e?.response?.data?.detail || e?.message || e}`),
  });

  const syncMut = useMutation({
    mutationFn: () => syncFactoryOrdersFromOrders(),
    onSuccess: (r) => {
      message.success(`已并入订单: 新增 ${r.created} 张, 跳过 ${r.skipped} 张(已存在)`);
      qc.invalidateQueries({ queryKey: ['factory-orders'] });
    },
    onError: (e: any) => message.error(`同步失败: ${e?.response?.data?.detail || e?.message || e}`),
  });

  const billMut = useMutation({
    mutationFn: (file: File) => importFactoryBill(file),
    onSuccess: (r) => {
      Modal.info({
        title: '工厂对账单导入完成',
        width: 640,
        content: (
          <div>
            <p>
              更新工厂实际 <b>{r.updated}</b> 单 · 关联补差免计费 <b>{r.topup_linked}</b> 单 · 未变 {r.unchanged} · 未匹配 {r.unmatched_count}
              (备货/售后/查无订单/价格非数字) · 备货售后行 {r.stock_or_aftersales_skipped}
            </p>
            {r.unmatched_count > 0 && (
              <Text type="secondary">
                未匹配示例: {r.unmatched.slice(0, 5).map((u) => `${u.order_no || '—'}(${u.reason})`).join('; ')}
              </Text>
            )}
          </div>
        ),
      });
      qc.invalidateQueries({ queryKey: ['factory-orders'] });
    },
    onError: (e: any) => message.error(`导入失败: ${e?.response?.data?.detail || e?.message || e}`),
  });

  const openReconcile = (r: FactoryOrderRow) => {
    setEditing(r);
    form.setFieldsValue({
      factory_bill_amount: r.factory_bill_amount ?? r.expected_amount ?? undefined,
      payment_status: r.payment_status,
      payment_date: r.payment_date ? dayjs(r.payment_date) : undefined,
      alipay_flow_no: r.alipay_flow_no ?? undefined,
      remark: r.remark ?? undefined,
      unpaid_reason_note: r.unpaid_reason_note ?? undefined,
      factory_cost_type: r.factory_cost_type,
      related_primary_order_no: r.related_primary_order_no ?? undefined,
    });
  };

  const s = data?.summary;
  const columns = [
    { title: '工厂单号', dataIndex: 'factory_order_no', width: 150, fixed: 'left' as const },
    { title: '平台订单号', dataIndex: 'platform_order_no', width: 170, render: (v: string) => v || '—' },
    {
      title: '工厂费用类型', dataIndex: 'factory_cost_type', width: 160,
      render: (v: FactoryOrderRow['factory_cost_type']) =>
        v === 'same_order_topup' ? <Tag color="purple">同订单补差·不计费</Tag> : <Tag>正常计费</Tag>,
    },
    { title: '关联订单1', dataIndex: 'related_primary_order_no', width: 170, render: (v: string) => v || '—' },
    { title: '工厂', dataIndex: 'factory_name', width: 130, render: (v: string) => v || '—' },
    { title: '下单日期', dataIndex: 'order_date', width: 110, render: (v: string) => v || '—' },
    { title: '产品', dataIndex: 'product_name', ellipsis: true },
    { title: 'SKU', dataIndex: 'sku', width: 120, ellipsis: true, render: (v: string) => v || '—' },
    { title: '数量', dataIndex: 'qty', width: 64, align: 'right' as const },
    { title: '推算成本', dataIndex: 'expected_amount', width: 110, align: 'right' as const, render: money },
    { title: '工厂实际', dataIndex: 'factory_bill_amount', width: 110, align: 'right' as const, render: money },
    {
      title: '差异', dataIndex: 'diff', width: 110, align: 'right' as const,
      render: (v: number | null, r: FactoryOrderRow) =>
        r.no_factory_cost ? <Tag color="purple">不计费</Tag>
          : v === null ? <Text type="secondary">待核对</Text>
          : Math.abs(v) < 0.01 ? <Tag color="green">一致</Tag>
            : <Text type="danger" strong>{money(v)}</Text>,
    },
    {
      title: '支付', dataIndex: 'payment_status', width: 110,
      render: (v: string, r: FactoryOrderRow) =>
        r.no_factory_cost ? <Tag color="purple">无需支付</Tag>
          : v === 'paid' ? <Tag color="blue">已付{r.payment_date ? ` ${r.payment_date}` : ''}</Tag> : <Tag>未付</Tag>,
    },
    {
      title: '待付初判原因', dataIndex: 'unpaid_reason', width: 250,
      render: (v: string | null, r: FactoryOrderRow) =>
        r.no_factory_cost || r.payment_status === 'paid' ? <Text type="secondary">—</Text> : <Text>{v || '待人工判断'}</Text>,
    },
    {
      title: '人工排查备注', dataIndex: 'unpaid_reason_note', width: 220, ellipsis: true,
      render: (v: string | null, r: FactoryOrderRow) =>
        r.no_factory_cost || r.payment_status === 'paid' ? <Text type="secondary">—</Text>
          : v ? <Text>{v}</Text> : <Tag color="orange">待填写</Tag>,
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
            <Col xs={12} sm={8} md={4}><Card size="small"><Statistic title="单数" value={s.count} /></Card></Col>
            <Col xs={12} sm={8} md={5}><Card size="small"><Statistic title="推算合计(应付)" value={s.expected_sum} precision={2} prefix="¥" /></Card></Col>
            <Col xs={12} sm={8} md={5}><Card size="small"><Statistic title="工厂实际合计" value={s.actual_sum} precision={2} prefix="¥" /></Card></Col>
            <Col xs={12} sm={8} md={5}><Card size="small"><Statistic title="差异合计" value={s.diff_sum} precision={2} prefix="¥" valueStyle={{ color: Math.abs(s.diff_sum) >= 1 ? '#cf1322' : undefined }} /></Card></Col>
            <Col xs={12} sm={8} md={5}><Card size="small"><Statistic title="已核对" value={s.reconciled_pct} precision={1} suffix={`% (${s.reconciled}/${s.count})`} /></Card></Col>
          </Row>
          <Row gutter={12} style={{ margin: '12px 0' }}>
            <Col xs={24} sm={12}><Card size="small"><Statistic title="已付(工厂)" value={s.paid_sum} precision={2} prefix="¥" suffix={` · ${s.paid_count}单`} valueStyle={{ color: '#1677ff' }} /></Card></Col>
            <Col xs={24} sm={12}><Card size="small"><Statistic title="未付(待付)" value={s.unpaid_sum} precision={2} prefix="¥" suffix={` · ${s.unpaid_count}单`} valueStyle={{ color: s.unpaid_sum >= 1 ? '#fa8c16' : undefined }} /></Card></Col>
            <Col xs={24}><Text type="secondary">另有 {s.no_factory_cost_count} 单为同订单补差价，已单独记录且不计入工厂应付。</Text></Col>
          </Row>
          <Card
            size="small"
            title="按下单月汇总"
            extra={<Text type="secondary">点击“查看待付”逐月排查；金额优先取工厂账单，缺账单时取推算成本</Text>}
            style={{ marginBottom: 12 }}
          >
            <Table<FactoryOrderMonthlySummary>
              rowKey="month"
              size="small"
              pagination={false}
              dataSource={data?.monthly_summary || []}
              scroll={{ x: 1050 }}
              columns={[
                { title: '下单月', dataIndex: 'month', width: 100, fixed: 'left' },
                { title: '总单数', dataIndex: 'count', width: 80, align: 'right' },
                { title: '补差免计费', dataIndex: 'no_factory_cost_count', width: 110, align: 'right', render: (v) => v ? <Tag color="purple">{v} 单</Tag> : '—' },
                { title: '推算应付', dataIndex: 'expected_sum', width: 130, align: 'right', render: money },
                { title: '工厂实际', dataIndex: 'actual_sum', width: 130, align: 'right', render: money },
                { title: '已付', width: 150, render: (_, r) => `${money(r.paid_sum)} · ${r.paid_count}单` },
                { title: '未付(待付)', width: 160, render: (_, r) => <Text type={r.unpaid_count ? 'warning' : undefined}>{money(r.unpaid_sum)} · {r.unpaid_count}单</Text> },
                { title: '缺工厂账单', dataIndex: 'missing_bill_count', width: 110, align: 'right' },
                { title: '待写人工原因', dataIndex: 'unresolved_count', width: 120, align: 'right', render: (v) => v ? <Tag color="orange">{v} 单</Tag> : <Tag color="green">已完成</Tag> },
                {
                  title: '操作', width: 100, fixed: 'right',
                  render: (_, r) => r.month === '未注明日期'
                    ? null
                    : <Button size="small" onClick={() => { setMonth(r.month); setPayStatus('unpaid'); setView('all'); }}>查看待付</Button>,
                },
              ]}
            />
          </Card>
        </>
      )}
      <Space style={{ marginBottom: 12 }} wrap>
        <Input.Search
          allowClear
          placeholder="搜产品名 / SKU / 产品编码 (模糊)"
          style={{ width: 240 }}
          defaultValue={q}
          onSearch={(v) => setQ(v)}
          onChange={(e) => { if (!e.target.value) setQ(''); }}
        />
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
        <Popconfirm
          title="从订单系统并入工厂单"
          description="把 已付款/已发货/已签收(去补单/退款) 的订单并入本表, 已存在的不重复。"
          okText="并入" cancelText="取消"
          onConfirm={() => syncMut.mutate()}
        >
          <Button type="primary" ghost loading={syncMut.isPending}>从订单同步</Button>
        </Popconfirm>
        <Upload
          accept=".xlsx,.xls"
          showUploadList={false}
          beforeUpload={(file) => { billMut.mutate(file as File); return false; }}
        >
          <Button loading={billMut.isPending}>导入工厂对账单</Button>
        </Upload>
      </Space>
      <ResponsiveTable<FactoryOrderRow>
        data={data?.rows || []}
        rowKey={(r) => r.id}
        loading={isLoading}
        emptyText="暂无工厂单"
        renderCard={(r) => (
          <StatusCard
            title={r.product_name || r.factory_order_no}
            status={r.reconciled ? '已核对' : '待核对'}
            tone={r.reconciled ? 'done' : 'wait'}
            fields={[
              { label: '工厂单', value: r.factory_order_no },
              { label: '工厂', value: r.factory_name || '—' },
              { label: '支付', value: r.no_factory_cost ? '无需支付' : r.payment_status === 'paid' ? '已付' : '未付' },
              ...(r.payment_status === 'paid' ? [] : [{ label: '待付原因', value: r.unpaid_reason || '待人工判断' }]),
            ]}
            amount={money(r.factory_bill_amount ?? r.expected_amount)}
            actions={[{ label: '核对', primary: true, onClick: () => openReconcile(r) }]}
          />
        )}
        desktop={
          <Table<FactoryOrderRow>
            rowKey="id"
            loading={isLoading}
            dataSource={data?.rows || []}
            columns={columns}
            size="small"
            scroll={{ x: 2200 }}
            pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 单` }}
            expandable={{
              expandedRowRender: (r) => <AccessoryPanel no={r.factory_order_no} />,
              rowExpandable: () => true,
            }}
          />
        }
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
          <Form.Item label="工厂费用类型" name="factory_cost_type" rules={[{ required: true, message: '请选择工厂费用类型' }]}>
            <Select options={[
              { label: '正常工厂订单（计入工厂费用）', value: 'normal' },
              { label: '同订单补差价（工厂不产生费用）', value: 'same_order_topup' },
            ]} />
          </Form.Item>
          {formCostType === 'same_order_topup' && (
            <Form.Item
              label="关联订单1"
              name="related_primary_order_no"
              rules={[{ required: true, whitespace: true, message: '请填写产生工厂费用的订单1' }]}
              extra="当前订单作为订单2单独保留，但工厂费用按 0 处理，不进入待付和异常统计。"
            >
              <Input placeholder="填写订单1的平台订单号" />
            </Form.Item>
          )}
          <Form.Item label="工厂实际成本(账单)" name="factory_bill_amount">
            <InputNumber
              style={{ width: '100%' }} min={0} precision={2} addonBefore="¥"
              placeholder={formCostType === 'same_order_topup' ? '补差订单固定为 0' : '工厂账单实际金额'}
              disabled={formCostType === 'same_order_topup'}
            />
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
          <Form.Item label="待付人工排查备注" name="unpaid_reason_note">
            <Input.TextArea rows={3} placeholder="填写实际待付原因，例如：材料抵扣、售后抵扣、7月预付款、账单未到、付款流水待核销" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
