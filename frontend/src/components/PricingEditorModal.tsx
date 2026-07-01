/**
 * 定价统一编辑器 (用户拍板):
 *  - 表格单元格全部只读, 改数只走这里 — 防误触键盘把表改坏
 *  - 弹窗可随意拖动 (抓标题栏), 无遮罩 → 可以边看表边改
 *  - 价格/成本/22配件/渠道系数 全部整合, 每个字段带 ⏱ 修改历史 (最近30份)
 *  - 「保存并覆盖同产品全部 SKU」一键铺到全产品 (二次确认)
 */
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Collapse, DatePicker, Input, InputNumber, Modal, Space, Typography, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  PricingSku,
  updatePricingByProduct,
  updatePricingSku,
  upsertSkuCosts,
  upsertSkuPromo,
} from '../api/client';
import FieldHistoryPopover from './FieldHistoryPopover';

type FieldDef = { key: string; label: string; precision?: number };

// 三段字段组: 主表 / 22配件(costs) / 渠道(promo)
const SKU_FIELDS: FieldDef[] = [
  { key: 'list_price', label: '标价' }, { key: 'daily_price', label: '日常价' },
  { key: 'small_promo', label: '小促' }, { key: 'mid_promo', label: '中促' },
  { key: 'big_promo', label: '大促' },
  { key: 'factory_cost', label: '工厂成本' }, { key: 'wood_cost', label: '木作成本' },
  { key: 'logistics_cost', label: '物流成本' }, { key: 'install_cost', label: '安装成本' },
  { key: 'packaging_cost', label: '包装成本' }, { key: 'external_parts_cost', label: '外配件成本' },
  { key: 'platform_fee_rate', label: '平台费' }, { key: 'tax', label: '税费' },
];
const COSTS_FIELDS: FieldDef[] = [
  { key: 'rock_slab', label: '岩板' }, { key: 'drawer_rail', label: '抽屉轨道' },
  { key: 'led_strip', label: '灯带' }, { key: 'glass', label: '玻璃' },
  { key: 'electric_rail', label: '电力轨道' }, { key: 'packing_sheet', label: '打包纸片' },
  { key: 'iron_pin', label: '铁销' }, { key: 'connector', label: '连接片' },
  { key: 'aluminum_rail', label: '铝合金轨道' }, { key: 'plastic_rail', label: '塑料轨道' },
  { key: 'mini_handle', label: 'mini把手' }, { key: 'nail_free_glue', label: '免钉胶' },
  { key: 'engraving', label: '雕刻' }, { key: 'acrylic_strip', label: '亚克力条' },
  { key: 'embedded_sleeve', label: '预埋套杆' }, { key: 'cable_mgmt', label: '理线架插排' },
  { key: 'back_panel', label: '背板' }, { key: 'stainless_trim', label: '装饰条' },
  { key: 'leg', label: '腿部' }, { key: 'soft_pack', label: '软包' },
  { key: 'bed_board', label: '床铺板' }, { key: 'other_cost', label: '其他' },
];
const PROMO_FIELDS_E: FieldDef[] = [
  { key: 'shop_promo_rate', label: '店铺宝系数', precision: 4 },
  { key: 'mid_shop_rate', label: '中促系数', precision: 4 },
  { key: 'big_shop_rate', label: '大促系数', precision: 4 },
  { key: 'xhs_promo_discount', label: '小红书折扣', precision: 4 },
  { key: 'taobao_activity_price', label: '淘宝活动价' },
  { key: 'xhs_activity_price', label: '小红书活动价' },
];

const TABLE_OF: Record<'sku' | 'costs' | 'promo', string> = {
  sku: 'pricing_skus', costs: 'pricing_sku_costs', promo: 'pricing_sku_promo',
};

// 公式推导的结果字段 (= 物理成本 ÷ (基数 − 费税), 由促销系数决定):
// 用户拍板: 默认锁定, 提示改系数; 坚持直改需两次确认解锁。
const FORMULA_LOCKED: Record<string, string> = {
  small_promo: '小促价由「店铺宝系数」推导',
  mid_promo: '中促价由「中促系数」推导',
  big_promo: '大促价由「大促系数」推导',
};

function seed(row: PricingSku, defs: FieldDef[]): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  defs.forEach((d) => {
    const v = (row as any)[d.key];
    out[d.key] = v === null || v === undefined || v === '' ? null : Number(v);
  });
  return out;
}

function diffOf(vals: Record<string, number | null>, base: Record<string, number | null>) {
  const out: Record<string, number | null> = {};
  Object.keys(vals).forEach((k) => {
    if (vals[k] !== base[k]) out[k] = vals[k];
  });
  return out;
}

