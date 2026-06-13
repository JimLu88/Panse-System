import { lazy, Suspense, type CSSProperties } from 'react';
import { Card, Col, Progress, Row, Spin, Statistic, Tag, Tooltip, Typography } from 'antd';
import { ShoppingOutlined, AlertOutlined, DollarOutlined, CheckCircleOutlined, ExclamationCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getDashboard } from '../api/client';
import { getCashFlow, type CashFlowSummary, type CashFlowFreshness } from '../api/finance';
import MonthlyOpsPanel from '../components/MonthlyOpsPanel';
import AutomationStatusCard from '../components/AutomationStatusCard';

const ReactECharts = lazy(() => import('echarts-for-react'));

// ---- Mosaic 浅色调色板 ----
const M = {
  violet: '#8b5cf6', indigo: '#6366f1', sky: '#38bdf8', sky2: '#7dd3fc',
  emerald: '#10b981', amber: '#f59e0b', rose: '#f43f5e',
  ink: '#1e293b', sub: '#94a3b8', grid: '#eef2f7', bg: '#f8fafc',
};
const cardStyle: CSSProperties = {
  borderRadius: 16, border: '1px solid #eef0f4', boxShadow: '0 1px 2px rgba(15,23,42,.04)',
};
const sectionTitle: CSSProperties = { color: M.ink, fontWeight: 700, margin: '18px 2px 10px' };
const bigNum: CSSProperties = { color: M.ink, fontWeight: 800, fontSize: 26, letterSpacing: '-0.01em' };
const midNum: CSSProperties = { color: M.ink, fontWeight: 700, fontSize: 20 };

function MCard({ children, style, ...rest }: any) {
  return (
    <Card size="small" bordered={false} style={{ ...cardStyle, ...style }} {...rest}>
      {children}
    </Card>
  );
}

function ChartPlaceholder() {
  return <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>;
}

const STATUS_LABELS: Record<string, string> = {
  pending_payment: '待付款', paid: '已付款', shipped: '已发货',
  signed: '已签收', aftersales: '售后', cancelled: '已取消',
};
// Mosaic 同色系映射
const STATUS_COLORS: Record<string, string> = {
  pending_payment: M.amber, paid: M.sky, shipped: M.violet,
  signed: M.indigo, aftersales: M.rose, cancelled: '#cbd5e1',
};

function money(v: number) {
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}

const softArea = (rgb: string) => ({
  type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
  colorStops: [{ offset: 0, color: `rgba(${rgb},.22)` }, { offset: 1, color: `rgba(${rgb},0)` }],
});

// 数据新鲜度红绿灯
const FRESH_DOT: Record<string, { dot: string; color: string }> = {
  fresh: { dot: '🟢', color: 'success' },
  aging: { dot: '🟡', color: 'warning' },
  stale: { dot: '🔴', color: 'error' },
  unknown: { dot: '⚪', color: 'default' },
};

function freshAgo(f: CashFlowFreshness) {
  if (f.days_ago == null) return '无记录';
  if (f.days_ago === 0) return '今天';
  return `${f.days_ago} 天前`;
}

// 运营大盘上的「剩余流水 / 可用资金」卡片: 复用 /api/finance/cash-flow, 点击进完整页
function CashFlowBanner() {
  const nav = useNavigate();
  const { data, isLoading } = useQuery<CashFlowSummary>({
    queryKey: ['cash-flow'], queryFn: getCashFlow, refetchInterval: 60_000,
  });
  if (isLoading || !data) {
    return (
      <MCard style={{ marginBottom: 16 }}>
        <div style={{ height: 96, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>
      </MCard>
    );
  }
  const totalNum = Number(data.total);
  const hasStale = data.freshness.some((f) => f.status === 'stale');
  const invest = data.subtractions.find((s) => s.key === 'total_investment');
  return (
    <MCard
      hoverable
      onClick={() => nav('/cash-flow')}
      style={{
        marginBottom: 16, cursor: 'pointer',
        background: 'linear-gradient(135deg,#ffffff 0%,#f5f3ff 100%)',
        borderColor: hasStale ? '#fecaca' : '#e9d5ff',
      }}
    >
      <Row align="middle" gutter={[16, 12]}>
        <Col xs={24} md={10}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <DollarOutlined style={{ color: M.violet }} />
            <span style={{ color: M.sub, fontSize: 13, fontWeight: 600 }}>剩余流水 · 可用资金（实时）</span>
            {hasStale && <Tag color="error" style={{ borderRadius: 8 }}>数据偏旧</Tag>}
          </div>
          <div style={{ color: totalNum >= 0 ? M.emerald : M.rose, fontWeight: 800, fontSize: 30, letterSpacing: '-0.01em', marginTop: 2 }}>
            {money(totalNum)}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: M.sub }}>
            <span style={{ color: M.emerald }}>↑ 加项 {money(Number(data.total_additions))}</span>
            <span style={{ margin: '0 8px' }}>·</span>
            <span style={{ color: M.rose }}>↓ 减项 {money(Number(data.total_subtractions))}</span>
            {invest && (
              <>
                <span style={{ margin: '0 8px' }}>·</span>
                <span>总投资费用 {money(Number(invest.amount))}</span>
              </>
            )}
          </div>
        </Col>
        <Col xs={24} md={14}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'flex-end' }}>
            {data.freshness.map((f) => (
              <Tooltip key={f.source} title={`数据截至 ${f.as_of ? new Date(f.as_of).toLocaleDateString('zh-CN') : '无记录'}`}>
                <Tag color={FRESH_DOT[f.status]?.color || 'default'} style={{ borderRadius: 8, marginInlineEnd: 0 }}>
                  {FRESH_DOT[f.status]?.dot} {f.source} · {freshAgo(f)}
                </Tag>
              </Tooltip>
            ))}
          </div>
          <div style={{ textAlign: 'right', marginTop: 8, fontSize: 12, color: M.indigo }}>点击查看完整明细 →</div>
        </Col>
      </Row>
    </MCard>
  );
}

