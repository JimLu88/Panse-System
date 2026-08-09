import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Input,
  InputNumber,
  Row,
  Select,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DeleteOutlined,
  PictureOutlined,
  PlusOutlined,
  RobotOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { CompetitorImportButton } from '../components/CompetitorImportButton';
import { QuoteSettingsTab } from './CustomizationPage';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

// ── 与后端 /api/customization/v2/* 对应的类型 ──
interface ClassifyResult {
  customization_type: string;
  base_product_code: string | null;
  base_product_name: string | null;
  confidence: number;
  reasoning: string;
  ai_note?: string;
  category_guess?: string | null;
  target_length_m?: number | null;
  target_width_cm?: number | null;
  target_height_cm?: number | null;
  target_material?: string | null;
  add_parts?: { material: string; qty: number }[];
  remove_parts?: { material: string; qty: number }[];
  candidates?: { product_code: string; product_name: string; sku?: string | null; confidence: number; size_flag?: boolean }[];
  sku_candidates?: { sku_code: string; sku_name: string; price: number | null; confidence: number }[];
  ai_used?: boolean;
  size_warning?: boolean;
}
interface BreakdownItem {
  label: string;
  amount: number;
  note: string;
  paint_surcharge?: boolean;
}
interface PartDetail {
  name: string;
  material: string;
  unit: string;
  qty: number;
  length_cm: number;
  width_cm: number;
  area_m2: number;
  unit_price: number;
  material_cost: number;
  formula: string;
  change: 'add' | 'remove';
  delta: number;
  priced: boolean;
}
interface CompetitorRow {
  store: string | null;
  product?: string | null;
  sku_name: string | null;
  wood: string | null;
  price: number;
  source: string;
  diff_pct: number | null;
  is_lower: boolean;
  link?: string | null;
}
interface Comparison {
  our_price: number;
  competitors: CompetitorRow[];
  competitor_available: boolean;
  baseline: { label: string; price: number; diff_pct: number; is_lower: boolean } | null;
  note: string;
}
interface LightResult {
  final_price: number | null;
  anchor: number;
  anchor_method: string;
  material_delta: number;
  size_delta?: number;
  std_width_cm?: number | null;
  std_height_cm?: number | null;
  addremove_delta: number;
  base_product_name?: string | null;
  selected_sku_code?: string | null;
  selected_sku_name?: string | null;
  category?: string;
  subtotal_before_safety?: number;
  safety_delta?: number;
  paint_surcharge?: number;
  pricing_parameters?: {
    factory_profit_rate: number;
    panse_profit_rate: number;
    safety_rate: number;
  };
  specification?: {
    target_length_m?: number | null;
    target_width_cm?: number | null;
    target_height_cm?: number | null;
    target_material?: string | null;
    price_tier?: string;
    standard_width_cm?: number | null;
    standard_height_cm?: number | null;
  };
  factory_predicted?: number | null;
  break_even_factory?: number | null;
  break_even_buffer?: number | null;
  break_even_sell?: number | null;
  product_margin?: number | null;
  breakdown: BreakdownItem[];
  parts_detail?: PartDetail[];
  comparison?: Comparison;
  error?: string;
}
// 增减部位(可编辑: 分类器自动填 + 用户手调)
interface EditPart {
  key: number;
  change: 'add' | 'remove' | 'modify';
  material: string;        // 部位/原料名 (modify 时 = 原部位)
  material_real?: string;  // modify 时 = 改成的新材料
  qty: number;
  length_cm?: number;      // 尺寸(空=用模板几何); 手填覆盖→快速改价
  width_cm?: number;
}
interface HardwareItem {
  material: string;
  qty: number;
  unit: string;
}
interface HeavyResult {
  final_price: number;
  wood_cost: number;
  labor_fee: number;
  accessory_total: number;
  factory_quote_compare: number;
  factory_predicted?: number | null;
  break_even_factory?: number | null;
  break_even_buffer?: number | null;
  break_even_sell?: number | null;
  break_even_note?: string;
  inferred_hardware: HardwareItem[];
  error?: string;
}
interface TemplatePart {
  part: string;
  default_material: string;
  freq: number;
}
interface BoardRow {
  key: number;
  part: string;
  material: string;
  length_cm: number;
  width_cm: number;
  qty: number;
  unit?: string;              // 平方米/米/个 — 配件(电力轨道/铝轨等)按此计价, 缺省平方米
  is_accessory?: boolean;     // 配件(玻璃/岩板/五金)不计入工厂木作对比
}
interface QuoteLog {
  id: number;
  source: string | null;
  message: string;
  extra: Record<string, unknown> | null;
  created_at: string | null;
}

function authHeaders(json: boolean): Record<string, string> {
  const t = localStorage.getItem('panse_token');
  const h: Record<string, string> = {};
  if (json) h['Content-Type'] = 'application/json';
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const resp = await fetch('/api/customization' + path, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    const e = (await resp.json().catch(() => ({ detail: '请求失败' }))) as { detail?: string };
    throw new Error(e.detail ?? '请求失败');
  }
  return (await resp.json()) as T;
}

async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch('/api/customization' + path, { headers: authHeaders(false) });
  if (!resp.ok) throw new Error('请求失败');
  return (await resp.json()) as T;
}

const breakdownCols: ColumnsType<BreakdownItem> = [
  { title: '项目', dataIndex: 'label', key: 'label' },
  {
    title: '金额(元)',
    dataIndex: 'amount',
    key: 'amount',
    align: 'right',
    render: (v: number) => <Text style={{ color: v < 0 ? '#cf1322' : undefined }}>{v.toFixed(2)}</Text>,
  },
  {
    title: '说明',
    dataIndex: 'note',
    key: 'note',
    render: (v: string) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>,
  },
];

const competitorCols: ColumnsType<CompetitorRow> = [
  {
    title: '竞品',
    key: 'who',
    render: (_: unknown, r: CompetitorRow) => (
      <div>
        <Text style={{ fontSize: 12 }}>{r.store ?? '—'}</Text>
        <br />
        <Text type="secondary" style={{ fontSize: 11 }}>{r.sku_name ?? r.product ?? ''}</Text>
      </div>
    ),
  },
  { title: '木材', dataIndex: 'wood', key: 'wood', width: 76, render: (v: string | null) => v ?? '—' },
  {
    title: '竞品价',
    dataIndex: 'price',
    key: 'price',
    align: 'right',
    render: (v: number) => `¥${v.toFixed(0)}`,
  },
  {
    title: '我们 vs 它',
    key: 'diff',
    align: 'right',
    render: (_: unknown, r: CompetitorRow) =>
      r.diff_pct == null ? (
        '—'
      ) : (
        <Tag color={r.is_lower ? 'green' : 'red'}>
          {r.is_lower ? '低' : '高'} {Math.abs(r.diff_pct)}%
        </Tag>
      ),
  },
  {
    title: '来源',
    dataIndex: 'source',
    key: 'source',
    width: 76,
    render: (v: string) => (
      <Text type="secondary" style={{ fontSize: 11 }}>
        {v}
      </Text>
    ),
  },
];

