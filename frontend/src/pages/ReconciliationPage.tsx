import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  List,
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
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ReconcileWalkthroughResult,
  ReconciliationDiff,
  ReconciliationResult,
  reconcileWalkthrough,
  rerunSmartMatch,
  runReconciliation,
} from '../api/client';
import { useState } from 'react';
import dayjs, { Dayjs } from 'dayjs';

const { RangePicker } = DatePicker;

const RULE_LABELS: Record<string, { label: string; desc: string }> = {
  factory_payment: { label: '货款对账', desc: '工厂应付 ↔ 支付宝(工厂付款)' },
  install_fee: { label: '安装费收支', desc: '万师傅 CSV ↔ 售后表.安装费' },
  promotion: { label: '推广支出', desc: '推广记录.投入 ↔ 支付宝(推广)' },
  refill_compensation: { label: '补单赔实付', desc: '补单总成本 ↔ 主订单实付差额' },
  inventory_value: { label: '库存资产', desc: '配件库存 × 物料单价 = 账面价值' },
  logistics_fee: { label: '物流费销项', desc: '万师傅月结 ↔ 订单.运费汇总' },
};

const SEVERITY_COLOR: Record<string, string> = {
  ok: 'green',
  warning: 'orange',
  error: 'red',
  not_available: 'default',
};

