import {
  Alert,
  Badge,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { DownOutlined, EditOutlined, RobotOutlined, ThunderboltOutlined, UpOutlined } from '@ant-design/icons';
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AiDiagnoseResult,
  DataException,
  aiDiagnose,
  fixException,
  listExceptions,
  resolveException,
  runAllScanners,
  runDataQuality,
} from '../api/client';

const severityColor: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  error: 'red',
};

// 异常类型 → 友好分类名 (按类别分组用)
const TYPE_LABELS: Record<string, string> = {
  order_missing_cost: '订单缺成本',
  order_missing_alipay: '订单缺支付宝流水',
  order_missing_tracking: '订单缺物流单号',
  stale_import: '导入数据过旧',
  refill_unmatched: '补单无法匹配主订单',
  alipay_missing_txn: '支付宝缺流水号',
  factory_recon_incomplete: '工厂对账缺字段',
  outsourcing_missing: '外包费用缺字段',
  aftersales_empty: '售后表为空',
  signoff_questioned: '订单签收存疑',
  feishu_conflict: '飞书数据冲突',
  reconciliation_diff: '对账差异',
  autofill_missing_product_code: '自动生成缺产品编码',
  forced_status_transition: '强制状态变更',
};
const typeLabel = (t: string) => TYPE_LABELS[t] ?? t;
const SEVERITY_RANK: Record<string, number> = { error: 3, warning: 2, info: 1 };

// 根据异常类型渲染不同的补填字段
function FixFormFields({ exc }: { exc: DataException }) {
  const t = exc.exception_type;
  if (t === 'order_missing_cost') return (
    <>
      <Form.Item name="theoretical_cost" label="理论成本 (¥)"><InputNumber style={{ width: '100%' }} min={0} step={0.01} /></Form.Item>
      <Form.Item name="actual_cost" label="实际成本 (¥)"><InputNumber style={{ width: '100%' }} min={0} step={0.01} /></Form.Item>
    </>
  );
  if (t === 'order_missing_alipay') return (
    <Form.Item name="remark" label="备注 (临时标注流水号)"><Input /></Form.Item>
  );
  if (t === 'order_missing_tracking') return (
    <>
      <Form.Item name="carrier" label="承运商"><Input placeholder="顺丰 / 京东 / 德邦" /></Form.Item>
      <Form.Item name="tracking_no" label="物流单号" rules={[{ required: true }]}><Input /></Form.Item>
    </>
  );
  if (t === 'alipay_missing_txn') return (
    <Form.Item name="transaction_no" label="交易流水号" rules={[{ required: true }]}><Input /></Form.Item>
  );
  if (t === 'factory_recon_incomplete') return (
    <>
      <Form.Item name="bill_amount" label="工厂账单金额"><InputNumber style={{ width: '100%' }} step={0.01} /></Form.Item>
      <Form.Item name="paid_amount" label="实际支付金额"><InputNumber style={{ width: '100%' }} step={0.01} /></Form.Item>
      <Form.Item name="alipay_flow_no" label="支付宝流水号"><Input /></Form.Item>
    </>
  );
  if (t === 'outsourcing_missing') return (
    <>
      <Form.Item name="alipay_flow_no" label="支付宝流水号"><Input /></Form.Item>
      <Form.Item name="payment_date" label="支付日期"><Input type="date" /></Form.Item>
    </>
  );
  if (t === 'stale_import') return (
    <Alert type="warning" message="导入时间过旧" description="请前往「截图录单」或 Excel 导入页面，导入最新的订单数据后此异常将自动消除。" showIcon />
  );
  if (t === 'refill_unmatched') return (
    <>
      <Alert type="info" message="补单记录字段缺失或无法匹配主订单" style={{ marginBottom: 8 }} showIcon />
      <Form.Item name="order_no" label="关联订单号"><Input placeholder="主订单号" /></Form.Item>
      <Form.Item name="product_code" label="产品编码"><Input placeholder="如 P001" /></Form.Item>
    </>
  );
  if (t === 'aftersales_empty') return (
    <Alert type="info" message="售后表为空" description="请通过 Excel 导入上传售后记录（aftersales 标签页），导入后此异常将自动消除。" showIcon />
  );
  if (t === 'signoff_questioned') return (
    <>
      <Alert type="warning" message="订单签收存疑，请核对物流信息后填写" style={{ marginBottom: 8 }} showIcon />
      <Form.Item name="carrier" label="承运商"><Input placeholder="顺丰 / 京东 / 德邦" /></Form.Item>
      <Form.Item name="tracking_no" label="物流单号" rules={[{ required: true }]}><Input /></Form.Item>
    </>
  );
  if (t === 'autofill_missing_product_code') return (
    <>
      <Alert type="info" message="缺产品编码，无法自动生成工厂下单草稿" style={{ marginBottom: 8 }} showIcon />
      <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]}><Input placeholder="如 P001" /></Form.Item>
      <Form.Item name="product_name" label="产品名称（选填）"><Input /></Form.Item>
    </>
  );
  // 通用: 显示提示
  return (
    <Alert type="info" message="此异常类型无内联补填模板，请手动前往对应页面修改后点「已处理」。" />
  );
}

