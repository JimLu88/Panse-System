/**
 * 销售预测 + 备货建议 + 滞销 (Phase 4, 业务需求 7/8).
 *
 * 三个卡片:
 *   - 未来 30 天 SKU 预测销量 (移动平均 + 20% 安全系数)
 *   - 智能提前备货建议 (物料 lead_time 倒推何时下单)
 *   - 滞销分类 (长期未售 / 超大库存)
 */
import {
  Alert,
  Card,
  Col,
  Row,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import {
  fetchForecast30d,
  fetchSlowMoving,
  fetchStockAdvice,
} from '../api/client';
import ProductThumb from '../components/ProductThumb';

// SKU 构成标签 (按产品聚合后的明细; 定制咨询类 SKU 也归并在所属产品下)
function skuTags(r: any) {
  const skus: { sku: string; qty_60d: number }[] = r.skus ?? [];
  if (!skus.length) return <Typography.Text type="secondary">—</Typography.Text>;
  return (
    <Space size={4} wrap>
      {skus.slice(0, 4).map((s) => (
        <Tag key={s.sku} style={{ fontSize: 11 }}>{s.sku} ×{s.qty_60d}</Tag>
      ))}
      {skus.length > 4 && <Tag style={{ fontSize: 11 }}>+{skus.length - 4} 个</Tag>}
    </Space>
  );
}

// 产品列: 图 + 名称 + 编码 (没名没图的行至少有编码兜底)
function productCell(r: any) {
  return (
    <Space size={8}>
      <ProductThumb src={r.image_url || null} size={40} />
      <span>
        <div>{r.product_name || <Typography.Text type="secondary">—</Typography.Text>}</div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.product_code || '—'}</Typography.Text>
      </span>
    </Space>
  );
}


export default function ForecastPage() {
  return (
    <Tabs items={[
      { key: 'forecast', label: '销售预测 (30 天)', children: <ForecastTab /> },
      { key: 'advice', label: '备货建议', children: <AdviceTab /> },
      { key: 'slow', label: '滞销分类', children: <SlowMovingTab /> },
    ]} />
  );
}

function ForecastTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['forecast-30d'], queryFn: fetchForecast30d,
  });
  return (
    <Card size="small" title="未来 30 天预测销量 (基于过去 60 天移动平均 × 1.2)">
      <Table
        size="small" loading={isLoading}
        rowKey="product_code"
        dataSource={data?.forecast ?? []}
        columns={[
          { title: '产品', width: 240, render: (_: any, r: any) => productCell(r),
            sorter: (a: any, b: any) => String(a.product_name ?? a.product_code).localeCompare(String(b.product_name ?? b.product_code)) },
          { title: 'SKU 构成 (60天销量)', render: (_: any, r: any) => skuTags(r) },
          { title: '过去 60 天总销', dataIndex: 'last_60d_total', width: 130,
            sorter: (a: any, b: any) => (a.last_60d_total ?? 0) - (b.last_60d_total ?? 0) },
          { title: '日均', dataIndex: 'avg_daily', width: 100 },
          { title: '未来 30 天预测', dataIndex: 'forecast_30d', width: 130,
            render: (v: number) => <Tag color="blue">{v}</Tag>,
          },
        ]}
        pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
      />
    </Card>
  );
}

function AdviceTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['stock-advice'], queryFn: fetchStockAdvice,
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
             message="智能提前备货 = 按预测 30 天销量 + BOM 倒推每个物料的需求, 减去现库存"
             description="补货周期 lead_time_days 天的物料, 应在第 (30 - lead) 天前下单. should_order_now=true 表示现在就该下单了" />
      <Card size="small" title="按产品: 未来 30 天产能缺口">
        <Table
          size="small" loading={isLoading}
          rowKey={(r: any) => `${r.product_code}`}
          dataSource={data?.products ?? []}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '产品', width: 240, render: (_: any, r: any) => productCell(r),
              sorter: (a: any, b: any) => String(a.product_name ?? a.product_code).localeCompare(String(b.product_name ?? b.product_code)) },
            { title: '预测 30 天', dataIndex: 'forecast_30d', width: 110,
              sorter: (a: any, b: any) => (a.forecast_30d ?? 0) - (b.forecast_30d ?? 0) },
            { title: '现成品库存', dataIndex: 'in_stock', width: 110 },
            { title: '需生产', dataIndex: 'need_to_produce', width: 100,
              render: (v: number) => v > 0 ?
                <Tag color="orange">{v}</Tag> : <Tag color="green">充足</Tag>,
            },
          ]}
        />
      </Card>
      <Card size="small" title="按物料: 应下单时间">
        <Table
          size="small" loading={isLoading}
          rowKey={(r: any) => r.material_code}
          dataSource={data?.materials ?? []}
          pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '物料', dataIndex: 'material_code' },
            { title: '名称', dataIndex: 'material_name' },
            { title: '需求量', dataIndex: 'need_qty', width: 100 },
            { title: '现库存', dataIndex: 'have_qty', width: 80 },
            { title: '缺口', dataIndex: 'missing', width: 90,
              render: (v: number) => v > 0 ? <Tag color="red">{v.toFixed(0)}</Tag> : '-',
            },
            { title: '补货周期', dataIndex: 'lead_time_days', width: 90,
              render: (v: number) => `${v} 天`,
            },
            { title: '建议下单日', dataIndex: 'alert_at', width: 120 },
            { title: '优先级', dataIndex: 'priority', width: 70,
              render: (v: string) => <Tag color={
                v === 'high' ? 'red' : v === 'mid' ? 'orange' : 'default'
              }>{v}</Tag>,
            },
            { title: '现在该下单', dataIndex: 'should_order_now', width: 110,
              render: (v: boolean) => v ?
                <Tag color="red">立即下单</Tag> :
                <Tag color="default">未到时间</Tag>,
            },
          ]}
        />
      </Card>
    </Space>
  );
}

function SlowMovingTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['slow-moving'], queryFn: () => fetchSlowMoving(),
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="warning" showIcon
        message={`阈值: ${data?.thresholds?.long_no_sale_days ?? 60} 天未售 = 长期滞销; > ${data?.thresholds?.overstock_ratio ?? 3} 倍预测 = 超大库存`}
      />
      <Row gutter={16}>
        <Col span={12}>
          <Card size="small" title="长期未售 (物料)">
            <Table size="small" loading={isLoading}
                   rowKey="material_code"
                   dataSource={data?.long_idle ?? []}
                   pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
                   columns={[
                     { title: '物料', dataIndex: 'material_code', width: 110 },
                     { title: '名称', dataIndex: 'material_name', ellipsis: true },
                     { title: '库存', dataIndex: 'physical_qty', width: 80 },
                     { title: '最后出货', dataIndex: 'last_outbound_at' },
                     { title: '滞销天数', dataIndex: 'days_since', width: 90,
                       render: (v: number) => <Tag color="orange">{v} 天</Tag> },
                   ]} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="超大库存 (成品)">
            <Table size="small" loading={isLoading}
                   rowKey={(r: any) => `${r.product_code}_${r.sku}`}
                   dataSource={data?.overstock ?? []}
                   pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
                   columns={[
                     { title: '产品', width: 220, render: (_: any, r: any) => productCell(r) },
                     { title: 'SKU', dataIndex: 'sku' },
                     { title: '库存', dataIndex: 'physical_qty', width: 80 },
                     { title: '预测 30 天', dataIndex: 'forecast_30d', width: 100 },
                     { title: '倍数', dataIndex: 'ratio', width: 80,
                       render: (v: number) => <Tag color="red">{v} ×</Tag> },
                   ]} />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
