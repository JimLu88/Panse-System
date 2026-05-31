import { lazy, Suspense } from 'react';
import { Card, Col, Progress, Row, Spin, Statistic, Tag, Tooltip, Typography } from 'antd';
import { ArrowUpOutlined, ShoppingOutlined, AlertOutlined, DollarOutlined, CheckCircleOutlined, ExclamationCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getDashboard } from '../api/client';

const ReactECharts = lazy(() => import('echarts-for-react'));

function ChartPlaceholder() {
  return <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>;
}

const STATUS_LABELS: Record<string, string> = {
  pending_payment: '待付款',
  paid: '已付款',
  shipped: '已发货',
  signed: '已签收',
  aftersales: '售后',
  cancelled: '已取消',
};

const STATUS_COLORS: Record<string, string> = {
  pending_payment: '#faad14',
  paid: '#1890ff',
  shipped: '#52c41a',
  signed: '#722ed1',
  aftersales: '#ff4d4f',
  cancelled: '#8c8c8c',
};

function money(v: number) {
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
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
    },
    series: [{
      type: 'radar',
      data: [{
        value: (health_dimensions || []).map((d: any) => d.score),
        name: '健康度',
        areaStyle: { color: 'rgba(24,144,255,0.25)' },
        lineStyle: { color: '#1890ff' },
        itemStyle: { color: '#1890ff' },
      }],
    }],
  };

  // 订单状态饼图
  const pieOption = {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 10, top: 'center' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['35%', '50%'],
      data: Object.entries(orders.status_counts as Record<string, number>)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => ({ name: STATUS_LABELS[k] || k, value: v, itemStyle: { color: STATUS_COLORS[k] } })),
      label: { show: false },
    }],
  };

  // 近 30 天趋势折线
  const trendOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: ['订单数', '收入(¥)'] },
    xAxis: { type: 'category', data: orders.trend_30d.map((r: any) => r.date.slice(5)) },
    yAxis: [
      { type: 'value', name: '订单数', minInterval: 1 },
      { type: 'value', name: '收入', axisLabel: { formatter: (v: number) => `¥${(v / 1000).toFixed(0)}k` } },
    ],
    series: [
      { name: '订单数', type: 'bar', data: orders.trend_30d.map((r: any) => r.count), itemStyle: { color: '#1890ff' } },
      { name: '收入(¥)', type: 'line', yAxisIndex: 1, data: orders.trend_30d.map((r: any) => r.revenue), smooth: true, itemStyle: { color: '#52c41a' } },
    ],
  };

  const healthColor = health.health_score >= 80 ? 'success' : health.health_score >= 60 ? 'normal' : 'exception';

  return (
    <div style={{ width: '100%' }}>
      <Typography.Title level={4} style={{ marginBottom: 16 }}>运营大盘</Typography.Title>

      {/* KPI 卡片行 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic
              title="近 7 天订单"
              value={orders.count_7d}
              prefix={<ShoppingOutlined />}
              suffix="单"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic
              title="近 30 天收入"
              value={orders.revenue_30d}
              precision={0}
              prefix={<DollarOutlined />}
              formatter={(v) => `¥${Number(v).toLocaleString()}`}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic
              title="待处理异常"
              value={health.open_exceptions}
              prefix={<AlertOutlined />}
              valueStyle={{ color: health.open_exceptions > 10 ? '#ff4d4f' : health.open_exceptions > 3 ? '#faad14' : '#52c41a' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" title="数据健康度">
            <Progress
              type="dashboard"
              percent={health.health_score}
              status={healthColor}
              size={80}
            />
          </Card>
        </Col>
      </Row>

      {/* 图表行 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="订单状态分布">
            <Suspense fallback={<ChartPlaceholder />}>
              <ReactECharts option={pieOption} style={{ height: 240 }} />
            </Suspense>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="近 30 天订单趋势">
            <Suspense fallback={<ChartPlaceholder />}>
              <ReactECharts option={trendOption} style={{ height: 240 }} />
            </Suspense>
          </Card>
        </Col>
      </Row>

      {/* 财务概览 (近 30 天) */}
      <Typography.Title level={5} style={{ margin: '8px 0' }}>财务概览 (近 30 天)</Typography.Title>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="订单收入" value={finance.order_revenue_30d}
            formatter={(v) => money(Number(v))} /></Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="理论成本" value={finance.theoretical_cost_30d}
            formatter={(v) => money(Number(v))} /></Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="实际成本" value={finance.actual_cost_30d}
            formatter={(v) => money(Number(v))} /></Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small">
            <Statistic title="毛利" value={finance.gross_profit_30d}
              formatter={(v) => money(Number(v))}
              valueStyle={{ color: finance.gross_profit_30d >= 0 ? '#3f8600' : '#cf1322' }} />
            <Tag style={{ marginTop: 8 }}
              color={finance.gross_margin_rate >= 0.15 ? 'success' : finance.gross_margin_rate >= 0 ? 'warning' : 'error'}>
              毛利率 {(finance.gross_margin_rate * 100).toFixed(1)}%
            </Tag>
          </Card>
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="支付宝收入" value={finance.alipay_income_30d}
            formatter={(v) => money(Number(v))} /></Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="对账未清" value={finance.reconciliation_unresolved} suffix="条"
            valueStyle={{ color: finance.reconciliation_unresolved > 0 ? '#faad14' : '#52c41a' }} /></Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="售后笔数" value={finance.aftersales_count} suffix="单" /></Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small"><Statistic title="售后成本" value={finance.aftersales_cost}
            formatter={(v) => money(Number(v))} valueStyle={{ color: '#cf1322' }} /></Card>
        </Col>
      </Row>

      {/* 对账健康 */}
      <Typography.Title level={5} style={{ margin: '8px 0' }}>对账健康</Typography.Title>
      <Row gutter={[12, 12]} style={{ marginBottom: 8 }}>
        {(recon_rules || []).map((rule: any) => {
          const isOk = rule.status === 'ok';
          const isWarn = rule.status === 'warning';
          const isErr = rule.status === 'error';
          const icon = isOk
            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
            : isWarn ? <ExclamationCircleOutlined style={{ color: '#faad14' }} />
            : <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
          const borderColor = isOk ? '#b7eb8f' : isWarn ? '#ffe58f' : '#ffa39e';
          const tip = isOk ? '无差异' : `错误 ${rule.error} · 警告 ${rule.warning}`;
          return (
            <Col key={rule.key} xs={12} sm={8} md={4}>
              <Tooltip title={tip}>
                <Card
                  size="small"
                  hoverable
                  onClick={() => nav('/reconciliation')}
                  style={{ borderColor, cursor: 'pointer', textAlign: 'center' }}
                  bodyStyle={{ padding: '8px 4px' }}
                >
                  <div style={{ fontSize: 18, marginBottom: 2 }}>{icon}</div>
                  <div style={{ fontSize: 12, fontWeight: 500, lineHeight: 1.3 }}>{rule.label}</div>
                  {!isOk && (
                    <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 2 }}>
                      {rule.error > 0 && <span style={{ color: '#ff4d4f' }}>✕{rule.error} </span>}
                      {rule.warning > 0 && <span style={{ color: '#faad14' }}>△{rule.warning}</span>}
                    </div>
                  )}
                </Card>
              </Tooltip>
            </Col>
          );
        })}
      </Row>

      {/* 健康雷达 + 月结清单 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={10}>
          <Card size="small" title="多维健康雷达">
            <Suspense fallback={<ChartPlaceholder />}>
              <ReactECharts option={radarOption} style={{ height: 260 }} />
            </Suspense>
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card size="small" title="本月月结清单"
            extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
                        padding: '4px 8px', borderRadius: 4,
                        background: m.done ? '#f6ffed' : '#fffbe6',
                        border: `1px solid ${m.done ? '#b7eb8f' : '#ffe58f'}`,
                        cursor: m.category === '对账' ? 'pointer' : 'default',
                      }}>
                      {m.done
                        ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                        : <ExclamationCircleOutlined style={{ color: '#faad14' }} />}
                      <span style={{ fontSize: 12, lineHeight: 1.2 }}>{m.label}</span>
                    </div>
                  </Tooltip>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
      </Row>

      {/* 库存运营 */}
      <Typography.Title level={5} style={{ margin: '8px 0' }}>库存运营</Typography.Title>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" title="配件库存">
            <Statistic title="品种数" value={inventory.part_total} />
            <div style={{ marginTop: 8 }}>
              {inventory.part_negative > 0 && <Tag color="error">负库存 {inventory.part_negative}</Tag>}
              {inventory.part_below_safety > 0 && <Tag color="warning">缺料 {inventory.part_below_safety}</Tag>}
              {inventory.part_oversold > 0 && <Tag color="volcano">超卖 {inventory.part_oversold}</Tag>}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" title="成品库存">
            <Statistic title="品种数" value={inventory.product_total} />
            {inventory.product_low_stock > 0 && (
              <Tag color="warning" style={{ marginTop: 8 }}>低库存 {inventory.product_low_stock} 种</Tag>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