export default function ExceptionsPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<'open' | 'resolved' | 'ignored'>('open');
  const [diagnoseOpen, setDiagnoseOpen] = useState<{ exc: DataException; result?: AiDiagnoseResult } | null>(null);
  const [fixOpen, setFixOpen] = useState<DataException | null>(null);
  const [fixForm] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ['exceptions', status],
    queryFn: () => listExceptions(status),
  });

  const resolveMut = useMutation({
    mutationFn: ({ id, s }: { id: number; s: 'resolved' | 'ignored' }) =>
      resolveException(id, s),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  const diagnoseMut = useMutation({
    mutationFn: (id: number) => aiDiagnose(id),
    onSuccess: (result) => {
      setDiagnoseOpen((prev) => (prev ? { ...prev, result } : null));
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'AI 调用失败'),
  });

  const scanMut = useMutation({
    mutationFn: () => runAllScanners(false),
    onSuccess: (res) => {
      const total = Object.values(res).reduce((s, r) => s + r.written, 0);
      const skipped = Object.values(res).reduce((s, r) => s + r.skipped_duplicate, 0);
      message.success(`扫描完成：新增 ${total} 条，去重 ${skipped} 条`);
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  const dqMut = useMutation({
    mutationFn: runDataQuality,
    onSuccess: (res) => {
      const total = Object.values(res).filter(v => v > 0).length;
      message.success(`数据完整性扫描完成，发现 ${total} 类问题`);
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  const fixMut = useMutation({
    mutationFn: ({ id, fields }: { id: number; fields: Record<string, unknown> }) =>
      fixException(id, fields),
    onSuccess: () => {
      message.success('已补填并解除异常');
      setFixOpen(null);
      fixForm.resetFields();
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '补填失败'),
  });

  const handleDiagnose = (exc: DataException) => {
    setDiagnoseOpen({ exc });
    diagnoseMut.mutate(exc.id);
  };

  // 按异常类型分类
  const groups = useMemo(() => {
    const m = new Map<string, DataException[]>();
    (data ?? []).forEach((e) => {
      const arr = m.get(e.exception_type) ?? [];
      arr.push(e);
      m.set(e.exception_type, arr);
    });
    return Array.from(m.entries())
      .map(([type, items]) => ({
        type,
        items,
        worst: items.reduce((s, e) => Math.max(s, SEVERITY_RANK[e.severity] ?? 0), 0),
      }))
      .sort((a, b) => b.worst - a.worst || b.items.length - a.items.length);
  }, [data]);

  const [activeKeys, setActiveKeys] = useState<string[]>([]);
  const allExpanded = groups.length > 0 && activeKeys.length === groups.length;
  const toggleAll = () => setActiveKeys(allExpanded ? [] : groups.map((g) => g.type));

  const columns = [
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 80,
      render: (v: string) => <Tag color={severityColor[v] ?? 'default'}>{v}</Tag>,
    },
    { title: '来源表', dataIndex: 'source_table', width: 120 },
    {
      title: '主键',
      dataIndex: 'source_pk',
      width: 130,
      render: (v: string | null) => (v ? <code style={{ fontSize: 11 }}>{v}</code> : '-'),
    },
    { title: '异常类型', dataIndex: 'exception_type', width: 220 },
    { title: '描述', dataIndex: 'description', ellipsis: false },
    {
      title: '操作',
      width: 230,
      render: (_: unknown, row: DataException) =>
        row.status === 'open' ? (
          <Space size="small" wrap>
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => { setFixOpen(row); fixForm.resetFields(); }}
            >
              补填
            </Button>
            <Button
              size="small"
              icon={<RobotOutlined />}
              onClick={() => handleDiagnose(row)}
            >
              AI 分析
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={() => resolveMut.mutate({ id: row.id, s: 'resolved' })}
            >
              已处理
            </Button>
            <Button
              size="small"
              onClick={() => resolveMut.mutate({ id: row.id, s: 'ignored' })}
            >
              忽略
            </Button>
          </Space>
        ) : (
          <Tag color={row.status === 'resolved' ? 'green' : 'default'}>{row.status}</Tag>
        ),
    },
  ];

  // 分组面板内不再重复显示「异常类型」列 (面板标题已是分类)
  const panelColumns = columns.filter((c: any) => c.dataIndex !== 'exception_type');

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          异常处理 (Phase 3.5)
        </Typography.Title>
        <Space>
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => dqMut.mutate()}
            loading={dqMut.isPending}
          >
            数据完整性扫描
          </Button>
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => scanMut.mutate()}
            loading={scanMut.isPending}
          >
            全量扫描
          </Button>
          <Button
            icon={allExpanded ? <UpOutlined /> : <DownOutlined />}
            onClick={toggleAll}
            disabled={groups.length === 0}
          >
            {allExpanded ? '全部折叠' : '全部展开'}
          </Button>
          <Segmented
            value={status}
            onChange={(v) => setStatus(v as typeof status)}
            options={[
              { label: '未处理', value: 'open' },
              { label: '已处理', value: 'resolved' },
              { label: '已忽略', value: 'ignored' },
            ]}
          />
        </Space>
      </Space>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin tip="加载中..."><div style={{ minHeight: 40 }} /></Spin>
        </div>
      ) : groups.length === 0 ? (
        <Empty description={`没有${status === 'open' ? '未处理' : status === 'resolved' ? '已处理' : '已忽略'}的异常`} />
      ) : (
        <Collapse
          activeKey={activeKeys}
          onChange={(k) => setActiveKeys(k as string[])}
          items={groups.map((g) => ({
            key: g.type,
            label: (
              <Space>
                <Badge status={g.worst >= 3 ? 'error' : g.worst === 2 ? 'warning' : 'processing'} />
                <b>{typeLabel(g.type)}</b>
                <Tag color={g.worst >= 3 ? 'red' : g.worst === 2 ? 'orange' : 'blue'}>{g.items.length}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{g.type}</Typography.Text>
              </Space>
            ),
            children: (
              <Table<DataException>
                rowKey="id"
                dataSource={g.items}
                columns={panelColumns as any}
                pagination={g.items.length > 20 ? { pageSize: 20 } : false}
                size="small"
              />
            ),
          }))}
        />
      )}

      <Modal
        title={
          <Space>
            <RobotOutlined />
            <span>AI 诊断 — 异常 #{diagnoseOpen?.exc.id}</span>
          </Space>
        }
        open={!!diagnoseOpen}
        onCancel={() => setDiagnoseOpen(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setDiagnoseOpen(null)}>
            关闭
          </Button>,
        ]}
        width={700}
      >
        {diagnoseOpen && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Alert
              type="info"
              message={diagnoseOpen.exc.description}
              description={
                <Space size="small" wrap>
                  <Tag color={severityColor[diagnoseOpen.exc.severity]}>{diagnoseOpen.exc.severity}</Tag>
                  <Tag>{diagnoseOpen.exc.source_table}</Tag>
                  <code>{diagnoseOpen.exc.source_pk}</code>
                </Space>
              }
            />
            {diagnoseMut.isPending ? (
              <div style={{ textAlign: 'center', padding: 24 }}>
                <Spin tip="AI 分析中...">
                  <div style={{ minHeight: 40 }} />
                </Spin>
              </div>
            ) : diagnoseOpen.result ? (
              diagnoseOpen.result.error ? (
                <Alert type="warning" showIcon message="AI 暂不可用" description={diagnoseOpen.result.error} />
              ) : (
                <>
                  <div
                    style={{
                      whiteSpace: 'pre-wrap',
                      background: '#f7f7f7',
                      padding: 12,
                      borderRadius: 6,
                    }}
                  >
                    {diagnoseOpen.result.text}
                  </div>
                  <div style={{ fontSize: 12, color: '#999' }}>
                    模型: {diagnoseOpen.result.model} · in={diagnoseOpen.result.input_tokens}
                    {' '}out={diagnoseOpen.result.output_tokens}
                    {' '}cache_read={diagnoseOpen.result.cache_read_tokens}
                  </div>
                </>
              )
            ) : null}
          </Space>
        )}
      </Modal>
      {/* 内联补填弹窗 */}
      <Modal
        title={<span><EditOutlined style={{ marginRight: 6 }} />补填数据 — 异常 #{fixOpen?.id}</span>}
        open={!!fixOpen}
        onCancel={() => { setFixOpen(null); fixForm.resetFields(); }}
        onOk={() => fixForm.submit()}
        confirmLoading={fixMut.isPending}
        destroyOnClose
        width={520}
      >
        {fixOpen && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="来源">{fixOpen.source_table} / {fixOpen.source_pk}</Descriptions.Item>
              <Descriptions.Item label="问题">{fixOpen.description}</Descriptions.Item>
              <Descriptions.Item label="建议">{fixOpen.suggestion_action}</Descriptions.Item>
            </Descriptions>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              在下方填写要修正的字段值（字段名必须与系统字段一致）
            </Typography.Text>
            <Form
              form={fixForm}
              layout="vertical"
              onFinish={(vals) => {
                const ctx = fixOpen.context as Record<string, unknown> | null;
                // 动态生成补填表单基于 exception_type
                fixMut.mutate({ id: fixOpen.id, fields: vals });
              }}
            >
              <FixFormFields exc={fixOpen} />
            </Form>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
