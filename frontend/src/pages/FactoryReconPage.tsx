/**
 * 工厂逐单对账 — 导入工厂侧对账单 xlsx (价格=工厂结算价=成本),
 * 逐月对账「应付(Σ结算价) ↔ 实付(支付宝 factory_payment)」, 对不上的逐单「填原因做平」。
 */
import { useState } from 'react';
import PresetTable from '../components/PresetTable';
import {
  Alert, Button, Card, Col, Dropdown, Input, InputNumber, Modal, Row, Segmented, Select, Space, Statistic,
  Table, Tag, Typography, Upload, message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FactoryReconItem, FactoryReconPreviewRow, RESOLUTION_KINDS, confirmFactoryReconItem,
  fetchFactoryReconPreview, fetchFactoryReconSummary,
  importFactoryRecon, listFactoryReconItems, resolveFactoryReconItem, splitFactoryReconItem,
} from '../api/factoryRecon';

function PreviewTable({ rows }: { rows: FactoryReconPreviewRow[] }) {
  return (
    <PresetTable<FactoryReconPreviewRow>
      tableKey="factory_recon_preview"
      rowKey="factory_order_no" size="small" dataSource={rows}
      pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (t) => `共 ${t} 单` }}
      columns={[
        { title: '工厂单号', dataIndex: 'factory_order_no', width: 150, render: (v) => v || '-' },
        { title: '淘宝订单号', dataIndex: 'platform_order_no', width: 175, render: (v) => v || '-' },
        { title: '工厂', dataIndex: 'factory_name', width: 130, ellipsis: true, render: (v) => v || '-' },
        { title: '应付(预估)', dataIndex: 'payable', width: 100, align: 'right' as const,
          render: (v: number | null) => (v == null ? <Tag>无</Tag> : `¥${Number(v).toFixed(0)}`) },
        { title: '应付来源', dataIndex: 'payable_source', width: 150, render: (v) => <span style={{ fontSize: 12, color: '#888' }}>{v}</span> },
        { title: '下单', dataIndex: 'order_date', width: 100, render: (v) => v || '-' },
      ]}
    />
  );
}

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  balanced: { color: 'green', label: '已对平' },
  explained: { color: 'blue', label: '已归因做平' },
  diff: { color: 'red', label: '待归因' },
};