export default function DashboardPage() {
  const nav = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboard,
    refetchInterval: 60_000,
  });

  if (isLoading || !data) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}>
        <Spin tip="加载大盘..." size="large"><div style={{ minHeight: 60 }} /></Spin>
      </div>
    );
  }

  const { orders, inventory, finance, health, recon_rules, health_dimensions, monthly_close } = data as any;

  // 五维健康雷达
  const radarOption = {
    tooltip: {},
    radar: {
      indicator: (health_dimensions || []).map((d: any) => ({ name: d.name, max: 100 })),
      radius: '65%',
      axisName: { color: M.sub, fontSize: 11 },
      splitLine: { lineStyle: { color: M.grid } },
      splitArea: { areaStyle: { color: ['#ffffff', '#fafbff'] } },
      axisLine: { lineStyle: { color: M.grid } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: (health_dimensions || []).map((d: any) => d.score),
        name: '健康度',
        areaStyle: { color: 'rgba(139,92,246,0.18)' },
        lineStyle: { color: M.violet, width: 2 },
        itemStyle: { color: M.violet },
      }],
    }],
  };

  // 订单状态环图
  const pieOption = {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { color: M.sub, fontSize: 12 } },
    series: [{
      type: 'pie',
      radius: ['58%', '78%'],
      center: ['36%', '50%'],
      itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 6 },
      data: Object.entries(orders.status_counts as Record<string, number>)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => ({ name: STATUS_LABELS[k] || k, value: v, itemStyle: { color: STATUS_COLORS[k] } })),
      label: { show: false },
    }],
  };

  // 近 30 天趋势
  const trendOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: ['订单数', '收入(¥)'], textStyle: { color: M.sub }, top: 0 },
    grid: { top: 32, left: 8, right: 8, bottom: 2, containLabel: true },
    xAxis: {
      type: 'category', data: orders.trend_30d.map((r: any) => r.date.slice(5)),
      axisLine: { lineStyle: { color: '#e2e8f0' } }, axisTick: { show: false },
      axisLabel: { color: M.sub, fontSize: 10 },
    },
    yAxis: [
      { type: 'value', name: '订单数', minInterval: 1, splitLine: { lineStyle: { color: M.grid } }, axisLabel: { color: M.sub } },
      { type: 'value', name: '收入', splitLine: { show: false }, axisLabel: { color: M.sub, formatter: (v: number) => `¥${(v / 1000).toFixed(0)}k` } },
    ],
    series: [
      { name: '订单数', type: 'bar', data: orders.trend_30d.map((r: any) => r.count), itemStyle: { color: M.sky2, borderRadius: [4, 4, 0, 0] }, barWidth: '45%' },
      {
        name: '收入(¥)', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none',
        data: orders.trend_30d.map((r: any) => r.revenue),
        lineStyle: { color: M.violet, width: 2 }, itemStyle: { color: M.violet },
        areaStyle: { color: softArea('139,92,246') },
      },
    ],
  };

  const healthStroke = health.health_score >= 80 ? M.emerald : health.health_score >= 60 ? M.violet : M.rose;

  return (
    <div style={{ background: M.bg, minHeight: '100%', padding: '6px 6px 28px' }}>
      <div style={{ margin: '2px 2px 18px' }}>
        <Typography.Title level={4} style={{ margin: 0, color: M.ink, fontWeight: 800 }}>运营大盘</Typography.Title>
        <Typography.Text style={{ color: M.sub, fontSize: 13 }}>实时经营概览 · 每分钟自动刷新</Typography.Text>
      </div>

      {/* 剩余流水 · 可用资金 (实时, 含数据红绿灯; 点击进完整页) */}
      <CashFlowBanner />

      {/* 月度经营 (工厂口径利润/ROI + 销售占比饼图, 可切月; 未对清月标仅供参考) */}
      <MonthlyOpsPanel />

      {/* 自动化任务 · 今日状态 (用户拍板 2026-06-12: 可自动的指标一个框, 每天看状态) */}
      <div style={{ marginBottom: 16 }}><AutomationStatusCard /></div>

      {/* KPI 卡片行 — 每张卡点击进它关联最高的页面 (用户要求) */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <MCard onClick={() => nav('/orders')} style={{ cursor: 'pointer' }}>
            <Statistic title="近 7 天订单" value={orders.count_7d} prefix={<ShoppingOutlined style={{ color: M.violet }} />} suffix="单"
              valueStyle={{ ...bigNum }} />
          </MCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <MCard onClick={() => nav('/orders')} style={{ cursor: 'pointer' }}>
            <Statistic title="近 30 天收入 (不含补单)" value={orders.revenue_30d} precision={0}
              prefix={<DollarOutlined style={{ color: M.indigo }} />}
              formatter={(v) => `¥${Number(v).toLocaleString()}`} valueStyle={{ ...bigNum }} />
            {((orders as any).refill_excluded_30d ?? 0) > 0 && (
              <div style={{ color: '#999', fontSize: 12 }}>
                有补单 ¥{Math.round((orders as any).refill_excluded_30d).toLocaleString()} 未计入
              </div>
            )}
          </MCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <MCard onClick={() => nav('/exceptions')} style={{ cursor: 'pointer' }}>
            <Statistic title="待处理异常" value={health.open_exceptions} prefix={<AlertOutlined />}
              valueStyle={{ ...bigNum, color: health.open_exceptions > 10 ? M.rose : health.open_exceptions > 3 ? M.amber : M.emerald }} />
          </MCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <MCard title="数据健康度" onClick={() => nav('/reports')} style={{ cursor: 'pointer' }}>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <Progress type="dashboard" percent={health.health_score} strokeColor={healthStroke} trailColor="#eef2f7" size={84} />
            </div>
          </MCard>
        </Col>
      </Row>

      {/* 图表行 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <MCard title="订单状态分布" onClick={() => nav('/orders')} style={{ cursor: 'pointer' }}>
            <Suspense fallback={<ChartPlaceholder />}>
              <ReactECharts option={pieOption} style={{ height: 240 }} />
            </Suspense>
          </MCard>
        </Col>
        <Col xs={24} lg={12}>
          <MCard title="近 30 天订单趋势" onClick={() => nav('/orders')} style={{ cursor: 'pointer' }}>
            <Suspense fallback={<ChartPlaceholder />}>
              <ReactECharts option={trendOption} style={{ height: 240 }} />
            </Suspense>
          </MCard>
        </Col>
      </Row>

      {/* 财务概览 (近 30 天) */}
      <Typography.Title level={5} style={sectionTitle}>财务概览 (近 30 天)</Typography.Title>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/orders')} style={{ cursor: 'pointer' }}><Statistic title="订单收入" value={finance.order_revenue_30d} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
        </Col>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/pricing')} style={{ cursor: 'pointer' }}><Statistic title="理论成本" value={finance.theoretical_cost_30d} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
        </Col>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/reconciliation')} style={{ cursor: 'pointer' }}><Statistic title="实际成本" value={finance.actual_cost_30d} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
        </Col>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/assets-cashflow')} style={{ cursor: 'pointer' }}>
            <Statistic title="毛利" value={finance.gross_profit_30d} formatter={(v) => money(Number(v))}
              valueStyle={{ ...midNum, color: finance.gross_profit_30d >= 0 ? M.emerald : M.rose }} />
            <Tag style={{ marginTop: 8, borderRadius: 8 }}
              color={finance.gross_margin_rate >= 0.15 ? 'success' : finance.gross_margin_rate >= 0 ? 'warning' : 'error'}>
              毛利率 {(finance.gross_margin_rate * 100).toFixed(1)}%
            </Tag>
          </MCard>
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/alipay')} style={{ cursor: 'pointer' }}><Statistic title="支付宝收入" value={finance.alipay_income_30d} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
        </Col>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/reconciliation')} style={{ cursor: 'pointer' }}><Statistic title="对账未清" value={finance.reconciliation_unresolved} suffix="条"
            valueStyle={{ ...midNum, color: finance.reconciliation_unresolved > 0 ? M.amber : M.emerald }} /></MCard>
        </Col>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/aftersales')} style={{ cursor: 'pointer' }}><Statistic title="售后笔数" value={finance.aftersales_count} suffix="单" valueStyle={midNum} /></MCard>
        </Col>
        <Col xs={12} lg={6}>
          <MCard onClick={() => nav('/aftersales')} style={{ cursor: 'pointer' }}><Statistic title="售后成本" value={finance.aftersales_cost} formatter={(v) => money(Number(v))} valueStyle={{ ...midNum, color: M.rose }} /></MCard>
        </Col>
      </Row>

      {/* 对账健康 */}
      <Typography.Title level={5} style={sectionTitle}>对账健康</Typography.Title>
      <Row gutter={[12, 12]} style={{ marginBottom: 8 }}>
        {(recon_rules || []).map((rule: any) => {
          const isOk = rule.status === 'ok';
          const isWarn = rule.status === 'warning';
          const icon = isOk
            ? <CheckCircleOutlined style={{ color: M.emerald }} />
            : isWarn ? <ExclamationCircleOutlined style={{ color: M.amber }} />
            : <CloseCircleOutlined style={{ color: M.rose }} />;
          const borderColor = isOk ? '#bbf7d0' : isWarn ? '#fde68a' : '#fecaca';
          const tip = isOk ? '无差异' : `错误 ${rule.error} · 警告 ${rule.warning}`;
          return (
            <Col key={rule.key} xs={12} sm={8} md={4}>
              <Tooltip title={tip}>
                <MCard
                  hoverable
                  onClick={() => nav('/reconciliation')}
                  style={{ borderColor, cursor: 'pointer', textAlign: 'center' }}
                  styles={{ body: { padding: '10px 4px' } }}
                >
                  <div style={{ fontSize: 18, marginBottom: 2 }}>{icon}</div>
                  <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3, color: M.ink }}>{rule.label}</div>
                  {!isOk && (
                    <div style={{ fontSize: 11, color: M.sub, marginTop: 2 }}>
                      {rule.error > 0 && <span style={{ color: M.rose }}>✕{rule.error} </span>}
                      {rule.warning > 0 && <span style={{ color: M.amber }}>△{rule.warning}</span>}
                    </div>
                  )}
                </MCard>
              </Tooltip>
            </Col>
          );
        })}
      </Row>

      {/* 健康雷达 + 月结清单 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={10}>
          <MCard title="多维健康雷达">
            <Suspense fallback={<ChartPlaceholder />}>
              <ReactECharts option={radarOption} style={{ height: 260 }} />
            </Suspense>
          </MCard>
        </Col>
        <Col xs={24} lg={14}>
          <MCard title="本月月结清单"
            extra={<Typography.Text style={{ fontSize: 12, color: M.sub }}>
              {(monthly_close || []).filter((m: any) => m.done).length}/{(monthly_close || []).length} 已完成
            </Typography.Text>}>
            <Row gutter={[8, 8]}>
              {(monthly_close || []).map((m: any) => (
                <Col key={m.category + m.key} xs={12} sm={8}>
                  <Tooltip title={m.detail}>
                    <div
                      onClick={() => m.category === '对账' ? nav('/reconciliation') : undefined}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '6px 10px', borderRadius: 10,
                        background: m.done ? '#f0fdf4' : '#fffbeb',
                        border: `1px solid ${m.done ? '#bbf7d0' : '#fde68a'}`,
                        cursor: m.category === '对账' ? 'pointer' : 'default',
                      }}>
                      {m.done
                        ? <CheckCircleOutlined style={{ color: M.emerald }} />
                        : <ExclamationCircleOutlined style={{ color: M.amber }} />}
                      <span style={{ fontSize: 12, lineHeight: 1.2, color: M.ink }}>{m.label}</span>
                    </div>
                  </Tooltip>
                </Col>
              ))}
            </Row>
          </MCard>
        </Col>
      </Row>

      {/* 库存运营 */}
      <Typography.Title level={5} style={sectionTitle}>库存运营</Typography.Title>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <MCard title="配件库存">
            <Statistic title="品种数" value={inventory.part_total} valueStyle={midNum} />
            <div style={{ marginTop: 8 }}>
              {inventory.part_negative > 0 && <Tag color="error" style={{ borderRadius: 8 }}>负库存 {inventory.part_negative}</Tag>}
              {inventory.part_below_safety > 0 && <Tag color="warning" style={{ borderRadius: 8 }}>缺料 {inventory.part_below_safety}</Tag>}
              {inventory.part_oversold > 0 && <Tag color="volcano" style={{ borderRadius: 8 }}>超卖 {inventory.part_oversold}</Tag>}
            </div>
          </MCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <MCard title="成品库存">
            <Statistic title="品种数" value={inventory.product_total} valueStyle={midNum} />
            {inventory.product_low_stock > 0 && (
              <Tag color="warning" style={{ marginTop: 8, borderRadius: 8 }}>低库存 {inventory.product_low_stock} 种</Tag>
            )}
          </MCard>
        </Col>
      </Row>
    </div>
  );
}
