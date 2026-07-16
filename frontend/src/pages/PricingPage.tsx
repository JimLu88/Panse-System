import { useEffect, useMemo, useRef, useState, type Key, type ReactNode } from 'react';
import {
  Button,
  Card,
  DatePicker,
  Dropdown,
  Form,
  Grid,
  Image,
  Input,
  InputNumber,
  Modal,
  Popover,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import { DownloadOutlined, EditOutlined, ExportOutlined, HistoryOutlined, PlusOutlined, QuestionCircleOutlined, TableOutlined, UploadOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import FullColumnView from '../components/FullColumnView';
import FieldPresetBar, { type PresetField } from '../components/FieldPresetBar';
import ProductThumb from '../components/ProductThumb';
import PricingEditorModal from '../components/PricingEditorModal';
import PricingDownloadsTab from './PricingDownloadsTab';
import ActivityAutoFillTab from './ActivityAutoFillTab';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  PricingSku,
  PricingFormulaRule,
  createPricingSku,
  downloadPricingTemplate,
  downloadPricingCatalog,
  downloadSignupForm,
  listPricingSkus,
  listPricingTemplates,
  listProductCategories,
  listFormulaRules,
  recomputeAllPricing,
  updatePricingSku,
  updateFormulaRule,
  upsertSkuCosts,
  upsertSkuPromo,
  listTaobaoExportTypes,
  downloadTaobaoExport,
  getCoefficientStats,
  runPromoPriceCheck,
  importTaobaoTitles,
  getPromoParams,
  setPromoParams,
  listPriceVersions,
  type PriceVersion,
  type CoefficientStat,
} from '../api/client';
import ResponsiveTable from '../components/ResponsiveTable';
import { CatalogCard } from '../components/MobileCards';

// 表格金额统一口径: 无小数(四舍五入) + ¥ 前缀; 悬浮公式里保留两位看精确值
function money(v: number | null) {
  return v === null || v === undefined ? '-' : `¥${Math.round(Number(v)).toLocaleString()}`;
}
const num = (v: unknown): number | null =>
  v === null || v === undefined || v === '' ? null : Number(v);
const str = (v: unknown): string | null =>
  v === null || v === undefined ? null : String(v);

// ── 字段元数据: 配件成本(22) / 文本配件 / 活动价(淘宝+小红书) ──
const ACCESSORY_FIELDS: { key: string; label: string }[] = [
  { key: 'rock_slab', label: '岩板' }, { key: 'drawer_rail', label: '抽屉轨道' }, { key: 'led_strip', label: '灯带' },
  { key: 'glass', label: '玻璃' }, { key: 'electric_rail', label: '电力轨道' }, { key: 'packing_sheet', label: '打包纸片' },
  { key: 'iron_pin', label: '铁销' }, { key: 'connector', label: '连接片' }, { key: 'aluminum_rail', label: '铝合金轨道' },
  { key: 'plastic_rail', label: '塑料轨道' }, { key: 'mini_handle', label: 'mini把手' }, { key: 'nail_free_glue', label: '免钉胶' },
  { key: 'engraving', label: '雕刻' }, { key: 'acrylic_strip', label: '亚克力条' }, { key: 'embedded_sleeve', label: '预埋套杆' },
  { key: 'cable_mgmt', label: '理线架+插排' }, { key: 'back_panel', label: '背板' }, { key: 'stainless_trim', label: '装饰条(不锈钢)' },
  { key: 'leg', label: '腿部' }, { key: 'soft_pack', label: '软包' }, { key: 'bed_board', label: '床铺板' }, { key: 'other_cost', label: '其他配件' },
];
const ACCESSORY_TEXT_FIELDS: { key: string; label: string }[] = [
  { key: 'other_desc', label: '外配件说明' }, { key: 'parts_remark', label: '配件备注' },
];
type PromoField = { key: string; label: string; kind: 'num' | 'text'; editable: boolean; pct?: boolean };
const PROMO_FIELDS: PromoField[] = [
  { key: 'taobao_item_id', label: '淘宝商品ID', kind: 'text', editable: true },
  { key: 'taobao_sku_id', label: '淘宝SKUID', kind: 'text', editable: true },
  { key: 'signup_price_big', label: '淘宝活动报名价', kind: 'num', editable: false },
  { key: 'shop_promo_rate', label: '小促单品立减设置%', kind: 'num', editable: true, pct: true },
  { key: 'shop_internal_final', label: '小促到手价', kind: 'num', editable: false },
  { key: 'mid_platform_discount', label: '中促力度%', kind: 'num', editable: false, pct: true },
  { key: 'mid_shop_rate', label: '中促单品立减设置%', kind: 'num', editable: true, pct: true },
  { key: 'mid_buyer_price', label: '中促到手价', kind: 'num', editable: false },
  { key: 'mid_vip_commission', label: '中促88VIP佣金%', kind: 'num', editable: false, pct: true },
  { key: 'mid_shop_receipt', label: '中促店铺到账', kind: 'num', editable: false },
  { key: 'mid_vip_final', label: '中促会员价', kind: 'num', editable: false },
  { key: 'big_platform_discount', label: '大促力度%', kind: 'num', editable: false, pct: true },
  { key: 'big_shop_rate', label: '大促单品立减设置%', kind: 'num', editable: true, pct: true },
  { key: 'big_buyer_price', label: '大促到手价', kind: 'num', editable: false },
  { key: 'big_vip_commission', label: '大促88VIP佣金%', kind: 'num', editable: false, pct: true },
  { key: 'big_shop_receipt', label: '大促店铺到账', kind: 'num', editable: false },
  { key: 'big_vip_final', label: '大促会员价', kind: 'num', editable: false },
  { key: 'xhs_item_id', label: '小红书商品ID', kind: 'text', editable: true },
  { key: 'xhs_sku_name', label: '小红书SKU名', kind: 'text', editable: true },
  { key: 'xhs_sku_id', label: '小红书SKUID', kind: 'text', editable: true },
  { key: 'xhs_list_price', label: '小红书标价', kind: 'num', editable: false },
  { key: 'xhs_activity_price', label: '小红书活动价', kind: 'num', editable: true },
  { key: 'xhs_promo_discount', label: '小红书折扣率', kind: 'num', editable: true },
  { key: 'xhs_promo_price', label: '小红书促销价', kind: 'num', editable: false },
];
// 活动价计算列的公式说明 (鼠标悬停显示: 引用了哪个数字 + 用了什么系数)
const PROMO_FORMULA: Record<string, string> = {
  signup_price_big: '88VIP报名价(报名价法) = 大促到手锚−2元 反解÷0.88, 与实际推送一致 (老口径"=日常价"已废弃)',
  shop_internal_final: '小促到手价 = 日常价 × 小促单品立减设置%',
  mid_buyer_price: '中促到手价 = 日常价 × (1 − 中促力度%) × 中促单品立减设置% ｜ 买家实付(消费券前、88VIP佣金前); 店铺实收见「中促店铺到账」= 到手价×(1−88VIP佣金2%)',
  mid_shop_receipt: '中促店铺到账 = 中促到手价 × (1 − 88VIP佣金2%) ｜ 店铺实收(佣金后), 即定价总表「中促价」',
  mid_vip_final: '中促会员价 = 中促到手价 − 阶梯消费券(按到手价档位)',
  big_buyer_price: '大促到手价 = 日常价 × (1 − 大促力度%) × 大促单品立减设置% ｜ 买家实付(消费券前、88VIP佣金前); 店铺实收见「大促店铺到账」= 到手价×(1−88VIP佣金2%)',
  big_shop_receipt: '大促店铺到账 = 大促到手价 × (1 − 88VIP佣金2%) ｜ 店铺实收(佣金后), 即定价总表「大促价」',
  big_vip_final: '大促会员价 = 大促到手价 − 阶梯消费券(按到手价档位)',
};

// 消费券阶梯编辑器 (到手价 ≥阈值 → 减额)
function PromoTiersEditor({ title, tiers, setTiers }: { title: string; tiers: number[][]; setTiers: (t: number[][]) => void }) {
  return (
    <div>
      <div style={{ marginBottom: 4, color: '#666' }}>{title} 消费券阶梯（到手价 ≥阈值 → 减额）</div>
      {tiers.map((t, i) => (
        <Space key={i} style={{ marginBottom: 4 }} size={4}>
          <span>≥</span>
          <InputNumber size="small" value={t[0]} min={0} style={{ width: 110 }}
            onChange={(v) => setTiers(tiers.map((x, j) => (j === i ? [Number(v) || 0, x[1]] : x)))} />
          <span>→ 减</span>
          <InputNumber size="small" value={t[1]} min={0} prefix="¥" style={{ width: 110 }}
            onChange={(v) => setTiers(tiers.map((x, j) => (j === i ? [x[0], Number(v) || 0] : x)))} />
          <Button size="small" type="text" danger onClick={() => setTiers(tiers.filter((_, j) => j !== i))}>删</Button>
        </Space>
      ))}
      <Button size="small" onClick={() => setTiers([...tiers, [0, 0]])}>+ 加一档</Button>
    </div>
  );
}

// 本次活动参数设置 (全局按档: 力度/佣金/消费券阶梯) → 保存即全表重算
function PromoParamsModal({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [midDisc, setMidDisc] = useState(12);
  const [midComm, setMidComm] = useState(1);
  const [bigDisc, setBigDisc] = useState(12);
  const [bigComm, setBigComm] = useState(0);
  const [midTiers, setMidTiers] = useState<number[][]>([]);
  const [bigTiers, setBigTiers] = useState<number[][]>([]);
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getPromoParams().then((p) => {
      const pc = (x: number) => Number((x * 100).toFixed(2));
      setMidDisc(pc(p.mid_platform_discount)); setMidComm(pc(p.mid_vip_commission));
      setBigDisc(pc(p.big_platform_discount)); setBigComm(pc(p.big_vip_commission));
      setMidTiers(p.mid_coupon_tiers || []); setBigTiers(p.big_coupon_tiers || []);
    }).catch(() => {}).finally(() => setLoading(false));
  }, [open]);
  const save = async () => {
    setSaving(true);
    try {
      const r = await setPromoParams({
        mid_platform_discount: midDisc / 100, mid_vip_commission: midComm / 100,
        big_platform_discount: bigDisc / 100, big_vip_commission: bigComm / 100,
        mid_coupon_tiers: midTiers, big_coupon_tiers: bigTiers,
      });
      message.success(`已保存，按新参数重算了 ${r.recomputed} 条活动价`);
      onSaved();
      onClose();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal title="本次活动参数（全局按档，改一次全表重算）" open={open} onCancel={onClose}
      onOk={save} confirmLoading={saving} okText="保存并重算全部活动价" width={640} destroyOnClose>
      {loading ? <div style={{ padding: 24, textAlign: 'center' }}>加载中…</div> : (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            每次活动的力度/佣金/消费券都可能不同，在这里改一次，保存后全表活动价按新参数重算（到手价/店铺到账/会员价）。
          </Typography.Text>
          <Space size="large" wrap>
            <span>中促力度% <InputNumber value={midDisc} min={0} max={100} style={{ width: 90 }} onChange={(v) => setMidDisc(Number(v) || 0)} /></span>
            <span>中促88VIP佣金% <InputNumber value={midComm} min={0} max={100} style={{ width: 90 }} onChange={(v) => setMidComm(Number(v) || 0)} /></span>
          </Space>
          <Space size="large" wrap>
            <span>大促力度% <InputNumber value={bigDisc} min={0} max={100} style={{ width: 90 }} onChange={(v) => setBigDisc(Number(v) || 0)} /></span>
            <span>大促88VIP佣金% <InputNumber value={bigComm} min={0} max={100} style={{ width: 90 }} onChange={(v) => setBigComm(Number(v) || 0)} /></span>
          </Space>
          <PromoTiersEditor title="中促" tiers={midTiers} setTiers={setMidTiers} />
          <PromoTiersEditor title="大促" tiers={bigTiers} setTiers={setBigTiers} />
        </Space>
      )}
    </Modal>
  );
}

// 字段总表(供快捷按钮勾选, 带分组) + 内置默认按钮
const BASE_FIELDS: PresetField[] = [
  { key: 'product_code', label: '产品编码', group: '基础信息' }, { key: 'sku_code', label: 'SKU编码', group: '基础信息' },
  { key: 'sku', label: '描述', group: '基础信息' }, { key: 'size_category', label: '分类', group: '基础信息' },
  { key: 'image_url', label: '图片', group: '基础信息' },
  { key: 'list_price', label: '标价', group: '价格档位' }, { key: 'daily_price', label: '日常价', group: '价格档位' },
  { key: 'small_promo', label: '小促', group: '价格档位' }, { key: 'mid_promo', label: '中促', group: '价格档位' },
  { key: 'big_promo', label: '大促', group: '价格档位' },
  { key: 'big_promo_margin', label: '大促利润', group: '利润/毛利' }, { key: 'gross_margin_rate', label: '毛利率', group: '利润/毛利' },
  { key: 'accounting_cost', label: '会计成本', group: '成本拆分' }, { key: 'physical_cost', label: '物理成本', group: '成本拆分' },
  { key: 'factory_cost', label: '工厂成本', group: '成本拆分' }, { key: 'wood_cost', label: '木作成本', group: '成本拆分' },
  { key: 'logistics_cost', label: '物流成本', group: '成本拆分' }, { key: 'install_cost', label: '安装成本', group: '成本拆分' },
  { key: 'packaging_cost', label: '包装成本', group: '成本拆分' }, { key: 'external_parts_cost', label: '外配件成本', group: '成本拆分' },
  { key: 'platform_fee_rate', label: '平台费率', group: '成本拆分' }, { key: 'tax', label: '税费', group: '成本拆分' },
];
const ALL_FIELDS: PresetField[] = [
  ...BASE_FIELDS,
  { key: 'taobao_title', label: '淘宝标题', group: '淘宝/活动价' },
  ...ACCESSORY_FIELDS.map((f) => ({ ...f, group: '配件成本' })),
  ...ACCESSORY_TEXT_FIELDS.map((f) => ({ ...f, group: '配件成本' })),
  ...PROMO_FIELDS.filter((f) => !f.key.startsWith('xhs')).map((f) => ({ key: f.key, label: f.label, group: '淘宝/活动价' })),
  ...PROMO_FIELDS.filter((f) => f.key.startsWith('xhs')).map((f) => ({ key: f.key, label: f.label, group: '小红书' })),
];
const _baseKeys = ['product_code', 'sku_code', 'sku', 'image_url'];
const PRESET_DEFAULTS = [
  { name: '成本基础价', fields: [..._baseKeys, 'factory_cost', 'wood_cost', 'logistics_cost', 'install_cost', 'packaging_cost', 'external_parts_cost', 'accounting_cost', 'physical_cost', 'list_price', 'daily_price', 'gross_margin_rate'] },
  { name: '配件成本', fields: [..._baseKeys, ...ACCESSORY_FIELDS.map((f) => f.key), ...ACCESSORY_TEXT_FIELDS.map((f) => f.key)] },
  { name: '淘宝', fields: [..._baseKeys, 'taobao_title', 'list_price', 'daily_price', 'small_promo', 'mid_promo', 'big_promo', 'big_promo_margin', ...PROMO_FIELDS.filter((f) => !f.key.startsWith('xhs')).map((f) => f.key)] },
  { name: '小红书', fields: [..._baseKeys, 'list_price', 'daily_price', ...PROMO_FIELDS.filter((f) => f.key.startsWith('xhs')).map((f) => f.key)] },
];

// 可拖拽列宽的表头单元格 (拖右边缘改宽)
function ResizableTitle(props: any) {
  const { onResize, width, children, ...rest } = props;
  const start = useRef<{ x: number; w: number } | null>(null);
  if (!width || !onResize) return <th {...rest}>{children}</th>;
  const onMouseDown = (e: any) => {
    e.preventDefault();
    e.stopPropagation();
    start.current = { x: e.clientX, w: width };
    const move = (ev: MouseEvent) => {
      if (!start.current) return;
      onResize(Math.max(60, start.current.w + (ev.clientX - start.current.x)));
    };
    const up = () => {
      start.current = null;
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  };
  return (
    <th {...rest} style={{ ...(rest.style || {}), position: 'relative' }}>
      {children}
      <span
        onMouseDown={onMouseDown}
        style={{ position: 'absolute', right: -4, top: 0, bottom: 0, width: 9, cursor: 'col-resize', zIndex: 1, userSelect: 'none' }}
      />
    </th>
  );
}

// 计算列单元格悬浮: 显示"公式 + 本行实际引用的数值"(图1 引用逻辑展示)
function cellFormulaTip(r: PricingSku, field: string): ReactNode {
  const n = (k: string) => num((r as any)[k]);
  const m = (v: number | null) => (v == null ? '—' : `¥${Number(v).toLocaleString()}`);
  switch (field) {
    case 'physical_cost':
      return <span>物理成本 = 物流{m(n('logistics_cost'))} + 安装{m(n('install_cost'))} + 总出厂{m(n('factory_cost'))} = <b>{m(n('physical_cost'))}</b></span>;
    case 'factory_cost':
      return <span>总出厂 = 木作{m(n('wood_cost'))} + 打包{m(n('packaging_cost'))} + 外配件{m(n('external_parts_cost'))} = <b>{m(n('factory_cost'))}</b></span>;
    case 'accounting_cost':
      return <span>会计成本 = 物理{m(n('physical_cost'))} + 平台费{m(n('platform_fee_rate'))} + 税{m(n('tax'))} = <b>{m(n('accounting_cost'))}</b></span>;
    case 'big_promo_margin':
      return <span>大促利润 = 大促价{m(n('big_promo'))} − 会计成本{m(n('accounting_cost'))} = <b>{m(n('big_promo_margin'))}</b></span>;
    case 'gross_margin_rate': {
      const g = n('gross_margin_rate');
      return <span>毛利率 = 大促利润{m(n('big_promo_margin'))} ÷ 大促价{m(n('big_promo'))} = <b>{g == null ? '—' : (g * 100).toFixed(1) + '%'}</b></span>;
    }
    case 'platform_fee_rate':
      return <span>平台费 = 大促价{m(n('big_promo'))} × 0.6% = <b>{m(n('platform_fee_rate'))}</b></span>;
    case 'tax':
      return <span>税费 = 大促价{m(n('big_promo'))} × 2% = <b>{m(n('tax'))}</b></span>;
    case 'logistics_cost':
      return <span>物流 = 按尺寸（{r.size_category || '未分类'}）：大700 / 中300 / 小80</span>;
    case 'install_cost':
      return <span>安装 = 按尺寸（{r.size_category || '未分类'}）：大150 / 中100 / 小0</span>;
    case 'external_parts_cost':
      return <span>外配件成本 = 22 项配件成本之和 = <b>{m(n('external_parts_cost'))}</b></span>;
    default:
      return null;
  }
}

// 只读数字格 (用户拍板: 单元格不再直接点击编辑, 统一走行「编辑」弹窗 — 防误触键盘改坏整表)
// onSave 参数保留以兼容旧调用点, 不再使用。tip=悬浮显示公式+引用值。
function EditableNumberCell({ value, unit = '¥', tip }: { value: number | null; onSave?: (v: number | null) => void; unit?: string; tip?: ReactNode }) {
  const span = (
    <span style={{ display: 'inline-block', minWidth: 36, borderBottom: tip ? '1px dotted #d9d9d9' : undefined }}
      title={tip ? undefined : '在行「编辑」里修改'}>
      {value === null || value === undefined
        ? <Typography.Text type="secondary">—</Typography.Text>
        : unit === '¥'
          ? `¥${Math.round(Number(value)).toLocaleString()}`
          : `${unit}${Number(value).toLocaleString()}`}
    </span>
  );
  return tip ? <Tooltip title={tip}>{span}</Tooltip> : span;
}

// 只读文本格 (统一走行「编辑」; onSave 保留兼容旧调用点)
function EditableTextCell({ value }: { value: string | null; onSave?: (v: string | null) => void }) {
  return (
    <span style={{ display: 'inline-block', minWidth: 36 }} title="在行「编辑」里修改">
      {value || <Typography.Text type="secondary">—</Typography.Text>}
    </span>
  );
}

// 价格档位格: 点击弹出「手动改值(仅这行) / 改系数(仅这行)」—— 都只改当前 SKU, 不影响别人。
// 中促/大促有「按SKU系数」(中促系数/大促系数): 改系数=给一个独立数字框, 用正确公式即时预览并只存这一行。
function PriceCell({
  value, physicalCost, baseLabel, feeTax, formulaText, onSaveValue, onSaveBase,
}: {
  value: number | null;
  physicalCost: number | null;
  baseLabel?: string;        // 有则=促销档(小/中/大促), 可改"基数"; 无则=结构档(标价/日常)只能手动改值
  feeTax: number;            // 抽佣+税 = 0.026
  formulaText: string;
  onSaveValue: (v: number | null) => void;   // 手动改值 → 存价(后端清该档基数, 手动锁定)
  onSaveBase?: (base: number | null) => void; // 改系数 → 存基数(后端按公式联动派生该档价)
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'value' | 'base'>('value');
  const [val, setVal] = useState<number | null>(value);
  // 会计基准 = 物理 ÷ (1 − feeTax); 基数 = 会计基准 ÷ 现价 (从现价反解显示当前基数)
  const curBase = baseLabel && value && physicalCost
    ? Math.round((physicalCost / (1 - feeTax) / value) * 10000) / 10000 : null;
  const [base, setBase] = useState<number | null>(curBase);
  useEffect(() => { setVal(value); }, [value]);
  useEffect(() => { setBase(curBase); }, [curBase]);

  // 预览 = ROUNDUP(会计基准 ÷ 基数, 到10) —— 与后端 recompute / 用户 Excel 口径一致
  const preview = baseLabel && base != null && base > 0 && physicalCost
    ? Math.ceil((physicalCost / (1 - feeTax) / base) / 10) * 10 : null;

  const panel = (
    <div style={{ width: 340 }}>
      <Segmented
        size="small" block value={mode} onChange={(v) => setMode(v as 'value' | 'base')}
        options={[{ label: '手动改值（仅这行）', value: 'value' }, { label: '改系数（仅这行）', value: 'base' }]}
      />
      {mode === 'value' ? (
        <Space style={{ marginTop: 10 }}>
          <InputNumber size="small" value={val} precision={2} min={0} onChange={setVal} style={{ width: 160 }} addonBefore="¥" />
          <Button size="small" type="primary" onClick={() => { setOpen(false); if (val !== value) onSaveValue(val); }}>保存</Button>
        </Space>
      ) : baseLabel ? (
        <div style={{ marginTop: 10 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {baseLabel}：价 = ROUNDUP(物理成本 ÷ (1 − 2.6%) ÷ {baseLabel}, 到10)。基数越大→越便宜(毛利≈1−基数)。
          </Typography.Text>
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13 }}>{baseLabel}</span>
            <InputNumber size="small" value={base} precision={4} min={0} max={1.2} step={0.01} onChange={setBase} style={{ width: 120 }} />
            <span style={{ color: '#999', fontSize: 13 }}>→ 预览 <b>¥{preview ?? '—'}</b></span>
          </div>
          <Button size="small" type="primary" style={{ marginTop: 10 }}
            disabled={base == null || !onSaveBase}
            onClick={() => { if (base != null && onSaveBase) { onSaveBase(base); setOpen(false); } }}>
            保存（只改这一行）
          </Button>
          <div style={{ marginTop: 6, fontSize: 11, color: '#52c41a' }}>✓ 存的是「基数」，成本一变价格自动联动；口径与定价总表/你的Excel一致</div>
        </div>
      ) : (
        <div style={{ marginTop: 12, fontSize: 12, color: '#999' }}>
          这一档（{formulaText}）是<b>结构性全局系数</b>，全表统一。要单独改这一行，请用上面「手动改值」。
        </div>
      )}
    </div>
  );

  // 用户拍板: 单元格只读 — 价格档也统一走行「编辑」; 悬浮仍展示公式口径
  void open; void setOpen; void panel; void mode;
  return (
    <Tooltip title={formulaText}>
      <span style={{ borderBottom: '1px dashed #d9d9d9' }} title="在行「编辑」里修改">
        {value === null || value === undefined ? <Typography.Text type="secondary">—</Typography.Text> : `¥${Math.round(Number(value)).toLocaleString()}`}
      </span>
    </Tooltip>
  );
}

// 毛利率格: 彩色 Tag (只读 — 统一走行「编辑」改价, 毛利率由系统联动重算)
function MarginCell({ row }: { row: PricingSku; onSaveDaily?: (dailyPrice: number) => void }) {
  const v = num(row.gross_margin_rate);
  return v === null || v === undefined
    ? <Typography.Text type="secondary">—</Typography.Text>
    : <Tag color={Number(v) >= 0.3 ? 'green' : Number(v) >= 0.15 ? 'orange' : 'red'}>{(Number(v) * 100).toFixed(1)}%</Tag>;
}

function SkuFormFields() {
  return (
    <>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]} style={{ minWidth: 180 }}>
          <Input placeholder="如 PS-24-21-001-0814" />
        </Form.Item>
        <Form.Item name="sku" label="SKU 描述">
          <Input placeholder="如 榉木餐桌-1.4米" style={{ minWidth: 200 }} />
        </Form.Item>
        <Form.Item name="sku_code" label="SKU 编码">
          <Input placeholder="系统自动生成可留空" style={{ minWidth: 160 }} />
        </Form.Item>
        <Form.Item name="size_category" label="尺寸分类">
          <Select style={{ width: 100 }} options={[
            { value: '小型', label: '小型' },
            { value: '中型', label: '中型' },
            { value: '大型', label: '大型' },
          ]} allowClear />
        </Form.Item>
      </Space>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="list_price" label="标价"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="daily_price" label="日常价"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="small_promo" label="小促价"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="mid_promo" label="中促价"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="big_promo" label="大促价"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
      </Space>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="accounting_cost" label="会计成本"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="physical_cost" label="物理成本"><InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} /></Form.Item>
        <Form.Item name="platform_fee_rate" label="平台佣金率"><InputNumber min={0} max={1} step={0.01} style={{ width: 100 }} placeholder="如 0.05" /></Form.Item>
        <Form.Item name="tax" label="税率"><InputNumber min={0} max={1} step={0.01} style={{ width: 100 }} placeholder="如 0.03" /></Form.Item>
      </Space>
      <Form.Item name="image_url" label="图片 URL（选填）"><Input placeholder="https://..." /></Form.Item>
    </>
  );
}

