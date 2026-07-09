/**
 * 工厂制作单视图 —— 平铺卡片展示「已付款待发货(在工厂制作中)」订单。
 *
 * 每卡: 下单日 + 剩余天数(颜色随临近发货越来越红) / 客户+完整地址 / 客户备注 / 产品+SKU。
 * 卡片可直接加备注(红色放大醒目) + 手动改发货截止(覆盖默认30天)。
 * 排序: 剩余发货时间(默认) / 下单日期 / 类目。
 */
import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, DatePicker, Empty, Input, Modal, Row, Segmented, Space, Tag, Typography, message,
} from 'antd';
import { DownloadOutlined, PrinterOutlined, ProfileOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';

// 打印样式: 隐藏全局导航/筛选/卡片按钮, 卡片一排三个、不跨页断开 (用户要求宽松卡片+PDF)
const PRINT_CSS = `
@media print {
  .ant-layout-header, .ant-layout-sider, .no-print { display: none !important; }
  .fp-print-col { width: 33.33% !important; max-width: 33.33% !important; flex: 0 0 33.33% !important; padding: 8px !important; }
  .fp-print-col .ant-card { break-inside: avoid; page-break-inside: avoid; box-shadow: none; border: 1px solid #999 !important; }
  body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  @page { size: A4 landscape; margin: 10mm; }
}`;
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchFactoryProduction, updateOrderProduction, type FactoryCard } from '../api/client';
import { listAccessories, markAllAccessoriesArrived, type AccessoryItem } from '../api/orders';

// 颜色随剩余天数: 初始绿 → 蓝 → 橙 → 越近越红 → 超期深红
function dayStyle(d: number | null): { color: string; weight: number } {
  if (d === null || d === undefined) return { color: '#999', weight: 400 };
  if (d <= 0) return { color: '#a8071a', weight: 800 };   // 超期
  if (d <= 5) return { color: '#f5222d', weight: 700 };   // 红(临近)
  if (d <= 11) return { color: '#fa8c16', weight: 600 };  // 橙
  if (d <= 19) return { color: '#1677ff', weight: 500 };  // 蓝
  return { color: '#52c41a', weight: 500 };               // 绿(初始/充裕)
}
function daysText(d: number | null): string {
  if (d === null || d === undefined) return '无下单日期';
  if (d < 0) return `已超期 ${-d} 天`;
  if (d === 0) return '今天到期';
  return `剩余 ${d} 天`;
}

// 紧急度分类(颜色) —— 与后端 status 对应
const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  overdue:  { label: '已超期',   color: '#a8071a', bg: '#fff1f0' },
  critical: { label: '非常紧急', color: '#f5222d', bg: '#fff1f0' },
  urgent:   { label: '紧急',     color: '#fa8c16', bg: '#fff7e6' },
  normal:   { label: '正常安排', color: '#52c41a', bg: '#f6ffed' },
  remote:   { label: '远期单',   color: '#722ed1', bg: '#f9f0ff' },
};
const STATUS_ORDER = ['overdue', 'critical', 'urgent', 'normal', 'remote'];

