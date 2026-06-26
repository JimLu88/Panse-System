/**
 * 大宗/消耗材料对账面板 — 配件 epic P2 (用户 2026-06-26)。
 *
 * 洞石板/木皮/双面胶/螺丝等大宗料工厂混裁、说不清对应哪单, 无法逐单。
 * 改按「材料 × 采购周期」对账: 当期实际采购额 vs 标准估值消耗 vs 差异%。
 * **铁律: 消费窗口按订单【发货日期 ship_date】圈定, 不用下单日期(生产周期~30天)。**
 *
 * 两个动作:
 *   - 回填标准估值: 把 Order.est_parts 按定价表配件成本补齐(对账基线, 零财务风险)。
 *   - 逐单采购汇总: 填了订单号的配件采购单 → 汇总写 actual_parts(先 dry-run 预览再落库)。
 */
import { useState } from 'react';
import { Alert, Button, Card, Modal, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { ReloadOutlined, MergeCellsOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  aggregateRelatedParts, backfillEstParts, fetchBulkMaterialRecon,
  type AggregateRelatedResult, type BulkMaterial, type BulkMaterialPeriod,
} from '../api/client';

const yuan = (v: number | null | undefined) =>
  v == null ? '—' : `¥${Math.round(v).toLocaleString()}`;

// 差异 = 实际采购 − 标准估值。正(实际>标准)= 估值偏低/采购偏多 → 橙红; 负 = 估值偏高 → 绿。
const varColor = (v: number) => (Math.abs(v) < 1 ? '#999' : v > 0 ? '#cf1322' : '#389e0d');

function MaterialCard({ m }: { m: BulkMaterial }) {
  const columns = [
    { title: '周期(发货)', dataIndex: 'period', width: 110 },
    {
      title: '标准估值消耗', dataIndex: 'standard_consume', width: 130, align: 'right' as const,
      render: (v: number, r: BulkMaterialPeriod) => (
        <span>{yuan(v)}{r.missing_est > 0 && (
          <Tooltip title={`${r.missing_est} 单命中但缺标准估值(est_parts 未回填), 标准被低估`}>
            <Tag color="orange" style={{ marginLeft: 4 }}>缺{r.missing_est}</Tag>
          </Tooltip>)}</span>
      ),
    },
    { title: '实际采购', dataIndex: 'actual_purchase', width: 110, align: 'right' as const, render: yuan },
    {
      title: '差异', dataIndex: 'variance', width: 110, align: 'right' as const,
      render: (v: number) => <span style={{ color: varColor(v), fontWeight: 600 }}>{v > 0 ? '+' : ''}{yuan(v)}</span>,
    },
    {
      title: '差异%', dataIndex: 'variance_pct', width: 90, align: 'right' as const,
      render: (v: number | null) => v == null ? '—'
        : <span style={{ color: varColor(v) }}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span>,
    },
    {
      title: '发货单数', dataIndex: 'order_count', width: 90, align: 'right' as const,
      render: (v: number) => v || <span style={{ color: '#bbb' }}>0</span>,
    },
  ];
  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title={
        <Space>
          <b>{m.name}</b>
          <Tag color={m.mode === 'by_order_kw' ? 'blue' : 'purple'}>
            {m.mode === 'by_order_kw' ? '选配型(按订单估值)' : '通用消耗型(每单标准×单数)'}
          </Tag>
        </Space>
      }
      extra={
        <Space size="large">
          <Typography.Text type="secondary">标准 {yuan(m.total_standard)} · 实际 {yuan(m.total_actual)}</Typography.Text>
          <span style={{ color: varColor(m.total_variance), fontWeight: 700 }}>
            合计差异 {m.total_variance > 0 ? '+' : ''}{yuan(m.total_variance)}
            {m.total_variance_pct != null && `（${m.total_variance_pct > 0 ? '+' : ''}${m.total_variance_pct.toFixed(1)}%）`}
          </span>
        </Space>
      }>
      <Table<BulkMaterialPeriod>
        rowKey="period" dataSource={m.periods} columns={columns as any} size="small"
        pagination={false} scroll={{ x: 640 }}
        locale={{ emptyText: '该材料暂无采购 / 标准消耗记录' }}
      />
      {m.mode === 'per_order_flat' && m.total_standard === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          ⚠ 该消耗料尚未设「每单标准用量」, 标准恒 0(仅显示实际采购)。在
          <code> parts_recon_service.BULK_MATERIALS </code>里填 flat_per_order 后即可比对。
        </Typography.Text>
      )}
    </Card>
  );
}