// 系数说明图例: 所有系数的中文标识 + 含义 + 全局默认(众数), 点开随时查
function CoefficientLegend({ coeffs }: { coeffs: CoefficientStat[] }) {
  if (!coeffs.length) return null;
  const grp = (s: string) => coeffs.filter((c) => c.scope === s);
  const row = (c: CoefficientStat) => (
    <div key={c.field} style={{ marginBottom: 6, lineHeight: 1.5 }}>
      <Tag color={c.scope === 'per_sku' ? 'orange' : 'blue'} style={{ marginInlineEnd: 6 }}>
        {c.label}{c.fixed != null ? ` = ${c.fixed}` : ''}
      </Tag>
      <span style={{ fontSize: 12, color: '#555' }}>{c.meaning}</span>
      {c.scope === 'per_sku' && c.mode != null && (
        <span style={{ fontSize: 11, color: '#999' }}>（全局默认/众数 {c.mode}，{c.distinct} 种取值）</span>
      )}
    </div>
  );
  const content = (
    <div style={{ maxWidth: 480, maxHeight: 440, overflow: 'auto' }}>
      <Typography.Text strong>结构性系数（全表统一，写死在公式里）</Typography.Text>
      <div style={{ margin: '4px 0 10px' }}>{grp('global').map(row)}</div>
      <Typography.Text strong>经营性系数（每个 SKU 可不同 · 表里橙色格 = 此 SKU 已单独改过）</Typography.Text>
      <div style={{ marginTop: 4 }}>{grp('per_sku').map(row)}</div>
    </div>
  );
  return (
    <Popover trigger="click" content={content} title="系数中文标识 + 含义">
      <Button size="small" icon={<QuestionCircleOutlined />}>系数说明</Button>
    </Popover>
  );
}