export default function FactoryProductionView() {
  const qc = useQueryClient();
  const [sortBy, setSortBy] = useState<'days' | 'order_date' | 'category'>('days');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [editing, setEditing] = useState<FactoryCard | null>(null);
  const [editDeadline, setEditDeadline] = useState<dayjs.Dayjs | null>(null);
  const [editNote, setEditNote] = useState('');
  const [prodQ, setProdQ] = useState('');   // 按产品搜索 (图1)

  const { data = [], isLoading } = useQuery({
    queryKey: ['factory-production', prodQ],
    queryFn: () => fetchFactoryProduction(prodQ || undefined),
    refetchInterval: 60000,
  });

  const saveMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Parameters<typeof updateOrderProduction>[1] }) =>
      updateOrderProduction(id, patch),
    onSuccess: () => {
      message.success('已保存');
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['factory-production'] });
    },
    onError: () => message.error('保存失败'),
  });

  // 配件配齐弹窗 (#12)
  const [accCard, setAccCard] = useState<FactoryCard | null>(null);
  const accQuery = useQuery({
    queryKey: ['order-accessories', accCard?.id],
    queryFn: () => listAccessories(accCard!.id),
    enabled: !!accCard,
  });
  const markArrivedMut = useMutation({
    mutationFn: (id: number) => markAllAccessoriesArrived(id),
    onSuccess: () => {
      message.success('已全部标记已到货');
      accQuery.refetch();
      qc.invalidateQueries({ queryKey: ['factory-production'] });
    },
    onError: () => message.error('操作失败'),
  });

  const sorted = useMemo(() => {
    const arr = [...data];
    if (sortBy === 'days') {
      arr.sort((a, b) => {
        const ax = a.days_left ?? 99999; const bx = b.days_left ?? 99999;
        return ax - bx;   // 最紧急(剩余最少/超期)在前
      });
    } else if (sortBy === 'order_date') {
      arr.sort((a, b) => String(b.order_date || '').localeCompare(String(a.order_date || '')));  // 最新下单在前
    } else {
      arr.sort((a, b) => String(a.category || '~').localeCompare(String(b.category || '~')));
    }
    return arr;
  }, [data, sortBy]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    data.forEach((x) => { c[x.status] = (c[x.status] || 0) + 1; });
    return c;
  }, [data]);
  const visible = useMemo(
    () => (filterStatus === 'all' ? sorted : sorted.filter((c) => c.status === filterStatus)),
    [sorted, filterStatus],
  );

  const openEdit = (c: FactoryCard) => {
    setEditing(c);
    setEditDeadline(c.ship_deadline ? dayjs(c.ship_deadline) : (c.effective_deadline ? dayjs(c.effective_deadline) : null));
    setEditNote(c.production_note || '');
  };

  // #23: 导出当前筛选的工厂制作单为 Excel (复用页面导出端点, 记录进 资料存档库→页面导出)
  const exportExcel = async () => {
    const cols = [
      { key: 'order_label', title: '工厂下单号' },
      { key: 'order_no', title: '订单号' }, { key: 'order_date', title: '下单日期' },
      { key: 'ship_date', title: '发货截止' }, { key: 'days_left', title: '剩余天数' },
      { key: 'status_label', title: '紧急度' }, { key: 'customer_name', title: '客户' },
      { key: 'customer_address', title: '地址' }, { key: 'product_name', title: '产品' },
      { key: 'sku', title: 'SKU' }, { key: 'remark', title: '备注' },
    ];
    const rows = visible.map((c: any) => ({
      order_label: c.order_label, order_no: c.order_no, order_date: c.order_date,
      ship_date: c.ship_date ?? c.ship_deadline ?? c.ship_eta ?? null,
      days_left: c.days_left ?? c.days ?? null,
      status_label: STATUS_META[c.status]?.label ?? c.status,
      customer_name: c.customer_name, customer_address: c.customer_address,
      product_name: c.product_name, sku: c.sku, remark: c.remark ?? c.note ?? null,
    }));
    try {
      const { api } = await import('../api/client');
      const resp = await api.post('/api/exports/page',
        { title: '工厂制作单', columns: cols, rows }, { responseType: 'blob' });
      const url = window.URL.createObjectURL(resp.data as Blob);
      const a = document.createElement('a');
      a.href = url; a.download = `工厂制作单_${dayjs().format('YYYY-MM-DD')}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
      message.success('已导出 (记录存 工具→资料存档库→页面导出)');
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导出失败');
    }
  };

  // 打印 (图5 优化): 不走 window.print() 受 app 布局拖累(首页空白/卡片被 flex 列截断),
  // 而是开干净的新窗口、用 CSS Grid 渲染卡片、每卡 break-inside:avoid 不跨页断, 再调打印。
  const printCards = () => {
    const esc = (v: unknown) =>
      String(v ?? '').replace(/[&<>"]/g, (m) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m] as string));
    const cards = visible;
    const win = window.open('', '_blank', 'width=1100,height=800');
    if (!win) { message.warning('浏览器拦截了打印窗口, 请允许弹窗后重试'); return; }
    const cardHtml = (c: FactoryCard) => {
      const sm = STATUS_META[c.status] ?? { label: c.status, color: '#555', bg: '#f5f5f5' };
      const ds = dayStyle(c.days_left);
      return `<div class="pc">
        <div class="hd">
          <span class="no">${esc(c.order_label || c.order_no)}</span>${c.order_label ? ` <span style="color:#888;font-size:11px">${esc(c.order_no)}</span>` : ''}
          <span class="badge" style="color:${sm.color};background:${sm.bg};border-color:${sm.color}">${esc(sm.label)}</span>
          ${c.is_custom ? '<span class="badge cust">定制</span>' : ''}
        </div>
        <div class="row"><b>下单</b> ${esc(c.order_date || '—')} ·
          <span style="color:${ds.color};font-weight:${ds.weight}">${esc(daysText(c.days_left))}</span></div>
        <div class="row"><b>客户</b> ${esc(c.customer_name || '—')}　${esc(c.customer_phone || '')}</div>
        <div class="row"><b>地址</b> ${esc(c.customer_address || '—')}</div>
        <div class="row prod"><b>产品</b> ${esc(c.product_name || '—')} <b>×${esc(c.qty ?? 1)}</b></div>
        <div class="row">SKU ${esc(c.sku || '—')}${c.sku_code ? ' (' + esc(c.sku_code) + ')' : ''}　<span class="cat">${esc(c.category || '')}</span></div>
        ${c.remark ? `<div class="row rem"><b>客户备注</b> ${esc(c.remark)}</div>` : ''}
        ${c.production_note ? `<div class="row note"><b>制作备注</b> ${esc(c.production_note)}</div>` : ''}
      </div>`;
    };
    const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>工厂制作单</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,"Microsoft YaHei",sans-serif;color:#222;padding:8mm}
  h1{font-size:16px;margin-bottom:8px}
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
  .pc{border:1px solid #888;border-radius:8px;padding:10px;break-inside:avoid;page-break-inside:avoid;font-size:12px;line-height:1.55}
  .hd{display:flex;align-items:center;gap:6px;border-bottom:1px solid #eee;padding-bottom:5px;margin-bottom:5px}
  .no{font-weight:700;font-size:12px}
  .badge{font-size:11px;padding:0 6px;border-radius:4px;border:1px solid;line-height:18px}
  .badge.cust{color:#722ed1;background:#f9f0ff;border-color:#722ed1}
  .row{margin-top:2px}
  .prod{font-size:13px}
  .cat{color:#888}
  .rem{color:#d4380d;background:#fff7e6;border-radius:4px;padding:2px 4px}
  .note{color:#0958d9}
  @page{size:A4 landscape;margin:8mm}
  @media print{.grid{gap:8px}}
</style></head><body>
  <h1>工厂制作单 · 共 ${cards.length} 单 · ${esc(dayjs().format('YYYY-MM-DD'))}</h1>
  <div class="grid">${cards.map(cardHtml).join('')}</div>
</body></html>`;
    win.document.write(html);
    win.document.close();
    win.focus();
    setTimeout(() => { win.print(); }, 350);
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <style>{PRINT_CSS}</style>
      <Space className="no-print" wrap>
        <Button icon={<ProfileOutlined />}>
          <Link to="/bom-list">BOM 清单</Link>
        </Button>
        <Button type="primary" icon={<PrinterOutlined />} onClick={printCards}>
          打印 / 存 PDF (一排三个)
        </Button>
        <Button icon={<DownloadOutlined />} onClick={exportExcel}>导出 Excel</Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          打开干净的打印页(无导航、卡片不跨页截断)、一排三张; 对话框里选「另存为 PDF」发同事。打印当前筛选下的 {visible.length} 单。
        </Typography.Text>
      </Space>
      <Alert
        className="no-print"
        type="info" showIcon
        message="工厂制作单: 已付款待发货(在工厂制作中)的订单。默认发货周期 30 天, 按下单日自动倒扣; 剩余越少字越红。"
        description="卡片可「编辑」改发货截止(特殊单)或加备注(红色醒目)。可按 剩余发货时间 / 下单日期 / 类目 排序。"
      />
      <Space className="no-print" align="center" wrap>
        <span style={{ fontSize: 20, fontWeight: 700 }}>
          共 <span style={{ color: '#1677ff', fontSize: 28 }}>{data.length}</span> 单在制作
        </span>
        <span style={{ color: '#ddd' }}>｜</span>
        <Typography.Text type="secondary">排序:</Typography.Text>
        <Segmented
          value={sortBy}
          onChange={(v) => setSortBy(v as 'days' | 'order_date' | 'category')}
          options={[
            { label: '剩余发货时间', value: 'days' },
            { label: '下单日期', value: 'order_date' },
            { label: '类目', value: 'category' },
          ]}
        />
        <span style={{ color: '#ddd' }}>｜</span>
        <Input.Search placeholder="按产品搜索(名称/编码/SKU)" allowClear style={{ width: 240 }}
          onSearch={setProdQ} onChange={(e) => { if (!e.target.value) setProdQ(''); }} />
      </Space>
      {/* 按紧急度分类筛选(点色块切换) */}
      <Space className="no-print" wrap size={6}>
        {[{ s: 'all', label: '全部', color: '#1677ff' },
          ...STATUS_ORDER.map((s) => ({ s, label: STATUS_META[s].label, color: STATUS_META[s].color }))
         ].map(({ s, label, color }) => {
          const n = s === 'all' ? data.length : (counts[s] || 0);
          const active = filterStatus === s;
          return (
            <Tag key={s} onClick={() => setFilterStatus(s)}
              style={{ cursor: 'pointer', padding: '3px 12px', fontSize: 13, borderRadius: 14,
                color: active ? '#fff' : color, background: active ? color : '#fff',
                borderColor: color, fontWeight: active ? 700 : 400 }}>
              {label} {n}
            </Tag>
          );
        })}
      </Space>

      {!isLoading && data.length === 0 && (
        <Empty description="当前没有「已付款待发货」的订单" />
      )}

      <Row gutter={[12, 12]}>
        {visible.map((c) => {
          const ds = dayStyle(c.days_left);
          return (
            <Col key={c.id} className="fp-print-col" xs={24} sm={12} lg={8} xxl={6}>
              <Card
                size="small"
                loading={isLoading}
                style={{ borderColor: c.days_left !== null && c.days_left <= 5 ? ds.color : undefined, height: '100%' }}
                styles={{ body: { padding: 10 } }}
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
                    <div>
                      {c.order_label && (
                        <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.2,
                          color: c.status === 'remote' ? '#722ed1' : '#c41d7f' }}>{c.order_label}</div>
                      )}
                      <span style={{ fontSize: 11, color: '#888' }}>{c.order_no}</span>
                    </div>
                    <Space size={2}>
                      <Tag style={{ marginInlineEnd: 0, color: STATUS_META[c.status]?.color, borderColor: STATUS_META[c.status]?.color, background: STATUS_META[c.status]?.bg }}>
                        {STATUS_META[c.status]?.label ?? c.status}
                      </Tag>
                      {c.is_custom && <Tag color="purple" style={{ marginInlineEnd: 0 }}>定制</Tag>}
                    </Space>
                  </Space>
                  <div style={{ fontSize: 12 }}>
                    <span style={{ color: '#888' }}>下单 {c.order_date || '-'}</span>
                    <span style={{ marginLeft: 8, color: ds.color, fontWeight: ds.weight }}>
                      {daysText(c.days_left)}
                    </span>
                    {c.ship_deadline && <Tag color="blue" style={{ marginLeft: 6 }}>已改截止 {c.ship_deadline}</Tag>}
                  </div>
                  <div style={{ fontSize: 12, lineHeight: 1.4 }}>
                    <div><b>{c.customer_name || '-'}</b>　{c.customer_phone || ''}</div>
                    <div style={{ color: '#555' }}>{c.customer_address || <span style={{ color: '#bbb' }}>无地址</span>}</div>
                  </div>
                  <div style={{ fontSize: 12 }}>
                    <div>{c.product_name || '-'} <span style={{ color: '#999' }}>×{c.qty}</span></div>
                    <div style={{ color: '#888' }}>{c.sku || ''}{c.sku_code ? ` (${c.sku_code})` : ''}</div>
                    {c.category && <Tag style={{ marginTop: 2 }}>{c.category}</Tag>}
                    {c.accessory && (
                      <Tag style={{ marginTop: 2 }} color={c.accessory.pending === 0 ? 'green' : 'orange'}>
                        {c.accessory.pending === 0 ? '配件配齐' : `配件缺 ${c.accessory.pending}/${c.accessory.total}`}
                      </Tag>
                    )}
                  </div>
                  {c.remark && (
                    <div style={{ fontSize: 12, background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 4, padding: '2px 6px' }}>
                      客户备注: {c.remark}
                    </div>
                  )}
                  {c.production_note && (
                    <div style={{ fontSize: 16, fontWeight: 800, color: '#f5222d', lineHeight: 1.3 }}>
                      ⚠ {c.production_note}
                    </div>
                  )}
                  <Space className="no-print" size={4} style={{ width: '100%' }}>
                    <Button size="small" style={{ flex: 1 }} onClick={() => openEdit(c)}>编辑(截止/备注)</Button>
                    <Button size="small" type={c.is_remote_ship ? 'primary' : 'default'}
                      onClick={() => saveMut.mutate({ id: c.id, patch: { is_remote_ship: !c.is_remote_ship } })}>
                      {c.is_remote_ship ? '取消远期' : '设为远期'}
                    </Button>
                    <Button size="small" onClick={() => setAccCard(c)}>配件</Button>
                  </Space>
                </Space>
              </Card>
            </Col>
          );
        })}
      </Row>

      <Modal
        title={`配件清单 — ${accCard?.order_no ?? ''}`}
        open={!!accCard}
        onCancel={() => setAccCard(null)}
        footer={[
          <Button key="arrive" type="primary"
            onClick={() => accCard && markArrivedMut.mutate(accCard.id)}>
            全部标已到货
          </Button>,
          <Button key="close" onClick={() => setAccCard(null)}>关闭</Button>,
        ]}
      >
        {accQuery.isLoading ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : (() => {
          const items = (accQuery.data || []).filter(
            (i: AccessoryItem) =>
              !i.is_factory_provided &&
              !(i.material_name || '').includes('木作') &&
              !(i.material_code || '').toUpperCase().startsWith('WD'),
          );
          if (items.length === 0) return <Empty description="本单无需采购的配件(木作/工厂提供不计入)" />;
          return (
            <Space direction="vertical" style={{ width: '100%' }} size={4}>
              {items.map((i: AccessoryItem) => (
                <Space key={i.id} style={{ width: '100%', justifyContent: 'space-between' }}>
                  <span>
                    {i.material_name || i.material_code}{' '}
                    <Typography.Text type="secondary">×{Number(i.qty_required)}{i.unit || ''}</Typography.Text>
                  </span>
                  <Tag color={['已到货', '工厂提供'].includes(i.status) ? 'green' : i.status === '未采购' ? 'default' : 'blue'}>
                    {i.status}
                  </Tag>
                </Space>
              ))}
            </Space>
          );
        })()}
      </Modal>

      <Modal
        title={`制作单 — ${editing?.order_no ?? ''}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() => editing && saveMut.mutate({ id: editing.id, patch: { ship_deadline: editDeadline ? editDeadline.format('YYYY-MM-DD') : null, production_note: editNote.trim() || null } })}
        confirmLoading={saveMut.isPending}
        okText="保存"
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Typography.Text type="secondary">发货截止（留空=按下单日+30天自动算；特殊单可改）</Typography.Text>
            <DatePicker value={editDeadline} onChange={setEditDeadline} style={{ width: '100%', marginTop: 4 }} allowClear />
          </div>
          <div>
            <Typography.Text type="secondary">备注（会在卡片上红色放大显示，提醒紧急/特殊事项）</Typography.Text>
            <Input.TextArea value={editNote} onChange={(e) => setEditNote(e.target.value)} rows={3} style={{ marginTop: 4 }} placeholder="如：客户催发 / 颜色特殊 / 加固包装…" />
          </div>
        </Space>
      </Modal>
    </Space>
  );
}
