import { Card, Space, Statistic, Table, Tabs, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import {
  AfterSalesRow,
  BrandMarketing,
  OutsourcingExpense,
  PromotionFlow,
  RoiResult,
  Sample,
  WoodLoss,
  getRoi,
  listAfterSales,
  listBrandMarketing,
  listOutsourcing,
  listPromotionFlows,
  listSamples,
  listWoodLoss,
} from '../api/client';

export default function MarketingPage() {
  const { data: roi } = useQuery({ queryKey: ['roi'], queryFn: () => getRoi() });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        营销与售后 (Phase 5)
      </Typography.Title>

      {roi && <RoiCard roi={roi} />}

      <Tabs
        items={[
          { key: 'promotion', label: '推广记录 (15)', children: <PromotionTab /> },
          { key: 'brand', label: '品牌营销 (14)', children: <BrandTab /> },
          { key: 'samples', label: '样品 (13)', children: <SamplesTab /> },
          { key: 'wood_loss', label: '木材损耗 (12)', children: <WoodLossTab /> },
          { key: 'aftersales', label: '售后 (18)', children: <AfterSalesTab /> },
          { key: 'outsourcing', label: '人员外包 (17)', children: <OutsourcingTab /> },
        ]}
      />
    </Space>
  );
}

function RoiCard({ roi }: { roi: RoiResult }) {
  const roiNum = roi.roi != null ? Number(roi.roi) : null;
  return (
    <Card title="推广 ROI 概览 (Phase 5)">
      <Space size="large" wrap>
        <Statistic
          title="推广支出"
          value={roi.promotion_spend}
          prefix="¥"
          valueStyle={{ color: '#cf1322' }}
        />
        <Statistic title="推广充值" value={roi.promotion_recharge} prefix="¥" />
        <Statistic title="订单数" value={roi.order_count} />
        <Statistic title="订单营收" value={roi.order_revenue} prefix="¥" valueStyle={{ color: '#3f8600' }} />
        <Statistic title="平均客单价" value={roi.avg_order_value} prefix="¥" />
        <Statistic
          title="ROI"
          value={roiNum != null ? roiNum.toFixed(2) : '—'}
          suffix="×"
          valueStyle={{ color: roiNum != null && roiNum > 1 ? '#3f8600' : '#cf1322' }}
        />
      </Space>
    </Card>
  );
}

function PromotionTab() {
  const { data, isLoading } = useQuery({ queryKey: ['promotion'], queryFn: listPromotionFlows });
  return (
    <Table<PromotionFlow>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      size="middle"
      pagination={{ pageSize: 20 }}
      columns={[
        { title: '日期', dataIndex: 'transaction_date', width: 120 },
        {
          title: '类型',
          dataIndex: 'flow_type',
          width: 100,
          render: (v: string | null) => v ? <Tag color={v === '支出' ? 'red' : v === '充值' ? 'green' : 'default'}>{v}</Tag> : '-',
        },
        {
          title: '金额',
          dataIndex: 'amount',
          width: 120,
          align: 'right' as const,
          render: (v: string) => `¥${v}`,
        },
        { title: '余额', dataIndex: 'balance_after', width: 120, align: 'right' as const, render: (v: string | null) => v ? `¥${v}` : '-' },
        { title: '备注', dataIndex: 'remark', ellipsis: true },
      ]}
    />
  );
}

function BrandTab() {
  const { data, isLoading } = useQuery({ queryKey: ['brand'], queryFn: listBrandMarketing });
  return (
    <Table<BrandMarketing>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      pagination={{ pageSize: 20 }}
      columns={[
        { title: '项目', dataIndex: 'project_name', ellipsis: true },
        { title: '类型', dataIndex: 'project_type', width: 100 },
        { title: '合作方', dataIndex: 'partner', ellipsis: true },
        { title: '预算', dataIndex: 'budget', render: (v: string | null) => v ? `¥${v}` : '-' },
        { title: '实际', dataIndex: 'actual_spend', render: (v: string | null) => v ? `¥${v}` : '-' },
        { title: '状态', dataIndex: 'status' },
      ]}
    />
  );
}

