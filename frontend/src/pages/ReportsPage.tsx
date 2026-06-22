import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import dayjs from 'dayjs';
import { useQuery } from '@tanstack/react-query';
import {
  BusinessMonthRow,
  HealthReport,
  KnowledgeRow,
  OperatingAnalysis,
  SalesSummary,
  fetchBusinessMonthly,
  fetchOperatingAnalysis,
  fetchSalesBreakdown,
  fetchSalesSummary,
  getMonthlyReport,
  listKnowledge,
} from '../api/client';
import { Radio } from 'antd';
import BriefingBanner from '../components/BriefingBanner';
import RefillCallout from '../components/RefillCallout';

// 对账规则中文名 (与对账面板一致, 用户要求界面不出现英文)
const RECON_RULE_LABELS: Record<string, string> = {
  factory_payment: '货款对账', install_fee: '安装费收支', promotion: '推广支出',
  refill_compensation: '补单赔实付', inventory_value: '库存资产', logistics_fee: '物流费销项',
  revenue_alipay: '收入对账', operating_expense: '经营支出', purchase_payment: '采购付款',
  refill_commission_payout: '补单佣金代付', refill_express_payout: '补单快递代付',
  aftersales_payout: '售后赔付代付', refund_reconciliation: '退款进出对账',
};

export default function ReportsPage() {
  const [period, setPeriod] = useState(() => dayjs());
  const [activeTab, setActiveTab] = useState('monthly');

  const { data, isLoading } = useQuery({
    queryKey: ['report', period.year(), period.month() + 1],
    queryFn: () => getMonthlyReport(period.year(), period.month() + 1),
  });

  // 用户拍板 (2026-06-11): 「异常严重度升级」按钮 + 「升级记录」tab 移除 —
  // 升级只是把堆积异常的严重度调档, 对用户没有实际动作价值, 徒增困惑。

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <BriefingBanner />
      {/* 刷单(补单)单列提示 — 各 tab 各自账期, 这里用本年至今统一亮出被剔除的刷单 */}
      <RefillCallout />
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          数据健康报告
        </Typography.Title>
        {/* 月份选择器只在「本月健康度」tab 显示 (它才用; 销售汇总等用各自的按月下拉) */}
        {activeTab === 'health' && (
          <DatePicker.MonthPicker
            value={period}
            onChange={(v) => v && setPeriod(v)}
            allowClear={false}
          />
        )}
      </Space>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: 'monthly', label: '月度经营数据', children: <BusinessMonthlyTab /> },
          { key: 'operating', label: '经营状况', children: <OperatingTab /> },
          { key: 'sales', label: '销售汇总', children: <SalesSummaryTab /> },
          { key: 'breakdown', label: '分产品销售', children: <SalesBreakdownTab /> },
          { key: 'health', label: '本月健康度', children: <ReportTab data={data} isLoading={isLoading} /> },
          { key: 'knowledge', label: 'AI 知识库', children: <KnowledgeTab /> },
        ]}
      />
    </Space>
  );
}

