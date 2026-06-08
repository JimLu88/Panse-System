/**
 * 工厂逐单对账 — 导入工厂侧对账单 xlsx (价格=工厂结算价=成本),
 * 逐月对账「应付(Σ结算价) ↔ 实付(支付宝 factory_payment)」, 对不上的逐单「填原因做平」。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Col, Input, Modal, Row, Segmented, Space, Statistic,
  Table, Tag, Typography, Upload, message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FactoryReconItem, fetchFactoryReconSummary, importFactoryRecon,
  listFactoryReconItems, resolveFactoryReconItem,
} from '../api/factoryRecon';

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
              { title: '应付(结算价)', dataIndex: 'billed', align: 'right' as const, render: (v: number) => `¥${v.toFixed(0)}` },
              { title: '实付(factory_payment)', dataIndex: 'paid', align: 'right' as const, render: (v: number) => `¥${v.toFixed(0)}` },
              { title: '差额', dataIndex: 'diff', align: 'right' as const, render: (v: number) => <span style={{ color: Math.abs(v) > 5 ? '#cf1322' : '#3f8600' }}>¥{v.toFixed(0)}</span> },
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
        <Table<FactoryReconItem>
          rowKey="id" size="small" loading={isLoading} dataSource={items?.rows ?? []}
          pagination={{ pageSize: 50, showTotal: (t) => `共 ${t} 笔` }}
          columns={[
            { title: '单号', dataIndex: 'doc_no', width: 70, render: (v) => v || '-' },
            { title: '订单号', dataIndex: 'order_no', width: 175, render: (v) => v || '-' },
            { title: '详情', dataIndex: 'detail', ellipsis: true },
            { title: '数量', dataIndex: 'qty', width: 56, align: 'center' as const },
            { title: '结算价', dataIndex: 'settle_price', width: 90, align: 'right' as const, render: (v: number) => `¥${v.toFixed(0)}` },
            { title: '客户', dataIndex: 'customer_info', width: 110, render: (v) => v || '-' },
            { title: '下单', dataIndex: 'order_date', width: 100, render: (v) => v || '-' },
            { title: '做平', dataIndex: 'resolved', width: 130, render: (r: boolean, row) => (
              r ? <Tag color="blue" title={row.settle_reason || ''}>已做平</Tag>
                : <Tag color="default">未做平</Tag>
            ) },
            { title: '操作', width: 90, render: (_, row) => (
              row.resolved
                ? <Button size="small" type="link" onClick={() => resolveMut.mutate({ id: row.id, reason: '', resolved: false })}>撤销</Button>
                : <Button size="small" type="link" onClick={() => { setResolving(row); setReason(''); }}>填原因做平</Button>
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
          订单 {resolving?.order_no} · 结算价 ¥{resolving?.settle_price?.toFixed(0)}。
          应付与实付对不上时, 在此记录扣减/减免/差异原因后标记为已做平。
        </p>
        <Input.TextArea
          rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="如: 平台扣减运费 / 工厂让利减免 / 批量分账 / 录入差异 …"
        />
      </Modal>
    </Space>
  );
}
