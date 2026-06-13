import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Input,
  List,
  Modal,
  Row,
  Segmented,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ReconcileWalkthroughResult,
  ReconciliationDiff,
  ReconciliationResult,
  detectRefunds,
  getFactoryAliases,
  listReconciliationWriteoffs,
  saveFactoryAliases,
  matchFactoryAlipay,
  rebuildFactoryReconciliation,
  reconcileWalkthrough,
  rerunSmartMatch,
  routeAlipayFlows,
  runReconciliation,
  writeoffReconciliationDiff,
} from '../api/client';
import { Suspense, lazy, useEffect, useState } from 'react';
import dayjs, { Dayjs } from 'dayjs';
import { listReconSnapshots, matchExpenseFlows } from '../api/client';

const ReactECharts = lazy(() => import('echarts-for-react'));

const { RangePicker } = DatePicker;

const RULE_LABELS: Record<string, { label: string; desc: string }> = {
  factory_payment: { label: '货款对账', desc: '工厂应付 ↔ 支付宝(工厂付款)' },
  install_fee: { label: '安装费收支', desc: '万师傅 CSV ↔ 售后表.安装费' },
  promotion: { label: '推广支出', desc: '推广记录.投入 ↔ 支付宝(推广)' },
  refill_compensation: { label: '补单赔实付', desc: '补单总成本 ↔ 主订单实付差额' },
  inventory_value: { label: '库存资产', desc: '配件库存 × 物料单价 = 账面价值' },
  logistics_fee: { label: '物流费销项', desc: '万师傅月结 ↔ 订单.运费汇总' },
  revenue_alipay: { label: '收入对账', desc: '订单营收 ↔ 支付宝订单收入 (按月)' },
  operating_expense: { label: '经营支出', desc: '日常经营/外包/品牌 ↔ 支付宝(按流水号)' },
  purchase_payment: { label: '采购付款', desc: '配件采购单 ↔ 支付宝(按流水号)' },
  refill_commission_payout: { label: '补单佣金代付', desc: '补单记录.佣金 ↔ 代付台账实付 (按月)' },
  refill_express_payout: { label: '补单快递代付', desc: '补单记录.补单运费 ↔ 代付台账实付 (按月)' },
  aftersales_payout: { label: '售后赔付代付', desc: '售后表(赔付+好评返+二次上门+返厂运费) ↔ 代付台账实付 (按月)' },
  refund_reconciliation: { label: '退款进出对账', desc: '退款支出 ↔ 退款回流 (按流水配对)' },
  ledger_check: { label: '总账勾稽', desc: '账户余额月变动 ↔ 支付宝流水净额 (漏导/录错一眼现形)' },
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
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  // 点概要卡片 → 切到对应明细 tab (用户反馈: 点卡片没反应)
  const [activeRule, setActiveRule] = useState<string | undefined>(undefined);
  // 工厂别名自助维护 (用户拍板: 不需要经过开发)
  const [aliasOpen, setAliasOpen] = useState(false);
  // 已人工做平的差异键 {rule: [key...]}
  const { data: writeoffs } = useQuery({
    queryKey: ['recon-writeoffs'], queryFn: listReconciliationWriteoffs,
  });
  const writeoffMut = useMutation({
    mutationFn: ({ rule, key, reason }: { rule: string; key: string; reason: string }) =>
      writeoffReconciliationDiff(rule, key, reason),
    onSuccess: () => {
      message.success('已做平, 以后对账不再报这条差异');
      qc.invalidateQueries({ queryKey: ['recon-writeoffs'] });
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '做平失败'),
  });
  const askWriteoff = (rule: string, key: string) => {
    let reason = '';
    Modal.confirm({
      title: `做平 ${RULE_LABELS[rule]?.label ?? rule} · ${key}`,
      content: (
        <div>
          <p>做平 = 确认这条差异无法/无需再追, 系统永久豁免, 不再报异常。动作会记入「修改档案」。</p>
          <Input.TextArea rows={2} placeholder="做平原因 (必填), 如: 历史账无凭证 / 线下已结清"
            onChange={(e) => { reason = e.target.value; }} />
        </div>
      ),
      okText: '确认做平',
      okButtonProps: { danger: true },
      onOk: () => {
        if (!reason.trim()) { message.warning('请填写做平原因'); return Promise.reject(); }
        return writeoffMut.mutateAsync({ rule, key, reason: reason.trim() });
      },
    });
  };

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

  const detectRefundsMut = useMutation({
    mutationFn: detectRefunds,
    onSuccess: (res) => {
      message.success(res.message);
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
    },
    onError: () => message.error('退款对识别失败'),
  });

  const routeMut = useMutation({
    mutationFn: () => routeAlipayFlows(true),
    onSuccess: (res) => {
      message.success(
        `归类完成 — 售后建${res.aftersales_created} 推广${res.promotion_filled} ` +
        `日常${res.daily_filled} 外包${res.outsourcing_filled} ` +
        `采购${res.purchases_created} 工厂翻付${res.factory_flipped}`,
      );
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
    },
    onError: () => message.error('归类流水失败'),
  });

  // 经营支出自动配流水 (用户拍板): 只在金额+日期窗口唯一命中时回填, 多候选留人工
  const expenseMatchMut = useMutation({
    mutationFn: matchExpenseFlows,
    onSuccess: (res) => {
      const total = Object.values(res.matched).reduce((s, n) => s + n, 0);
      Modal.info({
        title: '经营支出自动配流水完成',
        width: 640,
        content: (
          <div>
            <p>
              配上 {total} 条（{Object.entries(res.matched).map(([k, v]) => `${k} ${v}`).join('、')}）；
              同金额多候选留人工 {res.ambiguous} 条；窗口内没找到 {res.unmatched} 条。
            </p>
            {res.details.length > 0 && (
              <div style={{ maxHeight: 300, overflow: 'auto', fontSize: 12, lineHeight: 1.8 }}>
                {res.details.map((d, i) => <div key={i}>{d}</div>)}
              </div>
            )}
            <p style={{ color: '#999', fontSize: 12, marginTop: 8 }}>
              回填动作已记入「修改档案」，可回溯撤销。
            </p>
          </div>
        ),
      });
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '自动配流水失败'),
  });

  const matchFactoryMut = useMutation({
    mutationFn: () => matchFactoryAlipay().then(async (r) => {
      await rebuildFactoryReconciliation();
      return r;
    }),
    onSuccess: (res) => {
      message.success(res.message + ' — 对账汇总已重算');
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
    },
    onError: () => message.error('工厂流水匹配失败'),
  });

  if (isLoading) return <Spin />;
  if (!data) return <Empty />;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列 (工厂对账)', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="factory_reconciliation" />}
      {viewMode === 'curated' && (
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
            loading={detectRefundsMut.isPending}
            onClick={() => detectRefundsMut.mutate()}
            title="识别支付宝流水中金额相等方向相反的退款对，避免被归为重复流水"
          >
            退款对识别
          </Button>
          <Button
            icon={<ThunderboltOutlined />}
            loading={routeMut.isPending}
            onClick={() => routeMut.mutate()}
            title="将支付宝流水归类回填到推广/日常/外包/售后/采购各表，并翻转工厂已付款状态"
          >
            归类流水
          </Button>
          <Button
            icon={<ReloadOutlined />}
            loading={matchFactoryMut.isPending}
            onClick={() => matchFactoryMut.mutate()}
            title="按工厂账单金额在支付宝支出流水中找等额记录，回填工厂订单流水号并重算对账汇总"
          >
            工厂流水匹配
          </Button>
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
            loading={expenseMatchMut.isPending}
            onClick={() => expenseMatchMut.mutate()}
            title="给缺流水号的 日常经营/人员外包/品牌营销 记录按金额+日期窗口配支付宝支出流水；只在唯一命中时回填并留痕"
          >
            自动配流水
          </Button>
          <Button onClick={() => setAliasOpen(true)}>工厂别名</Button>
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
      {writeoffs?.synced_at && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          异常池最近同步: {writeoffs.synced_at.slice(0, 16).replace('T', ' ')} (UTC) — 每日自动对账后更新
        </Typography.Text>
      )}
      <FactoryAliasModal open={aliasOpen} onClose={() => setAliasOpen(false)} />

      <Row gutter={[12, 12]}>
        {Object.entries(data).map(([rule, res]) => (
          <Col span={8} key={rule}>
            <Card size="small" hoverable
              onClick={() => {
                setActiveRule(rule);
                document.getElementById('recon-detail-tabs')?.scrollIntoView({ behavior: 'smooth' });
              }}
              title={
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

      {(writeoffs?.count ?? 0) > 0 && (
        <Alert type="warning" showIcon
          message={`已人工做平 ${writeoffs!.count} 条差异, 涉及金额合计 ¥${Math.round(writeoffs!.grand_total).toLocaleString()}`}
          description="做平是永久豁免 — 金额持续变大说明有系统性问题被掩盖, 请定期复查 (明细见 工具→修改档案 搜「做平」)。" />
      )}
      <SnapshotTrendCard />

      <div id="recon-detail-tabs">
      <Tabs
        activeKey={activeRule ?? Object.keys(data)[0]}
        onChange={setActiveRule}
        items={Object.entries(data).map(([rule, res]) => ({
          key: rule,
          label: RULE_LABELS[rule]?.label ?? rule,
          children: (
            <DiffTable rule={rule} result={res}
              writtenOff={new Set(writeoffs?.keys?.[rule] ?? [])}
              onWriteoff={(key) => askWriteoff(rule, key)} />
          ),
        }))}
      />
      </div>
      </Space>
      )}
    </Space>
  );
}

const SEVERITY_LABEL: Record<string, string> = {
  ok: '正常', warning: '提示', error: '严重', not_available: '未启用',
};

// 对账快照趋势图 (用户拍板): 每日 23:30 调度写 recon_snapshots, 这里画近 N 天
// 各规则差异趋势 — 看差异是在收敛还是恶化。
function SnapshotTrendCard() {
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<'amount' | 'count'>('amount');
  const { data: rows } = useQuery({
    queryKey: ['recon-snapshots', days],
    queryFn: () => listReconSnapshots(days),
  });
  if (!rows || rows.length === 0) {
    return (
      <Card size="small" title="差异趋势 (每日快照)">
        <Typography.Text type="secondary">
          暂无快照数据 — 每晚 23:30 自动记录一次, 运行一天后这里会出现趋势线。
        </Typography.Text>
      </Card>
    );
  }
  const dates = [...new Set(rows.map((r) => r.snap_date))].sort();
  const rules = [...new Set(rows.map((r) => r.rule))];
  const byKey = new Map(rows.map((r) => [`${r.snap_date}|${r.rule}`, r]));
  const series = rules.map((rule) => ({
    name: RULE_LABELS[rule]?.label ?? rule,
    type: 'line',
    smooth: true,
    showSymbol: dates.length <= 31,
    data: dates.map((d) => {
      const r = byKey.get(`${d}|${rule}`);
      if (!r) return null;
      return metric === 'amount' ? Math.round(r.total_diff_abs) : r.warning + r.error;
    }),
  }));
  const option = {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 11 } },
    grid: { left: 60, right: 16, top: 24, bottom: 40 },
    xAxis: { type: 'category', data: dates.map((d) => d.slice(5)) },
    yAxis: {
      type: 'value',
      axisLabel: metric === 'amount'
        ? { formatter: (v: number) => `¥${v >= 10000 ? `${Math.round(v / 10000)}万` : v}` }
        : undefined,
    },
    series,
  };
  return (
    <Card
      size="small"
      title="差异趋势 (每日快照) — 线往下走 = 账越对越平"
      extra={
        <Space>
          <Segmented
            size="small"
            value={metric}
            onChange={(v) => setMetric(v as 'amount' | 'count')}
            options={[
              { label: '差异金额', value: 'amount' },
              { label: '差异条数', value: 'count' },
            ]}
          />
          <Segmented
            size="small"
            value={days}
            onChange={(v) => setDays(v as number)}
            options={[
              { label: '近30天', value: 30 },
              { label: '近90天', value: 90 },
              { label: '近180天', value: 180 },
            ]}
          />
        </Space>
      }
    >
      <Suspense fallback={<Spin />}>
        <ReactECharts option={option} style={{ height: 300 }} notMerge />
      </Suspense>
    </Card>
  );
}

