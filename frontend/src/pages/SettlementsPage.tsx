/**
 * 结算对账 — 两个标签:
 *  1) 逐笔对账: 每单一行的四方对账 (应付/实付/补贴/实收/2%补贴税/软件费 ↔ 实际到账 + 成本侧),
 *               按用户口径逐笔核对, 不按月总额对账。
 *  2) 结算明细导入: 导入 微信/聚合(billDetail) 与 支付宝 结算账单, 看每笔流水的收款/扣款。
 */
import { useState, type ReactNode } from 'react';
import {
  Alert, Card, Col, Input, Row, Segmented, Space, Statistic, Table, Tabs, Tag, Tooltip,
  Typography, Upload, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ReconGap, ReconRow, SettlementRow,
  fetchReconGap, fetchReconSummary, fetchSettlementSummary, importSettlementBill,
  listReconciliation, listSettlements,
} from '../api/settlements';

const SOURCE_LABEL: Record<string, string> = { wechat: '微信/聚合', alipay: '支付宝' };

const yuan = (v: number | null | undefined) =>
  v == null ? '-' : `¥${Number(v).toFixed(2)}`;

const STATUS_META: Record<string, { color: string; text: string }> = {
  matched: { color: 'success', text: '已对平' },
  diff: { color: 'error', text: '有差异' },
  pending: { color: 'default', text: '待补流水' },
};

function diffCell(v: number | null) {
  if (v == null) return <span style={{ color: '#bbb' }}>-</span>;
  const color = Math.abs(v) < 0.005 ? '#888' : v > 0 ? '#389e0d' : '#cf1322';
  return <span style={{ color }}>{v > 0 ? '+' : ''}{v.toFixed(2)}</span>;
}

const numCol = (
  title: ReactNode, key: keyof ReconRow, width = 96, tip?: string,
) => ({
  title: tip ? <Tooltip title={tip}>{title}</Tooltip> : title,
  dataIndex: key as string,
  width,
  align: 'right' as const,
  render: (v: number | null) => yuan(v),
});

// 到账覆盖缺口诊断: 按月铺开覆盖率, 指出该补哪几个月的流水/账单
function ReconGapCard() {
  const { data } = useQuery<ReconGap>({ queryKey: ['recon-gap'], queryFn: fetchReconGap });
  if (!data || data.months.length === 0) return null;
  return (
    <Card size="small" title="到账覆盖缺口诊断（按月该补哪批流水/账单）" style={{ marginTop: 12 }}>
      {data.worst_months.length > 0 && (
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message={`待补到账金额最高的月份: ${data.worst_months.join('、')} — 优先补导这几个月的早期订单 / billDetail / 企业号流水`}
        />
      )}
      <Table<ReconGap['months'][number]>
        rowKey="period"
        dataSource={data.months}
        pagination={false}
        size="small"
        columns={[
          { title: '月份', dataIndex: 'period' },
          { title: '订单', dataIndex: 'orders', align: 'right' },
          { title: '有到账', dataIndex: 'evidence', align: 'right' },
          { title: '待补', dataIndex: 'pending', align: 'right' },
          {
            title: '待补金额', dataIndex: 'pending_amount', align: 'right',
            render: (v: number) => <Tag color={v > 0 ? 'orange' : 'green'}>{yuan(v)}</Tag>,
          },
          {
            title: '覆盖率', dataIndex: 'coverage_pct', align: 'right',
            render: (v: number) => {
              const color = v >= 80 ? 'green' : v >= 30 ? 'orange' : 'red';
              return <Tag color={color}>{v}%</Tag>;
            },
          },
        ]}
      />
    </Card>
  );
}

