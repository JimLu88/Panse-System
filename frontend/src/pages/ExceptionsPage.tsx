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
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AiDiagnoseResult,
  DataException,
  aiDiagnose,
  fixException,
  getExceptionsSummary,
  listExceptions,
  resolveException,
  resolveImportConflict,
  runAllScanners,
  runDataQuality,
} from '../api/client';

const severityColor: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  error: 'red',
};

const severityLabel: Record<string, string> = {
  info: '提示',
  warning: '警告',
  error: '严重',
};

const statusLabel: Record<string, string> = {
  open: '待处理',
  resolved: '已解决',
  ignored: '已忽略',
};

// 来源表英文 → 中文 (与飞书绑定一致, 额外含对账等虚拟来源)
const SOURCE_TABLE_LABELS: Record<string, string> = {
  products: '产品表', pricing_sku: '定价表', bom_lines: 'BOM 表', materials: '物料价格',
  product_inventory: '成品库存', part_inventory: '配件库存', orders: '销售订单',
  factory_orders: '工厂下单', factory_reconciliations: '工厂对账', alipay_flows: '支付宝流水',
  account_balances: '账户余额', wood_losses: '木材损耗', samples: '样品',
  brand_marketing: '品牌营销', promotion_flows: '推广记录', daily_operations: '日常经营',
  order_details: '订单细节', outsourcing_expenses: '人员外包', after_sales: '售后',
  customers: '客户', wanshifu_bills: '万师傅安装账单', logistics_bills: '物流费账单',
  refill_records: '补单对账', suppliers: '供应商', part_purchases: '配件采购',
  reconciliation: '财务对账',
};

// ============ 异常分类体系 (产品经理视角, 让非技术同事看得懂) ============
// 6 大类: 产品资料 / 订单 / 对账财务 / 库存 / 售后 / 系统导入
type CategoryKey = 'sync' | 'product' | 'order' | 'finance' | 'inventory' | 'aftersales' | 'system';

const CATEGORY_META: Record<CategoryKey, { label: string; color: string; desc: string }> = {
  sync: { label: '同步冲突', color: 'red', desc: '飞书与系统同一条数据两边都改过，需要你裁决以哪边为准' },
  product: { label: '产品资料问题', color: 'magenta', desc: '产品、物料或定价的资料有缺失或填错' },
  order: { label: '订单问题', color: 'blue', desc: '订单本身缺少必要信息（成本、物流、数量等）' },
  finance: { label: '对账 / 财务问题', color: 'gold', desc: '支付宝流水、工厂对账、外包费用对不上账' },
  inventory: { label: '库存问题', color: 'volcano', desc: '库存数量异常，如负库存、超卖' },
  aftersales: { label: '售后问题', color: 'purple', desc: '售后 / 退货数据缺失' },
  system: { label: '系统 / 导入问题', color: 'geekblue', desc: '数据导入、AI 核查、状态变更相关' },
};

