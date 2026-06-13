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
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
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

  const { data, isLoading } = useQuery({
    queryKey: ['report', period.year(), period.month() + 1],
    queryFn: () => getMonthlyReport(period.year(), period.month() + 1),
  });

  // 用户拍板 (2026-06-11): 「异常严重度升级」按钮 + 「升级记录」tab 移除 —
  // 升级只是把堆积异常的严重度调档, 对用户没有实际动作价值, 徒增困惑。

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <BriefingBanner />
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          数据健康报告
        </Typography.Title>
        <DatePicker.MonthPicker
          value={period}
          onChange={(v) => v && setPeriod(v)}
          allowClear={false}
        />
      </Space>

      <Tabs
        defaultActiveKey="monthly"
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

type Period = '7d' | '30d' | 'month' | 'year';

function PeriodPicker({ value, onChange }: { value: Period; onChange: (v: Period) => void }) {
  return (
    <Radio.Group value={value} onChange={(e) => onChange(e.target.value)}>
      <Radio.Button value="7d">7 天</Radio.Button>
      <Radio.Button value="30d">30 天</Radio.Button>
      <Radio.Button value="month">本月</Radio.Button>
      <Radio.Button value="year">本年</Radio.Button>
    </Radio.Group>
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
        <Col span={6}><Card size="small"><Statistic title="净利率" value={data.net_profit_rate.toFixed(1)} suffix="%" /></Card></Col>
      </Row>
      <Card size="small" title={`支出占比 (占销售额 %, ${data.period_start} ~ ${data.period_end})`}>
        {data.expense_items.map((i) => (
          <div key={i.name} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 72, flexShrink: 0 }}>{i.name}</span>
            <Progress percent={Math.min(100, Number(i.pct.toFixed(1)))} size="small" style={{ flex: 1 }}
                      format={() => `¥${Math.round(i.amount).toLocaleString()} · ${i.pct.toFixed(1)}%`} />
          </div>
        ))}
      </Card>
    </Space>
  );
}

function SalesSummaryTab() {
  const [period, setPeriod] = useState<Period>('30d');
  const [brand, setBrand] = useState('');
  const { data, isLoading } = useQuery({
    queryKey: ['sales-summary', period, brand],
    queryFn: () => fetchSalesSummary(period, undefined, brand || undefined),
  });
  if (isLoading || !data) return <Spin />;
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space>
        <PeriodPicker value={period} onChange={setPeriod} />
        <Select size="small" value={brand} onChange={setBrand} options={BRAND_OPTS} style={{ width: 130 }} />
      </Space>
      <Row gutter={12}>
        <Col span={4}><Card size="small"><Statistic title="订单数" value={data.order_count} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="销售额" value={data.revenue.toFixed(2)} prefix="¥" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="成本" value={data.cost.toFixed(2)} prefix="¥" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="毛利" value={data.gross_profit.toFixed(2)} prefix="¥" valueStyle={{ color: '#52c41a' }} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="净利" value={data.net_profit.toFixed(2)} prefix="¥" valueStyle={{ color: data.net_profit >= 0 ? '#52c41a' : '#cf1322' }} /></Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="利润率"
                     value={data.revenue > 0 ? (data.net_profit / data.revenue * 100).toFixed(1) : 0}
                     suffix="%" />
        </Card></Col>
      </Row>
      <Card size="small" title="产品利润排行 Top 10">
        <Table size="small" rowKey={(r) => r.product_code || r.product_name}
               pagination={false}
               dataSource={data.top_products_by_profit}
               columns={[
                 { title: '产品', dataIndex: 'product_code' },
                 { title: '名称', dataIndex: 'product_name' },
                 { title: '订单数', dataIndex: 'order_count', width: 80 },
                 { title: '销售额', dataIndex: 'revenue', width: 120,
                   render: (v: number) => `¥${(v ?? 0).toFixed(2)}` },
                 { title: '成本', dataIndex: 'cost', width: 120,
                   render: (v: number) => `¥${(v ?? 0).toFixed(2)}` },
                 { title: '净利', dataIndex: 'net_profit', width: 120,
                   render: (v: number) =>
                     <Tag color={v >= 0 ? 'green' : 'red'}>¥{(v ?? 0).toFixed(2)}</Tag> },
                 { title: '利润率', dataIndex: 'profit_rate', width: 90,
                   render: (v: number) => `${((v ?? 0) * 100).toFixed(1)}%` },
               ]} />
      </Card>
      <Card size="small" title="产品利润率排行 Top 10">
        <Table size="small" rowKey={(r) => r.product_code || r.product_name}
               pagination={false}
               dataSource={data.top_products_by_profit_rate}
               columns={[
                 { title: '产品', dataIndex: 'product_code' },
                 { title: '名称', dataIndex: 'product_name' },
                 { title: '订单数', dataIndex: 'order_count', width: 80 },
                 { title: '利润率', dataIndex: 'profit_rate', width: 120,
                   render: (v: number) =>
                     <Tag color={v > 0.3 ? 'green' : v > 0.1 ? 'orange' : 'red'}>
                       {((v ?? 0) * 100).toFixed(1)}%
                     </Tag> },
                 { title: '净利', dataIndex: 'net_profit', width: 120,
                   render: (v: number) => `¥${(v ?? 0).toFixed(2)}` },
               ]} />
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
                   render: (v: number) => `¥${(v ?? 0).toFixed(2)}` },
                 { title: '成本', dataIndex: 'cost', width: 110,
                   render: (v: number) => `¥${(v ?? 0).toFixed(2)}` },
                 { title: '净利', dataIndex: 'net_profit', width: 110,
                   render: (v: number) =>
                     <Tag color={v >= 0 ? 'green' : 'red'}>¥{(v ?? 0).toFixed(2)}</Tag> },
                 { title: '毛利率', dataIndex: 'gross_profit_rate', width: 90,
                   render: (v: number) => `${((v ?? 0) * 100).toFixed(1)}%` },
                 { title: '净利率', dataIndex: 'net_profit_rate', width: 90,
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
  return `${v.toFixed(1)}%`;
}

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
        { title: '工厂账单', dataIndex: 'factory_bill', width: 100, render: fmtY },
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
        { title: '人员外包', dataIndex: 'outsourcing_expense', width: 90, render: fmtY },
        { title: '平台费', dataIndex: 'platform_fee', width: 90, render: fmtY },
        { title: '支出合计', dataIndex: 'total_expense', width: 100, render: fmtY },
      ],
    },
    {
      title: '利润',
      children: [
        { title: '总收入', dataIndex: 'total_revenue', width: 100, render: fmtY },
        {
          title: '净利润', dataIndex: 'net_profit', width: 100,
          render: (v: number) => (
            <Typography.Text type={v >= 0 ? 'success' : 'danger'} strong>
              {fmtY(v)}
            </Typography.Text>
          ),
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
      </Typography.Text>
      <Table<BusinessMonthRow>
        rowKey="period"
        columns={columns}
        dataSource={allRows}
        loading={isLoading}
        pagination={false}
        scroll={{ x: 1400 }}
        size="small"
        bordered
        rowClassName={(r) => r.period.includes('合计') ? 'ant-table-summary-row' : ''}
      />
    </Space>
  );
}