const logCols: ColumnsType<QuoteLog> = [
  { title: '#', dataIndex: 'id', key: 'id', width: 56 },
  {
    title: '来源',
    dataIndex: 'source',
    key: 'source',
    width: 130,
    render: (s: string | null) => <Tag>{s ?? '—'}</Tag>,
  },
  { title: '输入', dataIndex: 'message', key: 'message', ellipsis: true },
  {
    title: '报价',
    key: 'price',
    width: 90,
    align: 'right',
    render: (_: unknown, r: QuoteLog) => {
      const p = r.extra?.final_price;
      return p != null ? `¥${Number(p).toFixed(0)}` : '—';
    },
  },
  {
    title: '时间',
    dataIndex: 'created_at',
    key: 'created_at',
    width: 160,
    render: (t: string | null) => (t ? t.replace('T', ' ').slice(0, 19) : '—'),
  },
];

function FactoryRedline({
  predicted,
  breakEven,
  buffer,
}: {
  predicted?: number | null;
  breakEven?: number | null;
  buffer?: number | null;
}) {
  if (predicted == null && breakEven == null) return null;
  return (
    <Card size="small" type="inner" title="工厂价红线(预测 / 盈亏平衡)" styles={{ body: { padding: 8 } }}>
      <Space size="large" wrap>
        <span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            预测工厂价
          </Text>
          <br />
          <Text strong style={{ fontSize: 18 }}>
            {predicted != null ? `¥${predicted.toFixed(2)}` : '—'}
          </Text>
        </span>
        <span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            盈亏平衡价(红线)
          </Text>
          <br />
          <Text strong style={{ fontSize: 18, color: '#cf1322' }}>
            {breakEven != null ? `¥${breakEven.toFixed(2)}` : '—'}
          </Text>
        </span>
        <span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            工厂涨价余地(还能涨多少到亏线)
          </Text>
          <br />
          <Text strong style={{ fontSize: 18, color: buffer != null && buffer >= 0 ? '#389e0d' : '#cf1322' }}>
            {buffer != null ? `¥${buffer.toFixed(2)}` : '—'}
          </Text>
        </span>
      </Space>
      <div style={{ marginTop: 6 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          工厂实际报价 ≤ 红线才不亏;红线 − 预测 = 工厂涨价余地(≈本单利润)。净不亏口径(已扣运费/安装/配件/平台扣点/税)。
        </Text>
      </div>
    </Card>
  );
}

// A3: 增减部位可搜索下拉(常用部位 + 物料表), 仍允许手填自定义
function PartSelect({
  value,
  onChange,
  groups,
}: {
  value: string;
  onChange: (v: string) => void;
  groups: { label: string; options: { value: string; label: string }[] }[];
}) {
  const [search, setSearch] = useState('');
  const opts = useMemo(() => {
    const known = new Set(groups.flatMap((g) => g.options.map((o) => o.value)));
    const extra = search && !known.has(search) ? search : value && !known.has(value) ? value : '';
    return extra
      ? [{ label: '自定义', options: [{ value: extra, label: `${extra}(手填)` }] }, ...groups]
      : groups;
  }, [groups, search, value]);
  return (
    <Select
      showSearch
      value={value || undefined}
      onChange={(v) => onChange((v as string) ?? '')}
      onSearch={setSearch}
      placeholder="选部位/材料(可搜索·可手填)"
      style={{ width: 240 }}
      options={opts}
      filterOption={(input, option) =>
        String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
      }
      allowClear
    />
  );
}

// 保本价 = 报价 × (1 − 本款大促毛利率); 毛利率实时取自该款 SKU, 可手动改
function MarginFloor({
  finalPrice,
  productMargin,
  factoryPredicted,
}: {
  finalPrice: number;
  productMargin?: number | null;
  factoryPredicted?: number | null;
}) {
  const auto = productMargin != null ? Math.round(productMargin * 1000) / 10 : null;
  const [marginPct, setMarginPct] = useState<number | null>(auto);
  useEffect(() => setMarginPct(auto), [auto]);
  const breakEven = marginPct != null ? finalPrice * (1 - marginPct / 100) : null;
  return (
    <Card size="small" type="inner" title="保本价(按本款大促毛利率)" styles={{ body: { padding: 8 } }}>
      <Space wrap size="large" align="center">
        <span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            本款大促毛利率(实时·可改)
          </Text>
          <br />
          <InputNumber
            value={marginPct}
            onChange={(v) => setMarginPct(v)}
            addonAfter="%"
            min={0}
            max={95}
            step={0.5}
            style={{ width: 120 }}
          />
          {auto != null ? (
            <Text type="secondary" style={{ fontSize: 11 }}>
              {' '}系统取 {auto}%
            </Text>
          ) : (
            <Text type="warning" style={{ fontSize: 11 }}>
              {' '}该款无大促价数据,请手填
            </Text>
          )}
        </span>
        <span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            保本价(最低可卖)
          </Text>
          <br />
          <Text strong style={{ fontSize: 22, color: '#fa8c16' }}>
            {breakEven != null ? `¥${breakEven.toFixed(2)}` : '—'}
          </Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {' '}= 报价×(1−毛利率)
          </Text>
        </span>
        {factoryPredicted != null && (
          <span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              预测工厂价
            </Text>
            <br />
            <Text strong style={{ fontSize: 18 }}>
              ¥{factoryPredicted.toFixed(2)}
            </Text>
          </span>
        )}
      </Space>
      <div style={{ marginTop: 6 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          毛利率实时取自本款 SKU 的「大促价÷会计成本」(=系统现有大促公式),可手动覆盖;保本价随之变。卖低于保本价即亏。
        </Text>
      </div>
    </Card>
  );
}

export default function CustomQuoteV2Page() {
  // ── 分类 ──
  const [desc, setDesc] = useState('');
  const [clsImages, setClsImages] = useState<File[]>([]);
  const [clsLoading, setClsLoading] = useState(false);
  const [cls, setCls] = useState<ClassifyResult | null>(null);
  const [candidates, setCandidates] = useState<NonNullable<ClassifyResult['candidates']>>([]);
  const [skuCandidates, setSkuCandidates] = useState<NonNullable<ClassifyResult['sku_candidates']>>([]);
  const [selectedSku, setSelectedSku] = useState<string | undefined>(undefined);
  const [skuLoading, setSkuLoading] = useState(false);
  const skuRequestSeq = useRef(0);
  const [autoQuote, setAutoQuote] = useState(true);   // 说一句话→自动往下算价
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // ── 普通定制 ──
  const [pcode, setPcode] = useState('');
  const [len, setLen] = useState<number | null>(null);
  const [wid, setWid] = useState<number | null>(null);   // 宽/深(cm), 空=该长度的标准宽
  const [hgt, setHgt] = useState<number | null>(null);   // 高(cm), 空=标准高
  const [mat, setMat] = useState('');
  const [tier, setTier] = useState('big');   // 报价档位 (默认大促)
  const [lightLoading, setLightLoading] = useState(false);
  const [light, setLight] = useState<LightResult | null>(null);
  const [customQ, setCustomQ] = useState<{ final_price: number | null; note?: string; error?: string; from_bom?: boolean } | null>(null);   // 纯定制方向(板单引擎)并排价
  const [customBoards, setCustomBoards] = useState<BoardRow[]>([]);   // ②真实BOM带出的部件, 供带入③编辑
  const [parts, setParts] = useState<EditPart[]>([]);   // 增减部位(分类器自动填, 可手调)
  const partSeq = useRef(1);
  const [partOpts, setPartOpts] = useState<{ parts: string[]; materials: string[]; woods: string[] }>({ parts: [], materials: [], woods: [] });

  // ── 特殊定制 ──
  const [ptype, setPtype] = useState('');
  const [hlen, setHlen] = useState<number | null>(null);
  const [boards, setBoards] = useState<BoardRow[]>([
    { key: 1, part: '', material: '樱桃木-2.2cm', length_cm: 0, width_cm: 0, qty: 1 },
  ]);
  const [heavyLoading, setHeavyLoading] = useState(false);
  const [heavy, setHeavy] = useState<HeavyResult | null>(null);
  const [cat, setCat] = useState('');
  const [tmpl, setTmpl] = useState<TemplatePart[] | null>(null);
  // 模板自动出板(按外形)
  const [tDepth, setTDepth] = useState<number | null>(null);
  const [tHeight, setTHeight] = useState<number | null>(null);
  const [tDrawers, setTDrawers] = useState<number | null>(null);
  const [tDoors, setTDoors] = useState<number | null>(null);
  const [tplLoading, setTplLoading] = useState(false);

  // ── 留痕对账 ──
  const [logs, setLogs] = useState<QuoteLog[] | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);

  // A3: 加载增减部位下拉数据(常用部位 + 物料表); 命中产品后按其品类补 BOM 部位
  useEffect(() => {
    const cat = light?.category ? `?category=${encodeURIComponent(light.category)}` : '';
    apiGet<{ parts: string[]; materials: string[]; woods: string[] }>(`/v2/part-options${cat}`)
      .then(setPartOpts)
      .catch(() => undefined);
  }, [light?.category]);
  const partGroups = useMemo(
    () => [
      { label: '常用部位', options: partOpts.parts.map((p) => ({ value: p, label: p })) },
      { label: '物料', options: partOpts.materials.map((m) => ({ value: m, label: m })) },
    ],
    [partOpts],
  );

  const loadSkuCandidates = async (code: string) => {
    const requestId = ++skuRequestSeq.current;
    setSelectedSku(undefined);
    setSkuCandidates([]);
    if (!code.trim()) return;
    setSkuLoading(true);
    try {
      const r = await apiGet<{ product_code: string; items: NonNullable<ClassifyResult['sku_candidates']> }>(
        `/v2/sku-candidates?product_code=${encodeURIComponent(code.trim())}&text=${encodeURIComponent(desc.trim())}`,
      );
      if (requestId === skuRequestSeq.current && r.product_code === code.trim()) {
        setSkuCandidates(r.items);
      }
    } catch {
      if (requestId === skuRequestSeq.current) message.error('该产品的 SKU 列表加载失败');
    } finally {
      if (requestId === skuRequestSeq.current) setSkuLoading(false);
    }
  };

  // 说一句话 → 自动分类 + (普通定制)自动算价; 判错可点「停止」中断
  const runPipeline = async () => {
    if (!desc.trim() && clsImages.length === 0) {
      message.warning('请输入描述或上传图片');
      return;
    }
    const ac = new AbortController();
    abortRef.current = ac;
    setRunning(true);
    setClsLoading(true);
    setCls(null);
    try {
      const fd = new FormData();
      fd.append('message', desc.trim());
      clsImages.forEach((f) => fd.append('images', f, f.name));
      const resp = await fetch('/api/customization/v2/classify', {
        method: 'POST',
        body: fd,
        headers: authHeaders(false),
        signal: ac.signal,
      });
      if (!resp.ok) throw new Error('分类失败');
      const r = (await resp.json()) as ClassifyResult;
      setCls(r);
      setCandidates(r.candidates ?? []);
      setSkuCandidates(r.sku_candidates ?? []);
      setSelectedSku(undefined);
      if (r.base_product_code) setPcode(r.base_product_code);
      if (r.target_length_m) setLen(r.target_length_m);
      if (r.target_height_cm) setHgt(r.target_height_cm);
      if (r.target_width_cm) setWid(r.target_width_cm);
      if (r.target_material) setMat(r.target_material);
      // 分类器识别出的增减部位 → 自动填入可编辑表(用户可改/删/加)
      const detected: EditPart[] = [];
      (r.add_parts ?? []).forEach((p) =>
        detected.push({ key: partSeq.current++, change: 'add', material: p.material, qty: p.qty || 1 }));
      (r.remove_parts ?? []).forEach((p) =>
        detected.push({ key: partSeq.current++, change: 'remove', material: p.material, qty: p.qty || 1 }));
      setParts(detected);
      // AI 降级不再静默(2026-07-12: AI挂了页面空白连原因都不给) —— 有 ai_note 必弹出来
      if (r.ai_note) message.warning(r.ai_note, 7);
      // 特殊定制: 把解析到的 品类猜测/长/深/高 预填进③, 用户点「按外形自动出板单」即可出价
      if (r.customization_type === '特殊定制') {
        if (r.category_guess) setPtype(r.category_guess);
        if (r.target_length_m) setHlen(r.target_length_m);
        if (r.target_width_cm) setTDepth(r.target_width_cm);
        if (r.target_height_cm) setTHeight(r.target_height_cm);
      }
      // 自动往下算价(仅普通定制; 特殊定制需在③填板单/外形)
      if (autoQuote && !ac.signal.aborted) {
        if (r.customization_type === '普通定制' && r.base_product_code) {
          await runLight(r.base_product_code, r.target_length_m ?? null, r.target_material ?? '', detected, ac.signal, tier, undefined, r.target_height_cm ?? null, r.target_width_cm ?? null);
        } else if (r.customization_type === '特殊定制') {
          message.info(
            r.category_guess
              ? `特殊定制: 已按「${r.category_guess}」预填③(尺寸/材质已带入) → 到③点「按外形自动出板单」再算价`
              : '特殊定制: 描述里没有品类词(如 岛台/餐边柜), 已把尺寸/材质带入③, 请填品类后点「按外形自动出板单」',
            9,
          );
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') message.error((e as Error).message);
    } finally {
      setRunning(false);
      setClsLoading(false);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setRunning(false);
    setClsLoading(false);
    setLightLoading(false);
    message.info('已停止, 可改「匹配产品」或表单后手动算价');
  };

  const addPartRow = (change: 'add' | 'remove' | 'modify') =>
    setParts((ps) => [...ps, { key: partSeq.current++, change, material: '', qty: 1 }]);
  const updatePartRow = (key: number, patch: Partial<EditPart>) =>
    setParts((ps) => ps.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  const removePartRow = (key: number) => setParts((ps) => ps.filter((p) => p.key !== key));

  const runLight = async (
    code: string,
    lenM: number | null,
    matStr: string,
    partsList: EditPart[],
    signal?: AbortSignal,
    tierVal: string = tier,
    skuVal: string | undefined = selectedSku,
    hgtVal: number | null = hgt,
    widVal: number | null = wid,
  ) => {
    if (!code.trim()) {
      message.warning('请填基础产品编码');
      return;
    }
    setLightLoading(true);
    setLight(null);
    setCustomQ(null);
    try {
      const both = await apiPost<{
        spec: LightResult;
        custom: { final_price: number | null; note?: string; error?: string; from_bom?: boolean } | null;
        custom_boards?: { part: string; material: string; length_cm: number; width_cm: number; qty: number; unit?: string; is_accessory?: boolean }[] | null;
      }>('/v2/quote-both', {
        base_product_code: code.trim(),
        target_length_m: lenM ?? undefined,
        target_width_cm: widVal ?? undefined,
        target_height_cm: hgtVal ?? undefined,
        target_material: matStr.trim() || undefined,
        add_parts: partsList
          .filter((p) => p.change === 'add' && p.material.trim())
          .map((p) => ({ material: p.material.trim(), qty: p.qty, length_cm: p.length_cm, width_cm: p.width_cm })),
        remove_parts: partsList
          .filter((p) => p.change === 'remove' && p.material.trim())
          .map((p) => ({ material: p.material.trim(), qty: p.qty, length_cm: p.length_cm, width_cm: p.width_cm })),
        modify_parts: partsList
          .filter((p) => p.change === 'modify' && p.material.trim() && (p.material_real || '').trim())
          .map((p) => ({ material: p.material.trim(), material_real: (p.material_real || '').trim(), qty: p.qty, length_cm: p.length_cm, width_cm: p.width_cm })),
        price_tier: tierVal,
        base_sku_code: skuVal,
        description: desc.trim() || undefined,
      }, signal);
      const r = both.spec;
      setLight(r);
      setCustomQ(both.custom);
      setCustomBoards(
        (both.custom_boards || []).map((b, i) => ({
          key: i + 1, part: b.part, material: b.material, length_cm: b.length_cm,
          width_cm: b.width_cm, qty: b.qty, unit: b.unit, is_accessory: b.is_accessory,
        })),
      );
      // C: 后端解析的尺寸回填到部位行(空的才填), 让用户看到+可继续手调快速改价
      if (r.parts_detail && r.parts_detail.length) {
        setParts((ps) =>
          ps.map((p) => {
            const d = (r.parts_detail || []).find((x) => x.change === p.change && x.name === p.material.trim());
            return d && (p.length_cm == null || p.width_cm == null)
              ? { ...p, length_cm: p.length_cm ?? d.length_cm, width_cm: p.width_cm ?? d.width_cm }
              : p;
          }),
        );
      }
      if (r.error) message.warning(r.error);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') message.error((e as Error).message);
    } finally {
      setLightLoading(false);
    }
  };

  const doLight = () => runLight(pcode, len, mat, parts);
  // 用户从「匹配产品」下拉手选纠正 → 立即换产品并重算
  const onPickProduct = async (code: string) => {
    setPcode(code);
    await loadSkuCandidates(code);
    await runLight(code, len, mat, parts, undefined, tier, undefined);
  };

  const doTemplate = async () => {
    if (!cat.trim()) {
      message.warning('请填品类(如 卧室-床)');
      return;
    }
    try {
      const r = await apiGet<{ parts: TemplatePart[] }>(
        `/v2/part-template?category=${encodeURIComponent(cat.trim())}`,
      );
      setTmpl(r.parts);
      if (!r.parts.length) message.info('该品类暂无 BOM 可聚合');
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const loadTemplateToBoards = () => {
    if (!tmpl || !tmpl.length) return;
    setBoards(
      tmpl.map((t, i) => ({
        key: i + 1,
        part: t.part,
        material: t.default_material,
        length_cm: 0,
        width_cm: 0,
        qty: 1,
      })),
    );
    message.success('已把模板部位填入板单, 请补每块尺寸');
  };

  const doAutoBoards = async () => {
    if (!ptype.trim() || !hlen) {
      message.warning('请先填上方「品类」和「整体长度」');
      return;
    }
    setTplLoading(true);
    try {
      const body: Record<string, unknown> = { category: ptype.trim(), length_cm: hlen * 100 };
      if (tDepth) body.depth_cm = tDepth;
      if (tHeight) body.height_cm = tHeight;
      if (tDrawers != null) body.drawers = tDrawers;
      if (tDoors != null) body.doors = tDoors;
      const r = await apiPost<
        HeavyResult & { generated_boards: { part: string; material: string; length_cm: number; width_cm: number; qty: number }[] }
      >('/v2/quote-from-template', body);
      setBoards(
        (r.generated_boards || []).map((b, i) => ({
          key: i + 1, part: b.part, material: b.material,
          length_cm: b.length_cm, width_cm: b.width_cm, qty: b.qty,
        })),
      );
      setHeavy(r);
      message.success('已按外形自动出板单(满配上限·只高不低, 请删减到实际再算价)');
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setTplLoading(false);
    }
  };

  const doHeavy = async () => {
    if (!ptype.trim() || !hlen) {
      message.warning('请填品类和整体长度');
      return;
    }
    setHeavyLoading(true);
    setHeavy(null);
    try {
      const r = await apiPost<HeavyResult>('/v2/quote-heavy', {
        product_type: ptype.trim(),
        length_m: hlen,
        boards: boards.filter((b) => b.part || b.material).map((b) => ({
          part: b.part, material: b.material, length_cm: b.length_cm, width_cm: b.width_cm,
          qty: b.qty, unit: b.unit || '平方米', is_accessory: b.is_accessory,
        })),
      });
      setHeavy(r);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setHeavyLoading(false);
    }
  };

  const loadLogs = async () => {
    setLogsLoading(true);
    try {
      const r = await apiGet<{ logs: QuoteLog[] }>('/v2/quote-logs?limit=50');
      setLogs(r.logs);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLogsLoading(false);
    }
  };

  const setBoard = (key: number, field: keyof BoardRow, value: string | number) =>
    setBoards((prev) => prev.map((b) => (b.key === key ? ({ ...b, [field]: value } as BoardRow) : b)));
  const addBoard = () =>
    setBoards((prev) => [
      ...prev,
      {
        key: (prev.length ? prev[prev.length - 1].key : 0) + 1,
        part: '',
        material: '樱桃木-2.2cm',
        length_cm: 0,
        width_cm: 0,
        qty: 1,
      },
    ]);
  const delBoard = (key: number) => setBoards((prev) => prev.filter((b) => b.key !== key));
  const [paramOpen, setParamOpen] = useState(false);  // 报价参数设置弹窗 (从尺寸微定制移来)

  return (
    <Space direction="vertical" style={{ width: '100%', maxWidth: 1180 }} size="middle">
      <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
        <Space align="center">
          <Title level={4} style={{ margin: 0 }}>
            定制报价 · 智能算价
          </Title>
          <Tag color="green" icon={<ThunderboltOutlined />}>v2</Tag>
        </Space>
        <Button icon={<SettingOutlined />} onClick={() => setParamOpen(true)}>报价参数设置</Button>
      </Space>
      <Modal title="报价参数设置" open={paramOpen} onCancel={() => setParamOpen(false)}
        width={1000} footer={null} destroyOnClose>
        <QuoteSettingsTab onSaved={() => pcode && runLight(pcode, len, mat, parts)} />
      </Modal>
      <Alert
        type="info"
        showIcon
        message="普通定制 = 真实SKU档价插值 + 材质/增减增量(纯算术, 秒级)；特殊定制 = 板单引擎 + 自动推五金。描述/截图由 AI 解析自动填表单(AI 不可用则确定性匹配)。"
      />
      <Space align="center" wrap>
        <Text type="secondary" style={{ fontSize: 12 }}>竞品价库(对比用):</Text>
        <CompetitorImportButton label="导入竞品价 xlsx" />
      </Space>

      {/* ── 1. 分类器 ── */}
      <Card size="small" title="① 智能分类(文字/图 → 类型 + 产品 + 尺寸 + 材质)">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <TextArea
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="例如: 蜂蜜餐桌 改 1.5 米 黑胡桃 / 客户要全新异形旋转吧台..."
            autoSize={{ minRows: 2, maxRows: 5 }}
          />
          <Space wrap>
            <Upload
              multiple
              accept="image/*"
              showUploadList={false}
              beforeUpload={(file) => {
                setClsImages((p) => [...p, file]);
                return false;
              }}
            >
              <Button icon={<PictureOutlined />}>加图片</Button>
            </Upload>
            {clsImages.length > 0 && (
              <Tag closable onClose={() => setClsImages([])} color="blue">
                {clsImages.length} 张图
              </Tag>
            )}
            <Button type="primary" icon={<RobotOutlined />} loading={running} onClick={runPipeline}>
              判定并算价
            </Button>
            {running && (
              <Button danger onClick={stop}>
                停止
              </Button>
            )}
            <Checkbox checked={autoQuote} onChange={(e) => setAutoQuote(e.target.checked)}>
              判定后自动算价
            </Checkbox>
          </Space>
          {cls && (
            <Alert
              type={cls.size_warning ? 'warning' : cls.customization_type === '普通定制' ? 'success' : 'warning'}
              message={
                <Space wrap>
                  <Tag color={cls.customization_type === '普通定制' ? 'blue' : 'orange'}>
                    {cls.customization_type}
                  </Tag>
                  {cls.ai_used && <Tag color="purple" icon={<RobotOutlined />}>AI 解析</Tag>}
                  {cls.base_product_code && (
                    <Text>
                      命中: <Text strong>{cls.base_product_name}</Text>（{cls.base_product_code}）
                    </Text>
                  )}
                  {cls.target_length_m ? <Tag color="cyan">尺寸 {cls.target_length_m}m</Tag> : null}
                  {cls.target_material ? <Tag color="gold">材质 {cls.target_material}</Tag> : null}
                  <Text type="secondary">置信度 {cls.confidence}</Text>
                </Space>
              }
              description={
                <Space direction="vertical" size={2}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {cls.reasoning}（已自动填入下方表单, 可手动改）
                  </Text>
                  {cls.ai_note && (
                    <Text type="warning" style={{ fontSize: 12 }}>⚠ {cls.ai_note}</Text>
                  )}
                </Space>
              }
            />
          )}
        </Space>
      </Card>

      {/* ── 2. 普通定制 ── */}
      <Card size="small" title="② 普通定制算价(改现有产品)">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          {candidates.length > 0 && (
            <Space wrap align="center">
              <Text type="secondary" style={{ fontSize: 12 }}>
                匹配产品(不一定准, 选错可改后自动重算):
              </Text>
              <Select
                style={{ width: 440 }}
                value={pcode || undefined}
                onChange={onPickProduct}
                placeholder="按匹配度排序的 Top-10 候选"
                options={candidates.map((c) => ({
                  value: c.product_code,
                  label: `${Math.round(c.confidence * 100)}%　${c.product_name}${c.sku ? `　· ${c.sku}` : ''}`,
                }))}
              />
            </Space>
          )}
          {pcode && (
            <Space wrap align="center">
              <Text type="secondary" style={{ fontSize: 12 }}>
                当前产品 SKU（切换产品后实时更新）:
              </Text>
              <Select
                style={{ width: 440 }}
                value={selectedSku}
                onChange={(v) => { setSelectedSku(v); runLight(pcode, len, mat, parts, undefined, tier, v); }}
                allowClear
                loading={skuLoading}
                disabled={skuLoading || skuCandidates.length === 0}
                placeholder={skuLoading ? '正在加载当前产品 SKU…' : skuCandidates.length === 0
                  ? '当前产品没有可报价的真实 SKU'
                  : '选具体 SKU → 锁定该变体计算'}
                options={skuCandidates.map((s) => ({
                  value: s.sku_code,
                  label: `${Math.round(s.confidence * 100)}%　${s.sku_name}${s.price != null ? `　¥${s.price}` : ''}`,
                }))}
              />
            </Space>
          )}
          <Space wrap>
            <Input
              addonBefore="基础产品编码"
              value={pcode}
              onChange={(e) => setPcode(e.target.value)}
              onBlur={() => loadSkuCandidates(pcode)}
              style={{ width: 320 }}
              placeholder="如 PFG25210021222(分类自动填)"
            />
            <InputNumber
              addonBefore="目标长度(米)"
              value={len}
              onChange={(v) => setLen(v)}
              min={0}
              step={0.1}
              style={{ width: 200 }}
            />
            <Select
              value={mat || undefined}
              onChange={(v) => setMat(v ?? '')}
              style={{ width: 220 }}
              placeholder="改材质(下拉选, 可空)"
              allowClear
              showSearch
              options={partOpts.woods.map((w) => ({ value: w, label: w }))}
            />
            <Select
              value={tier}
              onChange={(v) => { setTier(v); if (pcode) runLight(pcode, len, mat, parts, undefined, v); }}
              style={{ width: 160 }}
              options={[
                { value: 'big', label: '报价档·大促' },
                { value: 'mid', label: '报价档·中促' },
                { value: 'small', label: '报价档·小促' },
                { value: 'daily', label: '报价档·日常' },
              ]}
            />
            <Button type="primary" loading={lightLoading} onClick={doLight}>
              算价
            </Button>
          </Space>

          {/* 增减部位: 分类器自动填, 可手动增/删/改 → 后端逐部位算价(铁律: 删除偏保守, 只高不低) */}
          <Card
            size="small"
            type="inner"
            title="增减部位(自动识别 · 可手调 → 逐部位算价)"
            styles={{ body: { padding: 8 } }}
          >
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              {parts.length === 0 && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  无增减部位(纯改尺寸/材质)。要"加某部位/去某部位"点下方按钮。
                </Text>
              )}
              {parts.map((p) => (
                <Space key={p.key} wrap>
                  <Tag color={p.change === 'add' ? 'green' : p.change === 'remove' ? 'red' : 'blue'}>
                    {p.change === 'add' ? '追加' : p.change === 'remove' ? '删除' : '改料'}
                  </Tag>
                  <PartSelect
                    value={p.material}
                    onChange={(v) => updatePartRow(p.key, { material: v })}
                    groups={partGroups}
                  />
                  {p.change === 'modify' && (
                    <>
                      <Text type="secondary">改成→</Text>
                      <PartSelect
                        value={p.material_real || ''}
                        onChange={(v) => updatePartRow(p.key, { material_real: v })}
                        groups={partGroups}
                      />
                    </>
                  )}
                  <InputNumber
                    addonBefore="数量"
                    value={p.qty}
                    onChange={(v) => updatePartRow(p.key, { qty: v ?? 1 })}
                    min={0.1}
                    step={1}
                    style={{ width: 120 }}
                  />
                  <InputNumber addonBefore="长cm" value={p.length_cm} placeholder="模板"
                    onChange={(v) => updatePartRow(p.key, { length_cm: v ?? undefined })} min={0} style={{ width: 118 }} />
                  <InputNumber addonBefore="宽cm" value={p.width_cm} placeholder="模板"
                    onChange={(v) => updatePartRow(p.key, { width_cm: v ?? undefined })} min={0} style={{ width: 118 }} />
                  <Button
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => removePartRow(p.key)}
                  />
                </Space>
              ))}
              <Space>
                <Button size="small" icon={<PlusOutlined />} onClick={() => addPartRow('add')}>
                  加部位
                </Button>
                <Button size="small" danger icon={<PlusOutlined />} onClick={() => addPartRow('remove')}>
                  去部位
                </Button>
                <Button size="small" icon={<PlusOutlined />} onClick={() => addPartRow('modify')}>
                  改部位
                </Button>
              </Space>
            </Space>
          </Card>

          {/* 尺寸微调: 长(米)×宽(cm)×高(cm) → 宽高偏离标准按面积比例算尺寸变体价 (用户拍板 2026-06-20) */}
          <Card
            size="small"
            type="inner"
            title="尺寸微调(长×宽×高 → 算尺寸变体价)"
            styles={{ body: { padding: 8 } }}
          >
            <Space wrap align="center">
              <InputNumber addonBefore="长(米)" value={len} step={0.1} min={0.1}
                onChange={(v) => setLen(v)} style={{ width: 148 }} />
              <InputNumber addonBefore="宽(cm)" value={wid} step={1} min={1}
                placeholder={light?.std_width_cm ? `标准${light.std_width_cm}` : '标准'}
                onChange={(v) => setWid(v)} style={{ width: 148 }} />
              <InputNumber addonBefore="高(cm)" value={hgt} step={1} min={1}
                placeholder={light?.std_height_cm ? `标准${light.std_height_cm}` : '标准'}
                onChange={(v) => setHgt(v)} style={{ width: 148 }} />
              <Button type="primary" size="small" loading={lightLoading} onClick={doLight}>算尺寸变体价</Button>
            </Space>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
              {light?.std_width_cm
                ? `标准@${len}m ≈ ${light.std_width_cm}×${light.std_height_cm}cm。宽高留空=标准(不调价);改宽高→按面积比例缩放木作成本算变体价。`
                : '先算一次价, 这里会显示该长度的标准宽高, 再微调宽高算变体价。'}
            </Text>
          </Card>

          {light && light.final_price != null && customQ && customQ.final_price != null && (
            <Row gutter={16}>
              <Col xs={24} md={12}>
                <Card size="small" type="inner" title="① 按我们的规格(锚点)" styles={{ body: { padding: 12 } }}>
                  <Text strong style={{ fontSize: 26, color: '#1677ff' }}>¥{light.final_price.toFixed(2)}</Text>
                  <div><Text type="secondary" style={{ fontSize: 12 }}>锚在真实 SKU 售价 · 市场校准, 近标准款最准</Text></div>
                </Card>
              </Col>
              <Col xs={24} md={12}>
                <Card size="small" type="inner" title="② 按纯定制方向(板单)" styles={{ body: { padding: 12 } }}>
                  <Text strong style={{ fontSize: 26, color: '#722ed1' }}>¥{customQ.final_price.toFixed(2)}</Text>
                  <div><Text type="secondary" style={{ fontSize: 12 }}>板单逐块累加 · 物理校准, 出格/新奇更稳</Text></div>
                  {customQ.note && (
                    <div style={{ marginTop: 4 }}><Text type="warning" style={{ fontSize: 12 }}>⚠ {customQ.note}</Text></div>
                  )}
                  {customBoards.length > 0 && (
                    <Button
                      size="small"
                      type="link"
                      style={{ padding: 0, marginTop: 6 }}
                      onClick={() => {
                        setBoards(customBoards);
                        if (light?.category) setPtype(light.category.split('-').pop() || light.category);
                        if (len) setHlen(len);
                        message.success('已把真实部件板单带入③, 请核对每块尺寸后点「算价」精算');
                      }}
                    >
                      带入③板单核对精算 →（{customBoards.length} 个部件）
                    </Button>
                  )}
                </Card>
              </Col>
            </Row>
          )}

          {light && light.final_price != null && (
            <Row gutter={16}>
              <Col xs={24} lg={14}>
                <Space direction="vertical" style={{ width: '100%' }} size={8}>
                  <Card size="small" type="inner" title="计算规格明细" styles={{ body: { padding: 10 } }}>
                    <Descriptions size="small" column={{ xs: 1, sm: 2 }} colon={false}>
                      <Descriptions.Item label="匹配产品">
                        {light.base_product_name || '—'}
                      </Descriptions.Item>
                      <Descriptions.Item label="锁定 SKU">
                        {light.selected_sku_name || '未锁定（按同产品多档插值）'}
                      </Descriptions.Item>
                      <Descriptions.Item label="目标尺寸">
                        {light.specification?.target_length_m ? `${light.specification.target_length_m}m` : '标准长度'}
                        {' × '}{light.specification?.target_width_cm ?? light.specification?.standard_width_cm ?? '标准'}cm
                        {' × '}{light.specification?.target_height_cm ?? light.specification?.standard_height_cm ?? '标准'}cm
                      </Descriptions.Item>
                      <Descriptions.Item label="目标材质">
                        {light.specification?.target_material || '沿用原产品材质'}
                      </Descriptions.Item>
                      <Descriptions.Item label="报价档位">
                        {({ big: '大促价', mid: '中促价', small: '小促价', daily: '日常价' } as Record<string, string>)[light.specification?.price_tier || ''] || light.specification?.price_tier || '—'}
                      </Descriptions.Item>
                      <Descriptions.Item label="本次参数">
                        厂利 {((light.pricing_parameters?.factory_profit_rate ?? 0) * 100).toFixed(1)}% ·
                        畔色 {((light.pricing_parameters?.panse_profit_rate ?? 0) * 100).toFixed(1)}% ·
                        安全 ×{light.pricing_parameters?.safety_rate ?? 1}
                      </Descriptions.Item>
                    </Descriptions>
                  </Card>
                  <Card size="small" type="inner" title="报价汇总" styles={{ body: { padding: 10 } }}>
                    <Descriptions size="small" column={{ xs: 2, sm: 4 }} colon={false}>
                      <Descriptions.Item label="非油漆小计">¥{(light.subtotal_before_safety ?? 0).toFixed(2)}</Descriptions.Item>
                      <Descriptions.Item label="安全系数调整">¥{(light.safety_delta ?? 0).toFixed(2)}</Descriptions.Item>
                      <Descriptions.Item label="油漆固定追加">¥{(light.paint_surcharge ?? 0).toFixed(2)}</Descriptions.Item>
                      <Descriptions.Item label="最终报价">
                        <Text strong style={{ fontSize: 20, color: '#1677ff' }}>¥{light.final_price.toFixed(2)}</Text>
                      </Descriptions.Item>
                    </Descriptions>
                  </Card>
                  <MarginFloor
                    finalPrice={light.final_price}
                    productMargin={light.product_margin}
                    factoryPredicted={light.factory_predicted}
                  />
                  <Table<BreakdownItem>
                    size="small"
                    rowKey={(_, i) => String(i)}
                    pagination={false}
                    title={() => <Text strong>具体加减项（逐笔公式与金额）</Text>}
                    columns={breakdownCols}
                    dataSource={light.breakdown}
                  />
                </Space>
              </Col>
              <Col xs={24} lg={10}>
                {light.comparison && (
                  <Card
                    size="small"
                    type="inner"
                    title="竞品 / 标准款 对比(同尺寸·别家店)"
                    styles={{ body: { padding: 8 } }}
                  >
                    <Space direction="vertical" size={6} style={{ width: '100%' }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {light.comparison.note}
                      </Text>
                      {light.comparison.baseline && (
                        <Space wrap>
                          <Tag>{light.comparison.baseline.label}</Tag>
                          <Text>¥{light.comparison.baseline.price.toFixed(2)}</Text>
                          <Tag color={light.comparison.baseline.is_lower ? 'green' : 'red'}>
                            本单{light.comparison.baseline.is_lower ? '低' : '高'}{' '}
                            {Math.abs(light.comparison.baseline.diff_pct)}%
                          </Tag>
                        </Space>
                      )}
                      {light.comparison.competitors.length > 0 && (
                        <Table<CompetitorRow>
                          size="small"
                          rowKey={(_, i) => String(i)}
                          pagination={false}
                          columns={competitorCols}
                          dataSource={light.comparison.competitors}
                        />
                      )}
                    </Space>
                  </Card>
                )}
              </Col>
            </Row>
          )}
          {light && light.final_price == null && (
            <Alert type="error" showIcon message={light.error ?? '算价失败'} />
          )}
        </Space>
      </Card>

      {/* ── 3. 特殊定制 ── */}
      <Card size="small" title="③ 特殊定制算价(全新 · 板单引擎 + 自动推五金)">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space wrap>
            <Input
              addonBefore="品类"
              value={ptype}
              onChange={(e) => setPtype(e.target.value)}
              style={{ width: 200 }}
              placeholder="如 餐边柜"
            />
            <InputNumber
              addonBefore="整体长度(米)"
              value={hlen}
              onChange={(v) => setHlen(v)}
              min={0}
              step={0.1}
              style={{ width: 200 }}
            />
          </Space>

          <Space wrap size={4}>
            <InputNumber addonBefore="深cm" value={tDepth} onChange={(v) => setTDepth(v)} min={0} style={{ width: 130 }} />
            <InputNumber addonBefore="高cm" value={tHeight} onChange={(v) => setTHeight(v)} min={0} style={{ width: 130 }} />
            <InputNumber addonBefore="抽屉数" value={tDrawers} onChange={(v) => setTDrawers(v)} min={0} style={{ width: 120 }} />
            <InputNumber addonBefore="门数" value={tDoors} onChange={(v) => setTDoors(v)} min={0} style={{ width: 110 }} />
            <Button type="primary" ghost loading={tplLoading} onClick={doAutoBoards}>
              按外形自动出板单
            </Button>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            填品类+长(上方)+深高/抽屉/门 → 自动出「满配上限」板单(报价只高不低);在下方删减到实际再算价。深高/数量留空则用品类默认。
          </Text>

          <Space wrap size={4}>
            <Input
              addonBefore="查品类标准部位模板"
              value={cat}
              onChange={(e) => setCat(e.target.value)}
              style={{ width: 320 }}
              placeholder="品类如 卧室-床 / 餐厅-餐边柜"
            />
            <Button onClick={doTemplate}>查模板</Button>
            {tmpl && tmpl.length > 0 && (
              <Button type="dashed" onClick={loadTemplateToBoards}>
                把模板 {tmpl.length} 个部位填入板单 ↓
              </Button>
            )}
          </Space>
          {tmpl && tmpl.length > 0 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              模板(按 BOM 出现频次): {tmpl.slice(0, 12).map((t) => `${t.part}×${t.freq}`).join('、')}
              {tmpl.length > 12 ? ' …' : ''}
            </Text>
          )}

          <Divider style={{ margin: '4px 0' }} orientation="left" plain>
            板单(长宽 cm)
          </Divider>
          {boards.map((b) => (
            <Space key={b.key} wrap size={4}>
              <Input
                placeholder="部位(如 顶板)"
                value={b.part}
                onChange={(e) => setBoard(b.key, 'part', e.target.value)}
                style={{ width: 150 }}
              />
              <Input
                placeholder="材料"
                value={b.material}
                onChange={(e) => setBoard(b.key, 'material', e.target.value)}
                style={{ width: 160 }}
              />
              <InputNumber
                placeholder="长cm"
                value={b.length_cm}
                onChange={(v) => setBoard(b.key, 'length_cm', v ?? 0)}
                min={0}
                style={{ width: 90 }}
              />
              <InputNumber
                placeholder="宽cm"
                value={b.width_cm}
                onChange={(v) => setBoard(b.key, 'width_cm', v ?? 0)}
                min={0}
                style={{ width: 90 }}
              />
              <InputNumber
                placeholder="数量"
                value={b.qty}
                onChange={(v) => setBoard(b.key, 'qty', v ?? 1)}
                min={0}
                style={{ width: 80 }}
              />
              <Button danger size="small" icon={<DeleteOutlined />} onClick={() => delBoard(b.key)} />
            </Space>
          ))}
          <Space>
            <Button icon={<PlusOutlined />} onClick={addBoard}>
              加一行
            </Button>
            <Button type="primary" loading={heavyLoading} onClick={doHeavy}>
              算价(引擎 + 自动推五金)
            </Button>
          </Space>

          {heavy && heavy.final_price != null && (
            <>
              <Space align="baseline" wrap>
                <Text>最终报价:</Text>
                <Text strong style={{ fontSize: 26, color: '#fa8c16' }}>
                  ¥{heavy.final_price.toFixed(2)}
                </Text>
                <Text type="secondary">
                  木作 {heavy.wood_cost.toFixed(0)} · 人工 {heavy.labor_fee.toFixed(0)} · 配件{' '}
                  {heavy.accessory_total.toFixed(0)}
                </Text>
                <Tag color="volcano">工厂木作对比 ¥{heavy.factory_quote_compare.toFixed(0)}</Tag>
              </Space>
              {heavy.break_even_sell != null && (
                <Space align="baseline" wrap>
                  <Text type="secondary">保本价(最低可卖):</Text>
                  <Text strong style={{ fontSize: 18, color: '#d4380d' }}>
                    ¥{heavy.break_even_sell.toFixed(2)}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>全成本·不含畔色利润;低于此即亏</Text>
                </Space>
              )}
              <FactoryRedline
                predicted={heavy.factory_predicted ?? heavy.factory_quote_compare}
                breakEven={heavy.break_even_factory}
                buffer={heavy.break_even_buffer}
              />
              {heavy.inferred_hardware.length > 0 && (
                <Space wrap>
                  <Text type="secondary">自动推五金:</Text>
                  {heavy.inferred_hardware.map((h) => (
                    <Tag key={h.material} color="geekblue">
                      {h.material} ×{h.qty}
                      {h.unit}
                    </Tag>
                  ))}
                </Space>
              )}
            </>
          )}
        </Space>
      </Card>

      {/* ── 4. 留痕对账 ── */}
      <Card size="small" title="④ 报价留痕(灰度对账 · 新旧口径复盘)">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Button onClick={loadLogs} loading={logsLoading}>
            加载最近报价留痕
          </Button>
          {logs && (
            <Table<QuoteLog>
              size="small"
              rowKey="id"
              pagination={{ pageSize: 8 }}
              columns={logCols}
              dataSource={logs}
            />
          )}
        </Space>
      </Card>

      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        说明: 普通定制以「标准原价(同尺寸真实档价)」为基础做插值 + 增量;材质增量用 wood_cost 反推面积;删除部位只扣材料成本(决策①)。保本价 = 报价 ×(1 − 本款大促毛利率),毛利率实时取自该款 SKU(大促价÷会计成本)、可手动改。增减部位的利润系数在上方「报价系数」面板可改。本页只读计算, 不落订单。
      </Paragraph>
    </Space>
  );
}