// ---------------- 逐笔对账 ----------------
function ReconciliationTab() {
  const [status, setStatus] = useState<string>('');
  const [channel, setChannel] = useState<string>('');
  const [q, setQ] = useState<string>('');

  const { data: sum } = useQuery({ queryKey: ['recon-summary'], queryFn: fetchReconSummary });
  const { data, isLoading } = useQuery({
    queryKey: ['recon-list', status, channel, q],
    queryFn: () => listReconciliation({
      limit: 500,
      status: status || undefined,
      channel: channel || undefined,
      q: q || undefined,
    }),
  });

  const columns: ColumnsType<ReconRow> = [
    {
      title: '订单',
      children: [
        { title: '订单号', dataIndex: 'order_no', width: 180, fixed: 'left' as const,
          render: (v: string, r) => (
            <span>{v}{r.is_custom ? <Tag color="purple" style={{ marginLeft: 4 }}>定制</Tag> : null}</span>
          ) },
        { title: '日期', dataIndex: 'order_date', width: 100, render: (v: string | null) => v || '-' },
        { title: '产品', dataIndex: 'product_name', width: 160, ellipsis: true, render: (v: string | null) => v || '-' },
        { title: '客户', dataIndex: 'customer_name', width: 90, render: (v: string | null) => v || '-' },
      ],
    },
    {
      title: '收入侧 — 四方对账 (1 订单价 / 2 实际到账)',
      children: [
        numCol('买家应付', 'payable', 96, '卖家优惠后买家应付货款'),
        numCol('买家实付', 'paid', 96, '买家实际支付; (应付-实付)=平台优惠券补贴'),
        numCol('平台补贴', 'subsidy', 92, '平台优惠券补贴 = 应付 - 实付, 平台补给商家, 属应税收入'),
        { ...numCol('补贴税2%', 'tax', 90, '淘宝补贴属商家应税收入, 约2%税费经支付宝另付 (tax = 买家应付 × 2%)'),
          render: (v: number | null) => v == null ? '-' : <span style={{ color: '#d46b08' }}>{yuan(v)}</span> },
        numCol('软件费', 'platform_fee', 80, '平台软件服务费 (约0.6%)'),
        { ...numCol('店铺实收', 'received', 96, '平台口径净收 ≈ 应付 - 2%税 - 软件费'),
          render: (v: number | null) => <b>{yuan(v)}</b> },
        numCol('理论应到', 'expected_net', 96, '理论应到账 = 店铺实收 (或 应付-2%税-软件费)'),
        { ...numCol('实际到账', 'arrived', 100, '真金白银到账: 微信/聚合 + 支付宝'),
          render: (v: number | null, r) => v == null
            ? <Tag color="default">待补流水</Tag>
            : <b style={{ color: '#1677ff' }}>{yuan(v)}</b> },
        { title: <Tooltip title="实际到账 - 理论应到账">到账差额</Tooltip>, dataIndex: 'diff', width: 96,
          align: 'right' as const, render: (v: number | null) => diffCell(v) },
      ],
    },
    {
      title: '成本侧 (3 工厂成本 / 4 理论成本)',
      children: [
        numCol('理论成本', 'theoretical_cost', 92),
        numCol('实际成本', 'actual_cost', 92),
        { title: '成本差', dataIndex: 'cost_diff', width: 88, align: 'right' as const,
          render: (v: number | null) => diffCell(v) },
      ],
    },
    {
      title: '状态', fixed: 'right' as const,
      children: [
        { title: '渠道', dataIndex: 'channels', width: 90,
          render: (ch: string[]) => ch.length
            ? ch.map((c) => <Tag key={c} color={c === '微信' ? 'green' : 'blue'}>{c}</Tag>)
            : <span style={{ color: '#bbb' }}>-</span> },
        { title: '对账', dataIndex: 'status', width: 92,
          render: (s: string) => { const m = STATUS_META[s] ?? { color: 'default', text: s };
            return <Tag color={m.color}>{m.text}</Tag>; } },
      ],
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="逐笔对账口径 (不按月总额对，逐笔核对)"
        description={
          <div style={{ lineHeight: 1.7 }}>
            因工厂制作约30天 + 7天自动确认收货 + 平台收支延迟，<b>月度总额对不齐是正常的</b>，需逐笔订单核对。每单四方对照：
            <b>① 订单价</b>(买家应付 / 实付 / 店铺实收) · <b>② 实际到账</b>(支付宝企业号 + 微信聚合) ·
            <b>③ 工厂成本</b> · <b>④ 理论成本</b>。
            其中 <b style={{ color: '#d46b08' }}>淘宝平台优惠券补贴属于商家应税收入，约 2% 税费经支付宝另行支付</b>，
            已计入「补贴税2%」列：<code>理论应到账 = 买家应付 − 2%补贴税 − 软件服务费 = 店铺实收</code>。
            到账证据 = 微信聚合(billDetail) + 支付宝企业号流水；尚未导入流水的订单标「待补流水」。
          </div>
        }
      />

      {sum && (
        <>
          <Row gutter={12}>
            <Col span={4}><Card size="small"><Statistic title="纳入订单" value={sum.orders} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="买家应付合计" value={sum.payable_sum} precision={2} prefix="¥" /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="店铺实收合计" value={sum.received_sum} precision={2} prefix="¥" /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="补贴税合计(2%)" value={sum.tax_sum} precision={2} prefix="¥" valueStyle={{ color: '#d46b08' }} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="软件费合计" value={sum.platform_fee_sum} precision={2} prefix="¥" /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="平台补贴合计" value={sum.subsidy_sum} precision={2} prefix="¥" /></Card></Col>
          </Row>
          <Row gutter={12}>
            <Col span={5}><Card size="small"><Statistic title="实际到账合计" value={sum.arrived_sum} precision={2} prefix="¥" valueStyle={{ color: '#1677ff' }} /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="已对平" value={sum.matched} suffix="单" valueStyle={{ color: '#389e0d' }} /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="有差异" value={sum.diff} suffix="单" valueStyle={{ color: '#cf1322' }} /></Card></Col>
            <Col span={5}><Card size="small"><Statistic title="待补流水" value={sum.pending} suffix="单" /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="到账覆盖率" value={sum.coverage_pct} suffix="%" /></Card></Col>
          </Row>
          {sum.coverage_pct < 50 && (
            <Alert
              type="warning" showIcon
              message={`到账流水覆盖率仅 ${sum.coverage_pct}% (${sum.evidence_orders}/${sum.orders} 单有到账记录)`}
              description="多数订单尚无支付宝/微信到账流水可比对。请在「结算明细导入」继续导入 billDetail，并补导早期订单与支付宝企业号流水，覆盖率会随之上升。订单侧金额(应付/实付/实收/2%税)已完整。"
            />
          )}
          <ReconGapCard />
        </>
      )}

      <Card size="small" title="逐笔明细">
        <Space wrap style={{ marginBottom: 12 }}>
          <span>对账状态:</span>
          <Segmented
            value={status}
            onChange={(v) => setStatus(v as string)}
            options={[
              { label: '全部', value: '' },
              { label: '已对平', value: 'matched' },
              { label: '有差异', value: 'diff' },
              { label: '待补流水', value: 'pending' },
            ]}
          />
          <span>渠道:</span>
          <Segmented
            value={channel}
            onChange={(v) => setChannel(v as string)}
            options={[
              { label: '全部', value: '' },
              { label: '微信', value: 'wechat' },
              { label: '支付宝', value: 'alipay' },
              { label: '无到账', value: 'none' },
            ]}
          />
          <Input.Search
            allowClear placeholder="订单号 / 客户名"
            style={{ width: 220 }}
            onSearch={(v) => setQ(v)}
          />
        </Space>
        <Table<ReconRow>
          rowKey="order_no" size="small" loading={isLoading}
          dataSource={data?.rows ?? []}
          pagination={{ pageSize: 50, showTotal: (t) => `共 ${t} 单 (筛选后)` }}
          scroll={{ x: 1900 }}
          bordered
        />
      </Card>
    </Space>
  );
}

// ---------------- 结算明细导入 ----------------
function SettlementDetailTab() {
  const qc = useQueryClient();
  const [source, setSource] = useState<'wechat' | 'alipay'>('wechat');

  const { data: sum } = useQuery({ queryKey: ['settlement-summary'], queryFn: fetchSettlementSummary });
  const { data: rows = [], isLoading } = useQuery({ queryKey: ['settlements'], queryFn: () => listSettlements(300) });

  const importMut = useMutation({
    mutationFn: (file: File) => importSettlementBill(file, source),
    onSuccess: (r) => {
      if (r.error) { message.error(r.error); return; }
      message.success(`导入完成:新增 ${r.inserted ?? 0} 笔 / 更新 ${r.updated ?? 0} 笔`);
      qc.invalidateQueries({ queryKey: ['settlements'] });
      qc.invalidateQueries({ queryKey: ['settlement-summary'] });
      qc.invalidateQueries({ queryKey: ['recon-summary'] });
      qc.invalidateQueries({ queryKey: ['recon-list'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="导入淘宝结算账单(billDetail)"
        description="微信支付的订单货款走「聚合账户」,导出的 billDetail 即聚合收支明细;支付宝支付的订单在支付宝企业号流水。两者都有 订单号/入账时间/收款/扣款,导入后即可在「逐笔对账」里看实际到账。"
      />

      <Card size="small" title="导入账单">
        <Space wrap>
          <span>来源:</span>
          <Segmented
            value={source}
            onChange={(v) => setSource(v as 'wechat' | 'alipay')}
            options={[{ label: '微信 / 聚合 (billDetail)', value: 'wechat' }, { label: '支付宝 结算', value: 'alipay' }]}
          />
          <Upload
            accept=".xlsx,.xls"
            showUploadList={false}
            beforeUpload={(file) => { importMut.mutate(file as File); return false; }}
          >
            <Tag color="blue" style={{ cursor: 'pointer', padding: '4px 10px' }}>
              <UploadOutlined /> 选择 billDetail 文件上传
            </Tag>
          </Upload>
          {importMut.isPending && <span>导入中…</span>}
        </Space>
      </Card>

      {sum && (
        <Row gutter={12}>
          <Col span={5}><Card size="small"><Statistic title="结算笔数" value={sum.count} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="覆盖订单数" value={sum.orders} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="收款合计" value={sum.income} precision={2} prefix="¥" /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="扣款合计" value={sum.expense} precision={2} prefix="¥" valueStyle={{ color: '#cf1322' }} /></Card></Col>
          <Col span={4}><Card size="small"><Statistic title="净到账" value={sum.net} precision={2} prefix="¥" valueStyle={{ color: '#389e0d' }} /></Card></Col>
        </Row>
      )}

      <Card size="small" title="结算明细(近 300 笔)">
        <Table<SettlementRow>
          rowKey="id" size="small" loading={isLoading} dataSource={rows}
          pagination={{ pageSize: 50 }}
          columns={[
            { title: '来源', dataIndex: 'source', width: 90, render: (v) => <Tag>{SOURCE_LABEL[v] ?? v}</Tag> },
            { title: '入账时间', dataIndex: 'settle_time', width: 160, render: (v) => v ? new Date(v).toLocaleString('zh-CN') : <Tag color="warning">无日期</Tag> },
            { title: '淘宝订单编号', dataIndex: 'order_no', width: 180, render: (v) => v || '-' },
            { title: '入账类型', dataIndex: 'entry_type', width: 100 },
            { title: '收款', dataIndex: 'income', width: 100, align: 'right' as const, render: (v) => v > 0 ? `¥${v.toFixed(2)}` : '-' },
            { title: '扣款', dataIndex: 'expense', width: 100, align: 'right' as const, render: (v) => v > 0 ? <span style={{ color: '#cf1322' }}>¥{v.toFixed(2)}</span> : '-' },
            { title: '业务描述', dataIndex: 'description', ellipsis: true },
          ]}
        />
      </Card>
    </Space>
  );
}

export default function SettlementsPage() {
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>结算对账</Typography.Title>
      <Tabs
        defaultActiveKey="recon"
        items={[
          { key: 'recon', label: '逐笔对账', children: <ReconciliationTab /> },
          { key: 'detail', label: '结算明细导入', children: <SettlementDetailTab /> },
        ]}
      />
    </Space>
  );
}