// 每种异常: 归到哪一类 + 友好名称 + 一句话大白话(发生了什么 / 该去哪修)
type TypeMeta = { category: CategoryKey; label: string; hint: string };
const TYPE_META: Record<string, TypeMeta> = {
  // —— 产品资料问题 ——
  dangling_product_code: { category: 'product', label: '订单的产品编码在产品表里找不到',
    hint: '订单上填的产品编码，在「产品总表」里查不到对应产品。多半是产品还没建档，或编码填错了。请去产品页新建该产品，或修正订单里的编码。' },
  custom_material_missing_price: { category: 'product', label: '定制物料没填价格',
    hint: '这是定制类物料，但没有设置价格，会导致报价 / 成本算不出来。请去「物料表」补上该物料的价格。' },
  non_positive_price: { category: 'product', label: '物料价格 ≤ 0',
    hint: '物料价格是 0 或负数，应该是个正常单价。请去「物料表」修正该物料的价格。' },
  missing_material_autocreated: { category: 'product', label: '系统自动建了临时物料',
    hint: '导入时找不到对应物料，系统先建了个临时物料（编码 AC 开头占位）。请去「物料表」补全它的名称和价格。' },
  pricing_below_cost: { category: 'product', label: '定价低于成本（亏本）',
    hint: '这个 SKU 的售价比成本还低，卖出去就是亏的。请检查「定价」页的售价或成本是否填错。' },
  custom_sku_detected: { category: 'product', label: '识别到定制 SKU',
    hint: '系统判断这是一个定制款 SKU（仅提示）。核对无误后点「已处理」即可。' },
  unknown_product_code: { category: 'product', label: '库存里的产品编码不存在',
    hint: '库存表里的产品编码，在「产品总表」里查不到。请先建档该产品，或修正库存里的编码。' },

  // —— 订单问题 ——
  order_missing_cost: { category: 'order', label: '订单没填成本',
    hint: '这笔订单理论成本和实际成本都是空的，无法算毛利。点「补填」直接录入成本。' },
  order_missing_tracking: { category: 'order', label: '已发货订单缺物流单号',
    hint: '订单已发货但没填物流单号，客户查不到件。点「补填」录入承运商和物流单号。' },
  order_qty_invalid: { category: 'order', label: '订单数量 ≤ 0',
    hint: '订单数量是 0 或负数，明显不对。请去订单页修正数量。' },
  ship_before_order: { category: 'order', label: '发货日期早于下单日期',
    hint: '发货时间比下单时间还早，日期对不上。请核对该订单的下单 / 发货日期。' },
  signoff_questioned: { category: 'order', label: '订单签收存疑',
    hint: '物流确认和人工确认这两个核对环节没有同时完成。点「补填」补全物流信息并核对。' },
  autofill_missing_product_code: { category: 'order', label: '订单缺产品编码，无法生成工厂单',
    hint: '订单没有产品编码，系统没法自动生成工厂下单草稿。点「补填」补上产品编码。' },
  refill_unmatched: { category: 'order', label: '补单找不到对应主订单',
    hint: '补单记录里的订单号，在主订单表里匹配不到。点「补填」确认 / 修正关联订单号。' },

  // —— 对账 / 财务问题 ——
  order_missing_alipay: { category: 'finance', label: '订单缺支付宝收款流水',
    hint: '这笔订单找不到对应的支付宝收款记录，无法核实是否真收到钱。请去「支付宝流水」页关联对应流水。' },
  alipay_missing_txn: { category: 'finance', label: '支付宝流水缺交易号',
    hint: '这条支付宝流水没有交易流水号，无法对账。请去支付宝后台找到该笔，补填交易流水号。' },
  duplicate_alipay_flow: { category: 'finance', label: '支付宝流水跨账户重复',
    hint: '同一个交易流水号在「多个不同账户」里出现，可能重复入账。请合并或删除重复记录。（同一账户内同号的「在线支付＋分账」是正常配对，不算重复。）' },
  alipay_duplicate_flow: { category: 'finance', label: '支付宝流水疑似重复',
    hint: '同一账户里出现「同流水号＋同交易类型」的多条流水，可能是导入重跑或手工补录造成的重复，会把收入或手续费重复计一遍。请核对后删除多余的，仅保留一条。注意：同号但交易类型不同（在线支付＝收款 / 分账＝手续费）是正常配对，不会报这个异常。' },
  factory_recon_incomplete: { category: 'finance', label: '工厂对账单缺字段',
    hint: '工厂对账缺少账单金额 / 已付金额 / 支付宝流水号。点「补填」补全这些字段。' },
  factory_recon_unbalanced: { category: 'finance', label: '工厂对账不平（未付清 / 超付）',
    hint: '工厂账单金额和实际支付对不上：未付清＝账单大于实付（还欠工厂钱），超付＝实付大于账单（多付了）。请核对工厂账单与支付宝付款流水，补付 / 退回差额或登记说明。' },
  unclassified_purchase: { category: 'finance', label: '存疑采购（流水自动归类）',
    hint: '一笔支出流水对不上任何已知用途，系统先把它「猜」成了采购记录。请核对这笔钱的真实用途（采购 / 日常经营 / 外包 / 其它），修正归类或补全配件信息。' },
  outsourcing_missing: { category: 'finance', label: '外包费用缺字段',
    hint: '外包费用缺支付宝流水号或支付日期，无法核账。点「补填」补全。' },
  reconciliation_diff: { category: 'finance', label: '对账差异超阈值',
    hint: '对账时发现金额差异超过了允许范围。请用 AI 平滑或人工复核这笔差异。' },

  // —— 库存问题 ——
  negative_inventory: { category: 'inventory', label: '可用库存为负',
    hint: '可用库存（物理库存 − 锁定）算出来是负数，多半是出入库记反或漏记。请核对出入库单据。' },
  inventory_anomaly: { category: 'inventory', label: '库存数据异常',
    hint: '物理库存为负，或锁定数量超过了物理库存。请去库存页核对数量。' },

  // —— 售后问题 ——
  aftersales_empty: { category: 'aftersales', label: '售后表为空',
    hint: '系统里一条售后记录都没有。请在售后 / 退货页录入，或用 Excel 导入历史售后数据。' },

  // —— 系统 / 导入问题 ——
  import_conflict: { category: 'system', label: '表格导入冲突',
    hint: '重新导入 Excel 时，发现导入数据与系统已有记录的某些字段不一致。请对比新旧两个版本后，选择"采用新值"（使用导入数据）或"保留旧值"（不改动）。' },
  stale_import: { category: 'system', label: '导入数据太旧',
    hint: '已经很久没导入新订单了，大盘数据可能不是最新的。请去「导入」页上传最新的订单 Excel。' },
  forced_status_transition: { category: 'system', label: '订单状态被强制变更',
    hint: '有人绕过正常流程强制改了订单状态。请查审计日志确认这次变更是否合理。' },
  feishu_conflict: { category: 'sync', label: '飞书与系统数据冲突',
    hint: '飞书和系统里同一条数据不一样，需要你决定以哪边为准。点「去飞书裁决」处理。' },
  feishu_extra_field: { category: 'sync', label: '飞书表有多余的列',
    hint: '飞书表里有系统没有的列。点「去飞书裁决」选择删除该列（以系统为准）或保留。' },
  ai_logic_check: { category: 'system', label: 'AI 核查发现疑点',
    hint: 'AI 在导入后核查时发现了一处逻辑疑点。请人工复核确认。' },
};