function SamplesTab() {
  const { data, isLoading } = useQuery({ queryKey: ['samples'], queryFn: listSamples });

  const totalCost = (data ?? []).reduce((sum, row) => {
    const c = row.cost != null ? Number(row.cost) : 0;
    return sum + c;
  }, 0);

  const statusColor = (v: string | null) => {
    if (!v) return 'default';
    if (v === '在用') return 'green';
    if (v === '闲置') return 'orange';
    if (v === '报废') return 'red';
    return 'default';
  };

  const summaryRow = () => (
    <Table.Summary.Row>
      <Table.Summary.Cell index={0} colSpan={5}>
        <strong>合计</strong>
      </Table.Summary.Cell>
      <Table.Summary.Cell index={5} align="right">
        <strong>¥{totalCost.toFixed(2)}</strong>
      </Table.Summary.Cell>
      <Table.Summary.Cell index={6} colSpan={4} />
    </Table.Summary.Row>
  );

  return (
    <Table<Sample>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      pagination={{ pageSize: 20 }}
      summary={summaryRow}
      columns={[
        { title: '样品号', dataIndex: 'sample_no', width: 110, render: (v) => <code>{v}</code> },
        { title: '产品', dataIndex: 'product_name', ellipsis: true },
        { title: 'SKU', dataIndex: 'sku', ellipsis: true },
        { title: '类型', dataIndex: 'sample_type', width: 90 },
        { title: '数量', dataIndex: 'qty', width: 60 },
        {
          title: '成本',
          dataIndex: 'cost',
          width: 100,
          align: 'right' as const,
          render: (v: string | null) => v ? `¥${v}` : '-',
        },
        { title: '制作日期', dataIndex: 'made_at', width: 110 },
        { title: '位置', dataIndex: 'location', width: 140 },
        {
          title: '状态',
          dataIndex: 'status',
          width: 80,
          render: (v: string | null) => v ? <Tag color={statusColor(v)}>{v}</Tag> : '-',
        },
        { title: '用途', dataIndex: 'usage', width: 100 },
      ]}
    />
  );
}

function WoodLossTab() {
  const { data, isLoading } = useQuery({ queryKey: ['wood-loss'], queryFn: listWoodLoss });

  const lossRateColor = (v: string | null) => {
    if (v == null) return 'default';
    const n = Number(v);
    if (n < 5) return 'green';
    if (n <= 15) return 'orange';
    return 'red';
  };

  return (
    <Table<WoodLoss>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      size="middle"
      pagination={{ pageSize: 20 }}
      columns={[
        { title: '购买日期', dataIndex: 'purchase_date', width: 110 },
        { title: '木材种类', dataIndex: 'wood_type', width: 100 },
        { title: '规格', dataIndex: 'spec', ellipsis: true },
        { title: '单位', dataIndex: 'unit', width: 60 },
        {
          title: '入库量',
          dataIndex: 'inbound_qty',
          width: 90,
          align: 'right' as const,
          render: (v: string | null) => v ?? '-',
        },
        {
          title: '实用量',
          dataIndex: 'used_qty',
          width: 90,
          align: 'right' as const,
          render: (v: string | null) => v ?? '-',
        },
        {
          title: '损耗量',
          dataIndex: 'loss_qty',
          width: 90,
          align: 'right' as const,
          render: (v: string | null) => v ?? '-',
        },
        {
          title: '损耗率%',
          dataIndex: 'loss_rate_pct',
          width: 90,
          align: 'center' as const,
          render: (v: string | null) =>
            v != null ? <Tag color={lossRateColor(v)}>{Number(v).toFixed(1)}%</Tag> : '-',
        },
        {
          title: '可做家具数',
          dataIndex: 'related_product_qty',
          width: 100,
          align: 'right' as const,
          render: (v: string | null) => v ?? '-',
        },
        { title: '原因', dataIndex: 'reason', ellipsis: true },
        { title: '处理方式', dataIndex: 'disposition', ellipsis: true },
      ]}
    />
  );
}

function AfterSalesTab() {
  const { data, isLoading } = useQuery({ queryKey: ['after-sales'], queryFn: listAfterSales });
  return (
    <Table<AfterSalesRow>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      pagination={{ pageSize: 20 }}
      columns={[
        { title: '平台订单号', dataIndex: 'platform_order_no', width: 200, render: (v) => <code style={{ fontSize: 11 }}>{v}</code> },
        { title: '原因', dataIndex: 'reason', ellipsis: true },
        { title: '平台内成本', dataIndex: 'in_platform_total', render: (v: string | null) => v ? `¥${v}` : '-' },
        { title: '平台外成本', dataIndex: 'out_platform_total', render: (v: string | null) => v ? `¥${v}` : '-' },
        { title: '补发 SKU', dataIndex: 'refill_sku', ellipsis: true },
        { title: '状态', dataIndex: 'status' },
        { title: '满意度', dataIndex: 'customer_satisfaction' },
        { title: '处理日期', dataIndex: 'processed_at' },
      ]}
    />
  );
}

function OutsourcingTab() {
  const { data, isLoading } = useQuery({ queryKey: ['outsourcing'], queryFn: listOutsourcing });
  return (
    <Table<OutsourcingExpense>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      pagination={{ pageSize: 20 }}
      columns={[
        { title: '收款人', dataIndex: 'payee', width: 120 },
        {
          title: '金额',
          dataIndex: 'amount',
          width: 120,
          align: 'right' as const,
          render: (v: string) => <span style={{ color: '#cf1322' }}>¥{v}</span>,
        },
        { title: '项目', dataIndex: 'project' },
        {
          title: '成本属性',
          dataIndex: 'cost_category',
          width: 110,
          render: (v: string | null) => v ? <Tag color={v === '固定成本' ? 'blue' : 'orange'}>{v}</Tag> : '-',
        },
        { title: '支付日期', dataIndex: 'payment_date', width: 120 },
      ]}
    />
  );
}
