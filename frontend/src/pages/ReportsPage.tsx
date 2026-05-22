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
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  EscalationOut,
  HealthReport,
  KnowledgeRow,
  SalesSummary,
  fetchSalesBreakdown,
  fetchSalesSummary,
  getMonthlyReport,
  listKnowledge,
  runEscalation,
} from '../api/client';
import { Radio } from 'antd';

export default function ReportsPage() {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(() => dayjs());

  const { data, isLoading } = useQuery({
    queryKey: ['report', period.year(), period.month() + 1],
    queryFn: () => getMonthlyReport(period.year(), period.month() + 1),
  });

  const escalateMut = useMutation({
    mutationFn: runEscalation,
    onSuccess: (res) => {
      message.success(`${res.length} 组异常类型被升级严重度`);
      qc.invalidateQueries({ queryKey: ['report'] });
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          数据健康报告 (plan §12.2)
        </Typography.Title>
        <Space>
          <DatePicker.MonthPicker
            value={period}
            onChange={(v) => v && setPeriod(v)}
            allowClear={false}
          />
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => escalateMut.mutate()}
            loading={escalateMut.isPending}
          >
            异常严重度升级
          </Button>
        </Space>
      </Space>

      <Tabs
        items={[
          { key: 'sales', label: '销售汇总 (业务需求 15)', children: <SalesSummaryTab /> },
          { key: 'breakdown', label: '分产品销售 (业务需求 16)', children: <SalesBreakdownTab /> },
          { key: 'health', label: '本月健康度', children: <ReportTab data={data} isLoading={isLoading} /> },
          { key: 'knowledge', label: 'AI 知识库 (§12.2)', children: <KnowledgeTab /> },
          { key: 'escalations', label: '升级记录', children: <EscalationsTab last={escalateMut.data} /> },
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
            { title: '规则', dataIndex: 'rule', width: 200 },
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

function SalesSummaryTab() {
  const [period, setPeriod] = useState<Period>('30d');
  const { data, isLoading } = useQuery({
    queryKey: ['sales-summary', period],
    queryFn: () => fetchSalesSummary(period),
  });
  if (isLoading || !data) return <Spin />;
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <PeriodPicker value={period} onChange={setPeriod} />
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
  const { data, isLoading } = useQuery({
    queryKey: ['sales-breakdown', period],
    queryFn: () => fetchSalesBreakdown(period),
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <PeriodPicker value={period} onChange={setPeriod} />
      <Card size="small" title={`分 SKU 销售 (${data?.period_start} ~ ${data?.period_end})`}>
        <Table size="small" loading={isLoading}
               rowKey={(r) => `${r.product_code}_${r.sku_code}`}
               dataSource={data?.rows ?? []}
               pagination={{ pageSize: 30 }}
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
        pagination={{ pageSize: 20 }}
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

function EscalationsTab({ last }: { last?: EscalationOut[] }) {
  if (!last || last.length === 0) {
    return (
      <Empty
        description={
          <span style={{ color: '#999' }}>
            点上方「异常严重度升级」按钮跑一次。<br />
            规则: 同类型 open 异常 ≥3 时, 全部升一档严重度 (info → warning → error)。
          </span>
        }
      />
    );
  }
  return (
    <Table<EscalationOut>
      rowKey={(r) => r.exception_type + r.escalated_from}
      dataSource={last}
      size="small"
      pagination={false}
      columns={[
        { title: '异常类型', dataIndex: 'exception_type', width: 250 },
        { title: '原严重度', dataIndex: 'escalated_from', width: 100,
          render: (v: string) => <Tag>{v}</Tag> },
        { title: '→', width: 30 },
        { title: '新严重度', dataIndex: 'escalated_to', width: 100,
          render: (v: string) => <Tag color={{ warning: 'orange', error: 'red' }[v] ?? 'default'}>{v}</Tag> },
        { title: '影响条数', dataIndex: 'affected_ids', render: (v: number[]) => v.length },
      ]}
    />
  );
}
