/**
 * 大宗/消耗材料对账面板 — 配件 epic (用户 2026-06-26)。
 *
 * 配件厂多是当地小厂、手写单、一次付款盖到哪些订单无法回推 → 改「按月对账」:
 *   导出当月【已发货】订单(按发货日期, 100% 真实消耗了配件)给工厂 → 工厂返月度总额 → 填「实际」列。
 * 每材料每月并排: 历史平均 | 预估(Σest_parts) | 实际(工厂月度对账) | 差异%。全部按发货日期 ship_date。
 * 导出两份: 全部发货单(给工厂自己挑) + 按材料(逐单展开 BOM 部位/预设尺寸, 方便对照)。
 */
import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select,
  Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { ReloadOutlined, MergeCellsOutlined, PrinterOutlined, EditOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  aggregateRelatedParts, backfillEstParts, deleteMonthlyRecon, fetchBulkMaterialRecon,
  fetchShippedOrdersExport, listMonthlyRecon, saveMonthlyRecon,
  type AggregateRelatedResult, type BulkMaterial, type BulkMaterialPeriod, type ShippedOrdersExport,
} from '../api/client';

const yuan = (v: number | null | undefined) => (v == null ? '—' : `¥${Math.round(v).toLocaleString()}`);
const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m] as string));
// 差异 = 实际 − 预估。正(实际>预估)= 预估偏低 → 橙红; 负 = 预估偏高 → 绿。
const varColor = (v: number) => (Math.abs(v) < 1 ? '#999' : v > 0 ? '#cf1322' : '#389e0d');

