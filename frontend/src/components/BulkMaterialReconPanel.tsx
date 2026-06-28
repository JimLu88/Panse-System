/**
 * 配件对账面板 — 方向1: 按「配件分类」折叠 + BOM 驱动 (用户 2026-06-26)。
 *
 * 分类来自配件库(Material.category)。每个分类一个折叠块, 收起只看分类小计, 展开看逐月
 * 历史平均 | 预估(Σ发货单BOM该类配件 price×qty) | 实际(工厂月度对账) | 差异%。全部按发货日期 ship_date。
 * 导清单: 当月该分类发货单 + 逐单 BOM 部位/预设尺寸; 录实际: 工厂返回的月度总额。
 */
import { useMemo, useState } from 'react';
import {
  Alert, Button, Collapse, Form, Input, InputNumber, Modal, Popconfirm, Select,
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
      const partRows = (o.bom_parts || []).map((p) => {
        const warn = p.size_uncertain
          ? ` <span class="warn">⚠ ${p.alt_size_count ? `模板${(p.alt_size_count ?? 0) + 1}种尺寸已取最大, ` : ''}请确认尺寸是否正确</span>`
          : '';
        return `<tr>
        <td>${esc(p.category)}</td><td>${esc(p.part_name)}</td><td class="num">${esc(p.qty)}${esc(p.unit ?? '')}</td>
        <td>${esc(p.size_note ?? '—')}${warn}</td></tr>`;
      }).join('');
      const custom = o.is_custom ? ' <span class="warn">⚠定制·BOM为模板, 以实际为准</span>' : '';
      const spor = o.sporadic
        ? `<div class="spor">⚠ ${esc(o.sporadic_note || '查看是否为零星采购,非月结付款')}</div>`
        : '';
      return `<div class="ordsec"><div class="ordh">${esc(o.order_no)}${o.ship_date ? ' · 发货 ' + esc(o.ship_date) : ''}${o.product_name ? ' · ' + esc(o.product_name) : ''}${o.sku ? ' · ' + esc(o.sku) : ''}${custom}</div>${spor}
        <table><thead><tr><th>类别</th><th>部位 / 料</th><th>数量</th><th>预设尺寸(实际可能有出入)</th></tr></thead>
        <tbody>${partRows || '<tr><td colspan="4" class="muted">无 BOM 明细</td></tr>'}</tbody></table></div>`;
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
    th{background:#f5f5f5}.num{text-align:right}.code{font-family:monospace}.muted{color:#999}.warn{color:#cf1322;font-weight:600;font-size:11px}
    .spor{color:#cf1322;font-weight:700;font-size:11px;background:#fff1f0;border:1px solid #ffccc7;padding:3px 6px;margin:3px 0 6px}
    .ordsec{break-inside:avoid;page-break-inside:avoid;margin-bottom:8px}
    .ordh{font-weight:700;background:#f0f5ff;padding:4px 6px;border-left:3px solid #1677ff}
    @page{size:A4 portrait;margin:12mm}</style></head><body>${head}${body}</body></html>`);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 400);
}

// ── 录入工厂月度对账总额 (按分类, 可多家工厂) ────────────────────────────────
function ActualEntryModal({ info, onClose, onSaved }: {
  info: { categoryKey: string; yearMonth: string };
  onClose: () => void;
  onSaved: () => void;
}) {
  const { data: rows = [], refetch } = useQuery({
    queryKey: ['monthly-recon', info.categoryKey, info.yearMonth],
    queryFn: () => listMonthlyRecon(info.categoryKey, info.yearMonth),
  });
  const [form] = Form.useForm();
  const saveMut = useMutation({
    mutationFn: (v: { supplier?: string; actual_total: number; note?: string }) =>
      saveMonthlyRecon({ material_key: info.categoryKey, year_month: info.yearMonth,
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
    <Modal open width={640} title={`录入工厂月度对账 · ${info.categoryKey} · ${info.yearMonth} 发货`}
      onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="把工厂返回的当月总额填这里(同一分类多家工厂可各填一行)。系统求和作该分类该月的「实际」与预估对比。" />
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

function periodColumns(onExport: (period: string) => void, onEnterActual: (period: string) => void,
                       settleMode?: '月结' | '零星') {
  const isMonthly = settleMode !== '零星';   // 月结=工厂月度对账+导清单; 零星=采购单自动归账
  return [
    { title: '周期(发货)', dataIndex: 'period', width: 96 },
    { title: '历史平均', dataIndex: 'historical_avg', width: 96, align: 'right' as const, render: yuan },
    { title: '预估', dataIndex: 'standard_consume', width: 100, align: 'right' as const, render: yuan },
    {
      title: isMonthly ? '实际(工厂月结)' : '实际(采购)', dataIndex: 'actual', width: 116, align: 'right' as const,
      render: (v: number | null) => v == null
        ? <span style={{ color: '#bbb' }}>{isMonthly ? '未录' : '无采购'}</span> : <b>{yuan(v)}</b>,
    },
    {
      title: '差异%', dataIndex: 'variance_pct', width: 84, align: 'right' as const,
      render: (v: number | null, r: BulkMaterialPeriod) => (!r.has_actual || v == null) ? '—'
        : <span style={{ color: varColor(v), fontWeight: 600 }}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span>,
    },
    { title: '发货单', dataIndex: 'order_count', width: 64, align: 'right' as const,
      render: (v: number) => v || <span style={{ color: '#bbb' }}>0</span> },
    {
      title: '操作', width: 184,
      render: (_: unknown, r: BulkMaterialPeriod) => isMonthly ? (
        <Space size="small">
          <Tooltip title={r.order_count ? '导出该分类当月发货单给工厂填月度总额(含BOM部位/尺寸)' : '该月无此料发货单'}>
            <Button size="small" icon={<PrinterOutlined />} disabled={!r.order_count}
              onClick={() => onExport(r.period)}>导清单</Button>
          </Tooltip>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => onEnterActual(r.period)}>
            {r.has_factory_actual ? '改实际' : '录实际'}</Button>
        </Space>
      ) : (
        <Tooltip title="零星类: 实际从支付宝备注/导入的真实采购单自动归账, 无需导清单给对方、也不手录工厂月度">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>🏷️零星 · 采购自动归账</Typography.Text>
        </Tooltip>
      ),
    },
  ];
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

  const allMonths = useMemo(() => {
    const s = new Set<string>();
    (data?.materials ?? []).forEach((m) => m.periods.forEach((p) => s.add(p.period)));
    return Array.from(s).sort().reverse();
  }, [data]);
  const [expMonth, setExpMonth] = useState<string | undefined>(undefined);
  const effMonth = expMonth || allMonths[0];

  const doExport = async (yearMonth: string, categoryKey?: string) => {
    try {
      const d = await fetchShippedOrdersExport(yearMonth, categoryKey);
      if (!d.order_count) { message.info(`${yearMonth} 没有${categoryKey ? '该分类的' : ''}已发货订单`); return; }
      openExportPrint(d);
    } catch (e: any) {
      message.error(`导出失败: ${e?.response?.data?.detail || e?.message || e}`);
    }
  };

  const [actualModal, setActualModal] = useState<{ categoryKey: string; yearMonth: string } | null>(null);
  const [activeKeys, setActiveKeys] = useState<string[]>([]);
  const cats = data?.materials ?? [];

  const collapseItems = cats.map((m: BulkMaterial) => {
    const actual = m.total_actual ?? m.total_factory_actual;   // 月结=工厂月度 / 零星=采购单
    return {
      key: m.key,
      label: (
        <Space size="middle" wrap>
          <Tag color={m.settle_mode === '零星' ? 'gold' : 'blue'}>{m.settle_mode || '月结'}</Tag>
          <b style={{ fontSize: 14 }}>{m.name}</b>
          <Typography.Text type="secondary">预估 {yuan(m.total_standard)} · 实际 {yuan(actual)}</Typography.Text>
          {actual > 0 && (
            <span style={{ color: varColor(m.total_variance), fontWeight: 700 }}>
              差异 {m.total_variance > 0 ? '+' : ''}{yuan(m.total_variance)}
              {m.total_variance_pct != null && `（${m.total_variance_pct > 0 ? '+' : ''}${m.total_variance_pct.toFixed(1)}%）`}
            </span>)}
          <Tag>{m.periods.length} 个月</Tag>
        </Space>
      ),
      children: (
        <Table<BulkMaterialPeriod> rowKey="period" dataSource={m.periods} size="small" pagination={false}
          scroll={{ x: 720 }} locale={{ emptyText: '该分类暂无发货 / 对账记录' }}
          columns={periodColumns(
            (period) => doExport(period, m.key),
            (period) => setActualModal({ categoryKey: m.key, yearMonth: period }),
            m.settle_mode,
          ) as any} />
      ),
    };
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon message="配件对账（按分类折叠 · 历史平均 · 预估 · 工厂实际 · 差异%）"
        description={<>分类来自<b>配件库</b>(物料的「分类」字段), 对账由 <b>BOM 驱动</b>(谁用了什么由 BOM 说了算, 不靠关键词)。
          每月导当月「已发货」订单给工厂 → 工厂返月度总额 → 点「录实际」填进去与预估比。
          消费窗口按<b>发货日期</b>(生产周期~30天)。配件库里改物料分类即时联动这里。</>} />
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
        {cats.length > 0 && (
          <Button type="link" size="small"
            onClick={() => setActiveKeys(activeKeys.length === cats.length ? [] : cats.map((m) => m.key))}>
            {activeKeys.length === cats.length ? '全部收起' : '全部展开'}
          </Button>)}
      </Space>

      {cats.length > 0 ? (
        <Collapse items={collapseItems} activeKey={activeKeys}
          onChange={(k) => setActiveKeys(k as string[])} />
      ) : !isLoading && (
        <Typography.Text type="secondary">
          暂无分类数据 —— 去配件库给物料设「分类」(或先点上方回填), 这里就会按分类出对账。
        </Typography.Text>
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
