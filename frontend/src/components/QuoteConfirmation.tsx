import { useState } from 'react';
import { Alert, Checkbox, Descriptions, Modal, Select, Space, Typography } from 'antd';

export const QUOTE_TIERS = [
  { value: 'big', label: '定价表大促价（不是大促到手价）' },
  { value: 'big_buyer', label: '大促到手价（买家最终到手口径）' },
  { value: 'mid', label: '定价表中促价（不是中促到手价）' },
  { value: 'mid_buyer', label: '中促到手价（买家最终到手口径）' },
  { value: 'daily', label: '定价表日常价' },
  { value: 'list', label: '定价表标价' },
];

type Choice = { sku_code: string; sku_name: string; original_material?: string | null };
type Selection = { tier: string; material: string; sku?: string };
type Options = { identity: string; skus?: Choice[]; materials: string[]; costOnly?: boolean; detail?: string };

function ConfirmationForm({ options, changed }: { options: Options; changed: (value: Selection | null) => void }) {
  const [sku, setSku] = useState<string>();
  const [tier, setTier] = useState<string>();
  const [material, setMaterial] = useState<string>();
  const [checked, setChecked] = useState(false);
  const original = options.skus?.find(s => s.sku_code === sku)?.original_material;
  const publish = (next: { sku?: string; tier?: string; material?: string; checked: boolean }) => {
    changed(next.checked && next.tier && next.material && (options.costOnly || next.sku)
      ? { sku: next.sku, tier: next.tier, material: next.material } : null);
  };
  const edit = (key: 'sku' | 'tier' | 'material', value: string) => {
    const next = { sku, tier, material, [key]: value, checked: false };
    if (key === 'sku') { setSku(value); setMaterial(undefined); next.material = undefined; }
    if (key === 'tier') setTier(value);
    if (key === 'material') setMaterial(value);
    setChecked(false); publish(next);
  };
  return <Space direction="vertical" style={{ width: '100%' }} size={14}>
    <Alert type="warning" message="每次新问价都需重新确认；不沿用上一次的价型或主材。" />
    <Descriptions column={1} size="small" items={[
      { key: 'product', label: '本次产品/品类', children: options.identity },
      { key: 'detail', label: '规格及部件', children: options.detail || '请核对下方款式' },
    ]} />
    {!options.costOnly && <Select aria-label="确认精确款式" style={{ width: '100%' }} value={sku}
      placeholder="请明确选择本次精确SKU/款式（不自动选中）" onChange={v => edit('sku', v)}
      options={options.skus?.map(s => ({ value: s.sku_code, label: `${s.sku_name} · ${s.sku_code}` }))} />}
    <Select aria-label="确认价格口径" style={{ width: '100%' }} value={tier}
      placeholder="请明确选择本次价格口径" onChange={v => edit('tier', v)}
      options={options.costOnly ? [{ value: 'cost_quote', label: '板单成本推导报价（非定价表价、非活动到手价）' }] : QUOTE_TIERS} />
    {original && <Typography.Text>原材识别候选：{original}。沿用也需在下方选择并确认；请核对实际材质。</Typography.Text>}
    <Select aria-label="确认具体主材" showSearch style={{ width: '100%' }} value={material}
      placeholder="请明确选择本次主材/具体材质" onChange={v => edit('material', v)}
      options={[...new Set([...options.materials, ...(original ? [original] : [])])].filter(Boolean)
        .map(m => ({ value: m, label: m === original ? `${m}（沿用已核对原材）` : m }))} />
    <Checkbox checked={checked} onChange={e => { setChecked(e.target.checked); publish({sku,tier,material,checked:e.target.checked}); }}>
      我已核对本次精确款式、价格口径及具体主材
    </Checkbox>
  </Space>;
}

export function confirmQuote(options: Options): Promise<Selection | null> {
  return new Promise(resolve => {
    let selection: Selection | null = null;
    const modal = Modal.confirm({ title: '本次报价前确认', width: 660, icon: null,
      maskClosable: false, okText: '确认本次信息并计算', cancelText: '取消，不算价',
      okButtonProps: { disabled: true },
      content: <ConfirmationForm options={options} changed={value => {
        selection = value; modal.update({ okButtonProps: { disabled: !value } });
      }} />,
      onOk: () => { resolve(selection); }, onCancel: () => { resolve(null); },
    });
  });
}

export function confirmedBody<T extends Record<string, unknown>>(inputs: T): T & { confirmation: Record<string, unknown> } {
  // Bind confirmation to exactly the JSON body sent, including dimensions and parts.
  const body = JSON.parse(JSON.stringify(inputs));
  // LAN ERP is HTTP: randomUUID is secure-context-only, getRandomValues is not.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  const requestId = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  return { ...body, confirmation: { request_id: requestId, price_confirmed: true,
    material_confirmed: true, identity_confirmed: true, inputs: body } };
}
