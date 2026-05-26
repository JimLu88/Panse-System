import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Descriptions,
  Form,
  InputNumber,
  Radio,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  DimensionQuote,
  HighQuote,
  LightQuote,
  MaterialSwapResult,
  dimensionQuote,
  fuzzyMatch,
  highQuote,
  lightQuote,
  materialSwap,
} from '../api/client';

type Mode = 'light' | 'high' | 'dimension' | 'swap';

export default function QuotePage() {
  const [mode, setMode] = useState<Mode>('light');

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        报价计算器 (Phase 2)
      </Typography.Title>

      <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
        <Radio.Button value="light">轻定制 · 四档售价</Radio.Button>
        <Radio.Button value="high">高定 · 大中小型</Radio.Button>
        <Radio.Button value="dimension">任意尺寸差价</Radio.Button>
        <Radio.Button value="swap">换材差价</Radio.Button>
      </Radio.Group>

      {mode === 'light' && <LightCard />}
      {mode === 'high' && <HighCard />}
      {mode === 'dimension' && <DimensionCard />}
      {mode === 'swap' && <SwapCard />}
    </Space>
  );
}

function LightCard() {
  const [skuCode, setSkuCode] = useState<string | undefined>();
  const [search, setSearch] = useState('');
  const { data: candidates } = useQuery({
    queryKey: ['match', 'sku', search],
    queryFn: () => fuzzyMatch(search, 'sku', 10),
    enabled: search.length > 0,
  });
  const { data, isLoading, error } = useQuery({
    queryKey: ['quote', 'light', skuCode],
    queryFn: () => lightQuote(skuCode!),
    enabled: !!skuCode,
    retry: false,
  });

  return (
    <Card title="按 SKU 查 4 档售价">
      <Form layout="vertical">
        <Form.Item label="SKU（按编码或名称搜索）">
          <AutoComplete
            value={skuCode}
            onSearch={setSearch}
            onSelect={(v) => setSkuCode(v as string)}
            options={(candidates ?? []).map((c) => ({ value: c.code, label: `${c.code}  ${c.name}` }))}
            placeholder="如：榉木无边床 / PPS2633007032011"
            style={{ width: 400 }}
          />
        </Form.Item>
      </Form>
      {error ? (
        <Alert type="error" message="该 SKU 暂无定价记录" />
      ) : data ? (
        <LightResult data={data} loading={isLoading} />
      ) : null}
    </Card>
  );
}

function LightResult({ data, loading }: { data: LightQuote; loading: boolean }) {
  return (
    <Descriptions bordered size="middle" column={2} title={<span>{data.sku_code} <Tag>{data.size_category}</Tag></span>}>
      <Descriptions.Item label="SKU">{data.sku}</Descriptions.Item>
      <Descriptions.Item label="标价">¥{data.list_price ?? '-'}</Descriptions.Item>
      <Descriptions.Item label="日常价"><Tag color="blue">¥{data.daily_price ?? '-'}</Tag></Descriptions.Item>
      <Descriptions.Item label="小促价"><Tag color="cyan">¥{data.small_promo ?? '-'}</Tag></Descriptions.Item>
      <Descriptions.Item label="中促价"><Tag color="gold">¥{data.mid_promo ?? '-'}</Tag></Descriptions.Item>
      <Descriptions.Item label="大促价"><Tag color="red">¥{data.big_promo ?? '-'}</Tag></Descriptions.Item>
      <Descriptions.Item label="大促利润">¥{data.big_promo_margin ?? '-'}</Descriptions.Item>
      <Descriptions.Item label="毛利率">
        {data.gross_margin_rate ? (Number(data.gross_margin_rate) * 100).toFixed(1) + '%' : '-'}
      </Descriptions.Item>
    </Descriptions>
  );
}

function HighCard() {
  const [form] = Form.useForm();
  const [result, setResult] = useState<HighQuote | null>(null);
  const mut = useMutation({
    mutationFn: highQuote,
    onSuccess: setResult,
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '计算失败'),
  });

  return (
    <Card title="高定大中小型：售价 = 成本 / (1 − 利润率)">
      <Form
        form={form}
        layout="inline"
        onFinish={(v) =>
          mut.mutate({ ...v, margin_rate: v.margin_rate === undefined ? undefined : v.margin_rate })
        }
        initialValues={{ size_category: '中型' }}
      >
        <Form.Item name="cost" label="总成本" rules={[{ required: true }]}>
          <InputNumber min={0.01} step={100} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item name="size_category" label="大小类型">
          <Select
            style={{ width: 120 }}
            options={[
              { value: '小型', label: '小型 (15%)' },
              { value: '中型', label: '中型 (15%)' },
              { value: '大型', label: '大型 (25%)' },
            ]}
          />
        </Form.Item>
        <Form.Item name="margin_rate" label="利润率（选填覆盖）">
          <InputNumber min={0} max={0.99} step={0.05} style={{ width: 120 }} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={mut.isPending}>
            计算
          </Button>
        </Form.Item>
      </Form>
      {result && (
        <Space size="large" style={{ marginTop: 16 }}>
          <Statistic title="售价" value={result.final_price} prefix="¥" />
          <Statistic title="利润额" value={result.margin_amount} prefix="¥" />
          <Statistic
            title="利润率"
            value={(Number(result.margin_rate) * 100).toFixed(1)}
            suffix="%"
          />
        </Space>
      )}
    </Card>
  );
}

