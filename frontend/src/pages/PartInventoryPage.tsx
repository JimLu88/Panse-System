import { useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  PartInventory,
  addPartInventoryRow,
  listPartInventory,
  updatePartInventory,
} from '../api/client';
import {
  autoReconcileReturns,
  listPartInventoryWithStats,
  listPartReturns,
  markPartDefective,
  partReturnSummary,
  refundCandidates,
  resolvePartDefective,
  settlePartReturn,
  type PartInventoryStats,
  type PartReturn,
  type PartReturnSummary,
} from '../api/catalog';
import { FirstVisitTip } from '../components/FirstVisitTip';
import FullColumnView from '../components/FullColumnView';

// 预警状态 → 中文标签 + 颜色 (与成品库存一致)
const WARN_META: Record<string, { label: string; color: string }> = {
  critical: { label: '缺货', color: 'red' },
  danger: { label: '低于预警线', color: 'volcano' },
  warning: { label: '快用完', color: 'orange' },
  excess: { label: '滞销积压', color: 'purple' },
  ok: { label: '正常', color: 'green' },
};
const WARN_SEV: Record<string, number> = { critical: 0, danger: 1, warning: 2, ok: 3, excess: 4 };

export default function PartInventoryPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['part-inventory'],
    queryFn: listPartInventory,
  });
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [edits, setEdits] = useState<Record<number, { physical_qty?: number; locked_qty?: number }>>({});
  const [savingId, setSavingId] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<'curated' | 'alert' | 'full' | 'returns'>('curated');
  const alerts = useQuery({
    queryKey: ['part-inventory-stats'],
    queryFn: listPartInventoryWithStats,
    enabled: viewMode === 'alert',
  });
  // 坏件/返厂 (方案B): mark=报坏件(良品→待返厂), resolve=处理(回良品/报废/退款)
  const [defectRow, setDefectRow] = useState<PartInventory | null>(null);
  const [defectMode, setDefectMode] = useState<'mark' | 'resolve'>('mark');
  const [defectSaving, setDefectSaving] = useState(false);
  const [defectForm] = Form.useForm();

  function openDefect(row: PartInventory, mode: 'mark' | 'resolve') {
    setDefectRow(row);
    setDefectMode(mode);
    defectForm.resetFields();
  }

  async function submitDefect(values: {
    qty: number; reason?: string;
    disposition?: 'repaired' | 'scrapped' | 'returned'; remark?: string;
  }) {
    if (!defectRow) return;
    setDefectSaving(true);
    try {
      if (defectMode === 'mark') {
        await markPartDefective(defectRow.id, {
          qty: values.qty, reason: values.reason, remark: values.remark,
        });
        message.success('已标记坏件 → 待返厂/维修中');
      } else {
        await resolvePartDefective(defectRow.id, {
          qty: values.qty, disposition: values.disposition!, remark: values.remark,
        });
        message.success('已处理');
      }
      qc.invalidateQueries({ queryKey: ['part-inventory'] });
      qc.invalidateQueries({ queryKey: ['part-inventory-stats'] });
      setDefectRow(null);
      defectForm.resetFields();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '操作失败');
    } finally {
      setDefectSaving(false);
    }
  }

  // 返厂台账 (方案C): 退款应收/维修费/报废损失 + 供应商对账 + 结算
  const returns = useQuery({
    queryKey: ['part-returns'],
    queryFn: () => listPartReturns(),
    enabled: viewMode === 'returns',
  });
  const returnsSum = useQuery({
    queryKey: ['part-returns-summary'],
    queryFn: partReturnSummary,
    enabled: viewMode === 'returns',
  });
  const [settleRow, setSettleRow] = useState<PartReturn | null>(null);
  const [settleSaving, setSettleSaving] = useState(false);
  const [settleForm] = Form.useForm();

  async function submitSettle(values: { alipay_flow_no?: string; remark?: string }) {
    if (!settleRow) return;
    setSettleSaving(true);
    try {
      await settlePartReturn(settleRow.id, values);
      message.success('已标记结算');
      qc.invalidateQueries({ queryKey: ['part-returns'] });
      qc.invalidateQueries({ queryKey: ['part-returns-summary'] });
      setSettleRow(null);
      settleForm.resetFields();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '结算失败');
    } finally {
      setSettleSaving(false);
    }
  }

  // 结算弹窗打开且是退款单 → 拉疑似供应商退款流水候选
  const candidates = useQuery({
    queryKey: ['refund-candidates', settleRow?.id],
    queryFn: () => refundCandidates(settleRow!.id),
    enabled: settleRow !== null && settleRow.amount_kind === 'refund',
  });

  const [autoReconciling, setAutoReconciling] = useState(false);
  async function runAutoReconcile() {
    setAutoReconciling(true);
    try {
      const r = await autoReconcileReturns();
      message.success(
        r.matched > 0
          ? `自动对账成功 ${r.matched} 单`
          : '暂无可自动匹配的退款（需金额一致且供应商对得上）');
      qc.invalidateQueries({ queryKey: ['part-returns'] });
      qc.invalidateQueries({ queryKey: ['part-returns-summary'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '自动对账失败');
    } finally {
      setAutoReconciling(false);
    }
  }

  function setEdit(id: number, patch: { physical_qty?: number; locked_qty?: number }) {
    setEdits(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  async function saveRow(id: number) {
    const patch = edits[id];
    if (!patch) return;
    setSavingId(id);
    try {
      await updatePartInventory(id, patch);
      message.success('库存已更新');
      qc.invalidateQueries({ queryKey: ['part-inventory'] });
      setEdits(prev => { const n = { ...prev }; delete n[id]; return n; });
    } catch {
      message.error('保存失败');
    } finally {
      setSavingId(null);
    }
  }

  const addMut = useMutation({
    mutationFn: addPartInventoryRow,
    onSuccess: (res) => {
      if (res.material_created) {
        Modal.success({
          title: '自动建档定制物料',
          content: (
            <div>
              <p>
                录入名称在物料库中不存在，已自动分配编码{' '}
                <Tag color="orange">{res.material_code}</Tag>
              </p>
              <p>新建物料名：{res.material_name}</p>
              <p style={{ color: '#888' }}>
                价格 / 单位 / 尺寸类型已留空，请到「异常处理」页补全。
              </p>
            </div>
          ),
        });
      } else {
        message.success(`已加入 ${res.material_code} 的库存`);
      }
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['part-inventory'] });
      qc.invalidateQueries({ queryKey: ['materials'] });
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
    onError: (e: any) => {
      message.error(e?.response?.data?.detail ?? '录入失败');
    },
  });

  const columns = [
    { title: '仓库', dataIndex: 'warehouse', width: 110 },
    {
      title: '物料编码',
      dataIndex: 'material_code',
      width: 110,
      render: (v: string) =>
        v.startsWith('AC-') && parseInt(v.slice(3), 10) >= 1000 ? (
          <Tag color="orange">{v}</Tag>
        ) : (
          v
        ),
    },
    { title: '规格', dataIndex: 'spec', ellipsis: true },
    { title: '单位', dataIndex: 'unit', width: 70 },
    {
      title: '物理库存',
      dataIndex: 'physical_qty',
      width: 100,
      render: (v: number, row: PartInventory) => (
        <InputNumber
          size="small"
          min={0}
          value={edits[row.id]?.physical_qty ?? v}
          onChange={(val) => setEdit(row.id, { physical_qty: val ?? 0 })}
          style={{ width: 80 }}
        />
      ),
    },
    {
      title: '锁定',
      dataIndex: 'locked_qty',
      width: 90,
      render: (v: number, row: PartInventory) => (
        <InputNumber
          size="small"
          min={0}
          value={edits[row.id]?.locked_qty ?? v}
          onChange={(val) => setEdit(row.id, { locked_qty: val ?? 0 })}
          style={{ width: 70 }}
        />
      ),
    },
    { title: '可用', dataIndex: 'available_qty', width: 70 },
    {
      title: '待返厂',
      dataIndex: 'defective_qty',
      width: 80,
      render: (v: number) =>
        v > 0 ? <Tag color="volcano">{v}</Tag> : <span style={{ color: '#bbb' }}>0</span>,
    },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
    {
      title: '操作',
      width: 190,
      render: (_: unknown, row: PartInventory) => (
        <Space size={4}>
          {edits[row.id] !== undefined && (
            <Button size="small" type="primary" loading={savingId === row.id} onClick={() => saveRow(row.id)}>
              存
            </Button>
          )}
          <Button size="small" danger onClick={() => openDefect(row, 'mark')}>
            报坏件
          </Button>
          {row.defective_qty > 0 && (
            <Button size="small" onClick={() => openDefect(row, 'resolve')}>
              处理({row.defective_qty})
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <FirstVisitTip
        storageKey="part-inventory"
        title="配件库存录入指南"
        description={
          <ol style={{ marginBottom: 0 }}>
            <li>「物料名称」必填；如果库里没有该名字, 系统自动建一条「定制物料」(AC-1000+) 并写入异常表</li>
            <li>「物料编码」可空 — 选填用于精确指定既有物料 (如 AC-0172)</li>
            <li>定制物料价格 / 单位需要事后到「物料单价库」补全</li>
          </ol>
        }
      />
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          配件库存 (4b)
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          录入一行
        </Button>
      </Space>

      <Alert
        type="info"
        showIcon
        message="录入时如果物料名在「物料单价库」里没有，系统会自动建一条定制物料（AC-1000+），并把它丢进「异常处理」页等你补齐价格。"
      />

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'alert' | 'full' | 'returns')}
        options={[
          { label: '精选视图（可编辑）', value: 'curated' },
          { label: '智能预警', value: 'alert' },
          { label: '返厂台账', value: 'returns' },
          { label: '全部列', value: 'full' },
        ]}
      />

      {viewMode === 'alert' && (
        <>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
            预警依据导入的「日均消耗 / 提前期 / 滞销天数 / 安全库存」实时计算：可用量、库存天数、
            预警线、建议备货量。这些值你填得越全，预警越准。
          </Typography.Paragraph>
          <Table<PartInventoryStats>
            rowKey="id"
            loading={alerts.isLoading}
            dataSource={[...(alerts.data ?? [])].sort(
              (a, b) => (WARN_SEV[a.warning_status] ?? 9) - (WARN_SEV[b.warning_status] ?? 9))}
            pagination={{ pageSize: 20 }}
            size="middle"
            columns={[
              { title: '仓库', dataIndex: 'warehouse', width: 100 },
              { title: '物料编码', dataIndex: 'material_code', width: 110 },
              { title: '规格', dataIndex: 'spec', ellipsis: true },
              { title: '可用', dataIndex: 'available_qty', width: 70 },
              { title: '日均消耗', dataIndex: 'daily_sales', width: 90 },
              {
                title: '库存天数', dataIndex: 'days_of_stock', width: 90,
                render: (v: number | null) => (v == null ? '—' : v),
              },
              { title: '预警线', dataIndex: 'reorder_point_computed', width: 80 },
              {
                title: '预警状态', dataIndex: 'warning_status', width: 120,
                render: (v: string) => {
                  const m = WARN_META[v] ?? { label: v, color: 'default' };
                  return <Tag color={m.color}>{m.label}</Tag>;
                },
              },
              {
                title: '建议备货', dataIndex: 'auto_reorder_qty', width: 90,
                render: (v: number) => (v > 0 ? <b style={{ color: '#cf1322' }}>{v}</b> : '—'),
              },
            ] as any}
          />
        </>
      )}

      {viewMode === 'returns' && (
        <>
          <Space size="large" wrap>
            <Statistic title="待收退款" value={returnsSum.data?.pending_refund ?? 0}
                       precision={2} prefix="¥" valueStyle={{ color: '#cf1322' }} />
            <Statistic title="已收退款" value={returnsSum.data?.received_refund ?? 0}
                       precision={2} prefix="¥" valueStyle={{ color: '#3f8600' }} />
            <Statistic title="维修费合计" value={returnsSum.data?.repair_fee_total ?? 0}
                       precision={2} prefix="¥" />
            <Statistic title="报废损失" value={returnsSum.data?.scrap_loss_total ?? 0}
                       precision={2} prefix="¥" valueStyle={{ color: '#d48806' }} />
          </Space>
          <Space style={{ margin: '4px 0' }}>
            <Button onClick={runAutoReconcile} loading={autoReconciling}>
              一键自动对账（退款流水）
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              自动把「金额一致 + 供应商对得上」的支付宝退款流水结算到对应返厂单。
            </Typography.Text>
          </Space>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '8px 0' }}>
            「处理」坏件时填了金额就会进这里。退货退款默认「待收」，收到供应商退款后点「结算」并可填支付宝流水号对账。
          </Typography.Paragraph>
          <Table<PartReturn>
            rowKey="id"
            loading={returns.isLoading}
            dataSource={returns.data}
            pagination={{ pageSize: 20 }}
            size="middle"
            columns={[
              { title: '日期', dataIndex: 'processed_at', width: 110 },
              {
                title: '物料', width: 190,
                render: (_: unknown, r: PartReturn) => (
                  <Space direction="vertical" size={0}>
                    <span>{r.material_code}</span>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {r.material_name}
                    </Typography.Text>
                  </Space>
                ),
              },
              { title: '数量', dataIndex: 'qty', width: 60 },
              {
                title: '处置', dataIndex: 'disposition', width: 90,
                render: (v: string) => {
                  const m: Record<string, { l: string; c: string }> = {
                    returned: { l: '退货退款', c: 'volcano' },
                    repaired: { l: '返厂维修', c: 'blue' },
                    scrapped: { l: '报废', c: 'default' },
                  };
                  const x = m[v] ?? { l: v, c: 'default' };
                  return <Tag color={x.c}>{x.l}</Tag>;
                },
              },
              {
                title: '金额', dataIndex: 'amount', width: 90,
                render: (v: number | null) => (v == null ? '—' : `¥${v}`),
              },
              {
                title: '供应商', dataIndex: 'supplier', ellipsis: true,
                render: (v: string | null) => v ?? '—',
              },
              {
                title: '采购单', dataIndex: 'related_purchase_no', width: 120,
                render: (v: string | null) => v ?? '—',
              },
              {
                title: '状态', dataIndex: 'status', width: 90,
                render: (v: string) =>
                  v === 'open' ? <Tag color="orange">待收/待结</Tag> : <Tag color="green">已结算</Tag>,
              },
              {
                title: '流水号', dataIndex: 'alipay_flow_no', width: 120, ellipsis: true,
                render: (v: string | null) => v ?? '—',
              },
              {
                title: '操作', width: 80,
                render: (_: unknown, r: PartReturn) =>
                  r.status === 'open' ? (
                    <Button size="small" type="primary"
                            onClick={() => { setSettleRow(r); settleForm.resetFields(); }}>
                      结算
                    </Button>
                  ) : null,
              },
            ] as any}
          />
        </>
      )}

      {viewMode === 'full' && <FullColumnView entity="part_inventory" defaultShowAll />}

      {viewMode === 'curated' && (
      <Table<PartInventory>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
        size="middle"
      />
      )}

      <Modal
        title="录入一条配件库存"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={addMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => addMut.mutate(v)}
          initialValues={{ warehouse: '江西仓库', physical_qty: 1, locked_qty: 0 }}
        >
          <Form.Item name="warehouse" label="仓库" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="material_name"
            label="物料名称"
            tooltip="如果留空就必须填编码；如果填名字但物料库里没有，系统自动建定制物料"
          >
            <Input placeholder="例如：电力轨道-Xpower-T25-黑色-1.358-2插座" />
          </Form.Item>
          <Form.Item name="material_code" label="物料编码（选填）">
            <Input placeholder="如已知，例如 AC-0172" />
          </Form.Item>
          <Space style={{ width: '100%' }}>
            <Form.Item name="physical_qty" label="物理库存">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="locked_qty" label="锁定库存">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="unit" label="单位">
              <Input placeholder="如 条/个" />
            </Form.Item>
          </Space>
          <Form.Item name="spec" label="规格">
            <Input />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={defectMode === 'mark'
          ? `报坏件 — ${defectRow?.material_code ?? ''}`
          : `处理待返厂 — ${defectRow?.material_code ?? ''}`}
        open={defectRow !== null}
        onCancel={() => setDefectRow(null)}
        onOk={() => defectForm.submit()}
        confirmLoading={defectSaving}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={defectMode === 'mark'
            ? '坏件会从良品库移到「待返厂/维修中」，不再计入可用库存（全程留痕）。'
            : '修好/换新 → 回良品库；报废或退货退款 → 核销出库。'}
        />
        <Form
          form={defectForm}
          layout="vertical"
          onFinish={submitDefect}
          initialValues={{ qty: 1, reason: '到货不良', disposition: 'repaired' }}
        >
          <Form.Item
            name="qty"
            label={defectMode === 'mark'
              ? `数量（当前良品 ${defectRow?.physical_qty ?? 0}）`
              : `数量（待返厂 ${defectRow?.defective_qty ?? 0}）`}
            rules={[{ required: true, message: '请输入数量' }]}
          >
            <InputNumber
              min={1}
              max={defectMode === 'mark'
                ? Number(defectRow?.physical_qty ?? 0)
                : Number(defectRow?.defective_qty ?? 0)}
              style={{ width: '100%' }}
            />
          </Form.Item>
          {defectMode === 'mark' ? (
            <Form.Item name="reason" label="原因">
              <Select
                options={[
                  { value: '到货不良', label: '到货不良' },
                  { value: '使用中损坏', label: '使用中损坏' },
                  { value: '需返厂维修', label: '需返厂维修' },
                  { value: '需退货退款', label: '需退货退款' },
                ]}
              />
            </Form.Item>
          ) : (
            <>
              <Form.Item name="disposition" label="处理方式" rules={[{ required: true }]}>
                <Select
                  options={[
                    { value: 'repaired', label: '修好/换新 → 回良品库' },
                    { value: 'scrapped', label: '报废 → 核销' },
                    { value: 'returned', label: '退货退款 → 核销' },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="amount"
                label="金额（可选）"
                tooltip="退货退款=应收供应商退款；返厂维修=维修费；报废=损失金额。填了才进返厂台账对账"
              >
                <InputNumber min={0} style={{ width: '100%' }} addonAfter="元" />
              </Form.Item>
              <Form.Item name="supplier" label="供应商（可选）">
                <Input placeholder="退/返给哪个供应商" />
              </Form.Item>
              <Form.Item name="related_purchase_no" label="关联采购单（可选）">
                <Input placeholder="原采购单号，如 CG202600001" />
              </Form.Item>
              <Form.Item name="tracking_no" label="返厂快递单号（可选）">
                <Input placeholder="寄回供应商的快递单号" />
              </Form.Item>
            </>
          )}
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="如：返厂快递单号 / 供应商 / 退款金额" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`结算返厂单 — ${settleRow?.material_code ?? ''}`}
        open={settleRow !== null}
        onCancel={() => setSettleRow(null)}
        onOk={() => settleForm.submit()}
        confirmLoading={settleSaving}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={settleRow?.amount_kind === 'refund'
            ? `确认已收到供应商退款 ¥${settleRow?.amount ?? 0}？可填收款的支付宝流水号便于对账。`
            : '标记为已结算。'}
        />
        <Form form={settleForm} layout="vertical" onFinish={submitSettle}>
          {settleRow?.amount_kind === 'refund' && (candidates.data?.length ?? 0) > 0 && (
            <Form.Item label="疑似退款流水（点选自动填入）">
              <Select
                allowClear
                placeholder={candidates.isFetching ? '匹配中…' : '选择匹配到的退款流水'}
                options={(candidates.data ?? []).map((c) => ({
                  value: c.transaction_no,
                  label: `¥${c.amount} · ${c.counterparty ?? '—'} · ${c.reason}`,
                }))}
                onChange={(v) => settleForm.setFieldValue('alipay_flow_no', v)}
              />
            </Form.Item>
          )}
          <Form.Item name="alipay_flow_no" label="支付宝流水号（可选）">
            <Input placeholder="收到退款的那笔流水号" />
          </Form.Item>
          <Form.Item name="remark" label="备注（可选）">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