// ── 手机端: 定价按【产品】聚合成一张卡, 两级展开 (用户 2026-06-27) ──
// 一个产品一张卡(不再每个 SKU 一张); 第一步点卡展开列出该产品所有 SKU; 第二步点某个 SKU 展开它的价格/成本明细。
interface ProductGroup { product_code: string; product_name: string; image: string | null; skus: PricingSku[] }

function _skuVariant(sku: PricingSku, productName: string): string {
  const full = (sku.sku || '').trim();
  const stripped = productName && full.startsWith(productName)
    ? full.slice(productName.length).replace(/^[\s\-·_]+/, '') : full;
  return stripped || (sku as any).size_category || sku.sku_code || full;
}

// 第二级: 单个 SKU 行 — 点开展开它的价格/成本全明细
function SkuPriceRow({ sku, productName, onEdit }: { sku: PricingSku; productName: string; onEdit: (s: PricingSku) => void }) {
  const [open, setOpen] = useState(false);
  const pr = (k: string) => (sku as any)[k];
  const m = (v: any) => (v == null || v === '' ? null : `¥${Math.round(Number(v)).toLocaleString()}`);
  const gm = pr('gross_margin_rate');
  const daily = pr('daily_price');
  const ROWS: [string, string | null][] = [
    ['标价', m(pr('list_price'))], ['日常价', m(daily)],
    ['小促', m(pr('small_promo'))], ['中促', m(pr('mid_promo'))], ['大促', m(pr('big_promo'))],
    ['毛利率', gm != null ? `${(Number(gm) * 100).toFixed(1)}%` : null],
    ['物理成本', m(pr('physical_cost'))], ['会计成本', m(pr('accounting_cost'))],
    ['木作', m(pr('wood_cost'))], ['配件(外采)', m(pr('external_parts_cost'))], ['打包', m(pr('packaging_cost'))],
    ['物流', m(pr('logistics_cost'))], ['安装', m(pr('install_cost'))], ['工厂成本', m(pr('factory_cost'))],
  ];
  return (
    <div style={{ borderBottom: '1px solid #eef0f2' }}>
      <div onClick={() => setOpen((o) => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 2px', cursor: 'pointer' }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{_skuVariant(sku, productName)}</span>
        {(sku as any).size_category && <span style={{ fontSize: 11, color: '#80868b' }}>{(sku as any).size_category}</span>}
        <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
          {daily != null ? `日常¥${Math.round(Number(daily)).toLocaleString()}` : '—'}
        </span>
        <span style={{ color: '#1a73e8', fontSize: 11, whiteSpace: 'nowrap' }}>{open ? '收起 ▲' : '价格/成本 ▼'}</span>
      </div>
      {open && (
        <div style={{ padding: '0 2px 10px' }}>
          {ROWS.filter(([, v]) => v != null).map(([k, v], i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 12.5, borderBottom: '1px solid #f4f5f6' }}>
              <span style={{ color: '#5f6368' }}>{k}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</span>
            </div>
          ))}
          <Button size="small" block icon={<EditOutlined />} style={{ marginTop: 8 }} onClick={() => onEdit(sku)}>编辑此 SKU</Button>
        </div>
      )}
    </div>
  );
}

