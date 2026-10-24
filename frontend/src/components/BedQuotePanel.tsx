import { useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Descriptions, InputNumber, Select, Space, Table, Typography } from 'antd';

type Part = { name: string; qty: number; unit: string; price: number | null };
type RefSku = {
  product_code: string; product_name: string; sku_code: string; sku_name: string;
  material: string | null; wood: string | null; size_info: string; size_status: string | null;
  length_cm: number | null; width_cm: number | null; source: string; buyer_price: number | null;
  fees: { packaging_cost: number; logistics_cost: number; install_cost: number } | null;
  error: string | null; parts: Part[];
};
export type BedReferenceData = { products?: { product_code: string; product_name: string }[]; items: RefSku[]; materials: { name: string; unit: string; price: number | null }[] };
type Change = { material: string; qty: number; length_cm?: number; width_cm?: number; material_real?: string };
type Result = {
  final_price: number | null; error?: string; selected_sku_code: string;
  outer_dimension_basis?: { buyer_price: number; fees: RefSku['fees']; limitation: string };
  breakdown: { label: string; amount: number; note: string }[];
};
async function request<T>(path: string, body?: object): Promise<T> {
  const token = localStorage.getItem('panse_token');
  const response = await fetch(`/api/customization/v2/${path}`, {
    method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '报价请求失败，请检查输入或登录状态');
  return data;
}

/** Deterministic bed form; never calls classify, quote-both, product or order writes. */
export default function BedQuotePanel({ product, data }: { product: string; data: BedReferenceData }) {
  const [skuCode, setSkuCode] = useState<string>();
  const [unit, setUnit] = useState<'cm' | 'mm'>('cm');
  const [length, setLength] = useState<number | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [removeHead, setRemoveHead] = useState(false);
  const [headHeight, setHeadHeight] = useState<number | null>(null);
  const [headWidth, setHeadWidth] = useState<number | null>(null);
  const [headMaterial, setHeadMaterial] = useState<string>();
  const [removed, setRemoved] = useState<string[]>([]);
  const [additions, setAdditions] = useState<Change[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const revision = useRef(0);
  const ref = data.items.find(s => s.sku_code === skuCode && s.product_code === product);
  const reset = () => { revision.current++; setConfirmed(false); setResult(null); setError(''); };
  // Only individually priced BOM accessories can be removed. Never remove an entire wood assembly as a headboard.
  const removable = ref?.parts.filter(p => !/木作|铺板/.test(p.name)) ?? [];
  const specialHead = /软包|编织/.test(`${ref?.product_name || ''} ${ref?.sku_name || ''}`);
  const woods = data.materials.filter(m => /木/.test(m.name) && /平方|平米|㎡|m²|m2/i.test(m.unit || ''));
  const cm = (n: number) => unit === 'mm' ? n / 10 : n;
  const selectSku = (code: string) => {
    reset(); setSkuCode(code); setRemoved([]); setRemoveHead(false); setHeadHeight(null); setHeadWidth(null); setAdditions([]);
    const s = data.items.find(s => s.sku_code === code && s.product_code === product);
    setLength(s?.length_cm ? s.length_cm * (unit === 'mm' ? 10 : 1) : null);
    setWidth(s?.width_cm ? s.width_cm * (unit === 'mm' ? 10 : 1) : null);
    const candidate = woods.find(m => m.name === `${s?.wood}-2.2cm厚度`) ?? woods.find(m => m.name === `${s?.wood}-2.2cm`);
    setHeadMaterial(candidate?.name);
  };
  const calculate = async () => {
    if (!ref || !confirmed || ref.error || !length || !width) return;
    const current = revision.current;
    setBusy(true); setError(''); setResult(null);
    try {
      const remove_parts: Change[] = removed.map(name => {
        const p = removable.find(p => p.name === name)!;
        return { material: name, qty: p.qty };
      });
      if (removeHead) {
        if (!headWidth || !headHeight || !headMaterial) throw new Error('请填写床头板实际宽度、高度并确认木料；不能用整床尺寸代替');
        remove_parts.push({ material: '床头板', material_real: headMaterial, qty: 1, length_cm: cm(headWidth), width_cm: cm(headHeight) });
      }
      if (additions.some(p => !p.material || !(p.qty > 0) || (/平方|米/.test(data.materials.find(m => m.name === p.material)?.unit || '') && !(p.length_cm && p.width_cm)))) {
        throw new Error('新增配置须选择已知物料、数量；板件须填写厘米尺寸，缺失不能按零计价');
      }
      const r = await request<Result>('quote-light', {
        base_product_code: ref.product_code, base_sku_code: ref.sku_code, price_tier: 'big_buyer',
        outer_dimensions: { length_cm: cm(length), width_cm: cm(width) },
        add_parts: additions, remove_parts,
      });
      if (current === revision.current) { setResult(r); if (r.error) setError(r.error); }
    } catch (e) { if (current === revision.current) setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const fees = result?.outer_dimension_basis?.fees ?? ref?.fees;
  return <Card title="床的尺寸与配置" style={{ marginBottom: 20 }}>
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert type="info" message="选原款 → 确认材质与大促到手价 → 填床体外尺寸 → 增减配置 → 计算估算价" description="此入口不调用 AI，不改商品、价格或尺寸，不创建订单。床垫规格不是床体外尺寸。" />
      {!data.items.some(s => s.product_code === product) && <Alert type="error" message="该床款没有可报价的真实规格，请先核实原SKU与价格；不会套用其他床款" />}
      <Select aria-label="原款规格" placeholder="选择精确原款 SKU（材质 / 铺板）" showSearch optionFilterProp="label" style={{ width: '100%' }}
        value={skuCode} onChange={selectSku} options={data.items.filter(s => s.product_code === product).map(s => ({ value: s.sku_code, label: `${s.sku_name} · ${s.sku_code} · 大促到手价 ${s.buyer_price ?? '缺价'}元` }))} />
      {ref && <>
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="本次主材 / 铺板">{ref.material || '主材未核实'} / {ref.sku_name}</Descriptions.Item>
          <Descriptions.Item label="基准款大促到手价">{ref.buyer_price ?? '缺价'} 元（big_buyer，不再叠加15%）</Descriptions.Item>
          <Descriptions.Item label="原款床体外长 × 外宽">{ref.length_cm ?? '缺失'} × {ref.width_cm ?? '缺失'} cm</Descriptions.Item>
          <Descriptions.Item label="尺寸来源">{ref.source}；状态：{ref.size_status || '未标记确认'}；{ref.size_info}</Descriptions.Item>
        </Descriptions>
        {ref.error && <Alert type="error" message={ref.error} />}
        <Space wrap>
          <Select aria-label="外尺寸单位" value={unit} options={[{ value: 'cm', label: '厘米 cm' }, { value: 'mm', label: '毫米 mm' }]}
            onChange={next => { reset(); const k = next === 'mm' ? 10 : .1; setLength(length == null ? null : length*k); setWidth(width == null ? null : width*k); setHeadHeight(headHeight == null ? null : headHeight*k); setHeadWidth(headWidth == null ? null : headWidth*k); setUnit(next); }} />
          <label>目标床体外长 <InputNumber aria-label="目标床体外长" min={0.1} value={length} onChange={n => { reset(); setLength(n); }} /> {unit}</label>
          <label>目标床体外宽 <InputNumber aria-label="目标床体外宽" min={0.1} value={width} onChange={n => { reset(); setWidth(n); }} /> {unit}</label>
        </Space>
        {specialHead ? <Alert type="info" message="软包或编织床头不能按实木平板扣价；若下方有该SKU的独立床头配件，可按实际BOM移除，否则须先核实床头成本" /> :
        <Checkbox checked={removeHead} onChange={e => { reset(); setRemoveHead(e.target.checked); }}>去掉床头板（1件；需确认板件实际宽度、高度和木料）</Checkbox>}
        {removeHead && <Space wrap>
          <label>床头板宽度 <InputNumber aria-label="床头板宽度" min={0.1} value={headWidth} onChange={n => { reset(); setHeadWidth(n); }} /> {unit}</label>
          <label>床头板高度 <InputNumber aria-label="床头板高度" min={0.1} value={headHeight} onChange={n => { reset(); setHeadHeight(n); }} /> {unit}</label>
          <Select aria-label="床头板木料" style={{ width: 230 }} value={headMaterial} placeholder="确认床头板木料" options={woods.map(m => ({ value: m.name, label: `${m.name} / ${m.price ?? '缺价'}元` }))} onChange={v => { reset(); setHeadMaterial(v); }} />
        </Space>}
        {removable.map(p => <Checkbox key={p.name} checked={removed.includes(p.name)} onChange={e => { reset(); setRemoved(e.target.checked ? [...removed, p.name] : removed.filter(n => n !== p.name)); }}>
          去掉 {p.name}（原BOM {p.qty}{p.unit}；{p.price ?? '缺价'}元/{p.unit}，按BOM整套数量扣，不按灯头数重复扣）
        </Checkbox>)}
        {additions.map((p, i) => <Space key={i} wrap>
          <Select aria-label={`新增物料${i+1}`} style={{ width: 280 }} showSearch optionFilterProp="label" value={p.material || undefined} placeholder="选择已有物料（未知物料须先核价）"
            options={data.materials.map(m => ({ value: m.name, label: `${m.name} · ${m.price ?? '缺价'}元/${m.unit}` }))}
            onChange={v => { reset(); setAdditions(additions.map((a,j) => j === i ? { ...a, material: v } : a)); }} />
          {(['qty', 'length_cm', 'width_cm'] as const).map(k => <label key={k}>{ {qty:'数量',length_cm:'板件长cm',width_cm:'板件宽cm'}[k] } <InputNumber min={0.01} value={p[k]} onChange={n => { reset(); setAdditions(additions.map((a,j) => j === i ? { ...a, [k]: n ?? undefined } : a)); }} /></label>)}
          <Button onClick={() => { reset(); setAdditions(additions.filter((_,j) => j !== i)); }}>移除此配置</Button>
        </Space>)}
        <Button onClick={() => { reset(); setAdditions([...additions, { material: '', qty: 1 }]); }}>增加已有物料配置</Button>
        <Alert type="warning" message="当前为原款木作面积估算，不是工厂精确板单报价" description="支撑、床脚、龙骨结构和板单未核实；铺板采购价按原款整套保留。未知配置不能填零，改变床型或铺板请重新选准确原款。床头非同材质平板时不可套用本项。" />
        <Checkbox checked={confirmed} onChange={e => { revision.current++; setConfirmed(e.target.checked); setResult(null); }}>
          我确认本次材质 / 铺板为「{ref.material || '未核实'} / {ref.sku_name}」，价型为大促到手价，采用上述原款外尺寸及本次配置作估算
        </Checkbox>
        <Button type="primary" loading={busy} disabled={!confirmed || !!ref.error || !length || !width} onClick={calculate}>计算床定制估算价</Button>
      </>}
      {error && <Alert type="error" message={error} />}
      {result?.final_price != null && <Card title="本次定制估算结果（非工厂核价）" data-testid="bed-quote-result">
        <Typography.Title level={3}>大促到手估算价 ¥{result.final_price.toFixed(2)}</Typography.Title>
        <p>基准款到手价 ¥{result.outer_dimension_basis?.buyer_price}；增减差额 ¥{(result.final_price - (result.outer_dimension_basis?.buyer_price ?? 0)).toFixed(2)}</p>
        <p>继承原SKU费用：打包 {fees?.packaging_cost} 元 / 运费 {fees?.logistics_cost} 元 / 安装 {fees?.install_cost} 元（已在报价中，不重复加）</p>
        <Table rowKey={(_, i) => String(i)} pagination={false} size="small" dataSource={result.breakdown} columns={[{title:'项目',dataIndex:'label'},{title:'金额',dataIndex:'amount'},{title:'依据',dataIndex:'note'}]} />
      </Card>}
    </Space>
  </Card>;
}
