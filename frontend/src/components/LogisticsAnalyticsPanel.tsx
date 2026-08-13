import { lazy, Suspense, useMemo, useState } from 'react';
import { Alert, Card, Col, DatePicker, Empty, Row, Select, Space, Spin, Statistic, Table, Tabs, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const ReactECharts = lazy(() => import('echarts-for-react'));
const { RangePicker } = DatePicker;

interface AggregateRow {
  shipment_count: number;
  total_freight: number;
  avg_freight: number;
  median_freight: number;
  min_freight: number;
  max_freight: number;
  avg_billing_weight_kg: number | null;
  avg_actual_weight_kg: number | null;
  avg_volume_m3: number | null;
  avg_freight_per_kg: number | null;
  volume_sample_count: number;
  actual_weight_sample_count: number;
  latest_bill_date: string;
  province?: string;
  city?: string;
  weight_band?: string;
  volume_band?: string;
  product_name?: string;
  product_code?: string;
  sku_name?: string;
  sku_code?: string;
  province_count?: number;
  month?: string;
  change_pct?: number | null;
  carrier?: string;
}

interface AnalyticsResponse {
  options: { products: string[]; provinces: string[]; carriers: string[] };
  overview: {
    shipment_count: number; total_freight: number; avg_freight: number; median_freight: number;
    matched_count: number; single_product_count: number; multi_product_count: number;
    multi_quantity_count: number;
    unmatched_product_count: number; billing_weight_coverage: number;
    actual_weight_coverage: number; volume_coverage: number;
  };
  regions: AggregateRow[];
  weight_volume_bands: AggregateRow[];
  products: AggregateRow[];
  product_regions: AggregateRow[];
  product_months: AggregateRow[];
  carriers: AggregateRow[];
  anomalies: Array<Record<string, any>>;
  methodology: Record<string, string>;
}

const money = (value: number | null | undefined) => value == null ? '-' : `¥${Number(value).toFixed(2)}`;
const sampleTag = (count: number) => count < 3 ? <Tag color="orange">样本{count}·仅参考</Tag> : <Tag color="blue">{count}票</Tag>;
const cardStyle = { borderRadius: 12, border: '1px solid #edf1f7' };

export default function LogisticsAnalyticsPanel() {
  const [product, setProduct] = useState<string>();
  const [province, setProvince] = useState<string>();
  const [carrier, setCarrier] = useState<string>();
  const [dates, setDates] = useState<[string, string] | undefined>();
  const { data, isLoading } = useQuery<AnalyticsResponse>({
    queryKey: ['logistics-bills-analytics', product, province, carrier, dates],
    queryFn: () => api.get('/api/finance/logistics-bills/analytics', {
      params: {
        product, province, carrier,
        date_start: dates?.[0], date_end: dates?.[1],
      },
    }).then(response => response.data),
  });

  const regionChart = useMemo(() => ({
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 18, top: 24, bottom: 65 },
    xAxis: { type: 'category', data: (data?.regions ?? []).slice(0, 15).map(row => `${row.province}\n${row.city}`), axisLabel: { interval: 0, rotate: 35 } },
    yAxis: [{ type: 'value', name: '平均运费' }, { type: 'value', name: '票数', minInterval: 1 }],
    series: [
      { type: 'bar', name: '平均运费', data: (data?.regions ?? []).slice(0, 15).map(row => row.avg_freight), itemStyle: { color: '#4f83ff', borderRadius: [5, 5, 0, 0] } },
      { type: 'line', name: '票数', yAxisIndex: 1, data: (data?.regions ?? []).slice(0, 15).map(row => row.shipment_count), itemStyle: { color: '#f59e0b' } },
    ],
  }), [data?.regions]);

  const trendChart = useMemo(() => {
    const rows = [...(data?.product_months ?? [])].sort((a, b) => String(a.month).localeCompare(String(b.month)));
    const names = Array.from(new Set(rows.map(row => `${row.product_name}${row.sku_name ? ` · ${row.sku_name}` : ''}`))).slice(0, 8);
    const months = Array.from(new Set(rows.map(row => row.month ?? '未知月'))).sort();
    return {
      tooltip: { trigger: 'axis' }, legend: { type: 'scroll', top: 0 },
      grid: { left: 48, right: 18, top: 48, bottom: 38 },
      xAxis: { type: 'category', data: months }, yAxis: { type: 'value', name: '平均运费' },
      series: names.map(name => ({
        name, type: 'line', smooth: true, connectNulls: false,
        data: months.map(month => rows.find(row => `${row.product_name}${row.sku_name ? ` · ${row.sku_name}` : ''}` === name && row.month === month)?.avg_freight ?? null),
      })),
    };
  }, [data?.product_months]);

  if (isLoading || !data) return <div style={{ padding: 60, textAlign: 'center' }}><Spin tip="计算物流统计…"><div style={{ minHeight: 50 }} /></Spin></div>;
  const commonColumns = [
    { title: '票数', dataIndex: 'shipment_count', width: 80, render: (value: number) => sampleTag(value) },
    { title: '平均运费', dataIndex: 'avg_freight', width: 100, render: money },
    { title: '中位数', dataIndex: 'median_freight', width: 90, render: money },
    { title: '范围', width: 140, render: (_: unknown, row: AggregateRow) => `${money(row.min_freight)} – ${money(row.max_freight)}` },
    { title: '平均计费重', dataIndex: 'avg_billing_weight_kg', width: 110, render: (value: number | null) => value == null ? '-' : `${value}kg` },
    { title: '平均体积', dataIndex: 'avg_volume_m3', width: 100, render: (value: number | null, row: AggregateRow) => value == null ? <Typography.Text type="secondary">缺数据</Typography.Text> : `${value}m³ (${row.volume_sample_count})` },
  ];

  return <Space direction="vertical" style={{ width: '100%' }} size="middle">
    <Alert type="info" showIcon message="统计口径：整票运费不会重复分摊。只有单一实体商品且数量=1的订单进入产品均价；多品合箱和同款多件只在账单明细展示。样本少于3票时统一标为“仅参考”。" />
    <Card size="small" style={cardStyle}>
      <Space wrap>
        <Select allowClear showSearch placeholder="选择产品" style={{ width: 260 }} value={product} onChange={setProduct}
          options={data.options.products.map(value => ({ value, label: value }))} />
        <Select allowClear showSearch placeholder="选择省份" style={{ width: 160 }} value={province} onChange={setProvince}
          options={data.options.provinces.map(value => ({ value, label: value }))} />
        <Select allowClear placeholder="选择承运商" style={{ width: 150 }} value={carrier} onChange={setCarrier}
          options={data.options.carriers.map(value => ({ value, label: value }))} />
        <RangePicker onChange={(value) => setDates(value ? [value[0]!.format('YYYY-MM-DD'), value[1]!.format('YYYY-MM-DD')] : undefined)} />
        <Typography.Text type="secondary">筛选同时联动所有表格与图表</Typography.Text>
      </Space>
    </Card>
    <Row gutter={[12, 12]}>
      <Col xs={12} md={6}><Card size="small" style={cardStyle}><Statistic title="物流票数" value={data.overview.shipment_count} suffix="票" /></Card></Col>
      <Col xs={12} md={6}><Card size="small" style={cardStyle}><Statistic title="总运费" value={data.overview.total_freight} precision={2} prefix="¥" /></Card></Col>
      <Col xs={12} md={6}><Card size="small" style={cardStyle}><Statistic title="平均 / 中位运费" value={data.overview.avg_freight} precision={2} prefix="¥" suffix={` / ¥${data.overview.median_freight.toFixed(2)}`} /></Card></Col>
      <Col xs={12} md={6}><Card size="small" style={cardStyle}><Statistic title="可做单件统计" value={data.overview.single_product_count} suffix={`票 · 合箱${data.overview.multi_product_count} · 多件${data.overview.multi_quantity_count}`} /></Card></Col>
    </Row>
    <Space wrap>
      <Tag color="blue">计费重量覆盖 {data.overview.billing_weight_coverage}%</Tag>
      <Tag color={data.overview.actual_weight_coverage >= 80 ? 'green' : 'orange'}>实际重量覆盖 {data.overview.actual_weight_coverage}%</Tag>
      <Tag color={data.overview.volume_coverage >= 80 ? 'green' : 'orange'}>体积覆盖 {data.overview.volume_coverage}%</Tag>
      <Tag color={data.overview.unmatched_product_count ? 'red' : 'green'}>缺产品关联 {data.overview.unmatched_product_count}票</Tag>
    </Space>
    <Tabs items={[
      { key: 'region', label: '区域与重量体积', children: <Space direction="vertical" style={{ width: '100%' }}>
        <Card size="small" title="各省市实际物流价格（前15个高频地区）" style={cardStyle}>
          {data.regions.length ? <Suspense fallback={<Spin />}><ReactECharts option={regionChart} style={{ height: 320 }} /></Suspense> : <Empty />}
        </Card>
        <Table size="small" rowKey={(row) => `${row.province}-${row.city}`} dataSource={data.regions}
          columns={[{ title: '省份', dataIndex: 'province', width: 110 }, { title: '城市', dataIndex: 'city', width: 110 }, ...commonColumns]}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }} scroll={{ x: 1000 }} />
        <Typography.Title level={5}>同省份 · 重量/体积分档</Typography.Title>
        <Table size="small" rowKey={(row) => `${row.province}-${row.weight_band}-${row.volume_band}`} dataSource={data.weight_volume_bands}
          columns={[{ title: '省份', dataIndex: 'province', width: 110 }, { title: '计费重量档', dataIndex: 'weight_band', width: 120 }, { title: '体积档', dataIndex: 'volume_band', width: 120 }, ...commonColumns]}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }} scroll={{ x: 1100 }} />
      </Space> },
      { key: 'product', label: '产品与地区', children: <Space direction="vertical" style={{ width: '100%' }}>
        <Table size="small" rowKey={(row) => `${row.product_code}-${row.sku_code}-${row.product_name}-${row.sku_name}`} dataSource={data.products}
          columns={[{ title: '产品', dataIndex: 'product_name', width: 220 }, { title: 'SKU/规格', dataIndex: 'sku_name', width: 180, render: (value) => value || '-' }, { title: '编码', width: 150, render: (_: unknown, row: AggregateRow) => row.sku_code || row.product_code || '-' }, { title: '覆盖省份', dataIndex: 'province_count', width: 90 }, ...commonColumns]}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }} scroll={{ x: 1250 }} />
        <Typography.Title level={5}>产品在不同省市的价格</Typography.Title>
        <Table size="small" rowKey={(row) => `${row.product_name}-${row.sku_name}-${row.province}-${row.city}`} dataSource={data.product_regions}
          columns={[{ title: '产品', dataIndex: 'product_name', width: 210 }, { title: 'SKU/规格', dataIndex: 'sku_name', width: 170, render: (value) => value || '-' }, { title: '省份', dataIndex: 'province', width: 100 }, { title: '城市', dataIndex: 'city', width: 100 }, ...commonColumns]}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }} scroll={{ x: 1250 }} />
      </Space> },
      { key: 'trend', label: '价格波动', children: <Space direction="vertical" style={{ width: '100%' }}>
        <Card size="small" title="产品月度平均运费趋势（最多显示8条线，可用产品筛选收窄）" style={cardStyle}>
          {data.product_months.length ? <Suspense fallback={<Spin />}><ReactECharts option={trendChart} style={{ height: 340 }} /></Suspense> : <Empty />}
        </Card>
        <Table size="small" rowKey={(row) => `${row.product_name}-${row.sku_name}-${row.month}`} dataSource={data.product_months}
          columns={[{ title: '月份', dataIndex: 'month', width: 100 }, { title: '产品', dataIndex: 'product_name', width: 220 }, { title: 'SKU/规格', dataIndex: 'sku_name', width: 180, render: (value) => value || '-' }, { title: '较上个有数据月份', dataIndex: 'change_pct', width: 140, render: (value: number | null) => value == null ? '-' : <Tag color={value > 10 ? 'red' : value < -10 ? 'green' : 'default'}>{value > 0 ? '+' : ''}{value}%</Tag> }, ...commonColumns]}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }} scroll={{ x: 1200 }} />
      </Space> },
      { key: 'carrier', label: `承运商与异常 (${data.anomalies.length})`, children: <Space direction="vertical" style={{ width: '100%' }}>
        <Table size="small" rowKey="carrier" dataSource={data.carriers}
          columns={[{ title: '承运商', dataIndex: 'carrier', width: 140 }, ...commonColumns]} pagination={false} scroll={{ x: 900 }} />
        <Alert type="warning" showIcon message="异常规则：同产品、同规格、同省份至少3票；单票运费同时高于中位数50%且多30元。这里只提示核查，不自动判定账单错误。" />
        <Table size="small" rowKey="bill_id" dataSource={data.anomalies}
          columns={[
            { title: '日期', dataIndex: 'bill_date', width: 110 }, { title: '产品', dataIndex: 'product_name', width: 190 },
            { title: '规格', dataIndex: 'sku_name', width: 150 }, { title: '地区', width: 150, render: (_: unknown, row: any) => `${row.province ?? ''} ${row.city ?? ''}` },
            { title: '运费', dataIndex: 'freight_amount', width: 90, render: money }, { title: '同组中位数', dataIndex: 'product_median_freight', width: 110, render: money },
            { title: '高出', dataIndex: 'above_median_pct', width: 90, render: (value: number) => <Tag color="red">+{value}%</Tag> },
            { title: '样本', dataIndex: 'sample_count', width: 80 }, { title: '运单号', dataIndex: 'tracking_no', width: 160 },
          ]} pagination={{ defaultPageSize: 20 }} scroll={{ x: 1100 }} />
      </Space> },
    ]} />
  </Space>;
}