// ── 导出清单打印 ───────────────────────────────────────────────────────────
function openExportPrint(d: ShippedOrdersExport) {
  const win = window.open('', '_blank', 'width=980,height=860');
  if (!win) { message.warning('浏览器拦截了打印窗口, 请允许弹窗后重试'); return; }
  const title = d.material_key
    ? `配件对账清单 · ${d.material_name} · ${d.year_month} 发货`
    : `当月发货订单清单 · ${d.year_month} 发货`;
  const head = `<h1>${esc(title)}</h1>
    <div class="sub">共 ${d.order_count} 单 · 按发货日期(ship_date) · 预估配件合计 ¥${Math.round(d.total_est_parts).toLocaleString()}
      &nbsp;|&nbsp; 请工厂核对后填【本月实际总额】: ____________ 元</div>`;
  let body = '';
  if (d.material_key) {
    body = d.orders.map((o) => {
      const partRows = (o.bom_parts || []).map((p) => `<tr>
        <td>${esc(p.part_name)}</td><td class="num">${esc(p.qty)}${esc(p.unit ?? '')}</td>
        <td>${esc(p.size_note ?? '—')}</td></tr>`).join('');
      return `<div class="ordsec"><div class="ordh">${esc(o.order_no)}${o.ship_date ? ' · 发货 ' + esc(o.ship_date) : ''}${o.product_name ? ' · ' + esc(o.product_name) : ''}${o.sku ? ' · ' + esc(o.sku) : ''}</div>
        <table><thead><tr><th>部位 / 料</th><th>数量</th><th>预设尺寸(实际可能有出入)</th></tr></thead>
        <tbody>${partRows || '<tr><td colspan="3" class="muted">无 BOM 明细</td></tr>'}</tbody></table></div>`;
    }).join('');
  } else {
    const rows = d.orders.map((o) => `<tr>
      <td class="code">${esc(o.order_no)}</td><td>${esc(o.ship_date ?? '')}</td>
      <td>${esc(o.customer_name ?? '')}</td><td>${esc(o.product_name ?? '')}</td>
      <td>${esc(o.sku ?? '')}</td><td class="num">¥${Math.round(o.est_parts).toLocaleString()}</td></tr>`).join('');
    body = `<table><thead><tr><th>订单号</th><th>发货日</th><th>客户</th><th>产品</th><th>SKU(含尺寸)</th><th>预估配件</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  win.document.write(`<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>${esc(title)}</title>
    <style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,"Microsoft YaHei",sans-serif;color:#222;padding:10mm}
    h1{font-size:16px;margin-bottom:4px}.sub{color:#666;font-size:12px;margin-bottom:10px}
    table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px}th,td{border:1px solid #bbb;padding:4px 6px;text-align:left}
    th{background:#f5f5f5}.num{text-align:right}.code{font-family:monospace}.muted{color:#999}
    .ordsec{break-inside:avoid;page-break-inside:avoid;margin-bottom:8px}
    .ordh{font-weight:700;background:#f0f5ff;padding:4px 6px;border-left:3px solid #1677ff}
    @page{size:A4 portrait;margin:12mm}</style></head><body>${head}${body}</body></html>`);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 400);
}

// ── 录入工厂月度对账总额 (可多家工厂) ────────────────────────────────────────
function ActualEntryModal({ info, onClose, onSaved }: {
  info: { materialKey: string; materialName: string; yearMonth: string };
  onClose: () => void;
  onSaved: () => void;
}) {
  const { data: rows = [], refetch } = useQuery({
    queryKey: ['monthly-recon', info.materialKey, info.yearMonth],
    queryFn: () => listMonthlyRecon(info.materialKey, info.yearMonth),
  });
  const [form] = Form.useForm();
  const saveMut = useMutation({
    mutationFn: (v: { supplier?: string; actual_total: number; note?: string }) =>
      saveMonthlyRecon({ material_key: info.materialKey, year_month: info.yearMonth,
        actual_total: v.actual_total, supplier: v.supplier, note: v.note }),
    onSuccess: () => { message.success('已保存'); form.resetFields(); refetch(); onSaved(); },
    onError: (e: any) => message.error(`保存失败: ${e?.response?.data?.detail || e?.message || e}`),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteMonthlyRecon(id),
    onSuccess: () => { refetch(); onSaved(); },
  });
  const total = rows.reduce((s, r) => s + (r.actual_total || 0), 0);
  return (
    <Modal open width={640} title={`录入工厂月度对账 · ${info.materialName} · ${info.yearMonth} 发货`}
      onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="把工厂返回的当月总额填这里(同一材料多家工厂可各填一行)。系统把这些行求和, 作该料该月的「实际」与预估对比。" />
      <Table rowKey="id" size="small" dataSource={rows} pagination={false} style={{ marginBottom: 8 }}
        locale={{ emptyText: '还没录入工厂总额' }}
        columns={[
          { title: '供应商', dataIndex: 'supplier', render: (v: string | null) => v || '—' },
          { title: '实际金额', dataIndex: 'actual_total', align: 'right' as const, render: (v: number) => <b>{yuan(v)}</b> },
          { title: '备注', dataIndex: 'note', ellipsis: true, render: (v: string | null) => v || '—' },
          { title: '', width: 48, render: (_: unknown, r: any) => (
            <Popconfirm title="删除这条?" okText="删" cancelText="取消" onConfirm={() => delMut.mutate(r.id)}>
              <Button size="small" danger type="link">删</Button>
            </Popconfirm>) },
        ] as any} />
      <Typography.Text style={{ display: 'block', textAlign: 'right', marginBottom: 12 }}>
        合计(实际)= <b style={{ fontSize: 16 }}>{yuan(total)}</b>
      </Typography.Text>
      <Form form={form} layout="inline" onFinish={(v) => saveMut.mutate(v as any)}>
        <Form.Item name="supplier"><Input placeholder="供应商(选填)" style={{ width: 130 }} /></Form.Item>
        <Form.Item name="actual_total" rules={[{ required: true, message: '填金额' }]}>
          <InputNumber placeholder="金额 ¥" min={0} style={{ width: 130 }} />
        </Form.Item>
        <Form.Item name="note"><Input placeholder="备注(选填)" style={{ width: 130 }} /></Form.Item>
        <Form.Item><Button type="primary" htmlType="submit" loading={saveMut.isPending}>添加 / 保存</Button></Form.Item>
      </Form>
    </Modal>
  );
}

function MaterialCard({ m, onExport, onEnterActual }: {
  m: BulkMaterial;
  onExport: (period: string) => void;
  onEnterActual: (period: string) => void;
}) {
  const columns = [
    { title: '周期(发货)', dataIndex: 'period', width: 96 },
    { title: '历史平均', dataIndex: 'historical_avg', width: 96, align: 'right' as const, render: yuan },
    {
      title: '预估', dataIndex: 'standard_consume', width: 100, align: 'right' as const,
      render: (v: number, r: BulkMaterialPeriod) => (
        <span>{yuan(v)}{r.missing_est > 0 && (
          <Tooltip title={`${r.missing_est} 单命中但缺标准估值(est_parts 未回填)`}>
            <Tag color="orange" style={{ marginLeft: 4 }}>缺{r.missing_est}</Tag></Tooltip>)}</span>),
    },
    {
      title: '实际(工厂)', dataIndex: 'factory_actual', width: 104, align: 'right' as const,
      render: (v: number | null) => v == null ? <span style={{ color: '#bbb' }}>未录</span> : <b>{yuan(v)}</b>,
    },
    {
      title: '差异%', dataIndex: 'variance_pct', width: 84, align: 'right' as const,
      render: (v: number | null, r: BulkMaterialPeriod) => (!r.has_factory_actual || v == null) ? '—'
        : <span style={{ color: varColor(v), fontWeight: 600 }}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span>,
    },
    { title: '发货单', dataIndex: 'order_count', width: 64, align: 'right' as const,
      render: (v: number) => v || <span style={{ color: '#bbb' }}>0</span> },
    {
      title: '操作', width: 168,
      render: (_: unknown, r: BulkMaterialPeriod) => (
        <Space size="small">
          <Button size="small" icon={<PrinterOutlined />} onClick={() => onExport(r.period)}>导清单</Button>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => onEnterActual(r.period)}>
            {r.has_factory_actual ? '改实际' : '录实际'}</Button>
        </Space>),
    },
  ];
  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title={<Space><b>{m.name}</b>
        <Tag color={m.mode === 'by_order_kw' ? 'blue' : 'purple'}>
          {m.mode === 'by_order_kw' ? '选配型(按订单估值)' : '通用消耗型(每单标准×单数)'}</Tag></Space>}
      extra={<Space size="large">
        <Typography.Text type="secondary">预估 {yuan(m.total_standard)} · 实际 {yuan(m.total_factory_actual)}</Typography.Text>
        {m.total_factory_actual > 0 && (
          <span style={{ color: varColor(m.total_variance), fontWeight: 700 }}>
            差异 {m.total_variance > 0 ? '+' : ''}{yuan(m.total_variance)}
            {m.total_variance_pct != null && `（${m.total_variance_pct > 0 ? '+' : ''}${m.total_variance_pct.toFixed(1)}%）`}
          </span>)}</Space>}>
      <Table<BulkMaterialPeriod> rowKey="period" dataSource={m.periods} columns={columns as any}
        size="small" pagination={false} scroll={{ x: 720 }}
        locale={{ emptyText: '该材料暂无发货 / 对账记录' }} />
    </Card>
  );
}

export default function BulkMaterialReconPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['bulk-material-recon'], queryFn: () => fetchBulkMaterialRecon('month'),
  });

  const backfillMut = useMutation({
    mutationFn: () => backfillEstParts(),
    onSuccess: (r) => { message.success(`已回填配件标准估值 ${r.set} 单`); qc.invalidateQueries({ queryKey: ['bulk-material-recon'] }); },
    onError: () => message.error('回填失败'),
  });
  const [preview, setPreview] = useState<AggregateRelatedResult | null>(null);
  const dryRunMut = useMutation({
    mutationFn: () => aggregateRelatedParts(false),
    onSuccess: (r) => { if (!r.matched_orders) { message.info('没有填了订单号的配件采购单可汇总'); return; } setPreview(r); },
    onError: () => message.error('预览失败'),
  });
  const applyMut = useMutation({
    mutationFn: () => aggregateRelatedParts(true),
    onSuccess: (r) => { message.success(`已写入 ${r.applied_count} 单真实配件成本`); setPreview(null); qc.invalidateQueries({ queryKey: ['bulk-material-recon'] }); qc.invalidateQueries({ queryKey: ['purchases'] }); },
    onError: () => message.error('落库失败'),
  });

  // 导出月份: 取数据里出现过的发货月, 默认最新
  const allMonths = useMemo(() => {
    const s = new Set<string>();
    (data?.materials ?? []).forEach((m) => m.periods.forEach((p) => s.add(p.period)));
    return Array.from(s).sort().reverse();
  }, [data]);
  const [expMonth, setExpMonth] = useState<string | undefined>(undefined);
  const effMonth = expMonth || allMonths[0];

  const doExport = async (yearMonth: string, materialKey?: string) => {
    try {
      const d = await fetchShippedOrdersExport(yearMonth, materialKey);
      if (!d.order_count) { message.info(`${yearMonth} 没有${materialKey ? '该材料的' : ''}已发货订单`); return; }
      openExportPrint(d);
    } catch (e: any) {
      message.error(`导出失败: ${e?.response?.data?.detail || e?.message || e}`);
    }
  };

  const [actualModal, setActualModal] =
    useState<{ materialKey: string; materialName: string; yearMonth: string } | null>(null);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon message="大宗/消耗材料对账（历史平均 · 预估 · 工厂实际 · 差异%）"
        description={<>配件厂多是当地小厂、手写单、一次付款无法回推到具体订单 → 改<b>按月对账</b>:
          导出当月「已发货」订单(按<b>发货日期</b>, 100% 真实消耗)给工厂 → 工厂返月度总额 → 点「录实际」填进去, 即可和预估并排看准不准。
          消费窗口按发货日期(生产周期~30天)。</>} />
      <Space wrap>
        <Button icon={<ReloadOutlined />} loading={backfillMut.isPending} onClick={() => backfillMut.mutate()}>回填配件标准估值</Button>
        <Tooltip title="填了订单号的配件采购单 → 按订单汇总成真实配件成本(先预览再落库)">
          <Button icon={<MergeCellsOutlined />} loading={dryRunMut.isPending} onClick={() => dryRunMut.mutate()}>逐单采购汇总</Button>
        </Tooltip>
        <span style={{ borderLeft: '1px solid #eee', paddingLeft: 12 }}>
          <Select value={effMonth} onChange={setExpMonth} style={{ width: 120 }} placeholder="选发货月"
            options={allMonths.map((mo) => ({ label: mo, value: mo }))} />
          <Button icon={<PrinterOutlined />} style={{ marginLeft: 8 }} disabled={!effMonth}
            onClick={() => effMonth && doExport(effMonth)}>导出当月全部发货单</Button>
        </span>
      </Space>

      {(data?.materials ?? []).map((m) => (
        <MaterialCard key={m.key} m={m}
          onExport={(period) => doExport(period, m.key)}
          onEnterActual={(period) => setActualModal({ materialKey: m.key, materialName: m.name, yearMonth: period })} />
      ))}
      {!isLoading && (data?.materials?.length ?? 0) === 0 && (
        <Typography.Text type="secondary">暂无对账数据</Typography.Text>
      )}

      <Modal open={!!preview} width={820} title="逐单配件采购汇总 — 预览(确认后写入 actual_parts)"
        onCancel={() => setPreview(null)}
        footer={[
          <Button key="c" onClick={() => setPreview(null)}>取消</Button>,
          <Button key="ok" type="primary" loading={applyMut.isPending} disabled={!preview?.matched_orders}
            onClick={() => applyMut.mutate()}>确认落库（{preview?.matched_orders} 单）</Button>,
        ]}>
        {preview && (
          <Typography.Paragraph type="secondary">
            共 {preview.matched_orders} 单可写入, 配件合计 {yuan(preview.total_parts_amount)}。
            落库后这些单的商品成本改「逐项真实计价」。
          </Typography.Paragraph>)}
        {preview && (
          <Table rowKey="order_no" size="small" dataSource={preview.items.filter((i) => i.matched)}
            pagination={{ pageSize: 8 }} scroll={{ x: 560 }}
            columns={[
              { title: '订单号', dataIndex: 'order_no', render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
              { title: '产品', dataIndex: 'product_name', ellipsis: true },
              { title: '配件汇总', dataIndex: 'new_actual_parts', align: 'right' as const, render: yuan },
              { title: '商品成本变化', align: 'right' as const, render: (_: unknown, r: any) => (
                <span>{yuan(r.old_physical_cost)} → {yuan(r.new_physical_cost)}{' '}
                  <Tag color={r.physical_delta > 0 ? 'red' : r.physical_delta < 0 ? 'green' : 'default'}>
                    {r.physical_delta > 0 ? '+' : ''}{yuan(r.physical_delta)}</Tag></span>) },
            ] as any} />)}
      </Modal>

      {actualModal && (
        <ActualEntryModal info={actualModal} onClose={() => setActualModal(null)}
          onSaved={() => qc.invalidateQueries({ queryKey: ['bulk-material-recon'] })} />)}
    </Space>
  );
}