export default function PricingEditorModal({ row, onClose, onSaved, onSaveNext }: {
  row: PricingSku | null; onClose: () => void; onSaved: () => void;
  onSaveNext?: () => void;   // 保存(有改动才存)后自动跳到下一行继续编辑
}) {
  const qc = useQueryClient();
  const [vals, setVals] = useState<Record<string, number | null>>({});
  const [base, setBase] = useState<Record<string, number | null>>({});
  // 淘宝宝贝标题 (文本, 宝贝级) — 单独管理, 不进数值 vals
  const [title, setTitle] = useState<string>('');
  const [titleBase, setTitleBase] = useState<string>('');
  const [saving, setSaving] = useState(false);
  // 公式字段解锁集合 (每次打开编辑器重置)
  const [unlocked, setUnlocked] = useState<Set<string>>(new Set());
  // 调价生效日 (选填): 选了则此日之前的订单仍按老价/老成本, 历史利润不追溯改写
  const [effFrom, setEffFrom] = useState<any>(null);
  // 可拖动: 抓标题栏平移
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const drag = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);

  useEffect(() => {
    if (row) {
      const seeded = { ...seed(row, SKU_FIELDS), ...seed(row, COSTS_FIELDS), ...seed(row, PROMO_FIELDS_E) };
      setVals(seeded);
      setBase(seeded);
      setTitle(row.taobao_title ?? '');
      setTitleBase(row.taobao_title ?? '');
      setOffset({ x: 0, y: 0 });
      setUnlocked(new Set());
      setEffFrom(null);
    }
  }, [row]);

  // 公式字段双确认解锁
  const askUnlock = (key: string, label: string) => {
    Modal.confirm({
      title: `${label} 是公式结果`,
      content: `${FORMULA_LOCKED[key]}。正确做法是去「渠道系数」分组改系数, 系统自动重算。确定要绕过公式直接改这个价格吗？`,
      okText: '我要直接改',
      okButtonProps: { danger: true },
      onOk: () => {
        Modal.confirm({
          title: '再次确认',
          content: '直接改价会和系数脱钩, 下次重算可能被系数覆盖。确认继续？',
          okText: '确认解锁',
          okButtonProps: { danger: true },
          onOk: () => setUnlocked((p) => new Set(p).add(key)),
        });
      },
    });
  };

  if (!row) return null;

  const skuDiff = diffOf(
    Object.fromEntries(SKU_FIELDS.map((d) => [d.key, vals[d.key]])),
    Object.fromEntries(SKU_FIELDS.map((d) => [d.key, base[d.key]])));
  const costsDiff = diffOf(
    Object.fromEntries(COSTS_FIELDS.map((d) => [d.key, vals[d.key]])),
    Object.fromEntries(COSTS_FIELDS.map((d) => [d.key, base[d.key]])));
  const promoDiff = diffOf(
    Object.fromEntries(PROMO_FIELDS_E.map((d) => [d.key, vals[d.key]])),
    Object.fromEntries(PROMO_FIELDS_E.map((d) => [d.key, base[d.key]])));
  const titleDirty = title.trim() !== titleBase.trim();
  const dirtyCount = Object.keys(skuDiff).length + Object.keys(costsDiff).length + Object.keys(promoDiff).length + (titleDirty ? 1 : 0);
  // 主表补丁 = 数值差异 + (改过的)淘宝标题
  const skuPatch = (): Record<string, unknown> => ({
    ...skuDiff,
    ...(titleDirty ? { taobao_title: title.trim() || null } : {}),
    // 选了生效日且有价格/成本改动 → 后端把改前值封存历史, 此日前订单按老价
    ...(effFrom && Object.keys(skuDiff).length ? { effective_from: effFrom.format('YYYY-MM-DD') } : {}),
  });

  // 有改动才写库; 返回是否成功(无改动=直接算成功, 供"改下行"纯跳转)
  const persist = async (): Promise<boolean> => {
    if (!dirtyCount) return true;
    setSaving(true);
    try {
      if (Object.keys(skuPatch()).length) await updatePricingSku(row.id, skuPatch());
      if (Object.keys(costsDiff).length) await upsertSkuCosts(row.sku_code, costsDiff);
      if (Object.keys(promoDiff).length) await upsertSkuPromo(row.sku_code, promoDiff);
      message.success(`已保存 ${dirtyCount} 个字段 (含联动重算)`);
      qc.invalidateQueries({ queryKey: ['pricing-skus'] });
      onSaved();
      return true;
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const saveOne = async () => { if (await persist()) onClose(); };
  // 保存并改下一行: 存当前(有改动才存)→ 由父级把编辑器切到下一行 (row 变 → useEffect 重载表单)
  const saveAndNext = async () => { if (await persist()) onSaveNext?.(); };

  const saveAll = () => {
    Modal.confirm({
      title: '覆盖同产品全部 SKU？',
      content: `将把这 ${dirtyCount} 个字段的新值铺到产品 ${row.product_code} 下所有 SKU 并重算。各 SKU 原有差异会被覆盖, 修改档案有留痕可回查。`,
      okText: '确认覆盖',
      okButtonProps: { danger: true },
      onOk: async () => {
        setSaving(true);
        try {
          const r = await updatePricingByProduct(row.product_code, {
            sku: Object.keys(skuPatch()).length ? skuPatch() : undefined,
            costs: Object.keys(costsDiff).length ? costsDiff : undefined,
            promo: Object.keys(promoDiff).length ? promoDiff : undefined,
          });
          message.success(r.message);
          qc.invalidateQueries({ queryKey: ['pricing-skus'] });
          onSaved();
          onClose();
        } catch (e: any) {
          message.error(e?.response?.data?.detail ?? '覆盖失败');
        } finally {
          setSaving(false);
        }
      },
    });
  };

  const onTitleMouseDown = (e: React.MouseEvent) => {
    drag.current = { sx: e.clientX, sy: e.clientY, ox: offset.x, oy: offset.y };
    const move = (ev: MouseEvent) => {
      if (!drag.current) return;
      setOffset({ x: drag.current.ox + ev.clientX - drag.current.sx,
                  y: drag.current.oy + ev.clientY - drag.current.sy });
    };
    const up = () => {
      drag.current = null;
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  };

  const fieldRow = (group: 'sku' | 'costs' | 'promo', d: FieldDef) => {
    const isLocked = d.key in FORMULA_LOCKED && !unlocked.has(d.key);
    return (
      <div key={d.key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ width: 92, flexShrink: 0, fontSize: 13 }}>{d.label}</span>
        <InputNumber
          size="small" style={{ width: 130 }}
          precision={d.precision ?? 2}
          value={vals[d.key]}
          disabled={isLocked}
          onChange={(v) => setVals((p) => ({ ...p, [d.key]: v }))}
        />
        {isLocked && (
          <Typography.Link style={{ fontSize: 11 }} onClick={() => askUnlock(d.key, d.label)}
            title={FORMULA_LOCKED[d.key]}>
            🔒公式价·改系数
          </Typography.Link>
        )}
        {vals[d.key] !== base[d.key] && <Typography.Text type="warning" style={{ fontSize: 11 }}>改</Typography.Text>}
        <FieldHistoryPopover table={TABLE_OF[group]} pk={row.sku_code} field={d.key} label={d.label} />
      </div>
    );
  };

  return (
    <Modal
      open
      mask={false}
      maskClosable={false}
      width={560}
      onCancel={onClose}
      title={
        <div style={{ cursor: 'move', userSelect: 'none' }} onMouseDown={onTitleMouseDown}
             title="按住可拖动">
          编辑 {row.sku || row.sku_code} <Typography.Text type="secondary" style={{ fontSize: 12 }}>(抓这里拖动)</Typography.Text>
        </div>
      }
      modalRender={(node) => (
        <div style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}>{node}</div>
      )}
      footer={
        <Space>
          <Typography.Text type="secondary">{dirtyCount ? `${dirtyCount} 个字段待保存` : '未改动'}</Typography.Text>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} disabled={!dirtyCount} onClick={saveOne}>
            仅保存这一行
          </Button>
          {onSaveNext && (
            <Button type="primary" ghost loading={saving} onClick={saveAndNext}
              title="保存本行(有改动才存)并自动跳到下一行继续编辑">
              保存并改下一行 →
            </Button>
          )}
          <Button danger loading={saving} disabled={!dirtyCount} onClick={saveAll}>
            保存并覆盖同产品全部 SKU
          </Button>
        </Space>
      }
    >
      <Alert type="info" showIcon style={{ marginBottom: 10 }}
             message="改完点保存才生效, 系统自动联动重算 (促销价/会计成本/利润等)。每个字段右侧 ⏱ 可看最近 30 份修改记录。" />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ width: 64, flexShrink: 0, fontSize: 13 }}>淘宝标题</span>
        <Input
          size="small" allowClear
          placeholder="淘宝宝贝标题 (订单只带长标题没编码时, 系统按它对回本产品算成本)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        {titleDirty && <Typography.Text type="warning" style={{ fontSize: 11, flexShrink: 0 }}>改</Typography.Text>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ width: 64, flexShrink: 0, fontSize: 13 }}>调价生效日</span>
        <DatePicker size="small" allowClear value={effFrom} onChange={setEffFrom}
          placeholder="选填: 此日之前的订单仍按老价/老成本" style={{ width: 260 }} />
        {effFrom && <Typography.Text type="warning" style={{ fontSize: 11, flexShrink: 0 }}>
          此日前订单不受本次改价影响
        </Typography.Text>}
      </div>
      {effFrom && (
        <Alert type="warning" showIcon style={{ marginBottom: 10 }}
          message={`本次价格/成本改动将从 ${effFrom.format('YYYY-MM-DD')} 起生效; 该日之前的历史订单仍按调价前的老成本/老价核算, 利润不被追溯改写。`} />
      )}
      <div style={{ maxHeight: '60vh', overflowY: 'auto', paddingRight: 4 }}>
        <Collapse
          defaultActiveKey={['price']}
          size="small"
          items={[
            { key: 'price', label: '价格档 + 成本 (主表)',
              children: <div>{SKU_FIELDS.map((d) => fieldRow('sku', d))}</div> },
            { key: 'coef', label: '渠道系数 / 活动价 (淘宝·小红书)',
              children: <div>{PROMO_FIELDS_E.map((d) => fieldRow('promo', d))}</div> },
            { key: 'acc', label: '22 项配件成本',
              children: <div>{COSTS_FIELDS.map((d) => fieldRow('costs', d))}</div> },
          ]}
        />
      </div>
    </Modal>
  );
}