export default function BulkMaterialReconPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['bulk-material-recon'],
    queryFn: () => fetchBulkMaterialRecon('month'),
  });

  const backfillMut = useMutation({
    mutationFn: () => backfillEstParts(),
    onSuccess: (r) => {
      message.success(`已回填配件标准估值 ${r.set} 单(无定价 ${r.skipped_no_pricing} / 跳过关闭 ${r.skipped_closed})`);
      qc.invalidateQueries({ queryKey: ['bulk-material-recon'] });
    },
    onError: (e: any) => message.error(`回填失败: ${e?.response?.data?.detail || e?.message || e}`),
  });

  const [preview, setPreview] = useState<AggregateRelatedResult | null>(null);
  const dryRunMut = useMutation({
    mutationFn: () => aggregateRelatedParts(false),
    onSuccess: (r) => {
      if (r.matched_orders === 0) { message.info('没有填了订单号的配件采购单可汇总'); return; }
      setPreview(r);
    },
    onError: (e: any) => message.error(`预览失败: ${e?.response?.data?.detail || e?.message || e}`),
  });
  const applyMut = useMutation({
    mutationFn: () => aggregateRelatedParts(true),
    onSuccess: (r) => {
      message.success(`已写入 ${r.applied_count} 单真实配件成本(actual_parts)`);
      setPreview(null);
      qc.invalidateQueries({ queryKey: ['bulk-material-recon'] });
      qc.invalidateQueries({ queryKey: ['purchases'] });
    },
    onError: (e: any) => message.error(`落库失败: ${e?.response?.data?.detail || e?.message || e}`),
  });

  const previewCols = [
    { title: '订单号', dataIndex: 'order_no', width: 170, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    { title: '产品', dataIndex: 'product_name', ellipsis: true, render: (v: string | null) => v || '—' },
    { title: '采购笔数', dataIndex: 'purchases', width: 80, align: 'right' as const },
    { title: '配件汇总', dataIndex: 'new_actual_parts', width: 100, align: 'right' as const, render: yuan },
    {
      title: '商品成本变化', width: 170, align: 'right' as const,
      render: (_: unknown, r: any) => (
        <span>{yuan(r.old_physical_cost)} → {yuan(r.new_physical_cost)}{' '}
          <Tag color={r.physical_delta > 0 ? 'red' : r.physical_delta < 0 ? 'green' : 'default'}>
            {r.physical_delta > 0 ? '+' : ''}{yuan(r.physical_delta)}
          </Tag></span>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="大宗/消耗材料对账（实际采购 vs 标准估值消耗 vs 差异%）"
        description={
          <>
            <b>消费窗口按订单「发货日期」圈定</b>（生产周期~30天, 料在发货前才裁切消耗）——
            如 2/1–3/1 采购的洞石板, 对账范围 = 2/1–3/1<b>发货</b>的订单, 不是下单的订单。
            「标准估值消耗」来自定价表配件成本(est_parts); 差异即可喂下一步逐单建议值回推。
          </>
        }
      />
      <Space wrap>
        <Button icon={<ReloadOutlined />} loading={backfillMut.isPending}
          onClick={() => backfillMut.mutate()}>
          回填配件标准估值
        </Button>
        <Tooltip title="填了订单号的配件采购单 → 按订单汇总成真实配件成本(先预览再落库)">
          <Button icon={<MergeCellsOutlined />} loading={dryRunMut.isPending}
            onClick={() => dryRunMut.mutate()}>
            逐单采购汇总 → 真实配件
          </Button>
        </Tooltip>
      </Space>

      {(data?.materials ?? []).map((m) => <MaterialCard key={m.key} m={m} />)}
      {!isLoading && (data?.materials?.length ?? 0) === 0 && (
        <Typography.Text type="secondary">暂无对账数据</Typography.Text>
      )}

      <Modal
        open={!!preview} width={860} title="逐单配件采购汇总 — 预览(确认后写入 actual_parts)"
        onCancel={() => setPreview(null)}
        footer={[
          <Button key="c" onClick={() => setPreview(null)}>取消</Button>,
          <Button key="ok" type="primary" loading={applyMut.isPending}
            disabled={!preview?.matched_orders}
            onClick={() => applyMut.mutate()}>
            确认落库（{preview?.matched_orders} 单）
          </Button>,
        ]}>
        {preview && (
          <>
            <Typography.Paragraph type="secondary">
              共 {preview.matched_orders} 单可写入, 配件合计 {yuan(preview.total_parts_amount)}
              {preview.unmatched_orders > 0 && ` · ${preview.unmatched_orders} 个订单号在系统中未找到(跳过)`}。
              落库后这些单的商品成本改「逐项真实计价」(木作+物流+安装+打包+真实配件, 不再估算/兜底)。
            </Typography.Paragraph>
            <Table
              rowKey="order_no" size="small" dataSource={preview.items.filter((i) => i.matched)}
              columns={previewCols as any} pagination={{ pageSize: 10 }} scroll={{ x: 640 }}
            />
          </>
        )}
      </Modal>
    </Space>
  );
}
