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
  getMonthlyReport,
  listKnowledge,
  runEscalation,
} from '../api/client';

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