// 第一级: 产品卡 — 标题=产品名, 展开列出该产品全部 SKU
function ProductPricingCard({ group, onEdit }: { group: ProductGroup; onEdit: (s: PricingSku) => void }) {
  const skus = group.skus;
  const dailies = skus.map((s) => (s as any).daily_price).filter((v) => v != null).map(Number);
  const lo = dailies.length ? Math.min(...dailies) : null;
  const hi = dailies.length ? Math.max(...dailies) : null;
  const priceMeta = lo == null || hi == null ? '' : lo === hi
    ? `日常¥${Math.round(lo).toLocaleString()}` : `日常¥${Math.round(lo).toLocaleString()}~${Math.round(hi).toLocaleString()}`;
  const meta = [`${skus.length} 款`, priceMeta].filter(Boolean).join(' · ');
  return (
    <CatalogCard
      image={group.image}
      category={group.product_name}
      title={group.product_name}
      code={group.product_code}
      meta={meta}
      expandLabel={`${skus.length} 个 SKU`}
      renderExpand={() => (
        <div>{skus.map((s) => <SkuPriceRow key={s.id} sku={s} productName={group.product_name} onEdit={onEdit} />)}</div>
      )}
    />
  );
}

export default function PricingPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [downloadTab, setDownloadTab] = useState<'sheet' | 'downloads' | 'activity'>('sheet');
  const [sizeCategory, setSizeCategory] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [createOpen, setCreateOpen] = useState(false);
  const [importingTitles, setImportingTitles] = useState(false);
  const [editRow, setEditRow] = useState<PricingSku | null>(null);
  // 统一编辑器 (可拖动, 字段级历史, 一键覆盖同产品)
  const [editorRow, setEditorRow] = useState<PricingSku | null>(null);
  // 刚改过的行 → 「描述」列临时标绿; 改下一行或刷新页面自动消失 (只记最近一行)
  const [recentEditedId, setRecentEditedId] = useState<number | null>(null);
  // 工厂调价历史 (有效期定价) 浏览
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySku, setHistorySku] = useState<string>('');
  const { data: priceVersions = [], isFetching: pvLoading } = useQuery({
    queryKey: ['price-versions', historySku],
    queryFn: () => listPriceVersions(historySku.trim() ? { sku_code: historySku.trim(), limit: 500 } : { limit: 500 }),
    enabled: historyOpen,
  });
  const [promoParamsOpen, setPromoParamsOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [visibleKeys, setVisibleKeys] = useState<string[] | null>(null);   // null = 全部字段
  const [form] = Form.useForm();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;   // <768px: 卡片按产品聚合, 一次加载全量便于分组(桌面仍分页)

  const { data: categories = [] } = useQuery({ queryKey: ['product-categories'], queryFn: listProductCategories, staleTime: 5 * 60 * 1000 });
  const { data: formulaRules = [] } = useQuery({ queryKey: ['pricing-formula-rules-min'], queryFn: listFormulaRules, staleTime: 60 * 1000 });
  const ruleByField: Record<string, PricingFormulaRule> = {};
  formulaRules.forEach((r) => { ruleByField[r.field_name] = r; });
  // 系数目录(中文标识+含义) + 每个按SKU系数的众数(全局默认) — 三色覆盖标识用
  const { data: coeffStats = [] } = useQuery({ queryKey: ['pricing-coefficient-stats'], queryFn: getCoefficientStats, staleTime: 5 * 60 * 1000 });
  const coeffByField: Record<string, CoefficientStat> = {};
  coeffStats.forEach((c) => { coeffByField[c.field] = c; });

  const applyView = (cols: any[]) => {
    if (visibleKeys === null) return cols;
    const set = new Set(visibleKeys);
    return cols.filter((c) => c.fixed === 'right' || !c.dataIndex || set.has(c.dataIndex));
  };

  const { data, isFetching } = useQuery({
    queryKey: ['pricing-skus', q, sizeCategory, category, page, pageSize, isMobile],
    queryFn: () =>
      listPricingSkus({ q: q || undefined, size_category: sizeCategory, category,
        limit: isMobile ? 1000 : pageSize, offset: isMobile ? 0 : (page - 1) * pageSize }),
    placeholderData: keepPreviousData,
  });


  async function handleExportCatalog() {
    try {
      message.loading({ content: '正在生成带图图册 (含产品图, 稍候)…', key: 'catalog', duration: 0 });
      const blob = await downloadPricingCatalog();
      message.destroy('catalog');
      const url = URL.createObjectURL(new Blob([blob], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }));
      const a = document.createElement('a');
      a.href = url; a.download = '畔色定价图册.xlsx'; a.click();
      URL.revokeObjectURL(url);
      message.success('已下载「定价图册」Excel (带产品图)');
    } catch { message.destroy('catalog'); message.error('图册生成失败'); }
  }

  async function handleExportSignup() {
    try {
      message.loading({ content: '正在生成活动报名表 (带图, 稍候)…', key: 'signup', duration: 0 });
      const blob = await downloadSignupForm();
      message.destroy('signup');
      const url = URL.createObjectURL(new Blob([blob], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }));
      const a = document.createElement('a');
      a.href = url; a.download = '畔色活动报名表.xlsx'; a.click();
      URL.revokeObjectURL(url);
      message.success('已下载「活动报名表」Excel (报名价 + 单品立减折/立减金额)');
    } catch { message.destroy('signup'); message.error('报名表生成失败'); }
  }

  const invalidatePricing = () => qc.invalidateQueries({ queryKey: ['pricing-skus'] });
  const createMut = useMutation({
    mutationFn: createPricingSku,
    onSuccess: () => { message.success('定价 SKU 已创建'); setCreateOpen(false); form.resetFields(); invalidatePricing(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });
  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Record<string, unknown> }) => updatePricingSku(id, patch),
    onSuccess: () => { message.success('已更新'); setEditRow(null); form.resetFields(); invalidatePricing(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });
  const costMut = useMutation({
    mutationFn: ({ skuCode, patch }: { skuCode: string; patch: Record<string, unknown> }) => upsertSkuCosts(skuCode, patch),
    onSuccess: () => { message.success('配件成本已更新'); invalidatePricing(); },
    onError: () => message.error('保存失败'),
  });
  const promoMut = useMutation({
    mutationFn: ({ skuCode, patch }: { skuCode: string; patch: Record<string, unknown> }) => upsertSkuPromo(skuCode, patch),
    onSuccess: () => { message.success('活动价已更新'); invalidatePricing(); },
    onError: () => message.error('保存失败'),
  });
  const formulaMut = useMutation({
    mutationFn: ({ id, expression }: { id: number; expression: string }) => updateFormulaRule(id, { expression }),
    onSuccess: () => { message.success('公式已保存'); qc.invalidateQueries({ queryKey: ['pricing-formula-rules-min'] }); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '公式保存失败（检查语法）'),
  });
  const recomputeMut = useMutation({
    mutationFn: () => recomputeAllPricing(true),
    onSuccess: (r) => { message.success(r.message ?? '已重算'); invalidatePricing(); },
    onError: () => message.error('重算失败'),
  });

  function openEdit(row: PricingSku) { setEditRow(row); form.setFieldsValue(row); }

  // ── 列宽 (基础列可拖) ──
  const [colW, setColW] = useState<Record<string, number>>({
    product_code: 110, sku_code: 120, sku: 160, size_category: 70, image_url: 60,
    list_price: 90, daily_price: 90, small_promo: 90, mid_promo: 90, big_promo: 100,
    big_promo_margin: 90, gross_margin_rate: 90, accounting_cost: 100, physical_cost: 100,
    factory_cost: 90, wood_cost: 90, logistics_cost: 90, install_cost: 90,
    packaging_cost: 90, external_parts_cost: 100, platform_fee_rate: 90, tax: 80, actions: 70,
  });
  const cw = (key: string, def: number) => colW[key] ?? def;
  const mkResize = (key: string) => () => ({ width: colW[key], onResize: (w: number) => setColW((p) => ({ ...p, [key]: w })) });

  const items = data?.items ?? [];

  // 手机端: 把扁平的 SKU 列表按产品(product_code)聚合 → 一个产品一张卡 (同产品多尺寸 SKU 共享 product_code)
  const productGroups = useMemo<ProductGroup[]>(() => {
    const map = new Map<string, ProductGroup>();
    for (const r of items) {
      const pc = r.product_code || r.sku_code || '—';
      let g = map.get(pc);
      if (!g) { g = { product_code: pc, product_name: (r as any).product_name || pc, image: null, skus: [] }; map.set(pc, g); }
      g.skus.push(r);
      if (!g.image) g.image = (r as any).gallery_image_url || (r as any).image_url || null;
      if ((!g.product_name || g.product_name === pc) && (r as any).product_name) g.product_name = (r as any).product_name;
    }
    return Array.from(map.values());
  }, [items]);

  const saveField = (id: number, field: string, value: number | null) => { setRecentEditedId(id); updateMut.mutate({ id, patch: { [field]: value } }); };
  // 改基数(改系数): 写 base_* 列, 后端 recompute 按 ROUNDUP(物理÷(1−2.6%)÷基数,−1) 联动派生该档价
  const saveBase = (id: number, baseField: string, base: number | null) => { setRecentEditedId(id); updateMut.mutate({ id, patch: { [baseField]: base } }); };
  const saveCost = (row: PricingSku, field: string, value: number | string | null) => costMut.mutate({ skuCode: row.sku_code, patch: { [field]: value } });
  const savePromo = (row: PricingSku, field: string, value: number | string | null) => promoMut.mutate({ skuCode: row.sku_code, patch: { [field]: value } });

  // ── 多选 + 批量调价 ──
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const lastIdx = useRef<number | null>(null);
  const [batchField, setBatchField] = useState<string>('big_promo');
  const [batchMode, setBatchMode] = useState<'set' | 'multiply' | 'base'>('multiply');
  const [batchValue, setBatchValue] = useState<number | null>(null);
  const [batchEffFrom, setBatchEffFrom] = useState<any>(null);   // 批量调价生效日(选填): 此日前订单按老价
  const [batchRunning, setBatchRunning] = useState(false);
  const BATCH_FIELDS = [
    { value: 'list_price', label: '标价' }, { value: 'daily_price', label: '日常价' },
    { value: 'small_promo', label: '小促' }, { value: 'mid_promo', label: '中促' }, { value: 'big_promo', label: '大促' },
    { value: 'accounting_cost', label: '会计成本' }, { value: 'physical_cost', label: '物理成本' },
  ];
  async function batchApply() {
    if (batchValue === null || batchValue === undefined) { message.warning('请输入数值'); return; }
    const ids = selectedKeys.map(Number);
    const byId = new Map(items.map((r) => [r.id, r]));
    const tasks: Promise<unknown>[] = [];
    let skipped = 0;
    // 批量生效日(选填): 带则每单先封存旧值为历史区间, 此日前订单仍按老价; 不带=立即生效
    const eff = batchEffFrom ? { effective_from: batchEffFrom.format('YYYY-MM-DD') } : {};
    for (const id of ids) {
      const row = byId.get(id);
      if (batchMode === 'set') { tasks.push(updatePricingSku(id, { [batchField]: batchValue, ...eff })); }
      else if (batchMode === 'base') {
        // 设基数(小/中/大促): 写 base_* 列, 后端 recompute 按 ROUNDUP(物理÷(1−2.6%)÷基数,−1) 逐行用自己物理成本联动派生。
        // 不再前端算价直写(旧公式)→ 存基数, 成本一变价格自动跟, 且不与新引擎冲突。
        const baseCol = ({ small_promo: 'base_small', mid_promo: 'base_mid', big_promo: 'base_big' } as Record<string, string>)[batchField];
        if (!baseCol || batchValue <= 0) { skipped += 1; continue; }
        tasks.push(updatePricingSku(id, { [baseCol]: batchValue, ...eff }));
      }
      else {
        if (!row) { skipped += 1; continue; }
        const cur = Number((row as any)[batchField] ?? 0);
        tasks.push(updatePricingSku(id, { [batchField]: Math.round(cur * batchValue * 100) / 100, ...eff }));
      }
    }
    setBatchRunning(true);
    try {
      await Promise.all(tasks);
      message.success(`已套用 ${tasks.length} 个 SKU${skipped ? `（${skipped} 个跨页未加载，已跳过）` : ''}${batchEffFrom ? `（生效日 ${batchEffFrom.format('YYYY-MM-DD')}，此日前订单按老价）` : ''}`);
      setSelectedKeys([]); setBatchEffFrom(null); invalidatePricing();
    } catch { message.error('批量套用失败'); }
    finally { setBatchRunning(false); }
  }

  const fmtFormula: Record<string, string> = {
    list_price: '标价 = 物理成本 ÷ (1 − 2.6%) ÷ 0.4', daily_price: '日常价 = 标价 × 0.75',
    small_promo: '小促价 = 进位到10( 物理成本 ÷ (1 − 2.6%) ÷ 小促基数 )',
    mid_promo: '中促价 = 进位到10( 物理成本 ÷ (1 − 2.6%) ÷ 中促基数 )',
    big_promo: '大促价 = 进位到10( 物理成本 ÷ (1 − 2.6%) ÷ 大促基数 )',
  };
  // 小/中/大促 = 进位到10(物理成本 ÷ (1−2.6%) ÷ 基数); 基数按 SKU 不同(从现价反解)。改基数=只改这一行。
  const FEE_TAX = 0.026; // 成本加成率 = 支付手续费 0.6% + 税 2%
  const TIER_BASE: Record<string, string> = { small_promo: '小促基数', mid_promo: '中促基数', big_promo: '大促基数' };
  const TIER_BASE_COL: Record<string, string> = { small_promo: 'base_small', mid_promo: 'base_mid', big_promo: 'base_big' };
  // 各成本/计算列的真实公式(取自定价总表的单元格公式) — 悬浮显示。录入值的列标注来源。
  const COL_FORMULA: Record<string, string> = {
    accounting_cost: '会计总成本 = 物理总成本 + 平台费 + 税费',
    physical_cost: '物理总成本 = 物流费 + 安装费 + 总出厂成本',
    factory_cost: '总出厂成本 = 木作成本 + 打包 + 外采配件成本',
    external_parts_cost: '外采配件成本 = 22 项配件成本之和（岩板…其他）',
    logistics_cost: '物流费 = 按尺寸：大型 700 / 中型 300 / 小型 80',
    install_cost: '安装费 = 按尺寸：大型 150 / 中型 100 / 小型 0',
    platform_fee_rate: '支付手续费 = 大促价 × 0.6%（金额，非费率）',
    tax: '税费 = 大促价 × 2%',
    big_promo_margin: '大促利润 = 大促价 − 会计总成本(物理成本 + 支付手续费0.6% + 税2%)；大促价=店铺到账(已扣88VIP佣金2%)，故佣金也已反映',
    gross_margin_rate: '毛利率 = 大促利润 ÷ 大促价',
    wood_cost: '木作成本 = 录入值（来自 BOM 木料成本）',
    packaging_cost: '打包 = 录入值',
  };
  const colTitle = (label: string, key: string) =>
    COL_FORMULA[key]
      ? <Tooltip title={COL_FORMULA[key]}><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>{label}</span></Tooltip>
      : label;

  const priceTierCol = (key: string, label: string) => ({
    title: <Tooltip title={`公式：${fmtFormula[key]} ｜ 点格子可改值或改公式系数`}><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>{label}</span></Tooltip>,
    dataIndex: key, width: cw(key, 92), onHeaderCell: mkResize(key),
    render: (v: any, r: PricingSku) => (
      <PriceCell
        value={num(v)} physicalCost={num(r.physical_cost)}
        baseLabel={TIER_BASE[key]} feeTax={FEE_TAX} formulaText={`公式：${fmtFormula[key]}`}
        onSaveValue={(nv) => saveField(r.id, key, nv)}
        onSaveBase={TIER_BASE_COL[key] ? (b) => saveBase(r.id, TIER_BASE_COL[key], b) : undefined}
      />
    ),
  });

  // ── 列定义 ──
  const baseColumns: any[] = [
    { title: '产品编码', dataIndex: 'product_code', width: cw('product_code', 110), onHeaderCell: mkResize('product_code') },
    // 「漂移」标签已撤 (用户拍板 2026-06-12: BOM 单价只用于预估/定制报价, 不与定价对照)
    { title: 'SKU 编码', dataIndex: 'sku_code', width: cw('sku_code', 120), onHeaderCell: mkResize('sku_code') },
    { title: '描述', dataIndex: 'sku', width: cw('sku', 160), ellipsis: true, onHeaderCell: mkResize('sku'),
      render: (v: any, r: PricingSku) => r.id === recentEditedId
        ? <span style={{ background: '#b7eb8f', padding: '2px 6px', borderRadius: 4 }} title="刚改过这一行">{str(v)}</span>
        : str(v) },
    { title: '分类', dataIndex: 'size_category', width: cw('size_category', 70), onHeaderCell: mkResize('size_category') },
    { title: '图片', dataIndex: 'image_url', width: cw('image_url', 60), onHeaderCell: mkResize('image_url'),
      // SKU 图全部图库优先 (用户拍板 2026-06-12); 图库没有才回退淘宝 image_url
      render: (v: any, r: PricingSku) => <ProductThumb src={(r as any).gallery_image_url || (v ? String(v) : null)} size={40} /> },
    priceTierCol('list_price', '标价'),
    priceTierCol('daily_price', '日常价'),
    priceTierCol('small_promo', '小促'),
    priceTierCol('mid_promo', '中促'),
    priceTierCol('big_promo', '大促'),
    { title: <Tooltip title="毛利率 = 大促利润 ÷ 大促价 ｜ 点格子可按目标毛利率反算日常价"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>毛利率</span></Tooltip>, dataIndex: 'gross_margin_rate', width: cw('gross_margin_rate', 90), onHeaderCell: mkResize('gross_margin_rate'), render: (_: unknown, r: PricingSku) => <Tooltip title={cellFormulaTip(r, 'gross_margin_rate')}><span><MarginCell row={r} onSaveDaily={(dp) => saveField(r.id, 'daily_price', dp)} /></span></Tooltip> },
    { title: colTitle('会计成本', 'accounting_cost'), dataIndex: 'accounting_cost', width: cw('accounting_cost', 100), onHeaderCell: mkResize('accounting_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'accounting_cost')} onSave={(nv) => saveField(r.id, 'accounting_cost', nv)} /> },
    { title: colTitle('物理成本', 'physical_cost'), dataIndex: 'physical_cost', width: cw('physical_cost', 100), onHeaderCell: mkResize('physical_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'physical_cost')} onSave={(nv) => saveField(r.id, 'physical_cost', nv)} /> },
    { title: colTitle('大促利润', 'big_promo_margin'), dataIndex: 'big_promo_margin', width: cw('big_promo_margin', 90), onHeaderCell: mkResize('big_promo_margin'), render: (v: any, r: PricingSku) => <Tooltip title={cellFormulaTip(r, 'big_promo_margin')}><span style={{ borderBottom: '1px dotted #d9d9d9', cursor: 'help' }}>{money(num(v))}</span></Tooltip> },
    { title: colTitle('工厂成本', 'factory_cost'), dataIndex: 'factory_cost', width: cw('factory_cost', 90), onHeaderCell: mkResize('factory_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'factory_cost')} onSave={(nv) => saveField(r.id, 'factory_cost', nv)} /> },
    { title: colTitle('木作成本', 'wood_cost'), dataIndex: 'wood_cost', width: cw('wood_cost', 90), onHeaderCell: mkResize('wood_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} onSave={(nv) => saveField(r.id, 'wood_cost', nv)} /> },
    { title: colTitle('物流成本', 'logistics_cost'), dataIndex: 'logistics_cost', width: cw('logistics_cost', 90), onHeaderCell: mkResize('logistics_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'logistics_cost')} onSave={(nv) => saveField(r.id, 'logistics_cost', nv)} /> },
    { title: colTitle('安装成本', 'install_cost'), dataIndex: 'install_cost', width: cw('install_cost', 90), onHeaderCell: mkResize('install_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'install_cost')} onSave={(nv) => saveField(r.id, 'install_cost', nv)} /> },
    { title: colTitle('包装成本', 'packaging_cost'), dataIndex: 'packaging_cost', width: cw('packaging_cost', 90), onHeaderCell: mkResize('packaging_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} onSave={(nv) => saveField(r.id, 'packaging_cost', nv)} /> },
    { title: colTitle('外配件成本', 'external_parts_cost'), dataIndex: 'external_parts_cost', width: cw('external_parts_cost', 100), onHeaderCell: mkResize('external_parts_cost'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'external_parts_cost')} onSave={(nv) => saveField(r.id, 'external_parts_cost', nv)} /> },
    { title: colTitle('平台费', 'platform_fee_rate'), dataIndex: 'platform_fee_rate', width: cw('platform_fee_rate', 90), onHeaderCell: mkResize('platform_fee_rate'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'platform_fee_rate')} onSave={(nv) => saveField(r.id, 'platform_fee_rate', nv)} /> },
    { title: colTitle('税费', 'tax'), dataIndex: 'tax', width: cw('tax', 80), onHeaderCell: mkResize('tax'), render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} tip={cellFormulaTip(r, 'tax')} onSave={(nv) => saveField(r.id, 'tax', nv)} /> },
  ];
  const accessoryColumns: any[] = [
    ...ACCESSORY_FIELDS.map((f) => ({ title: f.label, dataIndex: f.key, width: 96, render: (v: any, r: PricingSku) => <EditableNumberCell value={num(v)} onSave={(nv) => saveCost(r, f.key, nv)} /> })),
    ...ACCESSORY_TEXT_FIELDS.map((f) => ({ title: f.label, dataIndex: f.key, width: 140, ellipsis: true, render: (v: any, r: PricingSku) => <EditableTextCell value={str(v)} onSave={(nv) => saveCost(r, f.key, nv)} /> })),
  ];
  // 按SKU系数: 三色覆盖标识(橙=与全局众数不同) + 表头中文标识带含义悬浮
  const COEFF_COLOR = ['shop_promo_rate', 'mid_shop_rate', 'big_shop_rate', 'xhs_promo_discount'];
  const coeffTitle = (f: PromoField) => {
    const meta = coeffByField[f.key];
    if (!meta) return f.label;
    return (
      <Tooltip title={<span>{meta.meaning}{meta.mode != null ? <><br />全局默认(众数)：{meta.mode}（{meta.distinct} 种取值）</> : null}</span>}>
        <span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>{f.label}</span>
      </Tooltip>
    );
  };
  const taobaoTitleCol: any = {
    title: '淘宝标题', dataIndex: 'taobao_title', width: 240, ellipsis: true,
    render: (v: any) => (v
      ? <Tooltip title={v}><span>{String(v)}</span></Tooltip>
      : <Typography.Text type="secondary" title="上传「淘宝商品导出」可批量填充">—</Typography.Text>),
  };
  const promoColumns: any[] = [taobaoTitleCol, ...PROMO_FIELDS.map((f) => ({
    title: COEFF_COLOR.includes(f.key) ? coeffTitle(f) : f.label,
    dataIndex: f.key, width: f.kind === 'text' ? 130 : 100, ellipsis: f.kind === 'text',
    render: (v: any, r: PricingSku) => {
      if (!f.editable) {
        if (f.kind !== 'num') return str(v) || <Typography.Text type="secondary">—</Typography.Text>;
        if (f.pct) return num(v) == null ? '—' : `${(num(v)! * 100).toFixed(1)}%`;
        const tip = PROMO_FORMULA[f.key];
        const body = money(num(v));
        return tip
          ? <Tooltip title={tip}><span style={{ borderBottom: '1px dotted #d9d9d9', cursor: 'help' }}>{body}</span></Tooltip>
          : body;
      }
      if (f.kind !== 'num') return <EditableTextCell value={str(v)} onSave={(nv) => savePromo(r, f.key, nv)} />;
      const cell = f.pct
        ? <EditableNumberCell value={num(v) == null ? null : num(v)! * 100} unit="%" onSave={(nv) => savePromo(r, f.key, nv == null ? nv : (nv as number) / 100)} />
        : <EditableNumberCell value={num(v)} unit={f.key.endsWith('_rate') || f.key.endsWith('_discount') ? '' : '¥'} onSave={(nv) => savePromo(r, f.key, nv)} />;
      const meta = coeffByField[f.key];
      const val = num(v);
      const override = COEFF_COLOR.includes(f.key) && meta?.mode != null && val != null && Math.abs(val - meta.mode) > 1e-6;
      if (!override) return cell;
      return (
        <Tooltip title={`单行覆盖：全局众数 ${meta!.mode} → 本行 ${val}（Δ${(val - meta!.mode!).toFixed(4)}）。橙色 = 此 SKU 系数与大多数不同`}>
          <span style={{ background: '#fff3e0', border: '1px solid #ffd591', borderRadius: 4, padding: '0 4px', display: 'inline-block' }}>{cell}</span>
        </Tooltip>
      );
    },
  }))];
  const actionsCol = {
    title: '操作', width: cw('actions', 70), fixed: 'right' as const,
    render: (_: unknown, row: PricingSku) => <Button size="small" icon={<EditOutlined />} onClick={() => setEditorRow(row)}>编辑</Button>,
  };

  // 列组配色: 价格档=蓝 / 利润汇总=黄 / 成本拆分=绿 / 22配件=紫 / 渠道(淘宝小红书)=粉
  // 只动底色不动逻辑, 让眼睛能按色块定位列组, 减少看错列。
  const GROUP_BG: Record<string, string> = {
    list_price: '#eef6ff', daily_price: '#eef6ff', small_promo: '#eef6ff',
    mid_promo: '#eef6ff', big_promo: '#eef6ff',
    gross_margin_rate: '#fffbe6', accounting_cost: '#fffbe6',
    physical_cost: '#fffbe6', big_promo_margin: '#fffbe6',
    factory_cost: '#f6ffed', wood_cost: '#f6ffed', logistics_cost: '#f6ffed',
    install_cost: '#f6ffed', packaging_cost: '#f6ffed',
    external_parts_cost: '#f6ffed', platform_fee_rate: '#f6ffed', tax: '#f6ffed',
  };
  ACCESSORY_FIELDS.forEach((f) => { GROUP_BG[f.key] = '#fbf4ff'; });
  PROMO_FIELDS.forEach((f) => { GROUP_BG[f.key] = '#eef4ff'; });
  GROUP_BG['taobao_title'] = '#eef4ff';
  const withGroupColor = (cols: any[]) => cols.map((c) => {
    const bg = GROUP_BG[c.dataIndex as string];
    if (!bg) return c;
    return {
      ...c,
      onCell: () => ({ style: { background: bg } }),
      onHeaderCell: (col: any) => ({
        ...(c.onHeaderCell ? c.onHeaderCell(col) : {}),
        style: { background: bg },
      }),
    };
  });

  const allColumns = withGroupColor([...baseColumns, ...accessoryColumns, ...promoColumns, actionsCol]);
  const visibleColumns = applyView(allColumns);
  const scrollX = visibleColumns.reduce((a: number, c: any) => a + (typeof c.width === 'number' ? c.width : 110), 0);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <PricingEditorModal
        row={editorRow}
        onClose={() => setEditorRow(null)}
        onSaved={() => { if (editorRow) setRecentEditedId(editorRow.id); invalidatePricing(); }}
        onSaveNext={() => {
          if (!editorRow) return;
          setRecentEditedId(editorRow.id);
          const idx = items.findIndex((x) => x.id === editorRow.id);
          setEditorRow(idx >= 0 && idx + 1 < items.length ? items[idx + 1] : null);
          if (idx >= 0 && idx + 1 >= items.length) message.info('已是最后一行');
        }}
      />
      <PromoParamsModal
        open={promoParamsOpen}
        onClose={() => setPromoParamsOpen(false)}
        onSaved={invalidatePricing}
      />
      <Modal open={historyOpen} onCancel={() => setHistoryOpen(false)} width={980}
        title="工厂调价历史 (有效期定价)"
        footer={<Button onClick={() => setHistoryOpen(false)}>关闭</Button>}>
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Space wrap>
            <Input.Search size="small" allowClear placeholder="按 SKU 编码筛选(留空=全部)" style={{ width: 260 }}
              onSearch={(v) => setHistorySku(v)} onChange={(e) => { if (!e.target.value) setHistorySku(''); }} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              每行 = 该「适用区间」内使用的旧价; 「生效日」当天起改用新价, 该日之前的订单仍按此行老价核算。最新价见定价表本身。
            </Typography.Text>
          </Space>
          <Table<PriceVersion>
            size="small" rowKey="id" loading={pvLoading} dataSource={priceVersions}
            pagination={{ pageSize: 20, size: 'small' }} scroll={{ x: 940 }}
            locale={{ emptyText: '暂无调价历史 (还没有做过"带生效日"的调价)' }}
            columns={[
              { title: '生效日', dataIndex: 'period_end', width: 100 },
              { title: 'SKU编码', dataIndex: 'sku_code', width: 150 },
              { title: '品名', dataIndex: 'sku', width: 180, ellipsis: true },
              { title: '适用区间(老价)', width: 175, render: (_: unknown, r: PriceVersion) => `${r.period_start ?? ''} ~ ${r.period_end ?? ''}` },
              { title: '老物理成本', dataIndex: 'physical_cost', width: 100, render: (v: number | null) => money(v) },
              { title: '老工厂成本', dataIndex: 'factory_cost', width: 100, render: (v: number | null) => money(v) },
              { title: '老大促', dataIndex: 'big_promo', width: 90, render: (v: number | null) => money(v) },
              { title: '老标价', dataIndex: 'list_price', width: 90, render: (v: number | null) => money(v) },
              { title: '操作人', dataIndex: 'created_by', width: 90 },
              { title: '记录时间', dataIndex: 'created_at', width: 145, render: (v: string | null) => v ? v.replace('T', ' ').slice(0, 16) : '' },
            ]}
          />
        </Space>
      </Modal>
      <Tabs
        activeKey={downloadTab}
        onChange={(k) => setDownloadTab(k as 'sheet' | 'downloads' | 'activity')}
        items={[{ key: 'sheet', label: '定价总表' }, { key: 'downloads', label: '📥 表格下载' },
          { key: 'activity', label: '🚀 活动自动填写' }]}
      />
      {downloadTab === 'downloads' && <PricingDownloadsTab />}
      {downloadTab === 'activity' && <ActivityAutoFillTab />}
      {downloadTab === 'sheet' && (
      <>
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>定价总表</Typography.Title>
        <Space>
          <Tooltip title="改价台: Excel 式逐个改 定价基数(0.86/0.88/0.9), 促价=ROUNDUP(成本÷基数)自动算 + 单品立减系数反推">
            <Link to="/shop-price-board"><Button type="primary" icon={<TableOutlined />}>改价台</Button></Link>
          </Tooltip>
          <Tooltip title="各类批量表格下载(定价图册 / 活动报名表 / 淘宝单品立减各档)已挪到上方「📥 表格下载」标签页">
            <Button icon={<DownloadOutlined />} onClick={() => setDownloadTab('downloads')}>表格下载</Button>
          </Tooltip>
          <Tooltip title="工厂/销售价的调价历史: 每条=某SKU某段时间使用的旧价, 分界日之前的订单按此老价核算">
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>调价历史</Button>
          </Tooltip>
          <Tooltip title="上传「淘宝商品导出.xlsx」(宝贝标题↔商家编码), 自动填入定价表淘宝标题, 并把只带长标题、没编码的订单对回编码、按定价表重算成本">
            <Upload
              accept=".xlsx,.xls"
              showUploadList={false}
              beforeUpload={(file) => {
                setImportingTitles(true);
                importTaobaoTitles(file as File)
                  .then((r) => {
                    message.success(
                      `淘宝标题已导入: 定价表填充 ${r.filled_by_sku_code + r.filled_by_product_code} 个SKU` +
                      `, 订单回填编码 ${r.orders_code_backfilled} 笔` +
                      (r.unmatched_titles.length ? `；${r.unmatched_titles.length} 个宝贝定价表里没有(需补SKU)` : ''));
                    invalidatePricing();
                  })
                  .catch((e: any) => message.error(e?.response?.data?.detail ?? '导入失败'))
                  .finally(() => setImportingTitles(false));
                return false;
              }}
            >
              <Button icon={<UploadOutlined />} loading={importingTitles}>导入淘宝标题</Button>
            </Upload>
          </Tooltip>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateOpen(true); form.resetFields(); }}>新增定价</Button>
        </Space>
      </Space>
      <Card size="small">
        <Space wrap>
          <Segmented value={viewMode} onChange={(v) => setViewMode(v as 'curated' | 'full')}
            options={[{ label: '精选视图（可编辑）', value: 'curated' }, { label: '全部列', value: 'full' }]} />
          {viewMode === 'curated' && (
            <>
              <Input.Search allowClear placeholder="搜编码 / 描述 / 淘宝商品ID / SKUID" style={{ width: 240 }} onSearch={(v) => { setQ(v); setPage(1); }} />
              <Select allowClear placeholder="大小分类" style={{ width: 110 }} value={sizeCategory} onChange={(v) => { setSizeCategory(v); setPage(1); }}
                options={[{ value: '小型', label: '小型' }, { value: '中型', label: '中型' }, { value: '大型', label: '大型' }]} />
              <Select allowClear showSearch placeholder="按类目筛" style={{ width: 170 }} value={category} onChange={(v) => { setCategory(v); setPage(1); }}
                options={categories.map((c) => ({ value: c, label: c }))} />
              <FieldPresetBar tableKey="pricing_sku" allFields={ALL_FIELDS} defaults={PRESET_DEFAULTS} onChange={setVisibleKeys} />
              <CoefficientLegend coeffs={coeffStats} />
              <Button size="small" onClick={async () => {
                try {
                  const r = await runPromoPriceCheck();
                  message[r.mismatch_count ? 'warning' : 'success'](
                    `活动价核对: 查 ${r.checked} 条, ${r.mismatch_count} 条不符${r.mismatch_count ? ' — 详见异常中心' : ''}`);
                } catch { message.error('核对失败'); }
              }}>活动价核对</Button>
              <Button size="small" type="primary" ghost onClick={() => setPromoParamsOpen(true)}>活动参数</Button>
              {/* 「BOM漂移检查」按钮已撤 (用户拍板 2026-06-12: BOM单价只用于预估/定制报价) */}
            </>
          )}
        </Space>
      </Card>

      {viewMode === 'full' && <FullColumnView entity="pricing_sku" defaultShowAll
        searchPlaceholder="搜编码 / 描述 / 淘宝商品ID / SKUID" />}

      {viewMode === 'curated' && selectedKeys.length > 0 && (
        <div style={{ background: '#f5f7fa', border: '1px solid #e6eaf0', borderRadius: 8, padding: '8px 12px' }}>
          <Space wrap>
            <span>已选 <b>{selectedKeys.length}</b> 个 SKU</span>
            <Select size="small" style={{ width: 110 }} value={batchField} onChange={setBatchField} options={BATCH_FIELDS} />
            <Select size="small" style={{ width: 160 }} value={batchMode} onChange={(v) => setBatchMode(v as 'set' | 'multiply' | 'base')}
              options={[{ value: 'multiply', label: '× 系数' }, { value: 'set', label: '设为固定值' }, { value: 'base', label: '设基数(小/中/大促)' }]} />
            <InputNumber size="small" style={{ width: 130 }} value={batchValue} onChange={setBatchValue} placeholder={batchMode === 'multiply' ? '如 0.95' : batchMode === 'base' ? '基数 如 0.87' : '如 1999'} />
            <DatePicker size="small" style={{ width: 150 }} value={batchEffFrom} onChange={setBatchEffFrom} allowClear placeholder="生效日(选填)" />
            <Button size="small" type="primary" loading={batchRunning} onClick={batchApply}>套用</Button>
            <Button size="small" type="text" onClick={() => setSelectedKeys([])}>取消</Button>
            {batchEffFrom && <Typography.Text type="warning" style={{ fontSize: 12 }}>此日之前的订单仍按老价, 不受本次批量影响</Typography.Text>}
          </Space>
        </div>
      )}

      {viewMode === 'curated' && (
      <ResponsiveTable<ProductGroup>
        data={productGroups}
        rowKey={(g) => g.product_code}
        loading={isFetching}
        emptyText="暂无定价"
        renderCard={(g) => <ProductPricingCard group={g} onEdit={setEditorRow} />}
        desktop={
      <Table<PricingSku>
        size="small"
        sticky
        rowKey="id"
        loading={isFetching}
        dataSource={items}
        components={{ header: { cell: ResizableTitle } }}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: setSelectedKeys,
          preserveSelectedRowKeys: true,
          onSelect: (record: PricingSku, selected: boolean, _rows: any, e: any) => {
            const idx = items.findIndex((r) => r.id === record.id);
            if (e?.shiftKey && lastIdx.current !== null && idx !== -1) {
              const a = Math.min(lastIdx.current, idx);
              const b = Math.max(lastIdx.current, idx);
              const range = items.slice(a, b + 1).map((r) => r.id);
              setSelectedKeys((prev) => {
                const set = new Set<Key>(prev);
                range.forEach((k) => (selected ? set.add(k) : set.delete(k)));
                return Array.from(set);
              });
            }
            lastIdx.current = idx;
          },
        }}
        scroll={{ x: scrollX }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          showSizeChanger: true,
          pageSizeOptions: [50, 100, 200],
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
        columns={visibleColumns as any}
      />
        }
      />
      )}
      </>
      )}

      {/* 新增弹窗 */}
      <Modal title="新增定价 SKU" open={createOpen} onCancel={() => { setCreateOpen(false); form.resetFields(); }} onOk={() => form.submit()} confirmLoading={createMut.isPending} width={720} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={(v) => createMut.mutate(v)}><SkuFormFields /></Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal title={`编辑定价 — ${editRow?.sku_code}`} open={!!editRow} onCancel={() => { setEditRow(null); form.resetFields(); }} onOk={() => form.submit()} confirmLoading={updateMut.isPending} width={720} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={(v) => editRow && updateMut.mutate({ id: editRow.id, patch: v })}><SkuFormFields /></Form>
      </Modal>
    </Space>
  );
}
