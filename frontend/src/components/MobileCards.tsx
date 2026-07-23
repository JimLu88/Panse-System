/**
 * 手机端卡片套件 (用户拍板 2026-06-26, 按 iOS / Material 交互设计):
 *   - MetricCard  数值型「方案B」: 净利大字 + 3 KPI + 渐进展开 (月度经营/逐单核对/销售排行)
 *   - CatalogCard 目录型「媒体列表卡」: 真图优先 / 无图回退 iOS 彩色实底瓷砖图标 (产品总表/定价/客户)
 *   - StatusCard  状态型「状态卡」: 标题 + 状态徽章 + 关键金额/日期 + 操作 (订单/工厂单/售后)
 *
 * 桌面端不渲染这些 (由 ResponsiveTable 按断点切换); 仅 <768px 用。
 * 缩略图配色贴合畔色 Google 蓝体系 (#1a73e8), 冷色为主, 与顶栏蓝不冲突。
 */
import { useState, type CSSProperties, type ReactNode } from 'react';
import { Button } from 'antd';
import { ColumnWidthOutlined, PictureOutlined, EditOutlined } from '@ant-design/icons';

const BLUE = '#1a73e8', INK = '#202124', SUB = '#5f6368', FAINT = '#80868b';
const LINE = '#e8eaed', BG = '#f1f3f4', GREEN = '#188038', RED = '#d93025';
void INK;