function ReportTab({ data, isLoading }: { data?: HealthReport; isLoading: boolean }) {
  if (isLoading) return <Spin />;
  if (!data) return <Empty />;

  const score = data.integrity_score;
  const scoreColor = score >= 90 ? '#3f8600' : score >= 70 ? '#d4b106' : '#cf1322';

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Row gutter={12}>
        <Col span={6}>
          <Card>
            <Statistic
              title="数据完整性评分"
              value={score}
              suffix="/ 100"
              valueStyle={{ color: scoreColor }}
            />
            <Progress
              percent={score}
              strokeColor={scoreColor}
              showInfo={false}
              style={{ marginTop: 12 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="未处理异常"
              value={data.exceptions.total_open}
              valueStyle={{ color: data.exceptions.total_open > 0 ? '#cf1322' : '#3f8600' }}
            />
            <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
              {Object.entries(data.exceptions.by_severity).map(([sev, n]) => (
                <Tag
                  key={sev}
                  color={{ info: 'blue', warning: 'orange', error: 'red' }[sev] ?? 'default'}
                >
                  {sev} {n}
                </Tag>
              ))}
            </div>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="本月订单" value={data.orders.month_count} />
            <Statistic title="营收" value={data.orders.month_revenue} prefix="¥" />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="库存账面" value={data.inventory.book_value} prefix="¥" />
            <div style={{ fontSize: 12, color: '#999' }}>
              {data.inventory.items_priced} 项已计入
            </div>
          </Card>
        </Col>
      </Row>

      {data.headlines.length > 0 && (
        <Alert
          type={score < 70 ? 'warning' : 'info'}
          showIcon
          message="本月头条"
          description={
            <ul style={{ marginBottom: 0 }}>
              {data.headlines.map((h, i) => <li key={i}>{h}</li>)}
            </ul>
          }
        />
      )}

      <Card title="对账规则状态">
        <Table
          rowKey="rule"
          size="small"
          pagination={false}
          dataSource={Object.entries(data.reconciliation).map(([rule, v]) => ({ rule, ...v }))}
          columns={[
            { title: '规则', dataIndex: 'rule', width: 200,
              render: (v: string) => RECON_RULE_LABELS[v] ?? v },
            { title: '总记录', dataIndex: 'total', width: 100 },
            {
              title: 'OK',
              dataIndex: 'ok',
              width: 80,
              render: (v) => v > 0 ? <Tag color="green">{v}</Tag> : '-',
            },
            {
              title: '警告',
              dataIndex: 'warning',
              width: 80,
              render: (v) => v > 0 ? <Tag color="orange">{v}</Tag> : '-',
            },
            {
              title: '严重',
              dataIndex: 'error',
              width: 80,
              render: (v) => v > 0 ? <Tag color="red">{v}</Tag> : '-',
            },
          ]}
        />
      </Card>

      <Card title="未处理异常按类型 Top 10">
        <Table
          rowKey="type"
          size="small"
          pagination={false}
          dataSource={Object.entries(data.exceptions.top_types).map(([type, count]) => ({ type, count }))}
          columns={[
            { title: '异常类型', dataIndex: 'type' },
            {
              title: '条数',
              dataIndex: 'count',
              width: 120,
              render: (v: number) => (
                <Progress
                  percent={Math.min(100, (v / Math.max(1, data.exceptions.total_open)) * 100)}
                  format={() => v}
                  size="small"
                />
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}

type Period = string; // '7d'|'30d'|'month'|'year'|'last_month'|'YYYY-MM'

// 按月下拉选项: 本月 / 上月 / 最近若干月 (用户拍板 2026-06-17)
const MONTH_OPTS = (() => {
  const opts: { value: string; label: string }[] = [
    { value: 'month', label: '本月' },
    { value: 'last_month', label: '上月' },
  ];
  const now = dayjs();
  const seen = new Set(['month', 'last_month']);
  for (let i = 0; i < 10; i++) {
    const d = now.subtract(i, 'month');
    const v = d.format('YYYY-MM');
    if (!seen.has(v)) { seen.add(v); opts.push({ value: v, label: d.format('YYYY年M月') }); }
  }
  return opts;
})();

function PeriodPicker({ value, onChange }: { value: Period; onChange: (v: Period) => void }) {
  const monthMode = value === 'month' || value === 'last_month' || /^\d{4}-\d{1,2}$/.test(value);
  return (
    <Space size={4}>
      <Radio.Group
        value={monthMode ? '__month__' : value}
        onChange={(e) => onChange(e.target.value === '__month__' ? 'month' : e.target.value)}
      >
        <Radio.Button value="7d">7 天</Radio.Button>
        <Radio.Button value="30d">30 天</Radio.Button>
        <Radio.Button value="__month__">按月</Radio.Button>
        <Radio.Button value="year">本年</Radio.Button>
      </Radio.Group>
      {monthMode && (
        <Select size="small" value={value} onChange={onChange} options={MONTH_OPTS} style={{ width: 110 }} />
      )}
    </Space>
  );
}

// Plan F8: 品牌筛选 (PS 畔色 / PFG 孚格)
const BRAND_OPTS = [
  { value: '', label: '全部品牌' },
  { value: 'PS', label: '畔色 (PS)' },
  { value: 'PFG', label: '孚格 (PFG)' },
];

// Plan F6: 经营状况 tab — 收支占比 + 净利卡片
function OperatingTab() {
  const [period, setPeriod] = useState<Period>('30d');
  const { data, isLoading } = useQuery({
    queryKey: ['operating-analysis', period],
    queryFn: () => fetchOperatingAnalysis(period),
  });
  if (isLoading || !data) return <Spin />;
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <PeriodPicker value={period} onChange={setPeriod} />
      <Row gutter={12}>
        <Col span={6}><Card size="small"><Statistic title="销售额" value={Math.round(data.revenue)} prefix="¥" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="总支出" value={Math.round(data.total_expense)} prefix="¥" /></Card></Col>
        <Col span={6}><Card size="small">
          <Statistic title="净利" value={Math.round(data.net_profit)} prefix="¥"
                     valueStyle={{ color: data.net_profit >= 0 ? '#52c41a' : '#cf1322' }} />
        </Card></Col>
        <Col span={6}><Card size="small"><Statistic title="净利率" value={Number(data.net_profit_rate).toFixed(1)} suffix="%" /></Card></Col>
      </Row>
      <Card size="small" title={`支出占比 (占销售额 %, ${data.period_start} ~ ${data.period_end})`}>
        {data.expense_items.map((i) => (
          <div key={i.name} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 72, flexShrink: 0 }}>{i.name}</span>
            <Progress percent={Math.min(100, Number(Number(i.pct).toFixed(1)))} size="small" style={{ flex: 1 }}
                      format={() => `¥${Math.round(i.amount).toLocaleString()} · ${Number(i.pct).toFixed(1)}%`} />
          </div>
        ))}
      </Card>
    </Space>
  );
}

function SalesSummaryTab() {
  const [period, setPeriod] = useState<Period>('30d');
  const [brand, setBrand] = useState('');
  // 排行切换 (用户拍板 2026-06-17): 按利润 / 按利润率, 用同一张表
  const [rankBy, setRankBy] = useState<'profit' | 'rate'>('profit');
  const { data, isLoading } = useQuery({
    queryKey: ['sales-summary', period, brand],
    queryFn: () => fetchSalesSummary(period, undefined, brand || undefined),
  });
  if (isLoading || !data) return <Spin />;

  const yuan = (v: number) => `¥${Number(v ?? 0).toFixed(2)}`;
  // 利润/利润率排行 共用列 (净利→利润, 不含推广)
  const rankCols = [
    { title: '产品', dataIndex: 'product_code' },
    { title: '名称', dataIndex: 'product_name' },
    { title: '订单数', dataIndex: 'order_count', width: 80 },
    { title: '销售额', dataIndex: 'revenue', width: 110, render: (v: number) => yuan(v) },
    { title: '成本', dataIndex: 'cost', width: 110, render: (v: number) => yuan(v) },
    {
      title: '利润', dataIndex: 'net_profit', width: 110,
      render: (v: number) => <Tag color={v >= 0 ? 'green' : 'red'}>{yuan(v)}</Tag>,
    },
    {
      title: '利润率', dataIndex: 'profit_rate', width: 90,
      render: (v: number) => `${((v ?? 0) * 100).toFixed(1)}%`,
    },
  ];
  const rankData = rankBy === 'profit' ? data.top_products_by_profit : data.top_products_by_profit_rate;
  const lowData = data.bottom_products_by_profit ?? [];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space>
        <PeriodPicker value={period} onChange={setPeriod} />
        <Select size="small" value={brand} onChange={setBrand} options={BRAND_OPTS} style={{ width: 130 }} />
      </Space>
      <Row gutter={12}>
        <Col span={4}><Card size="small"><Statistic title="订单数" value={data.order_count} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="销售额" value={Number(data.revenue).toFixed(2)} prefix="¥" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="成本" value={Number(data.cost).toFixed(2)} prefix="¥" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="毛利" value={Number(data.gross_profit).toFixed(2)} prefix="¥" valueStyle={{ color: '#52c41a' }} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="利润" value={Number(data.net_profit).toFixed(2)} prefix="¥" valueStyle={{ color: data.net_profit >= 0 ? '#52c41a' : '#cf1322' }} /></Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="利润率"
                     value={data.revenue > 0 ? (data.net_profit / data.revenue * 100).toFixed(1) : 0}
                     suffix="%" />
        </Card></Col>
      </Row>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        「成本」= <b>会计总成本</b> = 物理产品成本(工厂价/含木作·打包·外采配件) + 物流 + 安装/上楼 + 售后 +
        <b>平台扣点(手续费0.6%+活动抽成2%，或实付−店铺实收) + 税费2%</b>；
        「利润」= 实付 − 退款 − 会计总成本。费率在「对账中心→财务系数设置」可改。已剔除补单/刷单/待付款/退款单。
      </Typography.Text>
      <Card size="small" title="产品利润排行 Top 10"
            extra={<Segmented size="small" value={rankBy} onChange={(v) => setRankBy(v as 'profit' | 'rate')}
                     options={[{ label: '按利润', value: 'profit' }, { label: '按利润率', value: 'rate' }]} />}>
        <Table size="small" rowKey={(r) => r.product_code || r.product_name}
               pagination={false} dataSource={rankData} columns={rankCols} />
      </Card>
      <Card size="small" title="产品低利润排行 Top 10（亏损最多在前）">
        <Table size="small" rowKey={(r) => r.product_code || r.product_name}
               pagination={false} dataSource={lowData} columns={rankCols} />
      </Card>
    </Space>
  );
}

function SalesBreakdownTab() {
  const [period, setPeriod] = useState<Period>('30d');
  const [brand, setBrand] = useState('');
  const { data, isLoading } = useQuery({
    queryKey: ['sales-breakdown', period, brand],
    queryFn: () => fetchSalesBreakdown(period, brand || undefined),
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space>
        <PeriodPicker value={period} onChange={setPeriod} />
        <Select size="small" value={brand} onChange={setBrand} options={BRAND_OPTS} style={{ width: 130 }} />
      </Space>
      <Card size="small" title={`分 SKU 销售 (${data?.period_start} ~ ${data?.period_end})`}>
        <Table size="small" loading={isLoading}
               rowKey={(r) => `${r.product_code}_${r.sku_code}`}
               dataSource={data?.rows ?? []}
               pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
               scroll={{ x: 1200 }}
               columns={[
                 { title: '产品', dataIndex: 'product_code', width: 100 },
                 { title: '名称', dataIndex: 'product_name', width: 180 },
                 { title: 'SKU', dataIndex: 'sku_code', width: 100 },
                 { title: 'SKU 名', dataIndex: 'sku' },
                 { title: '件数', dataIndex: 'qty', width: 70 },
                 { title: '销售额', dataIndex: 'revenue', width: 110,
                   render: (v: number) => `¥${Number(v ?? 0).toFixed(2)}` },
                 { title: '成本', dataIndex: 'cost', width: 110,
                   render: (v: number) => `¥${Number(v ?? 0).toFixed(2)}` },
                 { title: '利润', dataIndex: 'net_profit', width: 110,
                   render: (v: number) =>
                     <Tag color={v >= 0 ? 'green' : 'red'}>¥{Number(v ?? 0).toFixed(2)}</Tag> },
                 { title: '毛利率', dataIndex: 'gross_profit_rate', width: 90,
                   render: (v: number) => `${((v ?? 0) * 100).toFixed(1)}%` },
                 { title: '利润率', dataIndex: 'net_profit_rate', width: 90,
                   render: (v: number) =>
                     <Tag color={v > 0.3 ? 'green' : v > 0.1 ? 'orange' : 'red'}>
                       {((v ?? 0) * 100).toFixed(1)}%
                     </Tag> },
               ]} />
      </Card>
    </Space>
  );
}

function KnowledgeTab() {
  const { data, isLoading } = useQuery({ queryKey: ['knowledge'], queryFn: () => listKnowledge(100) });
  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="AI 常见问题库 (plan §12.2)"
        description="AI 处理过的问题归档于此, 同类异常再次出现时直接复用, 不重复打 API."
      />
      <Table<KnowledgeRow>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        columns={[
          { title: '异常类型', dataIndex: 'exception_type', width: 220 },
          { title: '复用次数', dataIndex: 'usage_count', width: 100,
            render: (v: number) => <Tag color={v > 1 ? 'green' : 'default'}>{v}</Tag> },
          { title: '解决方案 (首段)', dataIndex: 'solution_text', ellipsis: true,
            render: (v: string) => v.split('\n')[0] },
          { title: '来源 SHA', dataIndex: 'context_hash', width: 110,
            render: (v: string) => <code style={{ fontSize: 11 }}>{v.slice(0, 8)}</code> },
        ]}
      />
    </>
  );
}

// ─── 月度经营数据表格 ─────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  // 钳制小数位到合法区间 [0,20], 否则 toLocaleString 会抛 "minimumFractionDigits value is out of range" 整页崩。
  const d = Math.max(0, Math.min(20, Math.trunc(Number(decimals) || 0)));
  return n.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtY(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `¥${fmt(v)}`;
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${Number(v).toFixed(1)}%`;
}

// 预估值标蓝(带"估"角标 + 悬浮说明); 实际值正常显示。用户拍板 2026-06-17: 蓝色标注哪些是预估。
const estCell = (estKey: keyof BusinessMonthRow, tip: string) =>
  (v: number, r: BusinessMonthRow) =>
    r[estKey]
      ? (
        <Tooltip title={`预估: ${tip}`}>
          <span style={{ color: '#1677ff' }}>
            {fmtY(v)}<sup style={{ fontSize: 9, marginLeft: 1 }}>估</sup>
          </span>
        </Tooltip>
      )
      : <span>{fmtY(v)}</span>;

function BusinessMonthlyTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['business-monthly'],
    queryFn: () => fetchBusinessMonthly(2026, 1),
    staleTime: 5 * 60 * 1000,
  });

  const allRows: BusinessMonthRow[] = data
    ? [...data.rows, { ...data.summary, period: '📊 合计' }]
    : [];

  const columns = [
    {
      title: '月份', dataIndex: 'period', width: 90, fixed: 'left' as const,
      render: (v: string) => <Typography.Text strong>{v}</Typography.Text>,
    },
    {
      title: '真实订单',
      children: [
        { title: '笔数', dataIndex: 'real_order_count', width: 70, render: fmt },
        { title: '金额', dataIndex: 'real_revenue', width: 100, render: fmtY },
      ],
    },
    {
      title: '补单',
      children: [
        { title: '笔数', dataIndex: 'refill_order_count', width: 60, render: fmt },
        { title: '金额', dataIndex: 'refill_revenue', width: 100, render: fmtY },
        {
          title: '订单占比', dataIndex: 'refill_order_ratio', width: 80,
          render: (v: number) => {
            const color = v > 30 ? '#ff4d4f' : v > 15 ? '#fa8c16' : '#52c41a';
            return <span style={{ color }}>{pct(v)}</span>;
          },
        },
        {
          title: '金额占比', dataIndex: 'refill_cost_ratio', width: 80,
          render: (v: number) => {
            const color = v > 30 ? '#ff4d4f' : v > 15 ? '#fa8c16' : '#52c41a';
            return <span style={{ color }}>{pct(v)}</span>;
          },
        },
      ],
    },
    {
      title: '支出',
      children: [
        {
          title: '推广费', dataIndex: 'promo_expense', width: 100,
          render: (v: number, r: BusinessMonthRow) => (
            <Space direction="vertical" size={0}>
              <span>{fmtY(v)}</span>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>{pct(r.promo_ratio)}</Typography.Text>
            </Space>
          ),
        },
        { title: '工厂账单', dataIndex: 'factory_bill', width: 95, render: fmtY },
        {
          title: '商品成本', dataIndex: 'effective_cost', width: 100,
          render: estCell('cogs_estimated', '未对账月用逐单成本估算(含定制推演)'),
        },
        { title: '物流费', dataIndex: 'freight_expense', width: 85, render: fmtY },
        { title: '安装上楼', dataIndex: 'install_upstairs_expense', width: 90, render: fmtY },
        {
          title: '售后赔付', dataIndex: 'aftersales_compensation', width: 100,
          render: (v: number, r: BusinessMonthRow) => (
            <Space direction="vertical" size={0}>
              <span>{fmtY(v)}</span>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {r.aftersales_count}单 / {pct(r.aftersales_rate)}
              </Typography.Text>
            </Space>
          ),
        },
        {
          title: <Tooltip title="平台扣点 = 实付−店铺实收(含手续费/活动抽成/平台优惠券)">平台扣点</Tooltip>,
          dataIndex: 'platform_deduction', width: 90, render: fmtY,
        },
        { title: '税费', dataIndex: 'tax_expense', width: 85, render: fmtY },
        {
          title: '人员外包', dataIndex: 'outsourcing_expense', width: 90,
          render: estCell('outsourcing_estimated', '无实际录入, 5月起按 ¥10000/月预估'),
        },
        {
          title: <Tooltip title="固定成本/管理费用(房租等), 在「逐单核对」页设置">固定成本</Tooltip>,
          dataIndex: 'fixed_costs', width: 85, render: fmtY,
        },
        {
          title: <Tooltip title="补单=刷单的纯成本(平台扣点+税+佣金), 本金来回滚不算收入">补单成本</Tooltip>,
          dataIndex: 'refill_cost', width: 85, render: fmtY,
        },
        { title: '支出合计', dataIndex: 'total_expense', width: 100, render: fmtY },
      ],
    },
    {
      title: '利润',
      children: [
        { title: '总收入', dataIndex: 'total_revenue', width: 100, render: fmtY },
        {
          title: '净利润', dataIndex: 'net_profit', width: 100,
          render: (v: number, r: BusinessMonthRow) => {
            const revenue = (r.net_profit ?? 0) + (r.total_expense ?? 0);
            const line = (label: string, val: number | null | undefined, strong = false) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontWeight: strong ? 700 : 400 }}>
                <span>{label}</span><span>{fmtY(val)}</span>
              </div>
            );
            const tip = (
              <div style={{ fontSize: 12, lineHeight: 1.65, minWidth: 210 }}>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>净利润 = 真实收入 − 支出合计</div>
                {line('真实收入(实付−退款)', revenue)}
                {line('− 支出合计', r.total_expense)}
                <div style={{ borderTop: '1px solid #ffffff44', margin: '3px 0' }} />
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontWeight: 700 }}>
                  <span>= 净利润</span><span>{fmtY(v)} ({pct(r.net_profit_rate)})</span>
                </div>
                <div style={{ marginTop: 6, marginBottom: 2, opacity: 0.8 }}>── 支出合计 拆解 ──</div>
                {line('商品成本', r.effective_cost)}
                {line('推广费', r.promo_expense)}
                {line('平台扣点', r.platform_deduction)}
                {line('税费', r.tax_expense)}
                {line('人员外包', r.outsourcing_expense)}
                {line('固定成本', r.fixed_costs)}
                {line('售后赔付', r.aftersales_compensation)}
                {line('物流费', r.freight_expense)}
                {line('安装上楼', r.install_upstairs_expense)}
                {line('补单成本', r.refill_cost)}
              </div>
            );
            return (
              <Tooltip title={tip} overlayStyle={{ maxWidth: 340 }}>
                <Typography.Text type={v >= 0 ? 'success' : 'danger'} strong
                  style={{ cursor: 'help', borderBottom: '1px dotted currentColor' }}>
                  {fmtY(v)}
                </Typography.Text>
              </Tooltip>
            );
          },
        },
        {
          title: '净利率', dataIndex: 'net_profit_rate', width: 80,
          render: (v: number) => {
            const color = v >= 20 ? '#52c41a' : v >= 5 ? '#fa8c16' : '#ff4d4f';
            return <span style={{ color }}>{pct(v)}</span>;
          },
        },
      ],
    },
    {
      title: '工厂交货',
      dataIndex: 'avg_lead_time_days',
      width: 90,
      render: (v: number | null) => v !== null ? `${v}天` : '—',
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Typography.Text type="secondary">
        数据范围：2026年1月至今，每月一行，合计行在底部。补单占比 &gt;30% 或售后率偏高时红色提示。
        <br />
        「真实订单」= <b>已付款且成交</b>的正式订单（已剔除 <b>待付款 / 取消 / 关闭 / 全额退款 / 补单</b>）；
        金额 = 买家实付 − 部分退款（真实到手）。净利润 = 真实金额 − 支出合计（完整会计成本），口径与「经营状况」一致。
      </Typography.Text>
      <Alert
        type="info" showIcon
        message={<span>全系统统一口径(与 经营状况/逐单核对/数据大盘 完全一致)。<Tag color="blue" style={{ marginLeft: 6 }}>估</Tag> 蓝色 = 预估值</span>}
        description={
          <Typography.Text style={{ fontSize: 12 }}>
            净利润 = 真实收入(实付−退款) − 支出合计。<b>支出合计</b> = 商品成本 + 物流 + 安装上楼 + 平台扣点 + 税 + 额外售后 + 推广 + 人员 + 固定成本 + 补单成本。
            <br />
            <b>平台扣点</b> = 实付−店铺实收(含手续费/活动/平台优惠券, 真实)；<b>商品成本</b> = 已对账用实际, 未对账用逐单推演(蓝色)；
            <b>额外售后</b> = 退款之外的额外赔付(货损/补发运费/万师傅扣款…, 按订单归属；客户退款已在收入扣过, 不算售后)；
            <b>人员外包</b> = 有录入用实际, 5月起无录入按¥10,000/月(财务系数设置可改)；
            <b>固定成本</b>(房租等)在「逐单核对」页设置；<b>补单</b>=刷单纯成本(平台扣点+税+佣金, 本金回流不算收入)。
          </Typography.Text>
        }
        style={{ marginBottom: 4 }}
      />
      <Table<BusinessMonthRow>
        rowKey="period"
        columns={columns}
        dataSource={allRows}
        loading={isLoading}
        pagination={false}
        scroll={{ x: 1720 }}
        size="small"
        bordered
        rowClassName={(r) => r.period.includes('合计') ? 'ant-table-summary-row' : ''}
      />
    </Space>
  );
}
