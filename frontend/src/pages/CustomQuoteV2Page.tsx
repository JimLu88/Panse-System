import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  InputNumber,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, PlusOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

// ── 与后端 /api/customization/v2/* 对应的类型 ──
interface ClassifyResult {
  customization_type: string;
  base_product_code: string | null;
  base_product_name: string | null;
  confidence: number;
  reasoning: string;
}
interface BreakdownItem {
  label: string;
  amount: number;
  note: string;
}
interface LightResult {
  final_price: number | null;
  anchor: number;
  anchor_method: string;
  material_delta: number;
  addremove_delta: number;
  base_product_name?: string | null;
  breakdown: BreakdownItem[];
  error?: string;
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
}

function authHeaders(json: boolean): Record<string, string> {
  const t = localStorage.getItem('panse_token');
  const h: Record<string, string> = {};
  if (json) h['Content-Type'] = 'application/json';
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch('/api/customization' + path, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(body),
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

export default function CustomQuoteV2Page() {
  // ── 分类 ──
  const [desc, setDesc] = useState('');
  const [clsLoading, setClsLoading] = useState(false);
  const [cls, setCls] = useState<ClassifyResult | null>(null);

  // ── 普通定制 ──
  const [pcode, setPcode] = useState('');
  const [len, setLen] = useState<number | null>(null);
  const [mat, setMat] = useState('');
  const [lightLoading, setLightLoading] = useState(false);
  const [light, setLight] = useState<LightResult | null>(null);

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

  const doClassify = async () => {
    if (!desc.trim()) {
      message.warning('请输入定制描述');
      return;
    }
    setClsLoading(true);
    setCls(null);
    try {
      const r = await apiPost<ClassifyResult>('/v2/classify', { text: desc.trim() });
      setCls(r);
      if (r.base_product_code) setPcode(r.base_product_code);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setClsLoading(false);
    }
  };

  const doLight = async () => {
    if (!pcode.trim()) {
      message.warning('请填基础产品编码');
      return;
    }
    setLightLoading(true);
    setLight(null);
    try {
      const r = await apiPost<LightResult>('/v2/quote-light', {
        base_product_code: pcode.trim(),
        target_length_m: len ?? undefined,
        target_material: mat.trim() || undefined,
      });
      setLight(r);
      if (r.error) message.warning(r.error);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLightLoading(false);
    }
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
        boards: boards.filter((b) => b.part || b.material),
      });
      setHeavy(r);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setHeavyLoading(false);
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

  return (
    <Space direction="vertical" style={{ width: '100%', maxWidth: 980 }} size="middle">
      <Space align="center">
        <Title level={4} style={{ margin: 0 }}>
          定制报价 · 智能算价
        </Title>
        <Tag color="green" icon={<ThunderboltOutlined />}>v2</Tag>
      </Space>
      <Alert
        type="info"
        showIcon
        message="新版口径: 普通定制 = 真实SKU档价插值 + 材质/增减部位增量(纯算术, 秒级)；特殊定制 = 板单引擎 + 自动推五金。与旧版「定制报价」并行, 可对比。"
      />

      {/* ── 1. 分类器 ── */}
      <Card size="small" title="① 智能分类(判普通/特殊 + 匹配产品)">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <TextArea
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="例如: 蜂蜜餐桌 改 1.5 米 / 客户要全新异形旋转吧台..."
            autoSize={{ minRows: 2, maxRows: 5 }}
          />
          <Button type="primary" icon={<RobotOutlined />} loading={clsLoading} onClick={doClassify}>
            判定
          </Button>
          {cls && (
            <Alert
              type={cls.customization_type === '普通定制' ? 'success' : 'warning'}
              message={
                <Space wrap>
                  <Tag color={cls.customization_type === '普通定制' ? 'blue' : 'orange'}>
                    {cls.customization_type}
                  </Tag>
                  {cls.base_product_code && (
                    <Text>
                      命中: <Text strong>{cls.base_product_name}</Text>（{cls.base_product_code}）
                    </Text>
                  )}
                  <Text type="secondary">置信度 {cls.confidence}</Text>
                </Space>
              }
              description={<Text type="secondary" style={{ fontSize: 12 }}>{cls.reasoning}</Text>}
            />
          )}
        </Space>
      </Card>

      {/* ── 2. 普通定制 ── */}
      <Card size="small" title="② 普通定制算价(改现有产品)">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space wrap>
            <Input
              addonBefore="基础产品编码"
              value={pcode}
              onChange={(e) => setPcode(e.target.value)}
              style={{ width: 320 }}
              placeholder="如 PFG25210021222(可由上方分类自动填)"
            />
            <InputNumber
              addonBefore="目标长度(米)"
              value={len}
              onChange={(v) => setLen(v)}
              min={0}
              step={0.1}
              style={{ width: 200 }}
            />
            <Input
              addonBefore="改材质"
              value={mat}
              onChange={(e) => setMat(e.target.value)}
              style={{ width: 220 }}
              placeholder="如 黑胡桃(可空)"
            />
            <Button type="primary" loading={lightLoading} onClick={doLight}>
              算价
            </Button>
          </Space>

          {light && light.final_price != null && (
            <>
              <Space align="baseline">
                <Text>最终报价:</Text>
                <Text strong style={{ fontSize: 26, color: '#1677ff' }}>
                  ¥{light.final_price.toFixed(2)}
                </Text>
                {light.base_product_name && <Text type="secondary">{light.base_product_name}</Text>}
              </Space>
              <Table<BreakdownItem>
                size="small"
                rowKey={(_, i) => String(i)}
                pagination={false}
                columns={breakdownCols}
                dataSource={light.breakdown}
              />
            </>
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
                  {heavy.accessory_total.toFixed(0)} · 工厂对比 {heavy.factory_quote_compare.toFixed(0)}
                </Text>
              </Space>
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

      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        说明: 普通定制锚在真实 SKU 档价上做插值 + 增量(P0 实测 88% 产品有多档价);材质增量用 wood_cost
        反推面积。系数/利润率在「报价参数设置」里可改。本页只读计算, 不落订单。
      </Paragraph>
    </Space>
  );
}
