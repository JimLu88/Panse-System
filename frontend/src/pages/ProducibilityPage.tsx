import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Card,
  Form,
  InputNumber,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import PresetTable from '../components/PresetTable';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  MaterialRequirement,
  ProducibilityResult,
  computeProducibility,
  fuzzyMatch,
} from '../api/client';

export default function ProducibilityPage() {
  const [skuCode, setSkuCode] = useState<string | undefined>();
  const [search, setSearch] = useState('');
  const [targetQty, setTargetQty] = useState(1);
  const [result, setResult] = useState<ProducibilityResult | null>(null);

  const { data: candidates } = useQuery({
    queryKey: ['match', 'sku', search],
    queryFn: () => fuzzyMatch(search, 'sku', 10),
    enabled: search.length > 0,
  });

  const mut = useMutation({
    mutationFn: () =>
      computeProducibility({ sku_code: skuCode, target_qty: targetQty }),
    onSuccess: setResult,
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '计算失败'),
  });

  const reqColumns = [
    { title: '物料编码', dataIndex: 'material_code', width: 110 },
    { title: '物料名', dataIndex: 'material_name', ellipsis: true },
    {
      title: '单产品用量',
      dataIndex: 'qty_per_product',
      width: 110,
    },
    {
      title: '可用库存',
      dataIndex: 'available_stock',
      width: 100,
    },
    {
      title: '仅看这种料能造',
      dataIndex: 'can_build_units',
      width: 130,
      render: (v: number, row: MaterialRequirement) =>
        result?.bottleneck?.material_code === row.material_code ? (
          <Tag color="red">{v} 件 · 瓶颈</Tag>
        ) : (
          <span>{v} 件</span>
        ),
    },
    {
      title: `造 ${targetQty} 件还缺`,
      dataIndex: 'shortage_for_target',
      width: 130,
      render: (v: string) =>
        Number(v) > 0 ? <Tag color="orange">差 {v}</Tag> : <Tag color="green">够</Tag>,
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        可生产数计算
      </Typography.Title>

      <Card>
        <Form layout="inline" onFinish={() => mut.mutate()}>
          <Form.Item label="SKU" required>
            <AutoComplete
              value={skuCode}
              onSearch={setSearch}
              onSelect={(v) => setSkuCode(v as string)}
              options={(candidates ?? []).map((c) => ({
                value: c.code,
                label: `${c.code}  ${c.name}`,
              }))}
              placeholder="按编码或名称搜索"
              style={{ width: 400 }}
            />
          </Form.Item>
          <Form.Item label="目标件数">
            <InputNumber min={0} value={targetQty} onChange={(v) => setTargetQty(v ?? 0)} />
          </Form.Item>
          <Form.Item>
            <a
              onClick={() => mut.mutate()}
              style={{ display: 'inline-block', padding: '4px 12px' }}
            >
              {mut.isPending ? '计算中…' : '计算'}
            </a>
          </Form.Item>
        </Form>
      </Card>

      {result && (
        <>
          <Card>
            <Space size="large">
              <Statistic title="成品库存" value={result.in_stock_qty} suffix="件" />
              <Statistic
                title="还能造"
                value={result.can_build_qty}
                suffix="件"
                valueStyle={{ color: '#1677ff' }}
              />
              <Statistic
                title="合计可发货"
                value={result.total_available_qty}
                suffix="件"
                valueStyle={{ color: '#3f8600' }}
              />
              {result.bottleneck && (
                <div>
                  <div style={{ color: '#999' }}>瓶颈物料</div>
                  <Tag color="red">{result.bottleneck.material_code}</Tag>
                  <div style={{ fontSize: 12, color: '#666' }}>
                    {result.bottleneck.material_name}
                  </div>
                </div>
              )}
            </Space>
          </Card>

          {result.requirements.length === 0 ? (
            <Alert
              type="warning"
              message="该 SKU 暂无 BOM 数据，只能按成品库存评估"
            />
          ) : (
            <Card title="BOM 物料明细">
              <PresetTable<MaterialRequirement>
                tableKey="producibility_req"
                rowKey="material_code"
                dataSource={result.requirements}
                columns={reqColumns as any}
                size="middle"
                pagination={false}
              />
            </Card>
          )}

          {targetQty > 0 && result.missing_for_target.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`要造 ${targetQty} 件还差 ${result.missing_for_target.length} 种料`}
              description={
                <ul style={{ marginBottom: 0 }}>
                  {result.missing_for_target.map((m) => (
                    <li key={m.material_code}>
                      <code>{m.material_code}</code> {m.material_name} —— 差{' '}
                      <b>{m.shortage_for_target}</b>
                    </li>
                  ))}
                </ul>
              }
            />
          )}
        </>
      )}
    </Space>
  );
}
