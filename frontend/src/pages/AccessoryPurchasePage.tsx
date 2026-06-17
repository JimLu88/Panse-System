/**
 * 配件采购视图 — 两种视角 (用户需求 2026-06-12):
 *   1) 配件汇总: 以配件为主, 跨订单按料号汇总还缺多少 (不显示订单信息)。
 *   2) 订单汇总: 以订单为主, 每个订单列出它对应的每一样配件。
 * 两种视图都可「打印 / 存 PDF」发采购申请。
 * 只统计「需采购且未到货」的配件 (未采购/已下单/运输中)。
 */
import { useMemo, useState } from 'react';
import { Alert, Button, Card, Input, Modal, Popconfirm, Segmented, Select, Space, Table, Tag, Typography, message } from 'antd';
import { PrinterOutlined, ProfileOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { bulkUpdateAccessories, fetchAccessoriesByComponent } from '../api/client';
import type { ComponentGroup, ComponentItem } from '../api/client';
import UrgentShortageGate from '../components/UrgentShortageGate';

// 打印样式: 隐藏导航/筛选/操作列, 表格清爽出 PDF 发采购同事 (用户要求可打印)
const PRINT_CSS = `
@media print {
  .ant-layout-header, .ant-layout-sider, .no-print,
  .acc-print th.acc-op-col, .acc-print td.acc-op-col { display: none !important; }
  .acc-print .ant-table { font-size: 12px; }
  .acc-print .ant-table-row { break-inside: avoid; page-break-inside: avoid; }
  body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  @page { size: A4 portrait; margin: 12mm; }
}`;

const STATUS_COLOR: Record<string, string> = {
  未采购: 'default', 已下单: 'blue', 运输中: 'gold', 已到货: 'green', 工厂提供: 'cyan',
};

// 订单维度的透视项 (一个订单 + 它的若干配件)
interface OrderGroup {
  order_no: string;
  product_name: string | null;
  customer_name: string | null;
  customer_address: string | null;
  order_date: string | null;      // 下单日期 (排序用)
  ship_deadline: string | null;   // 手填发货截止 (排序用; 无则按 下单日+默认周期)
  to_buy: number;                 // 该单未采购的配件项数
  items: (ComponentItem & { material_name: string | null; material_code: string; unit: string | null })[];
}

const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"]/g, (m) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m] as string));

// ── 订单汇总排序 ───────────────────────────────────────────────────
const SHIP_DAYS = 30;   // 默认发货周期 (与工厂制作单一致): 无手填截止时按 下单日+30天
const DAY = 86400000;
const effDeadlineMs = (o: OrderGroup) =>
  o.ship_deadline ? new Date(o.ship_deadline).getTime()
    : o.order_date ? new Date(o.order_date).getTime() + SHIP_DAYS * DAY
      : Infinity;
const daysLeftOf = (o: OrderGroup): number | null => {
  const ms = effDeadlineMs(o);
  return isFinite(ms) ? Math.ceil((ms - Date.now()) / DAY) : null;
};
const ORDER_SORTS = [
  { label: '排序：发货最紧', value: 'deadline' },
  { label: '排序：下单最早', value: 'time' },
  { label: '排序：配件最多', value: 'items' },
] as const;
type OrderSort = (typeof ORDER_SORTS)[number]['value'];
const SORT_LABEL: Record<OrderSort, string> = { deadline: '发货最紧', time: '下单最早', items: '配件最多' };
const sortOrders = (list: OrderGroup[], key: OrderSort): OrderGroup[] => {
  const a = [...list];
  if (key === 'items') {
    a.sort((x, y) => y.items.length - x.items.length || x.order_no.localeCompare(y.order_no));
  } else if (key === 'time') {
    const ts = (o: OrderGroup) => (o.order_date ? new Date(o.order_date).getTime() : Infinity);
    a.sort((x, y) => ts(x) - ts(y));                       // 下单早 → 晚
  } else {
    a.sort((x, y) => effDeadlineMs(x) - effDeadlineMs(y)); // 剩余发货少(紧) → 多(松)
  }
  return a;
};

export default function AccessoryPurchasePage() {
  const qc = useQueryClient();
  const [view, setView] = useState<'component' | 'order'>('component');   // 配件汇总 / 订单汇总
  const [prodQ, setProdQ] = useState('');   // 按产品搜 (后端过滤)
  const [q, setQ] = useState('');           // 配件汇总: 配件名/编码; 订单汇总: 订单号/产品
  const [onlyToBuy, setOnlyToBuy] = useState(false);
  const [orderSort, setOrderSort] = useState<OrderSort>('deadline');   // 订单汇总排序

  const { data: groups = [], isLoading } = useQuery({
    queryKey: ['acc-by-component', prodQ],
    queryFn: () => fetchAccessoriesByComponent(prodQ || undefined),
    refetchInterval: 60000,
  });

  const bulkMut = useMutation({
    mutationFn: bulkUpdateAccessories,
    onSuccess: (r) => {
      message.success(`已更新 ${r.updated} 项`);
      qc.invalidateQueries({ queryKey: ['acc-by-component'] });
      qc.invalidateQueries({ queryKey: ['orders-kanban-acc'] });
    },
    onError: () => message.error('更新失败'),
  });

  // ── 配件汇总 (按料号) ─────────────────────────────────────────────
  const compFiltered = groups.filter((g) => {
    if (onlyToBuy && Number(g.to_buy_qty) <= 0) return false;
    if (!q) return true;
    const s = q.toLowerCase();
    return (g.material_name ?? '').toLowerCase().includes(s) || (g.material_code ?? '').toLowerCase().includes(s);
  });

  // ── 订单汇总 (透视: 把配件项按订单号归拢) ───────────────────────────
  const orderGroups = useMemo<OrderGroup[]>(() => {
    const m = new Map<string, OrderGroup>();
    for (const g of groups) {
      for (const it of g.items) {
        let o = m.get(it.order_no);
        if (!o) {
          o = { order_no: it.order_no, product_name: it.product_name ?? null,
                customer_name: it.customer_name ?? null, customer_address: it.customer_address ?? null,
                order_date: it.order_date ?? null, ship_deadline: it.ship_deadline ?? null,
                to_buy: 0, items: [] };
          m.set(it.order_no, o);
        }
        o.items.push({ ...it, material_name: g.material_name, material_code: g.material_code, unit: g.unit });
        if (it.status === '未采购') o.to_buy += 1;
      }
    }
    return Array.from(m.values()).sort((a, b) => a.order_no.localeCompare(b.order_no));
  }, [groups]);

  const orderFiltered = sortOrders(
    orderGroups.filter((o) => {
      if (onlyToBuy && o.to_buy <= 0) return false;
      if (!q) return true;
      const s = q.toLowerCase();
      return o.order_no.toLowerCase().includes(s) || (o.product_name ?? '').toLowerCase().includes(s);
    }),
    orderSort,
  );

  // ── 操作 ──────────────────────────────────────────────────────────
  const compToBuyIds = (g: ComponentGroup) => g.items.filter((i) => i.status === '未采购').map((i) => i.id);
  const compAllIds = (g: ComponentGroup) => g.items.map((i) => i.id);

  const markComponentBought = (g: ComponentGroup) => {
    let po = '';
    Modal.confirm({
      title: `「${g.material_name ?? g.material_code}」标为已购买`,
      content: <Input placeholder="采购单号（选填）" onChange={(e) => { po = e.target.value; }} />,
      okText: '确认已购买', cancelText: '取消',
      onOk: () => bulkMut.mutate({ item_ids: compToBuyIds(g), status: '已下单', purchase_no: po || undefined }),
    });
  };

  const markOrderBought = (o: OrderGroup) => {
    let po = '';
    const ids = o.items.filter((i) => i.status === '未采购').map((i) => i.id);
    Modal.confirm({
      title: `订单 ${o.order_no} 的未采购配件全部标为已购买`,
      content: <Input placeholder="采购单号（选填）" onChange={(e) => { po = e.target.value; }} />,
      okText: '确认已购买', cancelText: '取消',
      onOk: () => bulkMut.mutate({ item_ids: ids, status: '已下单', purchase_no: po || undefined }),
    });
  };

  // ── 打印 (两种视图都支持) ──────────────────────────────────────────
  const openPrint = (title: string, bodyHtml: string) => {
    const win = window.open('', '_blank', 'width=900,height=800');
    if (!win) { message.warning('浏览器拦截了打印窗口, 请允许弹窗后重试'); return; }
    const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>${esc(title)}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,"Microsoft YaHei",sans-serif;color:#222;padding:10mm}
  h1{font-size:16px;margin-bottom:4px}.sub{color:#888;font-size:12px;margin-bottom:10px}
  h2{font-size:13px;margin:14px 0 4px;padding:4px 6px;background:#f0f5ff;border-left:3px solid #1677ff}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:6px}
  th,td{border:1px solid #bbb;padding:6px 8px;text-align:left}
  th{background:#f5f5f5}
  tr{break-inside:avoid;page-break-inside:avoid}
  .num{text-align:right}.code{color:#666;font-family:monospace}
  .buy{color:#cf1322;font-weight:700}
  .addr{font-size:12px;color:#444;margin:3px 0 6px}
  .ordsec{break-inside:avoid;page-break-inside:avoid}
  @page{size:A4 portrait;margin:12mm}
</style></head><body>${bodyHtml}</body></html>`;
    win.document.write(html);
    win.document.close();
    win.focus();
    setTimeout(() => { win.print(); }, 350);
  };

  const printComponent = () => {
    const today = new Date().toISOString().slice(0, 10);
    const title = prodQ ? `配件采购清单 · 产品「${prodQ}」` : '配件采购清单(按配件汇总)';
    const rowsHtml = compFiltered.map((g) => `<tr>
      <td>${esc(g.material_name ?? g.material_code)}</td>
      <td class="code">${esc(g.material_code)}</td>
      <td class="num buy">${Number(g.to_buy_qty) > 0 ? esc(g.to_buy_qty) + esc(g.unit ?? '') : '—'}</td>
      <td class="num">${Number(g.bought_pending_qty) > 0 ? esc(g.bought_pending_qty) + esc(g.unit ?? '') : '—'}</td>
    </tr>`).join('');
    openPrint(title,
      `<h1>${esc(title)}</h1>
       <div class="sub">共 ${compFiltered.length} 种配件 · ${today} · 「待买」=未采购, 「已买未到」=已下单/运输中</div>
       <table><thead><tr><th>配件</th><th>编码</th><th>待买</th><th>已买未到</th></tr></thead>
       <tbody>${rowsHtml}</tbody></table>`);
  };

  const printOrder = () => {
    const today = new Date().toISOString().slice(0, 10);
    const title = '配件采购清单(按订单汇总)';
    const sections = orderFiltered.map((o) => {
      const rows = o.items.map((it) => `<tr>
        <td>${esc(it.material_name ?? it.material_code)}</td>
        <td class="code">${esc(it.material_code)}</td>
        <td class="num ${it.status === '未采购' ? 'buy' : ''}">${esc(it.qty_required)}${esc(it.unit ?? '')}</td>
        <td>${esc(it.status)}</td>
      </tr>`).join('');
      const dl = daysLeftOf(o);
      const dlStr = dl === null ? '' : (dl < 0 ? ` · ⚠剩余发货 超期${-dl}天` : ` · 剩余发货 ${dl}天`);
      const head = `订单 ${esc(o.order_no)}` +
        (o.product_name ? ` · ${esc(o.product_name)}` : '') +
        (o.customer_name ? ` · ${esc(o.customer_name)}` : '') + dlStr;
      const addr = o.customer_address ? `<div class="addr">收货地址：${esc(o.customer_address)}</div>` : '';
      return `<div class="ordsec"><h2>${head}（待买 ${o.to_buy} 项）</h2>${addr}
        <table><thead><tr><th>配件</th><th>编码</th><th>数量</th><th>状态</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    }).join('');
    openPrint(title,
      `<h1>${esc(title)}</h1>
       <div class="sub">共 ${orderFiltered.length} 个订单 · ${today} · 排序：${SORT_LABEL[orderSort]} · 每单列出未到货的配件</div>
       ${sections}`);
  };

  // ── 列定义 ────────────────────────────────────────────────────────
  const componentColumns = [
    { title: '配件', dataIndex: 'material_name', render: (v: string, r: ComponentGroup) => v ?? r.material_code },
    { title: '编码', dataIndex: 'material_code', width: 120, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    {
      title: '待买', dataIndex: 'to_buy_qty', width: 100, align: 'right' as const,
      render: (v: string, r: ComponentGroup) =>
        Number(v) > 0 ? <Tag color="red">{v}{r.unit ?? ''}</Tag> : <span style={{ color: '#bbb' }}>0</span>,
    },
    {
      title: '已买未到', dataIndex: 'bought_pending_qty', width: 110, align: 'right' as const,
      render: (v: string, r: ComponentGroup) =>
        Number(v) > 0 ? <Tag color="gold">{v}{r.unit ?? ''}</Tag> : <span style={{ color: '#bbb' }}>0</span>,
    },
    {
      title: '操作', width: 300, className: 'acc-op-col',
      onHeaderCell: () => ({ className: 'acc-op-col' } as any),
      render: (_: unknown, g: ComponentGroup) => (
        <Space wrap>
          <Button size="small" type="primary" onClick={() => markComponentBought(g)}
                  disabled={compToBuyIds(g).length === 0}>标已购买</Button>
          <Popconfirm title="这种配件全部标为已到货？" okText="确认" cancelText="取消"
                      onConfirm={() => bulkMut.mutate({ item_ids: compAllIds(g), status: '已到货' })}>
            <Button size="small">已到货</Button>
          </Popconfirm>
          <Popconfirm title="标为自送(免物流号)且已到？" okText="确认" cancelText="取消"
                      onConfirm={() => bulkMut.mutate({ item_ids: compAllIds(g), status: '已到货', self_delivered: true })}>
            <Button size="small">自送已到</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const orderColumns = [
    { title: '订单号', dataIndex: 'order_no', width: 180, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    { title: '产品', dataIndex: 'product_name', ellipsis: true, render: (v: string | null) => v || <span style={{ color: '#bbb' }}>-</span> },
    { title: '客户', dataIndex: 'customer_name', width: 90, render: (v: string | null) => v || '-' },
    {
      title: '收货地址', dataIndex: 'customer_address', ellipsis: true, width: 240,
      render: (v: string | null) => v
        ? <span title={v}>{v}</span>
        : <span style={{ color: '#bbb' }}>-</span>,
    },
    {
      title: '剩余发货', width: 96, align: 'center' as const,
      render: (_: unknown, o: OrderGroup) => {
        const dl = daysLeftOf(o);
        if (dl === null) return <span style={{ color: '#bbb' }}>-</span>;
        if (dl < 0) return <Tag color="red">超期{-dl}天</Tag>;
        return <Tag color={dl <= 5 ? 'volcano' : dl <= 11 ? 'gold' : 'green'}>{dl}天</Tag>;
      },
    },
    { title: '配件项', width: 90, align: 'right' as const, render: (_: unknown, o: OrderGroup) => `${o.items.length} 项` },
    {
      title: '待买', dataIndex: 'to_buy', width: 90, align: 'right' as const,
      render: (v: number) => v > 0 ? <Tag color="red">{v} 项</Tag> : <span style={{ color: '#bbb' }}>0</span>,
    },
    {
      title: '操作', width: 140, className: 'acc-op-col',
      onHeaderCell: () => ({ className: 'acc-op-col' } as any),
      render: (_: unknown, o: OrderGroup) => (
        <Button size="small" type="primary" disabled={o.to_buy === 0} onClick={() => markOrderBought(o)}>
          整单标已购买
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle" className="acc-print">
      <style>{PRINT_CSS}</style>
      <UrgentShortageGate />
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>
          配件采购（{view === 'component' ? '按配件汇总' : '按订单汇总'}）
        </Typography.Title>
        <Space className="no-print" wrap>
          <Button icon={<ProfileOutlined />}><Link to="/bom-list">BOM 清单</Link></Button>
          <Button type="primary" icon={<PrinterOutlined />}
            onClick={view === 'component' ? printComponent : printOrder}>
            打印 / 存 PDF 发采购
          </Button>
        </Space>
      </Space>
      <Alert
        className="no-print" type="info" showIcon
        message="两种视角：「配件汇总」按料号看全局还缺多少(一次性采购)；「订单汇总」按订单看每单缺哪些配件。两种都能打印/存 PDF 发采购。"
        description="「待买」=未采购；「已买未到」=已下单/运输中。「标已购买」可填采购单号；玻璃这类工厂周边买、自己送的，用「自送已到」(免物流号)。"
      />
      <Space className="no-print" wrap>
        <Segmented value={view} onChange={(v) => setView(v as 'component' | 'order')}
          options={[{ label: '配件汇总', value: 'component' }, { label: '订单汇总', value: 'order' }]} />
        <Input.Search placeholder="按产品搜索(看该产品配件缺口)" allowClear style={{ width: 240 }}
          onSearch={setProdQ} onChange={(e) => { if (!e.target.value) setProdQ(''); }} />
        <Input.Search placeholder={view === 'component' ? '按配件名 / 编码搜' : '按订单号 / 产品搜'} allowClear style={{ width: 220 }}
          onSearch={setQ} onChange={(e) => { if (!e.target.value) setQ(''); }} />
        <Segmented value={onlyToBuy ? 'tobuy' : 'all'} onChange={(v) => setOnlyToBuy(v === 'tobuy')}
          options={[{ label: '全部', value: 'all' }, { label: '只看待买', value: 'tobuy' }]} />
        {view === 'order' && (
          <Select value={orderSort} onChange={(v) => setOrderSort(v as OrderSort)}
            style={{ width: 150 }} options={ORDER_SORTS as unknown as { label: string; value: string }[]} />
        )}
        <Typography.Text type="secondary">
          {view === 'component' ? `共 ${compFiltered.length} 种配件` : `共 ${orderFiltered.length} 个订单`}
        </Typography.Text>
      </Space>

      <Card size="small">
        {view === 'component' ? (
          <Table<ComponentGroup>
            rowKey="material_code" loading={isLoading} dataSource={compFiltered}
            columns={componentColumns as any} size="small" pagination={false}
            locale={{ emptyText: '当前没有待采购的配件（都已到货，或还没生成配件清单）' }}
            expandable={{
              // 点击展开看每个 SKU 的尺寸 (用户拍板 2026-06-17): SKU 名通常含尺寸
              defaultExpandAllRows: false,
              expandedRowRender: (g) => (
                <Table
                  rowKey="id" dataSource={g.items} size="small" pagination={false}
                  columns={[
                    { title: 'SKU (含尺寸)', dataIndex: 'sku', render: (v: string | null, r: any) => v ?? r.sku_code ?? '—' },
                    { title: '产品', dataIndex: 'product_name', width: 150, ellipsis: true, render: (v: string | null) => v ?? '—' },
                    { title: '配件尺寸', dataIndex: 'size', width: 100, render: (v: string | null) => v ?? <span style={{ color: '#ccc' }}>—</span> },
                    { title: '数量', dataIndex: 'qty_required', width: 80, align: 'right' as const, render: (v: string) => `${v}${g.unit ?? ''}` },
                    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
                    { title: '订单号', dataIndex: 'order_no', width: 165, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
                  ]}
                />
              ),
            }}
          />
        ) : (
          <Table<OrderGroup>
            rowKey="order_no" loading={isLoading} dataSource={orderFiltered}
            columns={orderColumns as any} size="small"
            pagination={{ defaultPageSize: 50, showSizeChanger: true }}
            locale={{ emptyText: '当前没有待采购配件的订单' }}
            expandable={{
              defaultExpandAllRows: false,
              expandedRowRender: (o) => (
                <Table
                  rowKey="id" dataSource={o.items} size="small" pagination={false}
                  columns={[
                    { title: '配件', dataIndex: 'material_name', render: (v: string | null, r: any) => v ?? r.material_code },
                    { title: '编码', dataIndex: 'material_code', width: 120, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
                    { title: '数量', dataIndex: 'qty_required', width: 90, align: 'right' as const, render: (v: string, r: any) => `${v}${r.unit ?? ''}` },
                    { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
                    { title: '采购单号', dataIndex: 'purchase_no', width: 140, render: (v: string | null) => v || <span style={{ color: '#ccc' }}>—</span> },
                    {
                      title: '物流号', dataIndex: 'tracking_no', width: 140,
                      render: (v: string | null, r: any) => r.self_delivered ? <Tag color="purple">自送</Tag> : (v || <span style={{ color: '#ccc' }}>—</span>),
                    },
                  ]}
                />
              ),
            }}
          />
        )}
      </Card>
    </Space>
  );
}
