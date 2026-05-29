import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Popover,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { BulbOutlined, DeleteOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  ComposeBomLine,
  ComposePricingSku,
  PricingSkuCosts,
  PricingSkuPromo,
  RatioHints,
  ValueHint,
  composeProduct,
  getRatioHints,
  getSkuCosts,
  getSkuPromo,
  getValueHints,
  listMaterials,
  listProducts,
  listRecentProducts,
  loadProductReference,
  upsertSkuCosts,
  upsertSkuPromo,
} from '../api/client';

let _rowSeq = 1;
const nextKey = () => `r${_rowSeq++}`;
type BomRow = ComposeBomLine & { _key: string };
type SkuRow = ComposePricingSku & { _key: string };

const emptyBom = (): BomRow => ({ _key: nextKey(), material_code: '', qty_per_product: 1 });
const emptySku = (): SkuRow => ({ _key: nextKey(), sku_code: '' });

// 大促到手价单元格: 输入框 + 聚焦时浮出「历史比例参考」, 点某条按 成本/比例 回填
function BigPromoCell({
  row,
  hints,
  category,
  onChange,
}: {
  row: SkuRow;
  hints?: RatioHints;
  category?: string;
  onChange: (n: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const accounting = row.accounting_cost == null || row.accounting_cost === '' ? null : Number(row.accounting_cost);
  const physical = row.physical_cost == null || row.physical_cost === '' ? null : Number(row.physical_cost);

  // 各口径对应这一行能用来回填的成本值 (出厂成本本表未录入, 仅作参考展示)
  const costFor: Record<string, number | null> = { accounting, physical, factory: null };

  const fill = (costField: string, ratio: number) => {
    const cost = costFor[costField];
    if (cost == null) {
      message.warning('本行该口径成本未填, 无法按比例回填 (可手动输入到手价)');
      return;
    }
    if (ratio <= 0) return;
    onChange(Math.round((cost / ratio) * 100) / 100);
    setOpen(false);
  };

  const hintContent = () => {
    if (!hints) return <Typography.Text type="secondary">加载中…</Typography.Text>;
    const calibers = Object.entries(hints.calibers).filter(([, c]) => c.sample > 0);
    if (calibers.length === 0) {
      return <Typography.Text type="secondary">暂无历史数据可参考</Typography.Text>;
    }
    return (
      <div style={{ width: 320 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          比例 = 成本 ÷ 大促到手价。点某条按「该口径成本 ÷ 比例」回填到手价。
          {hints.category ? `（类目: ${hints.category}）` : '（全部产品）'}
        </Typography.Text>
        {calibers.map(([key, c]) => {
          const canFill = costFor[key] != null;
          return (
            <div key={key} style={{ marginTop: 10 }}>
              <Space size={6}>
                <Typography.Text strong style={{ fontSize: 13 }}>{c.label}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  样本 {c.sample}{c.used_global ? ' · 全局' : ''}
                </Typography.Text>
              </Space>
              <div style={{ marginTop: 4 }}>
                {c.top.map((t) => (
                  <Tag
                    key={t.ratio}
                    color={canFill ? 'blue' : 'default'}
                    style={{ cursor: canFill ? 'pointer' : 'not-allowed', marginBottom: 4 }}
                    onClick={() => canFill && fill(key, t.ratio)}
                  >
                    {Math.round(t.ratio * 100)}% · {t.pct}% 产品
                  </Tag>
                ))}
                {c.range && (
                  <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                    中间 80% 落在 {Math.round(c.range.low * 100)}%–{Math.round(c.range.high * 100)}%
                  </Typography.Text>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger={[]}
      placement="bottomLeft"
      title={
        <Space size={4}>
          <BulbOutlined style={{ color: '#faad14' }} />
          <span style={{ fontSize: 13 }}>历史比例参考{category ? `（${category}）` : ''}</span>
        </Space>
      }
      content={hintContent()}
    >
      <InputNumber
        value={row.big_promo == null || row.big_promo === '' ? undefined : Number(row.big_promo)}
        size="small"
        min={0}
        step={0.01}
        style={{ width: 100 }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        onChange={(n) => onChange(n == null ? null : Number(n))}
      />
    </Popover>
  );
}

// 基础定价价格/成本字段单元格: 聚焦弹出「智能基准」历史比例, 点标签按锚字段×比例回填
type RatioFieldName = 'list_price' | 'daily_price' | 'small_promo' | 'mid_promo' | 'accounting_cost' | 'physical_cost';

function RatioHintInput({
  value,
  fieldName,
  hints,
  row,
  onChange,
  width = 100,
}: {
  value: string | number | null | undefined;
  fieldName: RatioFieldName;
  hints?: RatioHints;
  row: SkuRow;
  onChange: (n: number | null) => void;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const fh = hints?.fields?.[fieldName];

  const anchorValue = (): number | null => {
    if (!fh) return null;
    const raw = (row as any)[fh.anchor];
    if (raw == null || raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  };

  const fill = (ratio: number) => {
    if (!fh) return;
    const anchor = anchorValue();
    if (anchor == null) {
      message.warning(`请先填写 ${fh.anchor_label}`);
      return;
    }
    if (ratio <= 0) return;
    onChange(Math.round(anchor * ratio * 100) / 100);
    setOpen(false);
  };

  const content = () => {
    if (!hints) return <Typography.Text type="secondary">加载中…</Typography.Text>;
    if (!fh || fh.sample === 0 || fh.top.length === 0) {
      return <Typography.Text type="secondary">暂无历史数据可参考</Typography.Text>;
    }
    const isMul = fh.mode === 'multiplier';
    const canFill = anchorValue() != null;
    const tagText = (ratio: number) =>
      isMul ? `×${ratio.toFixed(2)}` : `${Math.round(ratio * 100)}%`;
    const rangeText = () => {
      if (!fh.range) return null;
      return isMul
        ? `中间 80% 落在 ×${fh.range.low.toFixed(2)}–×${fh.range.high.toFixed(2)}`
        : `中间 80% 落在 ${Math.round(fh.range.low * 100)}%–${Math.round(fh.range.high * 100)}%`;
    };
    return (
      <div style={{ width: 300 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {isMul
            ? `通常是 ${fh.anchor_label} 的若干倍。`
            : `通常是 ${fh.anchor_label} 的某百分比。`}
          点标签按「{fh.anchor_label} × 比例」回填。
        </Typography.Text>
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            样本 {fh.sample}{fh.used_global ? ' · 全局' : ''}
          </Typography.Text>
        </div>
        <div style={{ marginTop: 6 }}>
          {fh.top.map((t) => (
            <Tag
              key={t.ratio}
              color={canFill ? 'blue' : 'default'}
              style={{ cursor: canFill ? 'pointer' : 'not-allowed', marginBottom: 4 }}
              onClick={() => canFill && fill(t.ratio)}
            >
              {tagText(t.ratio)} · {t.pct}% 产品
            </Tag>
          ))}
        </div>
        {fh.range && (
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {rangeText()}
          </Typography.Text>
        )}
        {!canFill && (
          <div>
            <Typography.Text type="warning" style={{ fontSize: 11 }}>
              请先填写 {fh.anchor_label}
            </Typography.Text>
          </div>
        )}
      </div>
    );
  };

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger={[]}
      placement="bottomLeft"
      title={
        <Space size={4}>
          <BulbOutlined style={{ color: '#faad14' }} />
          <span style={{ fontSize: 13 }}>智能基准参考</span>
        </Space>
      }
      content={content()}
    >
      <InputNumber
        value={value == null || value === '' ? undefined : Number(value)}
        size="small"
        min={0}
        step={0.01}
        style={{ width }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        onChange={(n) => onChange(n == null ? null : Number(n))}
      />
    </Popover>
  );
}

// 配件成本 / 活动价格单元格: 聚焦弹出该字段历史「常见值」, 点标签直接填值
function ValueHintInput({
  value,
  table,
  field,
  category,
  onChange,
  width = 120,
  isRate = false,
}: {
  value: number | null | undefined;
  table: 'costs' | 'promo';
  field: string;
  category?: string;
  onChange: (n: number | null) => void;
  width?: number;
  isRate?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [fetched, setFetched] = useState(false);

  const { data: hint } = useQuery({
    queryKey: ['value-hints', table, field, category ?? ''],
    queryFn: () => getValueHints(table, field, category || undefined),
    enabled: fetched,
    staleTime: 5 * 60 * 1000,
  });

  const fmt = (v: number) => (isRate ? `${v}` : `${v}元`);

  const fill = (v: number) => {
    onChange(v);
    setOpen(false);
  };

  const content = () => {
    if (!fetched || hint === undefined) return <Typography.Text type="secondary">加载中…</Typography.Text>;
    const h = hint as ValueHint;
    if (h.sample === 0 || h.top.length === 0) {
      return <Typography.Text type="secondary">暂无历史数据可参考</Typography.Text>;
    }
    return (
      <div style={{ width: 280 }}>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          历史常见值。点标签直接填入。样本 {h.sample}{h.used_global ? ' · 全局' : ''}
        </Typography.Text>
        <div style={{ marginTop: 6 }}>
          {h.top.map((t) => (
            <Tag
              key={t.value}
              color="blue"
              style={{ cursor: 'pointer', marginBottom: 4 }}
              onClick={() => fill(t.value)}
            >
              {fmt(t.value)} · {t.pct}% 产品
            </Tag>
          ))}
        </div>
        {h.range && (
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            中间 80% 落在 {fmt(h.range.low)}–{fmt(h.range.high)}
          </Typography.Text>
        )}
      </div>
    );
  };

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger={[]}
      placement="bottomLeft"
      title={
        <Space size={4}>
          <BulbOutlined style={{ color: '#faad14' }} />
          <span style={{ fontSize: 13 }}>历史常见值</span>
        </Space>
      }
      content={content()}
    >
      <InputNumber
        value={value ?? undefined}
        size="small"
        min={0}
        step={isRate ? 0.001 : 0.01}
        style={{ width }}
        onFocus={() => { setFetched(true); setOpen(true); }}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        onChange={(n) => onChange(n == null ? null : Number(n))}
      />
    </Popover>
  );
}

// ---- 配件成本 Tab ----
const COST_FIELDS: { field: keyof PricingSkuCosts; label: string }[] = [
  { field: 'rock_slab', label: '岩板' },
  { field: 'drawer_rail', label: '抽屉轨道' },
  { field: 'led_strip', label: '灯带' },
  { field: 'glass', label: '玻璃' },
  { field: 'electric_rail', label: '电力轨道' },
  { field: 'packing_sheet', label: '打包纸片' },
  { field: 'iron_pin', label: '铁销' },
  { field: 'connector', label: '连接片' },
  { field: 'aluminum_rail', label: '铝合金轨道' },
  { field: 'plastic_rail', label: '塑料轨道' },
  { field: 'mini_handle', label: 'mini把手' },
  { field: 'nail_free_glue', label: '免钉胶' },
  { field: 'engraving', label: '雕刻' },
  { field: 'acrylic_strip', label: '亚克力条' },
  { field: 'embedded_sleeve', label: '预埋套杆' },
  { field: 'cable_mgmt', label: '理线架+插排' },
  { field: 'back_panel', label: '背板' },
  { field: 'stainless_trim', label: '装饰条（不锈钢）' },
  { field: 'leg', label: '腿部' },
  { field: 'soft_pack', label: '软包' },
  { field: 'bed_board', label: '床铺板' },
  { field: 'other_cost', label: '其他' },
];

function computePartsTotal(costs: Partial<PricingSkuCosts>): number {
  return COST_FIELDS.reduce((sum, { field }) => {
    const v = costs[field];
    return sum + (v != null ? Number(v) : 0);
  }, 0);
}

function SkuCostsTab({
  skuCodes,
  productCode,
  category,
}: {
  skuCodes: string[];
  productCode: string | null;
  category?: string;
}) {
  const [selectedSku, setSelectedSku] = useState<string>(skuCodes[0] ?? '');
  const [localCosts, setLocalCosts] = useState<Partial<PricingSkuCosts>>({});
  const [saving, setSaving] = useState(false);

  // Load existing costs when SKU changes
  useQuery({
    queryKey: ['sku-costs', selectedSku],
    queryFn: () => getSkuCosts(selectedSku),
    enabled: !!selectedSku,
    onSuccess: (data: PricingSkuCosts) => setLocalCosts(data),
    onError: () => setLocalCosts({}),
  } as any);

  if (!productCode) {
    return (
      <Alert
        type="warning"
        showIcon
        message="请先保存产品，再填写配件成本和活动价格"
      />
    );
  }

  const total = computePartsTotal(localCosts);

  const handleSave = async () => {
    if (!selectedSku) { message.warning('请先选择 SKU'); return; }
    setSaving(true);
    try {
      await upsertSkuCosts(selectedSku, localCosts);
      message.success('配件成本已保存');
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space>
        <Typography.Text>选择 SKU：</Typography.Text>
        <Select
          value={selectedSku}
          onChange={(v) => { setSelectedSku(v); setLocalCosts({}); }}
          options={skuCodes.map((c) => ({ value: c, label: c }))}
          style={{ width: 200 }}
        />
        <Typography.Text type="secondary">
          外采配件成本合计 = <Typography.Text strong>{total.toFixed(2)} 元</Typography.Text>
        </Typography.Text>
      </Space>

      <Row gutter={[16, 8]}>
        {COST_FIELDS.map(({ field, label }) => (
          <Col span={12} key={field}>
            <Space>
              <Typography.Text style={{ width: 130, display: 'inline-block' }}>{label}</Typography.Text>
              <ValueHintInput
                table="costs"
                field={field as string}
                category={category}
                value={(localCosts[field] as number | undefined) ?? undefined}
                onChange={(n) => setLocalCosts((prev) => ({ ...prev, [field]: n }))}
              />
            </Space>
          </Col>
        ))}
        <Col span={24}>
          <Space>
            <Typography.Text>外配件说明</Typography.Text>
            <Input
              size="small"
              style={{ width: 300 }}
              value={(localCosts.other_desc as string | undefined) ?? ''}
              onChange={(e) => setLocalCosts((prev) => ({ ...prev, other_desc: e.target.value }))}
            />
          </Space>
        </Col>
        <Col span={24}>
          <Space>
            <Typography.Text>配件备注</Typography.Text>
            <Input
              size="small"
              style={{ width: 300 }}
              value={(localCosts.parts_remark as string | undefined) ?? ''}
              onChange={(e) => setLocalCosts((prev) => ({ ...prev, parts_remark: e.target.value }))}
            />
          </Space>
        </Col>
      </Row>

      <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
        保存配件成本
      </Button>
    </Space>
  );
}

// ---- 活动价格 Tab ----
function computePromoFields(promo: Partial<PricingSkuPromo>, dailyPrice: number | null) {
  const daily = dailyPrice ?? null;
  const result = { ...promo };
  if (daily != null) {
    result.taobao_activity_price = daily;
    result.xhs_list_price = daily;
    if (promo.shop_promo_rate != null) {
      result.shop_internal_final = Math.round(daily * Number(promo.shop_promo_rate) * 100) / 100;
    }
    if (promo.mid_shop_rate != null) {
      const mid = Math.round(daily * 0.88 * Number(promo.mid_shop_rate) * 100) / 100;
      result.mid_buyer_price = mid;
      result.mid_shop_receipt = Math.round(mid * 0.99 * 100) / 100;
      result.mid_vip_final = Math.round((mid - 150) * 100) / 100;
    }
    if (promo.big_shop_rate != null) {
      const big = Math.round(daily * 0.88 * Number(promo.big_shop_rate) * 100) / 100;
      result.big_buyer_price = big;
      result.big_shop_receipt = big;
      result.big_vip_final = Math.round((big - 150) * 100) / 100;
    }
  }
  if (promo.xhs_activity_price != null) {
    const discount = promo.xhs_promo_discount ?? 0.15;
    result.xhs_promo_price = Math.round(Number(promo.xhs_activity_price) * (1 - Number(discount)) * 100) / 100;
  }
  return result;
}

function SkuPromoTab({
  skuCodes,
  productCode,
  skuRows,
  category,
}: {
  skuCodes: string[];
  productCode: string | null;
  skuRows: SkuRow[];
  category?: string;
}) {
  const [selectedSku, setSelectedSku] = useState<string>(skuCodes[0] ?? '');
  const [localPromo, setLocalPromo] = useState<Partial<PricingSkuPromo>>({});
  const [saving, setSaving] = useState(false);

  useQuery({
    queryKey: ['sku-promo', selectedSku],
    queryFn: () => getSkuPromo(selectedSku),
    enabled: !!selectedSku,
    onSuccess: (data: PricingSkuPromo) => setLocalPromo(data),
    onError: () => setLocalPromo({}),
  } as any);

  if (!productCode) {
    return (
      <Alert
        type="warning"
        showIcon
        message="请先保存产品，再填写配件成本和活动价格"
      />
    );
  }

  const currentSkuRow = skuRows.find((r) => r.sku_code === selectedSku);
  const dailyPrice = currentSkuRow?.daily_price != null ? Number(currentSkuRow.daily_price) : null;
  const computed = computePromoFields(localPromo, dailyPrice);

  const patch = (fields: Partial<PricingSkuPromo>) =>
    setLocalPromo((prev) => ({ ...prev, ...fields }));

  const handleSave = async () => {
    if (!selectedSku) { message.warning('请先选择 SKU'); return; }
    setSaving(true);
    try {
      await upsertSkuPromo(selectedSku, localPromo);
      message.success('活动价格已保存');
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const readonlyStyle: React.CSSProperties = {
    background: '#f5f5f5',
    border: '1px solid #d9d9d9',
    borderRadius: 4,
    padding: '1px 8px',
    minWidth: 100,
    display: 'inline-block',
    color: '#888',
  };

  const labelStyle: React.CSSProperties = { width: 160, display: 'inline-block' };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space>
        <Typography.Text>选择 SKU：</Typography.Text>
        <Select
          value={selectedSku}
          onChange={(v) => { setSelectedSku(v); setLocalPromo({}); }}
          options={skuCodes.map((c) => ({ value: c, label: c }))}
          style={{ width: 200 }}
        />
        {dailyPrice != null && (
          <Typography.Text type="secondary">日常价 = {dailyPrice} 元</Typography.Text>
        )}
      </Space>

      <Divider orientation="left" style={{ margin: '8px 0' }}>淘宝</Divider>
      <Row gutter={[16, 8]}>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>淘宝商品ID</Typography.Text>
            <Input size="small" style={{ width: 200 }} value={localPromo.taobao_item_id ?? ''} onChange={(e) => patch({ taobao_item_id: e.target.value })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>淘宝SKU_ID</Typography.Text>
            <Input size="small" style={{ width: 200 }} value={localPromo.taobao_sku_id ?? ''} onChange={(e) => patch({ taobao_sku_id: e.target.value })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>淘宝活动报名价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.taobao_activity_price ?? '—'}</span>
          </Space>
        </Col>
      </Row>

      <Divider orientation="left" style={{ margin: '8px 0' }}>店内活动（小促）</Divider>
      <Row gutter={[16, 8]}>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>店铺宝系数</Typography.Text>
            <ValueHintInput table="promo" field="shop_promo_rate" category={category} isRate
              value={localPromo.shop_promo_rate != null ? Number(localPromo.shop_promo_rate) : undefined}
              onChange={(n) => patch({ shop_promo_rate: n ?? undefined })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>小促到手价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.shop_internal_final ?? '—'}</span>
          </Space>
        </Col>
      </Row>

      <Divider orientation="left" style={{ margin: '8px 0' }}>无国补中促</Divider>
      <Row gutter={[16, 8]}>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>中促店铺系数</Typography.Text>
            <ValueHintInput table="promo" field="mid_shop_rate" category={category} isRate
              value={localPromo.mid_shop_rate != null ? Number(localPromo.mid_shop_rate) : undefined}
              onChange={(n) => patch({ mid_shop_rate: n ?? undefined })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>买家到手价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.mid_buyer_price ?? '—'}</span>
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>商家实收（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.mid_shop_receipt ?? '—'}</span>
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>88VIP最终价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.mid_vip_final ?? '—'}</span>
          </Space>
        </Col>
      </Row>

      <Divider orientation="left" style={{ margin: '8px 0' }}>无国补大促</Divider>
      <Row gutter={[16, 8]}>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>大促店铺系数</Typography.Text>
            <ValueHintInput table="promo" field="big_shop_rate" category={category} isRate
              value={localPromo.big_shop_rate != null ? Number(localPromo.big_shop_rate) : undefined}
              onChange={(n) => patch({ big_shop_rate: n ?? undefined })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>买家到手价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.big_buyer_price ?? '—'}</span>
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>商家实收（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.big_shop_receipt ?? '—'}</span>
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>88VIP最终价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.big_vip_final ?? '—'}</span>
          </Space>
        </Col>
      </Row>

      <Divider orientation="left" style={{ margin: '8px 0' }}>小红书</Divider>
      <Row gutter={[16, 8]}>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>小红书商品ID</Typography.Text>
            <Input size="small" style={{ width: 200 }} value={localPromo.xhs_item_id ?? ''} onChange={(e) => patch({ xhs_item_id: e.target.value })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>小红书SKU名称</Typography.Text>
            <Input size="small" style={{ width: 200 }} value={localPromo.xhs_sku_name ?? ''} onChange={(e) => patch({ xhs_sku_name: e.target.value })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>小红书SKU_ID</Typography.Text>
            <Input size="small" style={{ width: 200 }} value={localPromo.xhs_sku_id ?? ''} onChange={(e) => patch({ xhs_sku_id: e.target.value })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>挂牌价（自动=日常价）</Typography.Text>
            <span style={readonlyStyle}>{computed.xhs_list_price ?? '—'}</span>
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>RN单品宝报名价</Typography.Text>
            <ValueHintInput table="promo" field="xhs_activity_price" category={category}
              value={localPromo.xhs_activity_price != null ? Number(localPromo.xhs_activity_price) : undefined}
              onChange={(n) => patch({ xhs_activity_price: n ?? undefined })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>活动折扣（默认0.15）</Typography.Text>
            <InputNumber size="small" style={{ width: 100 }} min={0} max={1} step={0.01}
              value={localPromo.xhs_promo_discount != null ? Number(localPromo.xhs_promo_discount) : 0.15}
              onChange={(n) => patch({ xhs_promo_discount: n ?? 0.15 })} />
          </Space>
        </Col>
        <Col span={12}>
          <Space>
            <Typography.Text style={labelStyle}>活动价（自动）</Typography.Text>
            <span style={readonlyStyle}>{computed.xhs_promo_price ?? '—'}</span>
          </Space>
        </Col>
      </Row>

      <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
        保存活动价格
      </Button>
    </Space>
  );
}

export default function NewProductComposerPage() {
  const [form] = Form.useForm();
  const [bom, setBom] = useState<BomRow[]>([emptyBom()]);
  const [skus, setSkus] = useState<SkuRow[]>([emptySku()]);
  const categoryLabel = Form.useWatch('category_label', form) as string | undefined;
  // Track the saved product code so extension tabs can be unlocked
  const [savedProductCode, setSavedProductCode] = useState<string | null>(null);

  // 参考产品搜索 (空输入时显示最近更新)
  const [refOptions, setRefOptions] = useState<{ value: string; label: string }[]>([]);
  // 物料搜索 (BOM 物料编码联想)
  const [matOptions, setMatOptions] = useState<{ value: string; label: string; name: string; unit: string | null }[]>([]);

  // 比例参考分布 (按当前类目, 类目变了自动重拉)
  const { data: ratioHints } = useQuery({
    queryKey: ['ratio-hints', categoryLabel ?? ''],
    queryFn: () => getRatioHints(categoryLabel || undefined),
    staleTime: 5 * 60 * 1000,
  });

  const showRecent = async () => {
    const list = await listRecentProducts(10);
    setRefOptions(list.map((p) => ({ value: p.code, label: `${p.code} — ${p.name}（最近更新）` })));
  };

  const searchRef = async (kw: string) => {
    if (!kw) { showRecent(); return; }
    const list = await listProducts(kw);
    setRefOptions(list.slice(0, 20).map((p) => ({ value: p.code, label: `${p.code} — ${p.name}` })));
  };

  const searchMat = async (kw: string) => {
    if (!kw) { setMatOptions([]); return; }
    const list = await listMaterials(kw);
    setMatOptions(list.slice(0, 20).map((m) => ({
      value: m.code, label: `${m.code} — ${m.name}`, name: m.name, unit: m.unit,
    })));
  };

  const refMut = useMutation({
    mutationFn: (code: string) => loadProductReference(code),
    onSuccess: (r) => {
      form.setFieldsValue({
        name: r.product.name,
        remark: r.product.remark ?? undefined,
      });
      setBom(r.bom_lines.length ? r.bom_lines.map((b) => ({ ...b, _key: nextKey() })) : [emptyBom()]);
      setSkus(r.pricing_skus.length ? r.pricing_skus.map((s) => ({ ...s, _key: nextKey() })) : [emptySku()]);
      message.success(`已带入参考产品 ${r.product.code} 的 BOM(${r.bom_lines.length}) 与定价(${r.pricing_skus.length})，品牌/类目请重新选择`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '加载参考失败'),
  });

  const saveMut = useMutation({
    mutationFn: composeProduct,
    onSuccess: (r) => {
      message.success(`已创建产品 ${r.product_code}（BOM ${r.bom_lines} 行，定价 ${r.pricing_skus} 个 SKU）`);
      setSavedProductCode(r.product_code);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const setBomRow = (key: string, patch: Partial<BomRow>) =>
    setBom((prev) => prev.map((r) => (r._key === key ? { ...r, ...patch } : r)));
  const setSkuRow = (key: string, patch: Partial<SkuRow>) =>
    setSkus((prev) => prev.map((r) => (r._key === key ? { ...r, ...patch } : r)));

  const onSave = async () => {
    const vals = await form.validateFields();
    const cleanBom = bom.filter((b) => b.material_code?.trim());
    const cleanSku = skus.filter((s) => s.sku_code?.trim());
    if (cleanSku.length === 0) {
      message.warning('至少填写一个定价 SKU（含 SKU 编码）');
      return;
    }
    saveMut.mutate({
      name: vals.name,
      brand: (vals.brand || '').toUpperCase(),
      category: vals.category,
      category_label: vals.category_label || undefined,
      remark: vals.remark || undefined,
      taobao_id: vals.taobao_id || undefined,
      bom_lines: cleanBom.map(({ _key, ...rest }) => rest),
      pricing_skus: cleanSku.map(({ _key, ...rest }) => rest),
    });
  };

  const numInput = (
    v: string | number | null | undefined,
    on: (n: number | null) => void,
    width = 100,
  ) => (
    <InputNumber
      value={v == null || v === '' ? undefined : Number(v)}
      size="small" min={0} step={0.01} style={{ width }}
      onChange={(n) => on(n == null ? null : Number(n))}
    />
  );

  const bomColumns = [
    {
      title: '物料编码', dataIndex: 'material_code', width: 220,
      render: (_: any, row: BomRow) => (
        <AutoComplete
          value={row.material_code}
          size="small"
          style={{ width: 200 }}
          options={matOptions}
          onSearch={searchMat}
          onChange={(val) => setBomRow(row._key, { material_code: val })}
          onSelect={(val) => {
            const opt = matOptions.find((o) => o.value === val);
            setBomRow(row._key, { material_code: val, material_name: opt?.name, unit: opt?.unit ?? undefined });
          }}
          placeholder="搜编码/名称"
        />
      ),
    },
    { title: '物料名称', dataIndex: 'material_name', width: 160,
      render: (_: any, row: BomRow) => (
        <Input size="small" value={row.material_name ?? ''} onChange={(e) => setBomRow(row._key, { material_name: e.target.value })} />
      ) },
    { title: '单位', dataIndex: 'unit', width: 70,
      render: (_: any, row: BomRow) => (
        <Input size="small" value={row.unit ?? ''} onChange={(e) => setBomRow(row._key, { unit: e.target.value })} />
      ) },
    { title: '单耗', dataIndex: 'qty_per_product', width: 90,
      render: (_: any, row: BomRow) => numInput(row.qty_per_product, (n) => setBomRow(row._key, { qty_per_product: n ?? 1 }), 80) },
    { title: '尺寸类型', dataIndex: 'size_type', width: 110,
      render: (_: any, row: BomRow) => (
        <Input size="small" value={row.size_type ?? ''} placeholder="组合/个数" onChange={(e) => setBomRow(row._key, { size_type: e.target.value })} />
      ) },
    { title: '', width: 40,
      render: (_: any, row: BomRow) => (
        <Button size="small" type="text" danger icon={<DeleteOutlined />}
                onClick={() => setBom((prev) => prev.filter((r) => r._key !== row._key))} />
      ) },
  ];

  const skuColumns = [
    { title: 'SKU编码*', dataIndex: 'sku_code', width: 140,
      render: (_: any, row: SkuRow) => (
        <Input size="small" value={row.sku_code} onChange={(e) => setSkuRow(row._key, { sku_code: e.target.value })} placeholder="必填" />
      ) },
    { title: 'SKU描述', dataIndex: 'sku', width: 150,
      render: (_: any, row: SkuRow) => (
        <Input size="small" value={row.sku ?? ''} onChange={(e) => setSkuRow(row._key, { sku: e.target.value })} />
      ) },
    { title: '大小类型', dataIndex: 'size_category', width: 90,
      render: (_: any, row: SkuRow) => (
        <Input size="small" value={row.size_category ?? ''} placeholder="小/中/大型" onChange={(e) => setSkuRow(row._key, { size_category: e.target.value })} />
      ) },
    { title: <span>标价 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'list_price', width: 100,
      render: (_: any, row: SkuRow) => (
        <RatioHintInput value={row.list_price} fieldName="list_price" hints={ratioHints} row={row}
          onChange={(n) => setSkuRow(row._key, { list_price: n })} />
      ) },
    { title: <span>日常价 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'daily_price', width: 100,
      render: (_: any, row: SkuRow) => (
        <RatioHintInput value={row.daily_price} fieldName="daily_price" hints={ratioHints} row={row}
          onChange={(n) => setSkuRow(row._key, { daily_price: n })} />
      ) },
    { title: <span>小促 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'small_promo', width: 100,
      render: (_: any, row: SkuRow) => (
        <RatioHintInput value={row.small_promo} fieldName="small_promo" hints={ratioHints} row={row}
          onChange={(n) => setSkuRow(row._key, { small_promo: n })} />
      ) },
    { title: <span>中促 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'mid_promo', width: 100,
      render: (_: any, row: SkuRow) => (
        <RatioHintInput value={row.mid_promo} fieldName="mid_promo" hints={ratioHints} row={row}
          onChange={(n) => setSkuRow(row._key, { mid_promo: n })} />
      ) },
    { title: <span>大促 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'big_promo', width: 110,
      render: (_: any, row: SkuRow) => (
        <BigPromoCell
          row={row}
          hints={ratioHints}
          category={categoryLabel}
          onChange={(n) => setSkuRow(row._key, { big_promo: n })}
        />
      ) },
    { title: <span>会计成本 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'accounting_cost', width: 110,
      render: (_: any, row: SkuRow) => (
        <RatioHintInput value={row.accounting_cost} fieldName="accounting_cost" hints={ratioHints} row={row}
          onChange={(n) => setSkuRow(row._key, { accounting_cost: n })} />
      ) },
    { title: <span>物理成本 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'physical_cost', width: 110,
      render: (_: any, row: SkuRow) => (
        <RatioHintInput value={row.physical_cost} fieldName="physical_cost" hints={ratioHints} row={row}
          onChange={(n) => setSkuRow(row._key, { physical_cost: n })} />
      ) },
    { title: '', width: 40,
      render: (_: any, row: SkuRow) => (
        <Button size="small" type="text" danger icon={<DeleteOutlined />}
                onClick={() => setSkus((prev) => prev.filter((r) => r._key !== row._key))} />
      ) },
  ];

  const savedSkuCodes = skus.filter((s) => s.sku_code?.trim()).map((s) => s.sku_code);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>新产品综合录入</Typography.Title>
        <Button type="primary" icon={<SaveOutlined />} loading={saveMut.isPending} onClick={onSave}>
          一键创建（产品 + BOM + 定价）
        </Button>
      </Space>

      <Alert
        type="info"
        showIcon
        message="一个界面录完产品主数据、BOM 物料清单、定价 SKU，提交时在同一事务里创建，任一步失败全部回滚。"
        description="可先选「参考已有产品」把 BOM 和定价带进来，改改再存为新品。大促价填写时点 💡 看同类目历史比例参考。产品编码按 品牌+年份+类目 自动生成。"
      />

      <Card size="small" title="参考已有产品（可选）">
        <AutoComplete
          style={{ width: 460 }}
          options={refOptions}
          onSearch={searchRef}
          onFocus={() => { if (refOptions.length === 0) showRecent(); }}
          onSelect={(code) => refMut.mutate(code)}
          placeholder="不知道编码? 直接点这里看最近更新的产品，或搜编码 / 名称"
        />
      </Card>

      <Card size="small" title="① 产品主数据">
        <Form form={form} layout="inline" size="small" style={{ rowGap: 12 }}>
          <Form.Item name="name" label="产品名称" rules={[{ required: true, message: '必填' }]}>
            <Input style={{ width: 240 }} />
          </Form.Item>
          <Form.Item name="brand" label="品牌码" rules={[{ required: true, len: 2, message: '2 字母' }]}>
            <Input style={{ width: 80 }} placeholder="PS" maxLength={2} />
          </Form.Item>
          <Form.Item name="category" label="类目码" rules={[{ required: true, len: 2, message: '2 位数字' }]}>
            <Input style={{ width: 80 }} placeholder="33" maxLength={2} />
          </Form.Item>
          <Form.Item name="category_label" label="类目名">
            <Input style={{ width: 140 }} placeholder="卧室-床" />
          </Form.Item>
          <Form.Item name="taobao_id" label="淘宝商品ID">
            <Input style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input style={{ width: 200 }} />
          </Form.Item>
        </Form>
      </Card>

      <Card
        size="small"
        title="② BOM 物料清单"
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => setBom((p) => [...p, emptyBom()])}>加一行</Button>}
      >
        <Table rowKey="_key" size="small" dataSource={bom} columns={bomColumns as any} pagination={false} scroll={{ x: 800 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          物料编码须在物料库已存在（不存在请先去「物料单价库」建档）。
        </Typography.Text>
      </Card>

      <Card
        size="small"
        title="③ 定价 SKU"
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => setSkus((p) => [...p, emptySku()])}>加一行</Button>}
      >
        <Tabs
          defaultActiveKey="basic"
          items={[
            {
              key: 'basic',
              label: '基础定价',
              children: (
                <>
                  <Table rowKey="_key" size="small" dataSource={skus} columns={skuColumns as any} pagination={false} scroll={{ x: 1200 }} />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    大促价: 点输入框看「会计/物理/出厂」三口径的历史比例分布，点蓝色标签按 成本÷比例 自动回填。
                  </Typography.Text>
                </>
              ),
            },
            {
              key: 'costs',
              label: '配件成本',
              children: (
                <SkuCostsTab
                  skuCodes={savedSkuCodes}
                  productCode={savedProductCode}
                  category={categoryLabel}
                />
              ),
            },
            {
              key: 'promo',
              label: '活动价格',
              children: (
                <SkuPromoTab
                  skuCodes={savedSkuCodes}
                  productCode={savedProductCode}
                  skuRows={skus}
                  category={categoryLabel}
                />
              ),
            },
          ]}
        />
      </Card>

      <Divider style={{ margin: '4px 0' }} />
      <Button type="primary" size="large" icon={<SaveOutlined />} loading={saveMut.isPending} onClick={onSave} block>
        一键创建产品 + BOM + 定价
      </Button>
    </Space>
  );
}
