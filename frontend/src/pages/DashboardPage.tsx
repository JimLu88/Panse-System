import { lazy, Suspense } from 'react';
import { Card, Col, Progress, Row, Spin, Statistic, Tag, Typography } from 'antd';
import { ArrowUpOutlined, ShoppingOutlined, AlertOutlined, DollarOutlined } from '@ant-design/icons';
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

  const { orders, inventory, finance, health } = data;

  // 订单状态饼图
  const pieOption = {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 10, top: 'center' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['35%', '50%'],
      data: Object.entries(orders.status_counts)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => ({ name: STATUS_LABELS[k] || k, value: v, itemStyle: { color: STATUS_COLORS[k] } })),
      label: { show: false },
    }],
  };

  // 近 30 天趋势折线
  const trendOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: ['订单数', '收入(¥)'] },
    xAxis: { type: 'category', data: orders.trend_30d.map(r => r.date.slice(5)) },
    yAxis: [
      { type: 'value', name: '订单数', minInterval: 1 },
      { type: 'value', name: '收入', axisLabel: { formatter: (v: number) => `¥${(v / 1000).toFixed(0)}k` } },
    ],
    series: [
      { name: '订单数', type: 'bar', data: orders.trend_30d.map(r => r.count), itemStyle: { color: '#1890ff' } },
      { name: '收入(¥)', type: 'line', yAxisIndex: 1, data: orders.trend_30d.map(r => r.revenue), smooth: true, itemStyle: { color: '#52c41a' } },
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

      {/* 库存 + 财务 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" title="配件库存">
            <Statistic title="品种数" value={inventory.part_total} />
            {inventory.part_negative > 0 && (
              <Tag color="error" style={{ marginTop: 8 }}>负库存 {inventory.part_negative} 种</Tag>
            )}
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
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" title="支付宝收入 (近 30 天)">
            <Statistic value={finance.alipay_income_30d} precision={0}
              formatter={(v) => money(Number(v))} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" title="订单收入 (近 30 天)">
            <Statistic value={finance.order_revenue_30d} precision={0}
              formatter={(v) => money(Number(v))} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