const typeMeta = (t: string): TypeMeta =>
  TYPE_META[t] ?? { category: 'system', label: t, hint: '系统检测到一处数据异常，请人工核查后处理。' };
const typeLabel = (t: string) => typeMeta(t).label;
const SEVERITY_RANK: Record<string, number> = { error: 3, warning: 2, info: 1 };
const CATEGORY_ORDER: CategoryKey[] = ['sync', 'product', 'order', 'finance', 'inventory', 'aftersales', 'system'];

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

// 导入冲突详情展示: 对比旧值和新值
function ImportConflictDetail({ exc }: { exc: DataException }) {
  const ctx = exc.context as { diffs?: Array<{ field: string; old: unknown; new: unknown }> } | null;
  const diffs = ctx?.diffs ?? [];
  if (diffs.length === 0) return <Typography.Text type="secondary">无差异详情</Typography.Text>;
  return (
    <Table
      size="small"
      pagination={false}
      dataSource={diffs.map((d, i) => ({ ...d, key: i }))}
      columns={[
        { title: '字段', dataIndex: 'field', width: 160 },
        { title: '现有值（旧）', dataIndex: 'old', render: (v) => <span style={{ color: '#cf1322' }}>{v === null || v === undefined ? '—' : String(v)}</span> },
        { title: '导入值（新）', dataIndex: 'new', render: (v) => <span style={{ color: '#389e0d' }}>{v === null || v === undefined ? '—' : String(v)}</span> },
      ]}
    />
  );
}