export default function FactoryReconPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const [q, setQ] = useState('');
  const [resolving, setResolving] = useState<FactoryReconItem | null>(null);
  const [reason, setReason] = useState('');

  const { data: sum } = useQuery({ queryKey: ['factory-recon-summary'], queryFn: fetchFactoryReconSummary });
  const { data: items, isLoading } = useQuery({
    queryKey: ['factory-recon-items', statusFilter, period, q],
    queryFn: () => listFactoryReconItems({
      status: statusFilter === 'all' ? undefined : statusFilter,
      period, q: q || undefined,
    }),
  });

  // 工厂对账单未导入(逐单明细为空)时, 拉「我方下单逐单预估」补上, 不再空页
  const noBillItems = !isLoading && (items?.rows?.length ?? 0) === 0;
  const { data: preview } = useQuery({
    queryKey: ['factory-recon-preview'],
    queryFn: fetchFactoryReconPreview,
    enabled: noBillItems,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['factory-recon-summary'] });
    qc.invalidateQueries({ queryKey: ['factory-recon-items'] });
  };

  const importMut = useMutation({
    mutationFn: (file: File) => importFactoryRecon(file),
    onSuccess: (r) => {
      if (r.errors?.length) { message.error(r.errors[0]); return; }
      message.success(
        `导入完成: 新增 ${r.inserted} / 重复 ${r.skipped_duplicate} / 无效 ${r.skipped_invalid}`
        + ` · 回填成本 ${r.backfilled_cost} 单`
        + (r.unmapped_columns?.length ? ` · 未识别列: ${r.unmapped_columns.join(',')}` : '')
        + (r.duplicate_upload ? ' · 该文件曾上传过' : ''),
      );
      refresh();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  const resolveMut = useMutation({
    mutationFn: ({ id, reason, resolved }: { id: number; reason: string; resolved: boolean }) =>
      resolveFactoryReconItem(id, reason, resolved),
    onSuccess: () => {
      message.success('已更新');
      setResolving(null);
      setReason('');
      refresh();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '操作失败'),
  });

  // Plan L5: 确认归因 (漏单/价差/运费/补偿/其他) + 拆分归因
  const confirmMut = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: string }) => confirmFactoryReconItem(id, kind),
    onSuccess: (r: any) => { message.success(`已确认归因: ${r.resolution_kind}`); refresh(); },
    onError: (e: any) => message.error(typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : '确认失败'),
  });
  const [splitting, setSplitting] = useState<FactoryReconItem | null>(null);
  const [splitParts, setSplitParts] = useState<Array<{ amount: number | null; kind: string; remark: string }>>([]);
  const openSplit = (row: FactoryReconItem) => {
    setSplitting(row);
    setSplitParts([
      { amount: row.settle_price, kind: '价差', remark: '' },
      { amount: 0, kind: '运费', remark: '' },
    ]);
  };
  const splitMut = useMutation({
    mutationFn: () => splitFactoryReconItem(
      splitting!.id,
      splitParts.map((p) => ({ amount: String(p.amount ?? 0), resolution_kind: p.kind, remark: p.remark || undefined })),
    ),
    onSuccess: () => { message.success('已拆分'); setSplitting(null); refresh(); },
    onError: (e: any) => message.error(typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : '拆分失败'),
  });
  const splitSum = splitParts.reduce((a, p) => a + (p.amount ?? 0), 0);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>工厂逐单对账</Typography.Title>
      <Alert
        type="info" showIcon
        message="工厂侧对账单逐单对账 (价格 = 工厂结算价 = 我们付给工厂的成本)"
        description="导入工厂对账单 xlsx(两个 sheet, 表头在第2行, 日期为 Excel 序列号)。逐月核对「应付(Σ结算价) ↔ 实付(支付宝 factory_payment)」; 对不上的(可能有扣减/减免)在明细里逐单『填原因做平』。导入时自动按订单号回填 Order.actual_cost。"
      />

      {sum && (
        <Row gutter={12}>
          <Col span={5}><Card size="small"><Statistic title="对账明细笔数" value={sum.total_items} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="应付合计(结算价)" value={sum.total_billed} precision={0} prefix="¥" /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="实付合计(factory_payment)" value={sum.total_paid} precision={0} prefix="¥" /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="差额(实付-应付)" value={sum.total_diff} precision={0} prefix="¥" valueStyle={{ color: Math.abs(sum.total_diff) > 5 ? '#cf1322' : '#3f8600' }} /></Card></Col>
          <Col span={4}><Card size="small"><Statistic title="已做平条目" value={sum.resolved_items} /></Card></Col>
        </Row>
      )}

      <Card size="small" title="导入工厂侧对账单">
        <Upload accept=".xlsx,.xls" showUploadList={false} beforeUpload={(file) => { importMut.mutate(file as File); return false; }}>
          <Tag color="blue" style={{ cursor: 'pointer', padding: '4px 10px' }}>
            <UploadOutlined /> 上传工厂对账单 xlsx
          </Tag>
        </Upload>
        {importMut.isPending && <span style={{ marginLeft: 8 }}>导入中…</span>}
      </Card>

      {sum && sum.months.length > 0 && (
        <Card size="small" title="逐月对账">
          <Table<typeof sum.months[number]>
            rowKey="period" size="small" pagination={false} dataSource={sum.months}
            onRow={(m) => ({ onClick: () => setPeriod(period === m.period ? undefined : m.period), style: { cursor: 'pointer' } })}
            columns={[
              { title: '月份', dataIndex: 'period', render: (v) => <Tag color={period === v ? 'geekblue' : undefined}>{v}</Tag> },
              { title: '应付(结算价)', dataIndex: 'billed', align: 'right' as const, render: (v: number) => `¥${Number(v).toFixed(0)}` },
              { title: '实付(factory_payment)', dataIndex: 'paid', align: 'right' as const, render: (v: number) => `¥${Number(v).toFixed(0)}` },
              { title: '差额', dataIndex: 'diff', align: 'right' as const, render: (v: number) => <span style={{ color: Math.abs(v) > 5 ? '#cf1322' : '#3f8600' }}>¥{Number(v).toFixed(0)}</span> },
              { title: '条目/已做平', render: (_, m) => `${m.items_resolved}/${m.items_total}` },
              { title: '状态', dataIndex: 'status', render: (s: string) => <Tag color={STATUS_TAG[s]?.color}>{STATUS_TAG[s]?.label ?? s}</Tag> },
            ]}
          />
          <div style={{ color: '#999', fontSize: 12, marginTop: 6 }}>点击某月可筛选下方明细; 再次点击取消。</div>
        </Card>
      )}

      <Card
        size="small"
        title={`逐单明细${period ? ` · ${period}` : ''}`}
        extra={(
          <Space>
            <Input.Search allowClear placeholder="订单号/客户/详情" style={{ width: 220 }}
              onSearch={setQ} onChange={(e) => { if (!e.target.value) setQ(''); }} />
            <Segmented
              value={statusFilter} onChange={(v) => setStatusFilter(v as string)}
              options={[{ value: 'all', label: '全部' }, { value: 'open', label: '未做平' }, { value: 'resolved', label: '已做平' }]}
            />
          </Space>
        )}
      >
        <PresetTable<FactoryReconItem>
          tableKey="factory_recon_item"
          rowKey="id" size="small" loading={isLoading} dataSource={items?.rows ?? []}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (t) => `共 ${t} 笔` }}
          columns={[
            { title: '单号', dataIndex: 'doc_no', width: 70, render: (v) => v || '-' },
            { title: '订单号', dataIndex: 'order_no', width: 175, render: (v) => v || '-' },
            { title: '详情', dataIndex: 'detail', ellipsis: true },
            { title: '数量', dataIndex: 'qty', width: 56, align: 'center' as const },
            { title: '结算价', dataIndex: 'settle_price', width: 90, align: 'right' as const, render: (v: number) => `¥${Number(v).toFixed(0)}` },
            { title: '客户', dataIndex: 'customer_info', width: 110, render: (v) => v || '-' },
            { title: '下单', dataIndex: 'order_date', width: 100, render: (v) => v || '-' },
            { title: '做平', dataIndex: 'resolved', width: 130, render: (r: boolean, row) => (
              r ? <Tag color="blue" title={row.settle_reason || ''}>已做平</Tag>
                : <Tag color="default">未做平</Tag>
            ) },
            { title: '操作', width: 210, render: (_, row) => (
              row.resolved
                ? <Button size="small" type="link" onClick={() => resolveMut.mutate({ id: row.id, reason: '', resolved: false })}>撤销</Button>
                : <Space size={0}>
                    <Button size="small" type="link" onClick={() => { setResolving(row); setReason(''); }}>做平</Button>
                    <Dropdown menu={{
                      items: RESOLUTION_KINDS.map((k) => ({ key: k, label: k })),
                      onClick: ({ key }) => confirmMut.mutate({ id: row.id, kind: key }),
                    }}>
                      <Button size="small" type="link">确认归因</Button>
                    </Dropdown>
                    <Button size="small" type="link" onClick={() => openSplit(row)}>拆分</Button>
                  </Space>
            ) },
          ]}
          expandable={{
            expandedRowRender: (row) => (
              <div style={{ fontSize: 12, color: '#555' }}>
                追加单: {row.extra_order_no1 || '-'} / {row.extra_order_no2 || '-'} ·
                来源: {row.source_sheet} · 备注: {row.remark || '-'}
                {row.resolved && <> · 做平原因: <b>{row.settle_reason}</b> ({row.resolved_by || '?'})</>}
              </div>
            ),
          }}
        />
      </Card>

      {noBillItems && (
        <Card size="small" title={`我方下单逐单预估 (工厂对账单未导入)${preview ? ` · 应付合计 ¥${preview.total_payable.toLocaleString()}` : ''}`}>
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            message="工厂逐单明细需导入工厂正式对账单才有(系统不能凭空造工厂的结算价)。下方是用『我方工厂下单』数据生成的逐单预估应付, 工厂对账单到了以对账单为准。"
            description={preview?.note} />
          <PreviewTable rows={preview?.rows ?? []} />
        </Card>
      )}

      <Modal
        title="填原因做平"
        open={!!resolving}
        onCancel={() => setResolving(null)}
        onOk={() => {
          if (!reason.trim()) { message.warning('请填写差异原因'); return; }
          if (resolving) resolveMut.mutate({ id: resolving.id, reason: reason.trim(), resolved: true });
        }}
        confirmLoading={resolveMut.isPending}
        okText="做平"
      >
        <p style={{ color: '#888' }}>
          订单 {resolving?.order_no} · 结算价 ¥{resolving?.settle_price != null ? Number(resolving.settle_price).toFixed(0) : '—'}。
          应付与实付对不上时, 在此记录扣减/减免/差异原因后标记为已做平。
        </p>
        <Input.TextArea
          rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="如: 平台扣减运费 / 工厂让利减免 / 批量分账 / 录入差异 …"
        />
      </Modal>

      <Modal
        title={`拆分归因 — ${splitting?.order_no || ''} (原额 ¥${splitting?.settle_price != null ? Number(splitting.settle_price).toFixed(2) : '—'})`}
        open={!!splitting}
        onCancel={() => setSplitting(null)}
        onOk={() => splitMut.mutate()}
        confirmLoading={splitMut.isPending}
        okText="拆分"
        okButtonProps={{ disabled: !splitting || Math.abs(splitSum - (splitting?.settle_price ?? 0)) > 0.005 }}
        width={560}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="把一条差异拆成多条归因子行；各行金额合计必须等于原额才能提交。"
        />
        {splitParts.map((p, i) => (
          <Space key={i} style={{ marginBottom: 8 }}>
            <InputNumber value={p.amount} precision={2} style={{ width: 130 }} addonBefore="¥"
              onChange={(v) => setSplitParts((arr) => arr.map((x, j) => j === i ? { ...x, amount: v } : x))} />
            <Select value={p.kind} style={{ width: 100 }}
              options={RESOLUTION_KINDS.map((k) => ({ value: k, label: k }))}
              onChange={(v) => setSplitParts((arr) => arr.map((x, j) => j === i ? { ...x, kind: v } : x))} />
            <Input value={p.remark} placeholder="备注 (可选)" style={{ width: 160 }}
              onChange={(e) => setSplitParts((arr) => arr.map((x, j) => j === i ? { ...x, remark: e.target.value } : x))} />
            {splitParts.length > 2 && (
              <Button size="small" danger type="link"
                onClick={() => setSplitParts((arr) => arr.filter((_, j) => j !== i))}>删</Button>
            )}
          </Space>
        ))}
        <div>
          <Button size="small" onClick={() => setSplitParts((arr) => [...arr, { amount: 0, kind: '其他', remark: '' }])}>+ 加一行</Button>
          <span style={{ marginLeft: 12, color: Math.abs(splitSum - (splitting?.settle_price ?? 0)) > 0.005 ? '#cf1322' : '#389e0d' }}>
            合计 ¥{splitSum.toFixed(2)} / 需 ¥{splitting?.settle_price != null ? Number(splitting.settle_price).toFixed(2) : '—'}
          </span>
        </div>
      </Modal>
    </Space>
  );
}
