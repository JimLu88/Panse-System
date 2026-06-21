import { lazy, Suspense } from 'react';
import { Alert, Card, Space, Statistic, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const ReactECharts = lazy(() => import('echarts-for-react'));

interface VRow {
  order_no: string;
  customer_name?: string | null;
  product_name?: string | null;
  est: number;
  actual: number;
  diff: number;
  diff_pct: number | null;
}
interface VResp {
  rows: VRow[];
  count: number;
  total_est: number;
  total_actual: number;
  total_diff: number;
  diff_pct: number | null;
  gaps: { order_no: string; customer_name?: string | null; actual: number }[];
  gap_count: number;
  gap_actual: number;
}

const yuan = (v: number) => `¥${v.toLocaleString('zh', { maximumFractionDigits: 0 })}`;

/** 费用「实际 vs 预估」对比: 总计卡 + 饼图 + 逐单偏差表 + 数据缺口提示。
 *  url=variance 端点; label=费用名(物流费/打包费)。 */
export default function FeeVariancePanel({ url, label, queryKey }:
  { url: string; label: string; queryKey: string }) {
  const { data } = useQuery<VResp>({ queryKey: [queryKey], queryFn: () => api.get(url).then(r => r.data) });
  if (!data) return null;
  if (data.count === 0 && data.gap_count === 0) {
    return <Alert type="info" showIcon message={`暂无配到订单的${label}实际数据，导入并配单后这里会显示「实际 vs 预估」对比。`} />;
  }

  const up = data.total_diff > 0;   // 实际 > 预估 = 成本升、利润降
  // 逐单 预估 vs 实际 分组条形图(按偏差大小排前12单, 直观看哪单实际偏离预估)
  const topN = data.rows.slice(0, 12);
  const labelOf = (r: VRow) => (r.customer_name || r.order_no.slice(-6)) + ` ·${r.order_no.slice(-4)}`;
  const barOption = {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: (v: number) => yuan(v) },
    legend: { top: 0, itemWidth: 12, itemHeight: 12 },
    grid: { left: 8, right: 24, top: 30, bottom: 6, containLabel: true },
    xAxis: { type: 'value', axisLabel: { formatter: (v: number) => yuan(v) } },
    yAxis: { type: 'category', inverse: true, data: topN.map(labelOf),
      axisLabel: { fontSize: 11 } },
    series: [
      { name: '预估', type: 'bar', data: topN.map(r => Math.round(r.est)),
        itemStyle: { color: '#94a3b8' } },
      { name: '实际', type: 'bar', data: topN.map(r => Math.round(r.actual)),
        itemStyle: { color: '#6366f1' } },
    ],
  };

  const columns = [
    { title: '订单号', dataIndex: 'order_no', width: 170, ellipsis: true,
      render: (v: string) => <Typography.Text style={{ fontSize: 12 }} copyable={{ text: v }}>{v}</Typography.Text> },
    { title: '客户', dataIndex: 'customer_name', width: 90, render: (v: string | null) => v || '-' },
    { title: '预估', dataIndex: 'est', width: 90, align: 'right' as const, render: (v: number) => yuan(v) },
    { title: '实际', dataIndex: 'actual', width: 90, align: 'right' as const,
      render: (v: number) => <span style={{ color: '#6366f1' }}>{yuan(v)}</span> },
    { title: '偏差', dataIndex: 'diff', width: 100, align: 'right' as const,
      render: (v: number) => <span style={{ color: v > 0 ? '#cf1322' : '#389e0d', fontWeight: 600 }}>
        {v > 0 ? '+' : ''}{yuan(v)}</span> },
    { title: '偏差%', dataIndex: 'diff_pct', width: 90, align: 'right' as const,
      render: (v: number | null) => v == null ? '-'
        : <Tag color={v > 0 ? 'red' : 'green'}>{v > 0 ? '+' : ''}{v}%</Tag> },
  ];

  return (
    <Card size="small" title={`${label} · 实际 vs 预估 对比`} style={{ marginTop: 8 }}>
      <Space size="large" wrap style={{ width: '100%' }}>
        <Statistic title="预估合计" value={data.total_est} prefix="¥" valueStyle={{ fontSize: 20 }} />
        <Statistic title="实际合计" value={data.total_actual} prefix="¥" valueStyle={{ fontSize: 20, color: '#6366f1' }} />
        <Statistic title={up ? '偏差(成本↑利润↓)' : '偏差(成本↓利润↑)'} value={Math.abs(data.total_diff)}
          prefix={up ? '+¥' : '−¥'} valueStyle={{ fontSize: 20, color: up ? '#cf1322' : '#389e0d' }}
          suffix={data.diff_pct != null ? `  (${data.diff_pct > 0 ? '+' : ''}${data.diff_pct}%)` : ''} />
        <Statistic title="参与替换单数" value={data.count} suffix="单" valueStyle={{ fontSize: 20 }} />
      </Space>
      {topN.length > 0 && (
        <>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            逐单 预估 vs 实际（按偏差排序{data.rows.length > 12 ? '，前 12 单' : ''}）：
          </Typography.Text>
          <Suspense fallback={<div style={{ height: 240 }} />}>
            <ReactECharts option={barOption}
              style={{ width: '100%', height: Math.max(220, topN.length * 28 + 50) }} />
          </Suspense>
        </>
      )}

      {data.gap_count > 0 && (
        <Alert type="warning" showIcon style={{ margin: '8px 0' }}
          message={`另有 ${data.gap_count} 单配到实际、但 SKU 在定价表里缺${label}预估（实际合计 ${yuan(data.gap_actual)}）— 这些单未参与成本替换。给这些 SKU 补上预估后会自动纳入。`} />
      )}

      <Table size="small" rowKey="order_no" dataSource={data.rows} columns={columns}
        pagination={data.rows.length > 10 ? { defaultPageSize: 10 } : false}
        scroll={{ x: 640 }} style={{ marginTop: 8 }} />
    </Card>
  );
}
