import { useState } from 'react';
import { Card, Segmented, Space, Statistic, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  BrandMarketing,
  OutsourcingExpense,
  PromotionFlow,
  RoiResult,
  RoiMonthly,
  WoodLoss,
  getRoi,
  getRoiMonthly,
  listBrandMarketing,
  listOutsourcing,
  listPromotionFlows,
  listWoodLoss,
} from '../api/client';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';

interface DailyOperation {
  id: number;
  record_date: string | null;
  category: string | null;
  item: string | null;
  amount: number | null;
  expense_type: string | null;
  recipient: string | null;
  payment_account: string | null;
  remark: string | null;
}

function DailyTab() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const { data = [], isLoading } = useQuery<DailyOperation[]>({
    queryKey: ['daily-operations'],
    queryFn: () => api.get('/api/marketing/daily').then(r => r.data),
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="daily_operations" />}
      {viewMode === 'curated' && (
        <Table size="small" loading={isLoading} rowKey="id" dataSource={data}
          pagination={{ defaultPageSize: 100, showSizeChanger: true }}
          columns={[
            { title: '日期', dataIndex: 'record_date', width: 110 },
            { title: '分类', dataIndex: 'category', width: 90, render: (v: string | null) => v ? <Tag>{v}</Tag> : '-' },
            { title: '项目', dataIndex: 'item', width: 180, ellipsis: true },
            { title: '金额', dataIndex: 'amount', width: 100, align: 'right' as const,
              render: (v: number | null) => v != null ? `¥${Number(v).toFixed(2)}` : '-' },
            { title: '支出类型', dataIndex: 'expense_type', width: 100 },
            { title: '支付对象', dataIndex: 'recipient', width: 120, ellipsis: true },
            { title: '支付账户', dataIndex: 'payment_account', width: 110 },
            { title: '备注', dataIndex: 'remark', ellipsis: true },
          ]}
        />
      )}
    </Space>
  );
}

// 每个菜单项只看自己的分区 (用户拍板: 不再用页内 Tabs 混排; 导航条即入口)
const SECTION_TITLE: Record<string, string> = {
  promotion: '推广记录', brand: '品牌营销', daily: '日常经营',
  outsourcing: '人员外包', wood_loss: '木材损耗',
};

export default function MarketingPage() {
  const [params] = useSearchParams();
  const section = params.get('tab') || 'promotion';
  const { data: roi } = useQuery({
    queryKey: ['roi'], queryFn: () => getRoi(),
    enabled: section === 'promotion',   // ROI 只属于推广分区
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        {SECTION_TITLE[section] ?? '营销与经营'}
      </Typography.Title>

      {section === 'promotion' && (
        <>
          {roi && <RoiCard roi={roi} />}
          <RoiMonthlyCard />
          <PromotionTab />
        </>
      )}
      {section === 'brand' && <BrandTab />}
      {section === 'daily' && <DailyTab />}
      {section === 'outsourcing' && <OutsourcingTab />}
      {section === 'wood_loss' && <WoodLossTab />}
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

function RoiMonthlyCard() {
  const { data } = useQuery<RoiMonthly>({ queryKey: ['roi-monthly'], queryFn: () => getRoiMonthly() });
  if (!data || data.months.length === 0) return null;
  const pct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
  const yuan = (v: number) => `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
  return (
    <Card
      title="推广 ROI 按月占比（推广支出 ÷ 正式销售额，已剔除补单）"
      extra={<Typography.Text type="secondary">
        合计支出 {yuan(data.total_spend)} / 销售额 {yuan(data.total_revenue)} · 总占比 {pct(data.overall_spend_ratio)}
      </Typography.Text>}
    >
      <Table<RoiMonthly['months'][number]>
        rowKey="period"
        dataSource={data.months}
        pagination={false}
        size="small"
        columns={[
          { title: '月份', dataIndex: 'period' },
          { title: '推广支出', dataIndex: 'promotion_spend', align: 'right', render: (v) => <Typography.Text type="danger">{yuan(v)}</Typography.Text> },
          { title: '正式销售额', dataIndex: 'order_revenue', align: 'right', render: (v) => <Typography.Text type="success">{yuan(v)}</Typography.Text> },
          { title: '订单数', dataIndex: 'order_count', align: 'right' },
          {
            title: '推广占比', dataIndex: 'spend_ratio', align: 'right',
            render: (v: number | null) => {
              if (v == null) return '—';
              const color = v > 0.3 ? 'red' : v > 0.15 ? 'orange' : 'green';
              return <Tag color={color}>{pct(v)}</Tag>;
            },
          },
          { title: 'ROI', dataIndex: 'roi', align: 'right', render: (v: number | null) => (v == null ? '—' : `${v.toFixed(2)}×`) },
        ]}
      />
    </Card>
  );
}

function PromotionTab() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const { data, isLoading } = useQuery({ queryKey: ['promotion'], queryFn: listPromotionFlows });
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="promotion_flow" />}
      {viewMode === 'curated' && (
        <Table<PromotionFlow>
          rowKey="id"
          loading={isLoading}
          dataSource={data}
          size="middle"
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
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
      )}
    </Space>
  );
}

function BrandTab() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const { data, isLoading } = useQuery({ queryKey: ['brand'], queryFn: listBrandMarketing });
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="brand_marketing" />}
      {viewMode === 'curated' && (
        <Table<BrandMarketing>
          rowKey="id"
          loading={isLoading}
          dataSource={data}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '项目', dataIndex: 'project_name', ellipsis: true },
            { title: '类型', dataIndex: 'project_type', width: 100 },
            { title: '合作方', dataIndex: 'partner', ellipsis: true },
            { title: '预算', dataIndex: 'budget', render: (v: string | null) => v ? `¥${v}` : '-' },
            { title: '实际', dataIndex: 'actual_spend', render: (v: string | null) => v ? `¥${v}` : '-' },
            { title: '状态', dataIndex: 'status' },
          ]}
        />
      )}
    </Space>
  );
}

function WoodLossTab() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const { data, isLoading } = useQuery({ queryKey: ['wood-loss'], queryFn: listWoodLoss });

  const lossRateColor = (v: string | null) => {
    if (v == null) return 'default';
    const n = Number(v);
    if (n < 5) return 'green';
    if (n <= 15) return 'orange';
    return 'red';
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="wood_loss" />}
      {viewMode === 'curated' && (
        <Table<WoodLoss>
          rowKey="id"
          loading={isLoading}
          dataSource={data}
          size="middle"
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
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
      )}
    </Space>
  );
}

function OutsourcingTab() {
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const { data, isLoading } = useQuery({ queryKey: ['outsourcing'], queryFn: listOutsourcing });
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="outsourcing_expense" />}
      {viewMode === 'curated' && (
        <Table<OutsourcingExpense>
          rowKey="id"
          loading={isLoading}
          dataSource={data}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
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
      )}
    </Space>
  );
}