const money = (v: number | null | undefined) =>
  v == null ? '—' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${Number(v).toFixed(1)}%`;

// ── 品类 → iOS 线性图标 (SF Symbols 风, currentColor 描边) ───────────────────
function glyph(key: string, px: number): ReactNode {
  const c: Record<string, ReactNode> = {
    table: (<><path d="M3.5 10.5h21" /><path d="M5.5 7.5h17l2 3h-21z" /><path d="M6.5 10.5v9" /><path d="M21.5 10.5v9" /></>),
    cabinet: (<><rect x="5.5" y="4.5" width="17" height="19" rx="2" /><path d="M14 4.5v19" /><path d="M10.5 13h1.4" /><path d="M16.1 13h1.4" /><path d="M5.5 23.5v1.5" /><path d="M22.5 23.5v1.5" /></>),
    bed: (<><path d="M3.5 21v-9" /><path d="M3.5 16h21" /><path d="M24.5 21v-4a4 4 0 0 0-4-4H9" /><path d="M3.5 21v1.5" /><path d="M24.5 21v1.5" /><path d="M8 12.5v-1.6a1.5 1.5 0 0 1 1.5-1.5h3a1.5 1.5 0 0 1 1.5 1.5v1.6" /></>),
    nightstand: (<><rect x="7" y="6.5" width="14" height="16" rx="1.8" /><path d="M7 13h14" /><path d="M12 9.7h4" /><path d="M12 16.4h4" /><path d="M9 22.5v1.6" /><path d="M19 22.5v1.6" /></>),
    chair: (<><path d="M8 4v9" /><path d="M18 4v9" /><path d="M8 9h10" /><path d="M7 13h12l-1 5" /><path d="M9 18l-1 5" /><path d="M19 18l1 5" /></>),
    box: (<><path d="M14 3.5l9 5v10l-9 5-9-5v-10z" /><path d="M5 8.5l9 5 9-5" /><path d="M14 13.5v10" /></>),
  };
  return (
    <svg viewBox="0 0 28 28" width={px} height={px} fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">{c[key] ?? c.box}</svg>
  );
}

// 品类字符串 → (图标 key, 实底瓷砖颜色)。配色: 餐桌蓝/餐边柜青/床靛/床头柜绿/椅暖橙, 其余灰蓝。
function catStyle(category?: string | null): { key: string; color: string } {
  const s = category ?? '';
  if (s.includes('床头')) return { key: 'nightstand', color: '#1f9d57' };
  if (s.includes('床')) return { key: 'bed', color: '#5566e0' };
  if (s.includes('椅') || s.includes('凳')) return { key: 'chair', color: '#e8833a' };
  if (s.includes('边柜') || s.includes('餐边') || s.includes('柜') || s.includes('斗')) return { key: 'cabinet', color: '#0f9d8f' };
  if (s.includes('桌') || s.includes('几') || s.includes('台')) return { key: 'table', color: BLUE };
  return { key: 'box', color: '#7b8aa3' };
}

/** 缩略图瓷砖: 有真图用真图(圆角方), 无图/加载失败回退 iOS 彩色实底图标 (用户选 A)。 */
export function CategoryTile({ image, category, size = 56 }: { image?: string | null; category?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  const radius = Math.round(size * 0.25);
  const base: CSSProperties = { width: size, height: size, flex: `0 0 ${size}px`, borderRadius: radius };
  if (image && !broken) {
    return (
      <div style={{ ...base, overflow: 'hidden', boxShadow: 'inset 0 0 0 1px rgba(0,0,0,.06)' }}>
        <img src={image} alt="" loading="lazy" onError={() => setBroken(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
    );
  }
  const { key, color } = catStyle(category);
  return (
    <div style={{ ...base, background: color, color: '#fff', display: 'flex', alignItems: 'center',
      justifyContent: 'center', boxShadow: '0 1px 2px rgba(0,0,0,.12)' }}>
      {glyph(key, Math.round(size * 0.5))}
    </div>
  );
}

// ── 通用样式 ──────────────────────────────────────────────────────────────────
const card: CSSProperties = { background: '#fff', borderRadius: 14, boxShadow: '0 1px 2px rgba(60,64,67,.1),0 1px 3px rgba(60,64,67,.16)', marginBottom: 11, overflow: 'hidden' };
const clamp2: CSSProperties = { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' };

// ── 数值型: MetricCard (方案B) ───────────────────────────────────────────────
export interface MetricKpi { label: string; value: ReactNode }
export function MetricCard({ title, profit, profitRate, kpis, moreRows, highlight }: {
  title: ReactNode; profit: number | null; profitRate: number | null;
  kpis: MetricKpi[]; moreRows?: MetricKpi[]; highlight?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const pos = (profit ?? 0) >= 0;
  const pcolor = pos ? GREEN : RED;
  return (
    <div style={{ ...card, padding: 13, ...(highlight ? { background: '#eef4ff', boxShadow: '0 0 0 1px #d6e4ff' } : {}) }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 15 }}>{title}</span>
        <span style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: '-.5px', color: pcolor, fontVariantNumeric: 'tabular-nums' }}>{money(profit)}</div>
          <div style={{ fontSize: 11, fontWeight: 600, color: pcolor }}>净利率 {pct(profitRate)}</div>
        </span>
      </div>
      <div style={{ display: 'flex', gap: 7, marginTop: 11 }}>
        {kpis.map((k, i) => (
          <div key={i} style={{ flex: 1, background: BG, borderRadius: 10, padding: '7px 9px', textAlign: 'center', minWidth: 0 }}>
            <div style={{ fontSize: 10.5, color: SUB }}>{k.label}</div>
            <div style={{ fontSize: 13.5, fontWeight: 700, marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>{k.value}</div>
          </div>
        ))}
      </div>
      {open && moreRows && (
        <div style={{ marginTop: 11, borderTop: `1px dashed ${LINE}`, paddingTop: 4 }}>
          {moreRows.map((r, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', fontSize: 12.5, borderBottom: i < moreRows.length - 1 ? `1px solid ${LINE}` : 'none' }}>
              <span style={{ color: SUB }}>{r.label}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{r.value}</span>
            </div>
          ))}
        </div>
      )}
      {moreRows && moreRows.length > 0 && (
        <button onClick={() => setOpen((o) => !o)}
          style={{ marginTop: 8, width: '100%', background: 'none', border: 0, color: BLUE, fontSize: 12.5, fontWeight: 600, padding: 7, cursor: 'pointer' }}>
          {open ? '收起 ▲' : '展开全部 ▼'}
        </button>
      )}
    </div>
  );
}

// ── 目录型: CatalogCard (媒体列表卡) ─────────────────────────────────────────
export function CatalogCard({ image, category, title, code, brand, meta, onGallery, onDimensions, dimensionActions, onEdit, renderExpand, expandLabel = 'SKU' }: {
  image?: string | null; category?: string | null; title: ReactNode; code?: string;
  brand?: string | null; meta?: string; onGallery?: () => void; onDimensions?: () => void; dimensionActions?: ReactNode; onEdit?: () => void;
  renderExpand?: () => ReactNode; expandLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const codeChip: CSSProperties = { fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 10.5, color: SUB, background: BG, padding: '1px 6px', borderRadius: 5 };
  const brandChip: CSSProperties = { fontSize: 10.5, color: BLUE, background: '#e8f0fe', padding: '1px 7px', borderRadius: 5 };
  return (
    <div style={card}>
      <div style={{ display: 'flex', gap: 12, padding: 11, alignItems: 'center' }}>
        <CategoryTile image={image} category={category} size={60} />
        <div style={{ flex: 1, minWidth: 0, cursor: renderExpand ? 'pointer' : 'default' }} onClick={() => renderExpand && setOpen((o) => !o)}>
          <div style={{ fontWeight: 600, fontSize: 14.5, lineHeight: 1.32, ...clamp2 }}>{title}</div>
          <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {code && <code style={codeChip}>{code}</code>}
            {brand && <span style={brandChip}>{brand}</span>}
            {meta && <span style={{ fontSize: 11.5, color: SUB }}>{meta}</span>}
          </div>
        </div>
        {(onGallery || onDimensions || dimensionActions || onEdit) && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {onGallery && <Button size="small" type="primary" ghost icon={<PictureOutlined />} onClick={onGallery}>图库</Button>}
            {onDimensions && <Button size="small" icon={<ColumnWidthOutlined />} onClick={onDimensions}>尺寸</Button>}
            {dimensionActions}
            {onEdit && <Button size="small" icon={<EditOutlined />} onClick={onEdit}>编辑</Button>}
          </div>
        )}
      </div>
      {renderExpand && (
        <>
          <div onClick={() => setOpen((o) => !o)} style={{ fontSize: 11, color: BLUE, padding: '0 13px 9px', cursor: 'pointer' }}>
            {open ? '收起 ▲' : `展开 ${expandLabel} ▼`}
          </div>
          {open && <div style={{ borderTop: `1px solid ${LINE}`, background: '#fafbfc', padding: '8px 12px' }}>{renderExpand()}</div>}
        </>
      )}
    </div>
  );
}

// ── 状态型: StatusCard (状态卡) ──────────────────────────────────────────────
export type StatusTone = 'wait' | 'ship' | 'done' | 'close' | 'info';
const TONE: Record<StatusTone, { bg: string; fg: string }> = {
  wait: { bg: '#fef7e0', fg: '#b06000' }, ship: { bg: '#e8f0fe', fg: '#1557b0' },
  done: { bg: '#e6f4ea', fg: '#137333' }, close: { bg: '#fce8e6', fg: '#a50e0e' },
  info: { bg: '#f1f3f4', fg: '#5f6368' },
};
export interface StatusAction { label: string; onClick?: () => void; primary?: boolean }
export function StatusCard({ title, status, tone = 'info', fields, amount, actions }: {
  title: ReactNode; status: string; tone?: StatusTone;
  fields?: { label: string; value: ReactNode }[]; amount?: ReactNode; actions?: StatusAction[];
}) {
  const t = TONE[tone];
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 13px 8px' }}>
        <span style={{ fontWeight: 600, fontSize: 14, flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</span>
        <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 999, whiteSpace: 'nowrap', background: t.bg, color: t.fg }}>{status}</span>
      </div>
      {(fields?.length || amount != null) && (
        <div style={{ padding: '0 13px 9px', display: 'flex', flexWrap: 'wrap', gap: '4px 16px', alignItems: 'center' }}>
          {fields?.map((f, i) => (
            <span key={i} style={{ fontSize: 12 }}><span style={{ color: FAINT }}>{f.label} </span><span style={{ fontWeight: 600 }}>{f.value}</span></span>
          ))}
          {amount != null && <span style={{ marginLeft: 'auto', fontSize: 16, fontWeight: 800, fontVariantNumeric: 'tabular-nums' }}>{amount}</span>}
        </div>
      )}
      {actions && actions.length > 0 && (
        <div style={{ display: 'flex', gap: 8, borderTop: `1px solid ${LINE}`, padding: '8px 13px' }}>
          {actions.map((a, i) => (
            <div key={i} onClick={a.onClick}
              style={{ flex: 1, textAlign: 'center', fontSize: 12.5, fontWeight: 600, padding: 6, borderRadius: 8, cursor: 'pointer',
                color: a.primary ? BLUE : SUB, background: a.primary ? '#f4f8ff' : BG }}>
              {a.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 通用表格卡片: 任意 PresetTable 自动按列生成 (覆盖所有列表页, 无需逐页定制) ────────
function _colLabel(c: any): ReactNode {
  return typeof c?.title === 'string' ? c.title : (c?.title ?? c?.key ?? String(c?.dataIndex ?? ''));
}
function _cellVal(c: any, row: any, i: number): ReactNode {
  const di = c?.dataIndex;
  const v = di == null ? undefined
    : (Array.isArray(di) ? di.reduce((o: any, k: any) => (o == null ? o : o[k]), row) : row[di]);
  const out = c?.render ? c.render(v, row, i) : v;
  return (out === undefined || out === null || out === '') ? '—' : out;
}
const _TITLE_KEYS = ['name', 'order_no', 'product_name', 'title', 'sku', 'material_name',
  'customer_name', 'platform_order_no', 'factory_order_no', 'purchase_no', 'supplier'];

/** 任意表格的行 → 卡片: 标题(智能挑名称列) + 前4字段预览 + 展开全部 + 操作列。 */
export function GenericTableCard({ row, columns, index }: { row: any; columns: any[]; index: number }) {
  const [open, setOpen] = useState(false);
  const isAction = (c: any) => c && (c.key === 'actions' || c.fixed === 'right'
    || (typeof c.title === 'string' && c.title.includes('操作')));
  const cols = columns.filter((c: any) => c && (c.dataIndex != null || c.render));
  const actionCols = cols.filter(isAction);
  const bodyCols = cols.filter((c: any) => !actionCols.includes(c));
  const titleCol = bodyCols.find((c: any) => _TITLE_KEYS.includes(c.dataIndex))
    || bodyCols.find((c: any) => c.dataIndex !== 'id') || bodyCols[0];
  const fieldCols = bodyCols.filter((c: any) => c !== titleCol);
  const preview = fieldCols.slice(0, 4), rest = fieldCols.slice(4);
  const KV = ({ c }: { c: any }) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '4px 0', fontSize: 12.5, borderBottom: `1px solid ${LINE}` }}>
      <span style={{ color: SUB, flex: '0 0 auto', maxWidth: '42%' }}>{_colLabel(c)}</span>
      <span style={{ textAlign: 'right', minWidth: 0, overflowWrap: 'anywhere', fontVariantNumeric: 'tabular-nums' }}>{_cellVal(c, row, index)}</span>
    </div>
  );
  return (
    <div style={card}>
      <div style={{ padding: 11 }}>
        <div style={{ fontWeight: 600, fontSize: 14.5, lineHeight: 1.35, marginBottom: 7, ...clamp2 }}>
          {titleCol ? _cellVal(titleCol, row, index) : `#${index + 1}`}
        </div>
        {preview.map((c, i) => <KV key={i} c={c} />)}
        {open && rest.map((c, i) => <KV key={`r${i}`} c={c} />)}
        {rest.length > 0 && (
          <button onClick={() => setOpen((o) => !o)}
            style={{ marginTop: 6, width: '100%', background: 'none', border: 0, color: BLUE, fontSize: 12.5, fontWeight: 600, padding: 6, cursor: 'pointer' }}>
            {open ? '收起 ▲' : `展开全部 ${rest.length} 项 ▼`}
          </button>
        )}
      </div>
      {actionCols.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, borderTop: `1px solid ${LINE}`, background: '#fafbfc', padding: '8px 11px', alignItems: 'center' }}>
          {actionCols.map((c, i) => <span key={i}>{_cellVal(c, row, index)}</span>)}
        </div>
      )}
    </div>
  );
}