// 工厂别名自助维护弹窗: 支付宝对手方掩码名(如 **晶) ↔ 工厂下单表标准名 对不上时,
// 在这里加一行映射, 货款对账两侧自动归一。改动留痕修改档案。
function FactoryAliasModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: aliases } = useQuery({
    queryKey: ['factory-aliases'], queryFn: getFactoryAliases, enabled: open,
  });
  const [rows, setRows] = useState<{ alias: string; canonical: string }[]>([]);
  useEffect(() => {
    if (aliases) {
      setRows(Object.entries(aliases).map(([alias, canonical]) => ({ alias, canonical })));
    }
  }, [aliases]);
  const saveMut = useMutation({
    mutationFn: () => saveFactoryAliases(Object.fromEntries(
      rows.filter((r) => r.alias.trim() && r.canonical.trim())
          .map((r) => [r.alias.trim(), r.canonical.trim()]),
    )),
    onSuccess: () => {
      message.success('别名已保存, 点「立即对账」按新映射比对');
      qc.invalidateQueries({ queryKey: ['factory-aliases'] });
      qc.invalidateQueries({ queryKey: ['reconciliation'] });
      onClose();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });
  return (
    <Modal open={open} onCancel={onClose} title="工厂别名映射"
           okText="保存" confirmLoading={saveMut.isPending} onOk={() => saveMut.mutate()}>
      <Alert type="info" showIcon style={{ marginBottom: 10 }}
        message="左边填支付宝里的对手方名 (如 **晶), 右边填工厂下单表里的标准工厂名。货款对账两侧名称会先按此表归一再比对。" />
      {rows.map((r, i) => (
        <Space key={i} style={{ marginBottom: 6 }}>
          <Input style={{ width: 170 }} placeholder="别名 (如 **晶)" value={r.alias}
            onChange={(e) => { const rs = [...rows]; rs[i] = { ...r, alias: e.target.value }; setRows(rs); }} />
          <span>→</span>
          <Input style={{ width: 200 }} placeholder="标准工厂名" value={r.canonical}
            onChange={(e) => { const rs = [...rows]; rs[i] = { ...r, canonical: e.target.value }; setRows(rs); }} />
          <Button size="small" danger onClick={() => setRows(rows.filter((_, j) => j !== i))}>删</Button>
        </Space>
      ))}
      <Button size="small" onClick={() => setRows([...rows, { alias: '', canonical: '' }])}>
        加一行
      </Button>
    </Modal>
  );
}

function DiffTable({ rule, result, writtenOff, onWriteoff }: {
  rule: string; result: ReconciliationResult;
  writtenOff: Set<string>; onWriteoff: (key: string) => void;
}) {
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
      render: (v: string, r: ReconciliationDiff) =>
        writtenOff.has(r.key)
          ? <Tag>已做平</Tag>
          : <Tag color={SEVERITY_COLOR[v] ?? 'default'}>{SEVERITY_LABEL[v] ?? v}</Tag>,
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
    {
      title: '操作', width: 80,
      render: (_: unknown, r: ReconciliationDiff) =>
        r.severity === 'ok' || r.severity === 'not_available' || writtenOff.has(r.key)
          ? null
          : <Button size="small" onClick={() => onWriteoff(r.key)}>做平</Button>,
    },
  ];

  return (
    <PresetTable<ReconciliationDiff>
      tableKey="reconciliation_diff"
      rowKey="key"
      dataSource={result.diffs}
      columns={columns as any}
      size="small"
      pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
    />
  );
}
