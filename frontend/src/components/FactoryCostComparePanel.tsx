/**
 * 工厂实收 vs 我方预测 — 按月对比饼图。
 * 实收 = actual_cost(工厂结算价); 预测 = pricing factory_cost。
 * 只统计「已有工厂价格(actual_cost)」的成交单(用户拍板 2026-06-20)。
 * 覆盖率低的月(如 5/6月 工厂尚未结算)用红色标注 —— 对比仅代表已结算部分。
 */
import { lazy, Suspense } from 'react';
import { Alert, Card, Col, Row, Spin, Statistic, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchFactoryCostComparison, FactoryCostCompareMonth } from '../api/factoryRecon';

const ReactECharts = lazy(() => import('echarts-for-react'));

const money = (v: number | null | undefined) =>
  v == null ? '-' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;

// 实收 > 预测 = 比预想多付给工厂(偏贵, 红); 实收 < 预测 = 少付(偏省, 绿)
const diffColor = (d: number) => (d > 0 ? '#cf1322' : '#3f8600');

function monthDonut(m: FactoryCostCompareMonth) {
  return {
    tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
    color: ['#94a3b8', '#6366f1'], // 预测 灰, 实收 靛
    series: [{
      type: 'pie',
      radius: ['44%', '70%'],
      center: ['50%', '50%'],
      data: [
        { name: '预测', value: Math.round(m.predicted) },
        { name: '实收', value: Math.round(m.actual) },
      ],
      label: { show: true, formatter: '{b}\n¥{c}', fontSize: 10, color: '#475569', lineHeight: 13 },
      labelLine: { show: true, length: 6, length2: 6 },
    }],
  };
}

export default function FactoryCostComparePanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['factory-cost-comparison'],
    queryFn: fetchFactoryCostComparison,
  });

  if (isLoading) return <Card size="small"><Spin /> 加载工厂成本对比…</Card>;
  if (!data || !data.months.length) return null;

  const t = data.totals;
  const lowCoverage = data.months.filter((m) => m.coverage_pct < 50);

  return (
    <Card
      size="small"
      title={<span>工厂实收 vs 我方预测 — 按月对比饼图<Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>(仅统计已生成工厂价的单)</Typography.Text></span>}
    >
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={6}><Card size="small"><Statistic title="预测工厂成本合计" value={t.predicted} precision={0} prefix="¥" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="工厂实收合计" value={t.actual} precision={0} prefix="¥" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="差额(实收-预测)" value={t.diff} precision={0} prefix="¥" valueStyle={{ color: diffColor(t.diff) }} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="偏差率" value={t.diff_pct} precision={1} suffix="%" valueStyle={{ color: diffColor(t.diff) }} /></Card></Col>
      </Row>

      {lowCoverage.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`覆盖率偏低的月份(工厂尚未全部结算): ${lowCoverage.map((m) => `${m.month}(${m.coverage_pct}%)`).join('、')}`}
          description="这些月只有少数单生成了工厂实收价, 对比仅代表已结算部分, 待工厂出齐对账单后再看更准。"
        />
      )}

      <Row gutter={[12, 12]}>
        {data.months.map((m) => (
          <Col xs={12} sm={8} md={6} lg={4} key={m.month}>
            <Card size="small" styles={{ body: { padding: 8 } }}>
              <div style={{ textAlign: 'center', fontWeight: 600 }}>{m.month}</div>
              <Suspense fallback={<Spin />}>
                <ReactECharts option={monthDonut(m)} style={{ height: 150 }} />
              </Suspense>
              <div style={{ textAlign: 'center', fontSize: 12 }}>
                差 <span style={{ color: diffColor(m.diff) }}>{m.diff_pct > 0 ? '+' : ''}{m.diff_pct.toFixed(1)}%</span>
              </div>
              <div style={{ textAlign: 'center', fontSize: 12 }}>
                覆盖{' '}
                <Tag color={m.coverage_pct < 50 ? 'red' : 'green'} style={{ marginInlineEnd: 0 }}>
                  {m.coverage_pct}%
                </Tag>{' '}
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>{m.n_actual}/{m.n_total}单</Typography.Text>
              </div>
              <div style={{ textAlign: 'center', fontSize: 11, color: '#94a3b8' }}>
                预测{money(m.predicted)} / 实收{money(m.actual)}
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </Card>
  );
}