export default function ExceptionsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [status, setStatus] = useState<'open' | 'resolved' | 'ignored'>('open');
  const [diagnoseOpen, setDiagnoseOpen] = useState<{ exc: DataException; result?: AiDiagnoseResult } | null>(null);
  const [fixOpen, setFixOpen] = useState<DataException | null>(null);
  const [conflictOpen, setConflictOpen] = useState<DataException | null>(null);
  const [fixForm] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ['exceptions', status],
    queryFn: () => listExceptions(status, 5000),
  });

  // 准确聚合 (GROUP BY, 不依赖明细是否被截断)
  const { data: summary } = useQuery({
    queryKey: ['exceptions-summary', status],
    queryFn: () => getExceptionsSummary(status),
  });

  // 顶栏角标只统计 warning/error (排除 info), 这里统计三档供页面对账, 解释"角标数 ≠ 页面数"的困惑
  const severityCount = useMemo(() => {
    const out = { info: 0, warning: 0, error: 0 };
    (data ?? []).forEach((e) => {
      out[e.severity as 'info' | 'warning' | 'error'] =
        (out[e.severity as 'info' | 'warning' | 'error'] ?? 0) + 1;
    });
    return out;
  }, [data]);

  const resolveMut = useMutation({
    mutationFn: ({ id, s }: { id: number; s: 'resolved' | 'ignored' }) =>
      resolveException(id, s),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['exceptions'] });
      qc.invalidateQueries({ queryKey: ['exceptions-summary'] });
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
      qc.invalidateQueries({ queryKey: ['exceptions-summary'] });
    },
  });

  const dqMut = useMutation({
    mutationFn: runDataQuality,
    onSuccess: (res) => {
      const total = Object.values(res).filter(v => v > 0).length;
      message.success(`数据完整性扫描完成，发现 ${total} 类问题`);
      qc.invalidateQueries({ queryKey: ['exceptions'] });
      qc.invalidateQueries({ queryKey: ['exceptions-summary'] });
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
      qc.invalidateQueries({ queryKey: ['exceptions-summary'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '补填失败'),
  });

  const conflictMut = useMutation({
    mutationFn: ({ id, choice }: { id: number; choice: 'new' | 'old' }) =>
      resolveImportConflict(id, choice),
    onSuccess: (_, vars) => {
      message.success(vars.choice === 'new' ? '已采用导入新值' : '已保留现有值');
      setConflictOpen(null);
      qc.invalidateQueries({ queryKey: ['exceptions'] });
      qc.invalidateQueries({ queryKey: ['exceptions-summary'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '操作失败'),
  });

  const handleDiagnose = (exc: DataException) => {
    setDiagnoseOpen({ exc });
    diagnoseMut.mutate(exc.id);
  };

  // 按异常类型分组
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

  // 再按 6 大类归拢: 每个分类下挂它的异常类型组
  const categories = useMemo(() => {
    const byCat = new Map<CategoryKey, typeof groups>();
    groups.forEach((g) => {
      const cat = typeMeta(g.type).category;
      const arr = byCat.get(cat) ?? [];
      arr.push(g);
      byCat.set(cat, arr);
    });
    return CATEGORY_ORDER
      .filter((c) => byCat.has(c))
      .map((c) => {
        const gs = byCat.get(c)!;
        return {
          key: c,
          meta: CATEGORY_META[c],
          groups: gs,
          total: gs.reduce((s, g) => s + g.items.length, 0),
          worst: gs.reduce((s, g) => Math.max(s, g.worst), 0),
        };
      });
  }, [groups]);

  const [activeKeys, setActiveKeys] = useState<string[]>([]);
  const allExpanded = groups.length > 0 && activeKeys.length === groups.length;
  const toggleAll = () => setActiveKeys(allExpanded ? [] : groups.map((g) => g.type));

  const columns = [
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 80,
      render: (v: string) => <Tag color={severityColor[v] ?? 'default'}>{severityLabel[v] ?? v}</Tag>,
    },
    { title: '来源表', dataIndex: 'source_table', width: 120,
      render: (v: string) => SOURCE_TABLE_LABELS[v] ?? v },
    {
      title: '主键',
      dataIndex: 'source_pk',
      width: 130,
      render: (v: string | null) => (v ? <code style={{ fontSize: 11 }}>{v}</code> : '-'),
    },
    {
      title: '异常类型', dataIndex: 'exception_type', width: 220,
      render: (t: string) => (
        <Space direction="vertical" size={0}>
          <span>{typeLabel(t)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>{t}</Typography.Text>
        </Space>
      ),
    },
    { title: '描述', dataIndex: 'description', ellipsis: false },
    {
      title: '操作',
      width: 230,
      render: (_: unknown, row: DataException) => {
        if (row.status !== 'open') {
          return <Tag color={row.status === 'resolved' ? 'green' : 'default'}>{statusLabel[row.status] ?? row.status}</Tag>;
        }
        // 表格导入冲突: 显示专用裁决按钮
        if (row.exception_type === 'import_conflict') {
          return (
            <Space size="small" wrap>
              <Button
                size="small"
                type="primary"
                onClick={() => setConflictOpen(row)}
              >
                查看差异并裁决
              </Button>
              <Button
                size="small"
                onClick={() => resolveMut.mutate({ id: row.id, s: 'ignored' })}
              >
                忽略
              </Button>
            </Space>
          );
        }
        // 飞书冲突 / 多余列: 解除必须走飞书设置页裁决, 不能内联补填, 否则两端不一致
        if (row.exception_type === 'feishu_conflict' || row.exception_type === 'feishu_extra_field') {
          return (
            <Space size="small" wrap>
              <Button size="small" type="primary" onClick={() => navigate('/feishu')}>
                去飞书裁决
              </Button>
              <Button size="small" icon={<RobotOutlined />} onClick={() => handleDiagnose(row)}>
                AI 分析
              </Button>
              <Button size="small" onClick={() => resolveMut.mutate({ id: row.id, s: 'ignored' })}>
                忽略
              </Button>
            </Space>
          );
        }
        return (
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
        );
      },
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

      {status === 'open' && !isLoading && (summary?.total ?? data?.length ?? 0) > 0 && (() => {
        const sev = summary?.by_severity ?? {};
        const total = summary?.total ?? data?.length ?? 0;
        const err = sev.error ?? severityCount.error;
        const warn = sev.warning ?? severityCount.warning;
        const info = sev.info ?? severityCount.info;
        const loaded = data?.length ?? 0;
        const truncated = total > loaded;
        return (
          <Alert
            type={truncated ? 'warning' : 'info'}
            showIcon
            message={
              <span>
                共 <b>{total}</b> 条未处理异常：
                error <b>{err}</b> · warning <b>{warn}</b> · info <b>{info}</b>
                {truncated && <>（本页已加载前 <b>{loaded}</b> 条明细）</>}
              </span>
            }
            description={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                顶栏红色角标只统计 <b>error + warning</b>（共 {err + warn} 条），
                不含 info 级提示，所以角标数会与本页总数不同。
                其中「订单缺成本 / 订单缺支付宝流水」会对每一笔历史订单各记一条，是大批量异常的主要来源；
                可用上方「数据完整性扫描」后按分组批量处理。
              </Typography.Text>
            }
            style={{ marginBottom: 4 }}
          />
        );
      })()}

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin tip="加载中..."><div style={{ minHeight: 40 }} /></Spin>
        </div>
      ) : groups.length === 0 ? (
        <Empty description={`没有${status === 'open' ? '未处理' : status === 'resolved' ? '已处理' : '已忽略'}的异常`} />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          {categories.map((cat) => (
            <div key={cat.key}>
              <Space style={{ marginBottom: 8 }} align="center">
                <Tag color={cat.meta.color} style={{ fontSize: 14, padding: '2px 10px', margin: 0 }}>
                  {cat.meta.label}
                </Tag>
                <Tag color={cat.worst >= 3 ? 'red' : cat.worst === 2 ? 'orange' : 'blue'}>
                  共 {cat.total} 条
                </Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {cat.meta.desc}
                </Typography.Text>
              </Space>
              <Collapse
                activeKey={activeKeys}
                onChange={(k) => setActiveKeys(k as string[])}
                items={cat.groups.map((g) => {
                  const meta = typeMeta(g.type);
                  const table = g.items[0]?.source_table;
                  return {
                    key: g.type,
                    label: (
                      <Space wrap>
                        <Badge status={g.worst >= 3 ? 'error' : g.worst === 2 ? 'warning' : 'processing'} />
                        <b>{meta.label}</b>
                        <Tag color={g.worst >= 3 ? 'red' : g.worst === 2 ? 'orange' : 'blue'}>{g.items.length}</Tag>
                        {table && <Tag>表: {table}</Tag>}
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{g.type}</Typography.Text>
                      </Space>
                    ),
                    children: (
                      <>
                        <Alert
                          type="info"
                          showIcon
                          style={{ marginBottom: 12 }}
                          message="这是什么问题"
                          description={meta.hint}
                        />
                        <Table<DataException>
                          rowKey="id"
                          dataSource={g.items}
                          columns={panelColumns as any}
                          pagination={g.items.length > 20 ? { pageSize: 20 } : false}
                          size="small"
                        />
                      </>
                    ),
                  };
                })}
              />
            </div>
          ))}
        </Space>
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
      {/* 导入冲突裁决弹窗 */}
      <Modal
        title="表格导入冲突裁决"
        open={!!conflictOpen}
        onCancel={() => setConflictOpen(null)}
        footer={null}
        width={700}
        destroyOnClose
      >
        {conflictOpen && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Alert
              type="warning"
              showIcon
              message={`来源: ${SOURCE_TABLE_LABELS[conflictOpen.source_table] ?? conflictOpen.source_table} / ${conflictOpen.source_pk}`}
              description="以下字段在导入数据与现有记录之间存在差异，请选择保留哪个版本。"
            />
            <ImportConflictDetail exc={conflictOpen} />
            <Space style={{ marginTop: 16 }}>
              <Button
                type="primary"
                loading={conflictMut.isPending}
                onClick={() => conflictMut.mutate({ id: conflictOpen.id, choice: 'new' })}
              >
                采用新值（使用导入数据）
              </Button>
              <Button
                loading={conflictMut.isPending}
                onClick={() => conflictMut.mutate({ id: conflictOpen.id, choice: 'old' })}
              >
                保留旧值（不改动）
              </Button>
            </Space>
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
