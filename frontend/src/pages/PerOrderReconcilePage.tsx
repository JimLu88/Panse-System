/**
 * 逐单核对 (财务) — 某月每笔真实成交订单的完整成本拆解 + 支付宝覆盖/对账状态 + 问题单高亮。
 * 方案1(完整明细宽表) + 方案5(问题单高亮) 合并。合计再减推广费、人员成本 = 本月真实净利。
 * 口径与「经营状况」一致 (order_financials 会计成本)。用户拍板 2026-06-18。
 */
import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Input, InputNumber, Modal, Segmented, Select, Space, Statistic,
  Switch, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { DeleteOutlined, FileExcelOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import RefillCallout from '../components/RefillCallout';
import {
  FixedCostItem, PerOrderRow, fetchPerOrderReconcile, getFixedCostItems, putFixedCostItems,
  downloadPerOrderReconcile, downloadPerOrderReconcileAll,
} from '../api/operations';
import ResponsiveTable from '../components/ResponsiveTable';
import { MetricCard } from '../components/MobileCards';

const { Title, Text } = Typography;

const yuan = (v: number | null | undefined) =>
  v == null ? '—' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const yuan2 = (v: number | null | undefined) =>
  v == null ? '—' : `¥${Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// 2026 年 1 月到当前月
function monthOptions(): string[] {
  const out: string[] = [];
  const now = dayjs();
  let d = dayjs('2026-01-01');
  while (d.isBefore(now) || d.isSame(now, 'month')) {
    out.push(d.format('YYYY-MM'));
    d = d.add(1, 'month');
  }
  return out.reverse();
}

export default function PerOrderReconcilePage() {
  const opts = useMemo(monthOptions, []);
  const [period, setPeriod] = useState<string>(opts[0] ?? '2026-06');
  const [onlyProblem, setOnlyProblem] = useState(false);
  const [searchInput, setSearchInput] = useState(''); // 输入框(未应用)
  const [productSearch, setProductSearch] = useState(''); // 已应用的产品搜索 (2026-07-03)
  const [y, m] = period.split('-').map(Number);

  const { data, isLoading } = useQuery({
    queryKey: ['per-order-reconcile', period, productSearch],
    queryFn: () => fetchPerOrderReconcile(y, m, productSearch),
  });

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    return onlyProblem ? all.filter((r) => r.is_loss || !r.alipay_covered) : all;
  }, [data, onlyProblem]);

  const st = data?.subtotal;

  // 固定成本/管理费用 自定义编辑
  const qc = useQueryClient();
  const [fixedOpen, setFixedOpen] = useState(false);
  const [draft, setDraft] = useState<FixedCostItem[]>([]);
  const openFixed = async () => {
    try { const r = await getFixedCostItems(); setDraft(r.items); } catch { setDraft([]); }
    setFixedOpen(true);
  };
  const saveFixed = useMutation({
    mutationFn: () => putFixedCostItems(draft.filter((i) => i.name.trim())),
    onSuccess: () => {
      message.success('固定成本已保存');
      setFixedOpen(false);
      qc.invalidateQueries({ queryKey: ['per-order-reconcile'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });
  const setItem = (i: number, patch: Partial<FixedCostItem>) =>
    setDraft((d) => d.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));

  // 商品成本悬浮: 计算公式 + 本单实际分项拆解 (推演 / 仅木作入账 / 片段封顶 / 无定价兜底)
  const costCell = () => (v: number, r: PerOrderRow) => {
    const reconciled = r.factory_bill_recorded;                 // 工厂账单已入账=已对账
    const wood = reconciled ? r.actual_wood : r.predicted_wood; // 已对账用实际木作, 否则定价木作
    const parts = r.est_parts;
    const pack = r.est_packaging;
    const hasPricing = wood != null || parts != null || pack != null;
    const w = wood ?? 0, p = parts ?? 0, k = pack ?? 0;
    const freightInstall = Math.round((v - (w + p + k)) * 100) / 100;  // 物流+安装(嵌在物理成本里, 反推)
    let body: JSX.Element;
    if (!hasPricing) {
      body = <div>无定价匹配 → 按「实付 × 类目成本率」兜底估算 = <b>{yuan(v)}</b></div>;
    } else if ((w + p + k) > v + 0.5) {
      body = <div>差价/定金小单(实付 &lt; 成本×50%)→ 按「实付 × 85%」封顶 = <b>{yuan(v)}</b></div>;
    } else {
      body = (
        <div>
          {reconciled ? '实际木作(工厂账单)' : '木作(定价表)'} {yuan(w)}<br />
          + 配件 {yuan(p)} + 打包 {yuan(k)} + 物流安装 {yuan(freightInstall)}<br />
          = <b>{yuan(v)}</b>
        </div>
      );
    }
    const tip = (
      <div style={{ fontSize: 12, lineHeight: 1.7, maxWidth: 300 }}>
        <div style={{ marginBottom: 4 }}>
          <b>{reconciled ? '商品成本 = 实际木作 + 非木作估算' : '商品成本 = 定价表物理总成本(推演)'}</b>
        </div>
        {body}
        <div style={{ color: '#8c8c8c', marginTop: 6 }}>
          工厂账单只含木作; 配件/打包/物流/安装恒按定价表估算。实付&lt;成本×50% 按实付×85%封顶。
          {r.cost_estimated ? ' 本单工厂未对账=全推演(蓝色)。' : ' 本单木作已对工厂账单。'}
        </div>
      </div>
    );
    return <Tooltip title={tip}><span style={{ color: r.cost_estimated ? '#1677ff' : undefined }}>{yuan(v)}</span></Tooltip>;
  };

  const cols: ColumnsType<PerOrderRow> = [
    { title: '订单号', dataIndex: 'order_no', width: 150, fixed: 'left',
      render: (v: string, r) => (
        <Space size={2}>
          <Text copyable={{ text: v }} style={{ fontSize: 11 }}>{v.length > 14 ? v.slice(0, 14) + '…' : v}</Text>
          {r.is_custom && <Tag color="purple" style={{ marginInlineEnd: 0, padding: '0 3px', fontSize: 10 }}>定制</Tag>}
        </Space>
      ) },
    { title: '产品', dataIndex: 'product_name', width: 130, ellipsis: true,
      render: (v: string) => <Tooltip title={v}><span style={{ fontSize: 12 }}>{v || '—'}</span></Tooltip> },
    { title: '订单金额', dataIndex: 'paid_amount', width: 90, align: 'right', render: yuan },
    { title: '退款', dataIndex: 'refund_amount', width: 75, align: 'right',
      render: (v: number) => v > 0 ? <Text type="warning">−{yuan(v)}</Text> : '—' },
    { title: '真实收入', dataIndex: 'revenue', width: 90, align: 'right',
      render: (v: number) => <Text strong>{yuan(v)}</Text> },
    { title: '商品成本', dataIndex: 'cost_goods', width: 90, align: 'right', render: costCell() },
    { title: '物流', dataIndex: 'cost_freight', width: 70, align: 'right', render: (v: number) => yuan(v) },
    { title: '安装', dataIndex: 'cost_install', width: 70, align: 'right', render: (v: number) => yuan(v) },
    { title: '平台扣点', dataIndex: 'cost_platform', width: 80, align: 'right', render: (v: number) => yuan(v) },
    { title: '税', dataIndex: 'cost_tax', width: 65, align: 'right', render: (v: number) => yuan(v) },
    { title: '售后', dataIndex: 'cost_aftersales', width: 70, align: 'right',
      render: (v: number) => v > 0 ? yuan(v) : '—' },
    { title: '成本合计', dataIndex: 'cost_total', width: 95, align: 'right',
      render: (v: number) => <Text strong>{yuan(v)}</Text> },
    { title: '净利', dataIndex: 'net_profit', width: 90, align: 'right',
      render: (v: number) => <Text strong type={v >= 0 ? 'success' : 'danger'}>{yuan(v)}</Text> },
    { title: '净利率', dataIndex: 'net_margin', width: 70, align: 'right',
      render: (v: number) => <span style={{ color: v >= 15 ? '#52c41a' : v >= 0 ? '#fa8c16' : '#ff4d4f' }}>{Number(v).toFixed(1)}%</span> },
    { title: '支付宝', dataIndex: 'alipay_covered', width: 75, align: 'center',
      render: (v: boolean) => v
        ? <Tag color="green" style={{ marginInlineEnd: 0 }}>已覆盖</Tag>
        : <Tag color="red" style={{ marginInlineEnd: 0 }}>未覆盖</Tag> },
    { title: '对账', dataIndex: 'cost_reconciled', width: 100, align: 'center',
      render: (v: boolean) => v
        ? <Tooltip title="仅木作已对工厂账单; 配件/打包/物流/安装仍为定价表预估">
            <Tag color="green" style={{ marginInlineEnd: 0 }}>仅木作入账</Tag></Tooltip>
        : <Tag color="blue" style={{ marginInlineEnd: 0 }}>推演</Tag> },
    {
      title: '工厂成本核对(预算 vs 实际,账单仅木作)',
      children: [
        { title: '工厂账单', dataIndex: 'factory_bill_recorded', width: 80, align: 'center',
          render: (v: boolean) => v
            ? <Tag color="green" style={{ marginInlineEnd: 0 }}>已入账</Tag>
            : <Text type="secondary" style={{ fontSize: 11 }}>未入账</Text> },
        { title: '预算木作', dataIndex: 'predicted_wood', width: 80, align: 'right',
          render: (v: number | null) => v == null ? <Text type="secondary">—</Text> : yuan(v) },
        { title: '预估配件', dataIndex: 'est_parts', width: 75, align: 'right',
          render: (v: number | null) => v == null ? <Text type="secondary">—</Text>
            : <Tooltip title="定价表预估(工厂账单不含配件)"><span style={{ color: '#8c8c8c' }}>{yuan(v)}</span></Tooltip> },
        { title: '预估打包', dataIndex: 'est_packaging', width: 75, align: 'right',
          render: (v: number | null) => v == null ? <Text type="secondary">—</Text>
            : <Tooltip title="定价表预估(工厂账单不含打包)"><span style={{ color: '#8c8c8c' }}>{yuan(v)}</span></Tooltip> },
        { title: '实际木作', dataIndex: 'actual_wood', width: 85, align: 'right',
          render: (v: number | null) => v == null
            ? <Tooltip title="工厂账单未入账"><Text type="secondary">—</Text></Tooltip>
            : <Text strong style={{ color: '#389e0d' }}>{yuan(v)}</Text> },
        { title: '木作差额', dataIndex: 'wood_diff', width: 90, align: 'right',
          render: (v: number | null) => v == null ? <Text type="secondary">—</Text>
            : <Tooltip title="实际工厂木作 − 预算木作; 正(红)=工厂报价超预算, 负(绿)=省">
                <Text strong type={v > 0 ? 'danger' : 'success'}>{v > 0 ? '+' : ''}{yuan(v)}</Text>
              </Tooltip> },
      ],
    },
    { title: '问题', width: 90, fixed: 'right',
      render: (_: unknown, r) => (
        <Space size={2} wrap>
          {r.is_loss && <Tag color="red" style={{ marginInlineEnd: 0 }}>亏损</Tag>}
          {!r.alipay_covered && <Tag color="orange" style={{ marginInlineEnd: 0 }}>未覆盖</Tag>}
        </Space>
      ) },
  ];

  // 合计行: 各列求和 (取 subtotal)
  const sumRow = st && (
    <Table.Summary fixed>
      <Table.Summary.Row style={{ background: '#fafafa', fontWeight: 600 }}>
        <Table.Summary.Cell index={0}>合计 {rows.length} 单</Table.Summary.Cell>
        <Table.Summary.Cell index={1} />
        <Table.Summary.Cell index={2} align="right">{yuan(st.paid_amount)}</Table.Summary.Cell>
        <Table.Summary.Cell index={3} align="right">{st.refund_amount > 0 ? `−${yuan(st.refund_amount)}` : '—'}</Table.Summary.Cell>
        <Table.Summary.Cell index={4} align="right">{yuan(st.revenue)}</Table.Summary.Cell>
        <Table.Summary.Cell index={5} align="right">{yuan(st.cost_goods)}</Table.Summary.Cell>
        <Table.Summary.Cell index={6} align="right">{yuan(st.cost_freight)}</Table.Summary.Cell>
        <Table.Summary.Cell index={7} align="right">{yuan(st.cost_install)}</Table.Summary.Cell>
        <Table.Summary.Cell index={8} align="right">{yuan(st.cost_platform)}</Table.Summary.Cell>
        <Table.Summary.Cell index={9} align="right">{yuan(st.cost_tax)}</Table.Summary.Cell>
        <Table.Summary.Cell index={10} align="right">{yuan(st.cost_aftersales)}</Table.Summary.Cell>
        <Table.Summary.Cell index={11} align="right">{yuan(st.cost_total)}</Table.Summary.Cell>
        <Table.Summary.Cell index={12} align="right">
          <Text strong type={st.net_profit >= 0 ? 'success' : 'danger'}>{yuan(st.net_profit)}</Text>
        </Table.Summary.Cell>
        <Table.Summary.Cell index={13} />
        <Table.Summary.Cell index={14} />
        <Table.Summary.Cell index={15} />
        <Table.Summary.Cell index={16} />
        <Table.Summary.Cell index={17} align="right">{yuan(st.predicted_wood)}</Table.Summary.Cell>
        <Table.Summary.Cell index={18} align="right">{yuan(st.est_parts)}</Table.Summary.Cell>
        <Table.Summary.Cell index={19} align="right">{yuan(st.est_packaging)}</Table.Summary.Cell>
        <Table.Summary.Cell index={20} align="right">
          <Text strong style={{ color: '#389e0d' }}>{yuan(st.actual_wood)}</Text>
        </Table.Summary.Cell>
        <Table.Summary.Cell index={21} align="right">
          <Text strong type={st.wood_diff > 0 ? 'danger' : 'success'}>{st.wood_diff > 0 ? '+' : ''}{yuan(st.wood_diff)}</Text>
        </Table.Summary.Cell>
        <Table.Summary.Cell index={22} />
      </Table.Summary.Row>
    </Table.Summary>
  );

  return (
    <div style={{ padding: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={4} style={{ margin: 0 }}>逐单核对</Title>
        <Space wrap>
          <Segmented value={onlyProblem ? 'problem' : 'all'}
            onChange={(v) => setOnlyProblem(v === 'problem')}
            options={[{ label: '全部', value: 'all' }, { label: '只看问题单', value: 'problem' }]} />
          <Select value={period} style={{ width: 130 }} onChange={setPeriod}
            options={opts.map((p) => ({ label: p, value: p }))} />
          <Input.Search
            placeholder="搜产品名/SKU/产品编码"
            allowClear enterButton style={{ width: 240 }}
            value={searchInput}
            onChange={(e) => { setSearchInput(e.target.value); if (!e.target.value) setProductSearch(''); }}
            onSearch={(v) => setProductSearch(v.trim())}
          />
          <Button icon={<FileExcelOutlined />} disabled={!data}
            onClick={async () => {
              const [yy, mo] = period.split('-').map(Number);
              try {
                await downloadPerOrderReconcile(yy, mo);
              } catch (e: any) {
                message.error(e?.response?.data?.detail ?? '导出失败');
              }
            }}>
            导出 Excel
          </Button>
          <Tooltip title="导出该月「成本只能估算、未录工厂账单」的订单, 对着把工厂对账单补录进系统">
            <Button icon={<FileExcelOutlined />}
              onClick={() => window.open('/api/factory-statement/missing-bill-export?period=' + period, '_blank')}>
              导出未录订单
            </Button>
          </Tooltip>
          <Tooltip title="导出 2026 起每月一个 sheet; 真实收入/成本合计/净利/净利率/木作差额 全带 Excel 公式可回推; 颜色+来源列+批注 标注 实际/预估/85%兜底">
            <Button type="primary" ghost icon={<FileExcelOutlined />}
              onClick={async () => {
                try {
                  await downloadPerOrderReconcileAll(2026, 1);
                } catch (e: any) {
                  message.error(e?.response?.data?.detail ?? '导出失败');
                }
              }}>
              导出全部(按月·公式)
            </Button>
          </Tooltip>
        </Space>
      </Space>

      {/* 刷单(补单)单列提示 — 账期=所选月份 */}
      <RefillCallout
        periodStart={dayjs(period + '-01').startOf('month').format('YYYY-MM-DD')}
        periodEnd={dayjs(period + '-01').endOf('month').format('YYYY-MM-DD')}
      />

      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="每笔订单的完整成本拆解 — 与「经营状况」同口径(会计成本)"
        description={
          <Text style={{ fontSize: 12 }}>
            净利 = 真实收入(实付−退款) − 成本合计(商品+平台扣点+税+额外售后+单列实报的物流/安装)。
            <b>物流/安装列=展示值</b>(实际账单金额, 多数单已折在商品成本里, 合计不重复计);

            <b style={{ color: '#1677ff' }}>蓝色商品成本=推演</b>(工厂未对账,实际成本到位后覆盖);
            <b>支付宝「已覆盖」</b>=该单已配上支付宝到账流水;<b>对账「推演」</b>=用估算成本。
            <br /><b style={{ color: '#389e0d' }}>工厂成本核对</b>:预算木作/配件/打包来自定价表(预估);
            <b>实际木作</b>=工厂账单(actual_cost,仅木作,「已入账」才有);
            <b>木作差额</b>=实际−预算(<Text type="danger">正=工厂报价超预算</Text>)。配件/打包工厂账单不含、恒为预估。
            合计行下方再减该月<b>推广费、人员成本</b> = 本月真实净利。
          </Text>
        } />

      <Space size="large" style={{ marginBottom: 12 }} wrap>
        <Statistic title="订单数" value={data?.order_count ?? 0} />
        <Statistic title="问题单 (亏损/未覆盖)" value={data?.problem_count ?? 0}
          valueStyle={{ color: (data?.problem_count ?? 0) > 0 ? '#cf1322' : '#3f8600' }} />
        <Statistic title="亏损单" value={data?.loss_count ?? 0}
          valueStyle={{ color: (data?.loss_count ?? 0) > 0 ? '#cf1322' : undefined }} />
        <Statistic title="支付宝未覆盖" value={data?.uncovered_count ?? 0}
          valueStyle={{ color: (data?.uncovered_count ?? 0) > 0 ? '#cf1322' : undefined }} />
        <Statistic title="用推演成本(未对账)" value={data?.estimated_count ?? 0} />
      </Space>

      <ResponsiveTable<PerOrderRow>
        data={rows}
        rowKey={(r) => r.order_no}
        loading={isLoading}
        emptyText="本月暂无订单"
        renderCard={(r) => (
          <MetricCard
            title={r.product_name || r.order_no}
            profit={r.net_profit}
            profitRate={r.net_margin}
            kpis={[
              { label: '销售额', value: yuan(r.revenue) },
              { label: '商品成本', value: yuan(r.cost_goods) },
              { label: '总成本', value: yuan(r.cost_total) },
            ]}
            moreRows={[
              { label: '订单号', value: r.order_no },
              { label: '实付', value: yuan(r.paid_amount) },
              { label: '退款', value: r.refund_amount ? `−${yuan(r.refund_amount)}` : '—' },
              { label: '物流', value: yuan(r.cost_freight) },
              { label: '安装上楼', value: yuan(r.cost_install) },
              { label: '平台扣点', value: yuan(r.cost_platform) },
              { label: '税费', value: yuan(r.cost_tax) },
              { label: '额外售后', value: yuan(r.cost_aftersales) },
            ]}
          />
        )}
        desktop={
          <Card size="small" styles={{ body: { padding: 0 } }}>
            <Table<PerOrderRow>
              rowKey="order_no" size="small" loading={isLoading}
              columns={cols} dataSource={rows}
              scroll={{ x: 2030, y: 520 }}
              pagination={{ pageSize: 100, showSizeChanger: true, showTotal: (t) => `${t} 单` }}
              rowClassName={(r) => r.is_loss ? 'per-order-loss-row' : ''}
              summary={() => sumRow}
            />
          </Card>
        }
      />

      {/* 本月真实净利 = 行净利合计 − 推广 − 人员 − 固定成本 + 补单净 */}
      {st && (
        <Card size="small" style={{ marginTop: 12, background: '#f6ffed', borderColor: '#b7eb8f' }}>
          <Space size="large" wrap split={<Text type="secondary">|</Text>}>
            <span>行净利合计 <Text strong>{yuan2(st.net_profit)}</Text></span>
            <span>− 推广费 <Text type="danger">{yuan2(st.promo_expense)}</Text></span>
            <span>− 人员成本 <Text type="danger">{yuan2(st.outsourcing_expense)}</Text>
              {st.outsourcing_estimated && <Tag color="blue" style={{ marginLeft: 4 }}>估</Tag>}</span>
            <span>
              <Tooltip title={(st.fixed_cost_items ?? []).map((i) => `${i.name} ¥${i.amount}/${i.period === 'yearly' ? '年' : '月'}`).join('; ') || '未设置'}>
                − 固定成本 <Text type="danger">{yuan2(st.fixed_costs)}</Text>
              </Tooltip>
              <Button type="link" size="small" icon={<SettingOutlined />} onClick={openFixed} style={{ paddingInline: 4 }}>设置</Button>
            </span>
            <Tooltip title={`补单=刷单(${st.refill_count}单, 流水¥${st.refill_gmv}本金来回滚抵销不算收入)。纯支出=平台扣点¥${st.refill_platform}+税¥${st.refill_tax}+佣金¥${st.refill_commission}`}>
              <span>− 补单成本(刷单) <Text type="danger">{yuan2(st.refill_cost)}</Text></span>
            </Tooltip>
            <span>=&nbsp; 本月真实净利{' '}
              <Text strong style={{ fontSize: 18, color: st.period_net_profit >= 0 ? '#389e0d' : '#cf1322' }}>
                {yuan2(st.period_net_profit)}
              </Text>{' '}
              <Text type="secondary">({Number(st.period_net_margin).toFixed(1)}%)</Text>
            </span>
          </Space>
        </Card>
      )}

      {/* 固定成本/管理费用 自定义编辑 (房租/水电/软件/折旧…, 年度项自动÷12) */}
      <Modal title="固定成本 / 管理费用 (按月分摊计入利润)" open={fixedOpen}
        onCancel={() => setFixedOpen(false)} onOk={() => saveFixed.mutate()}
        confirmLoading={saveFixed.isPending} okText="保存" width={620}>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          这些费用会按月计入「本月真实净利」。选「年」的项自动 ÷12 摊到每月(如房租 ¥40000/年 = ¥3333.33/月)。可自由增删。
        </Typography.Paragraph>
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          {draft.map((it, i) => (
            <Space key={i} wrap>
              <Input placeholder="名称(如 房租)" value={it.name} style={{ width: 150 }}
                onChange={(e) => setItem(i, { name: e.target.value })} />
              <InputNumber placeholder="金额" value={it.amount} min={0} step={100} style={{ width: 130 }}
                onChange={(v) => setItem(i, { amount: Number(v ?? 0) })} addonAfter="元" />
              <Select<'monthly' | 'yearly'> value={it.period} style={{ width: 80 }}
                onChange={(v) => setItem(i, { period: v })}
                options={[{ label: '每月', value: 'monthly' }, { label: '每年', value: 'yearly' }]} />
              <Switch checked={it.active} checkedChildren="启用" unCheckedChildren="停用"
                onChange={(v) => setItem(i, { active: v })} />
              <Button danger type="text" icon={<DeleteOutlined />}
                onClick={() => setDraft((d) => d.filter((_, idx) => idx !== i))} />
            </Space>
          ))}
          <Button type="dashed" icon={<PlusOutlined />} block
            onClick={() => setDraft((d) => [...d, { name: '', amount: 0, period: 'monthly', active: true }])}>
            添加一项
          </Button>
        </Space>
      </Modal>

      <style>{`.per-order-loss-row > td { background: #fff1f0 !important; }`}</style>
    </div>
  );
}