export default function ReconciliationPage() {
  const qc = useQueryClient();
  const [walkthroughResult, setWalkthroughResult] = useState<ReconcileWalkthroughResult | null>(null);
  const [period, setPeriod] = useState<[Dayjs, Dayjs] | null>(null);

  const periodParams = period
    ? { period_start: period[0].format('YYYY-MM-DD'), period_end: period[1].format('YYYY-MM-DD') }
    : undefined;

  const { data, isLoading } = useQuery({
    queryKey: ['reconciliation', periodParams],
    queryFn: () =>
      runReconciliation(undefined, periodParams) as Promise<Record<string, ReconciliationResult>>,
  });

  const runMut = useMutation({
    mutationFn: () =>
      runReconciliation(undefined, periodParams) as Promise<Record<string, ReconciliationResult>>,
    onSuccess: (res) => {
      qc.setQueryData(['reconciliation', periodParams], res);
      message.success('对账完成');
    },
    onError: () => message.error('对账失败'),
  });

  const rematchMut = useMutation({
    mutationFn: () => rerunSmartMatch(),
    onSuccess: (res) => {
      const total = Object.values(res.tagged).reduce((s, n) => s + n, 0);
      message.success(`重新核销完成：本次新打标 ${total} 条，仍未识别 ${res.untouched} 条`);
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
    },
    onError: () => message.error('重新核销失败'),
  });

  const walkthroughMut = useMutation({
    mutationFn: reconcileWalkthrough,
    onSuccess: (res) => {
      setWalkthroughResult(res);
      message.success(`AI 走查完成，发现 ${res.total} 条问题`);
    },
    onError: () => message.error('AI 走查失败'),
  });

  if (isLoading) return <Spin />;
  if (!data) return <Empty />;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          财务对账面板 — plan §8 六条规则
        </Typography.Title>
        <Space>
          <RangePicker
            picker="date"
            allowClear
            value={period as any}
            onChange={(v) => setPeriod(v && v[0] && v[1] ? [v[0], v[1]] : null)}
            presets={[
              { label: '本月', value: [dayjs().startOf('month'), dayjs().endOf('month')] },
              { label: '上月', value: [dayjs().subtract(1, 'month').startOf('month'), dayjs().subtract(1, 'month').endOf('month')] },
              { label: '今年', value: [dayjs().startOf('year'), dayjs().endOf('year')] },
            ]}
          />
          <Button
            icon={<ReloadOutlined />}
            loading={rematchMut.isPending}
            onClick={() => rematchMut.mutate()}
            title="按 关联订单号→工厂名→关键字 重新给支付宝流水打核销类型"
          >
            重新核销
          </Button>
          <Button
            icon={<ThunderboltOutlined />}
            loading={walkthroughMut.isPending}
            onClick={() => walkthroughMut.mutate()}
          >
            AI 走查
          </Button>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={runMut.isPending}
            onClick={() => runMut.mutate()}
          >
            立即对账
          </Button>
        </Space>
      </Space>

      <Row gutter={[12, 12]}>
        {Object.entries(data).map(([rule, res]) => (
          <Col span={8} key={rule}>
            <Card size="small" title={
              <Space>
                <span>{RULE_LABELS[rule]?.label ?? rule}</span>
                {res.error_count > 0 && <Tag color="red">{res.error_count} 严重</Tag>}
                {res.warning_count > 0 && <Tag color="orange">{res.warning_count} 提示</Tag>}
                {(res as any).unresolved_count > 0 && (
                  <Badge count={(res as any).unresolved_count} title="未对清异常数" />
                )}
              </Space>
            }>
              <Space>
                <Statistic title="OK" value={res.ok_count} valueStyle={{ color: '#3f8600' }} />
                <Statistic
                  title="差异"
                  value={res.warning_count + res.error_count}
                  valueStyle={{ color: res.error_count > 0 ? '#cf1322' : '#d4b106' }}
                />
                <Statistic title="总计" value={res.total_diffs} />
              </Space>
              <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
                {RULE_LABELS[rule]?.desc}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {walkthroughResult && (
        <Card
          size="small"
          title={
            <Space>
              <span>AI 走查结果</span>
              <Tag color="blue">{walkthroughResult.total} 条</Tag>
              {walkthroughResult.ai_used && <Tag color="purple">AI 分析</Tag>}
            </Space>
          }
          extra={<Button size="small" onClick={() => setWalkthroughResult(null)}>关闭</Button>}
        >
          <List
            size="small"
            dataSource={walkthroughResult.issues.slice(0, 30)}
            renderItem={(item) => (
              <List.Item>
                <Tag color="warning" style={{ fontSize: 11 }}>{item.type}</Tag>
                <Typography.Text style={{ fontSize: 13 }}>
                  {item.ai_analysis || item.suggestion || item.description}
                </Typography.Text>
              </List.Item>
            )}
          />
        </Card>
      )}

      <Tabs
        items={Object.entries(data).map(([rule, res]) => ({
          key: rule,
          label: RULE_LABELS[rule]?.label ?? rule,
          children: <DiffTable rule={rule} result={res} />,
        }))}
      />
    </Space>
  );
}

function DiffTable({ rule, result }: { rule: string; result: ReconciliationResult }) {
  const notAvailable = result.diffs.length === 1 && result.diffs[0].severity === 'not_available';
  if (notAvailable) {
    return (
      <Alert
        type="info"
        showIcon
        message={result.diffs[0].message}
        description="可在后续 Phase 5/6 把 CSV 数据接入后启用"
      />
    );
  }

  const columns = [
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      render: (v: string) => <Tag color={SEVERITY_COLOR[v] ?? 'default'}>{v}</Tag>,
    },
    {
      title: '业务键', dataIndex: 'key', width: 200, ellipsis: true,
      render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code>,
    },
    {
      title: '主表 (应/账面)', dataIndex: 'expected', width: 130, align: 'right' as const,
      render: (v: string | null) => v != null ? `¥${v}` : '-',
    },
    {
      title: '校验 (实/对照)', dataIndex: 'actual', width: 130, align: 'right' as const,
      render: (v: string | null) => v != null ? `¥${v}` : '-',
    },
    {
      title: '差额', dataIndex: 'diff', width: 110, align: 'right' as const,
      render: (v: string | null) =>
        v == null ? '-' : (
          <span style={{ color: Number(v) === 0 ? '#666' : Number(v) > 0 ? '#3f8600' : '#cf1322', fontWeight: 600 }}>
            ¥{v}
          </span>
        ),
    },
    { title: '说明', dataIndex: 'message', ellipsis: true },
  ];

  return (
    <Table<ReconciliationDiff>
      rowKey="key"
      dataSource={result.diffs}
      columns={columns as any}
      size="small"
      pagination={{ pageSize: 20 }}
    />
  );
}