function DimensionCard() {
  const [form] = Form.useForm();
  const [result, setResult] = useState<DimensionQuote | null>(null);
  const mut = useMutation({
    mutationFn: dimensionQuote,
    onSuccess: setResult,
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '计算失败'),
  });

  return (
    <Card title="任意尺寸差价 (Δcm × per_cm_cost × (1+利润率))">
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="per_cm_cost 是「每多 1cm 多花的成本」"
        description="10 款主力的系数表 (plan §11 第 3 条) 还没沉淀；目前需要手填。"
      />
      <Form
        form={form}
        layout="inline"
        onFinish={(v) => mut.mutate(v)}
        initialValues={{ margin_rate: 0.15 }}
      >
        <Form.Item name="base_cm" label="基础尺寸 (cm)" rules={[{ required: true }]}>
          <InputNumber style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="target_cm" label="目标尺寸 (cm)" rules={[{ required: true }]}>
          <InputNumber style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="per_cm_cost" label="每 1cm 成本" rules={[{ required: true }]}>
          <InputNumber min={0} style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="margin_rate" label="利润率">
          <InputNumber min={0} max={0.99} step={0.05} style={{ width: 110 }} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={mut.isPending}>
            计算
          </Button>
        </Form.Item>
      </Form>
      {result && (
        <Space size="large" style={{ marginTop: 16 }}>
          <Statistic title="尺寸差" value={result.cm_diff} suffix="cm" />
          <Statistic
            title="差价"
            value={result.delta}
            prefix="¥"
            valueStyle={{ color: Number(result.delta) >= 0 ? '#cf1322' : '#3f8600' }}
          />
        </Space>
      )}
    </Card>
  );
}

function SwapCard() {
  const [fromSearch, setFromSearch] = useState('');
  const [toSearch, setToSearch] = useState('');
  const { data: fromCands } = useQuery({
    queryKey: ['match', 'material', fromSearch],
    queryFn: () => fuzzyMatch(fromSearch, 'material', 8),
    enabled: fromSearch.length > 0,
  });
  const { data: toCands } = useQuery({
    queryKey: ['match', 'material', toSearch],
    queryFn: () => fuzzyMatch(toSearch, 'material', 8),
    enabled: toSearch.length > 0,
  });

  const [form] = Form.useForm();
  const [result, setResult] = useState<MaterialSwapResult | null>(null);
  const mut = useMutation({
    mutationFn: materialSwap,
    onSuccess: setResult,
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '计算失败'),
  });

  return (
    <Card title="换材差价：(B 单价 − A 单价) × 数量">
      <Form
        form={form}
        layout="inline"
        onFinish={(v) => mut.mutate(v)}
        initialValues={{ qty: 1 }}
      >
        <Form.Item name="from_code" label="原物料 A" rules={[{ required: true }]}>
          <AutoComplete
            style={{ width: 260 }}
            onSearch={setFromSearch}
            options={(fromCands ?? []).map((c) => ({ value: c.code, label: `${c.code}  ${c.name}` }))}
          />
        </Form.Item>
        <Form.Item name="to_code" label="换成 B" rules={[{ required: true }]}>
          <AutoComplete
            style={{ width: 260 }}
            onSearch={setToSearch}
            options={(toCands ?? []).map((c) => ({ value: c.code, label: `${c.code}  ${c.name}` }))}
          />
        </Form.Item>
        <Form.Item name="qty" label="数量">
          <InputNumber min={0.01} step={1} style={{ width: 100 }} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={mut.isPending}>
            计算
          </Button>
        </Form.Item>
      </Form>
      {result && (
        <Space size="large" style={{ marginTop: 16 }}>
          <Statistic title="A 单价" value={result.from_unit_price ?? '-'} prefix="¥" />
          <Statistic title="B 单价" value={result.to_unit_price ?? '-'} prefix="¥" />
          {result.delta == null ? (
            <Alert type="warning" message="任一物料价格缺失（多见于刚自动建的定制物料），请先到「物料单价库」补价" />
          ) : (
            <Statistic
              title="差价"
              value={result.delta}
              prefix="¥"
              valueStyle={{ color: Number(result.delta) >= 0 ? '#cf1322' : '#3f8600' }}
            />
          )}
        </Space>
      )}
    </Card>
  );
}
