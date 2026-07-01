/**
 * 改价台 (2026-07-02) — Excel 式逐个改价: 点「小/中/大促价」(=店铺实收) 直接改, 回车/Tab 跳下一格,
 * 后端按用户 Excel 口径倒推「店铺宝系数」(要填进淘宝的那个数) + 买家到手/VIP到手, 当场刷新。
 * 锚 = 店铺实收 = 小促价/中促价/大促价; 系数 = 反推结果 (只读, 灰显)。
 */
import { useEffect, useState } from 'react';
import { Alert, Image, Input, InputNumber, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CUTE_IMG } from '../components/ProductThumb';
import { fetchShopPriceBoard, updateShopPrice, type ShopPriceRow } from '../api/catalog';

const yuan = (v?: number | null) =>
  v == null ? '—' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const pct = (v?: number | null) => (v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`);

type Tier = 'small_promo' | 'mid_promo' | 'big_promo';

// 单个「到手价」单元格: 点着改, 回车/失焦保存(只在真变了才存)。受控 + 服务端回值同步。
function PriceCell({ value, onSave }: { value?: number | null; onSave: (v: number | null) => void }) {
  const [v, setV] = useState<number | null | undefined>(value);
  useEffect(() => { setV(value); }, [value]);   // 保存成功后服务端值回填 → 同步
  const commit = () => {
    const nv = v == null ? null : Number(v);
    if (nv !== (value ?? null)) onSave(nv);
  };
  return (
    <InputNumber
      value={v as number} onChange={(x) => setV(x as number)}
      onPressEnter={commit} onBlur={commit}
      controls={false} min={0} style={{ width: '100%' }} placeholder="—"
    />
  );
}

export default function ShopPriceBoardPage() {
  const [q, setQ] = useState('');
  const { data, isLoading } = useQuery({
    queryKey: ['shop-price-board', q],
    queryFn: () => fetchShopPriceBoard(q || undefined),
  });
  const [rows, setRows] = useState<ShopPriceRow[]>([]);
  useEffect(() => { if (data) setRows(data); }, [data]);

  const saveMut = useMutation({
    mutationFn: ({ id, tier, value }: { id: number; tier: Tier; value: number | null }) =>
      updateShopPrice(id, { [tier]: value }),
    onSuccess: (row) => {
      setRows((rs) => rs.map((r) => (r.id === row.id ? row : r)));   // 用回值刷新该行(系数/买家到手)
      message.success({ content: '已保存并反推系数', key: 'sp', duration: 1.2 });
    },
    onError: () => message.error({ content: '保存失败', key: 'sp' }),
  });

  const priceCol = (title: string, tier: Tier): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: tier, width: 108, align: 'right',
    render: (v: number | null, row) => (
      <PriceCell value={v} onSave={(nv) => saveMut.mutate({ id: row.id, tier, value: nv })} />
    ),
  });
  const rateCol = (title: string, key: keyof ShopPriceRow): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: key as string, width: 92, align: 'right',
    render: (v: number | null) => <span style={{ color: '#64748b' }}>{pct(v)}</span>,
  });

  const columns: ColumnsType<ShopPriceRow> = [
    {
      title: '图片', dataIndex: 'image', width: 68, align: 'center',
      render: (src: string | null) =>
        <Image src={src || CUTE_IMG} fallback={CUTE_IMG} width={52} height={52}
          style={{ objectFit: 'cover', borderRadius: 8 }} />,
    },
    {
      title: '产品', dataIndex: 'product_name', ellipsis: true,
      render: (v: string, row) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v || '(未命名)'}</div>
          <Tag style={{ marginTop: 2 }}>{row.product_code}</Tag>
        </div>
      ),
    },
    {
      title: 'SKU', dataIndex: 'sku', width: 180, ellipsis: true,
      render: (v: string, row) => (
        <div>
          <div>{v || '默认'}</div>
          {row.size_info ? <div style={{ fontSize: 12, color: '#94a3b8' }}>{row.size_info}</div> : null}
        </div>
      ),
    },
    { title: '日常价', dataIndex: 'daily_price', width: 90, align: 'right',
      render: (v: number | null) => <span style={{ color: '#94a3b8' }}>{yuan(v)}</span> },
    priceCol('小促价', 'small_promo'),
    priceCol('中促价', 'mid_promo'),
    priceCol('大促价', 'big_promo'),
    rateCol('小促系数', 'shop_promo_rate'),
    rateCol('中促系数', 'mid_shop_rate'),
    rateCol('大促系数', 'big_shop_rate'),
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>改价台</Typography.Title>
      <Alert
        type="info" showIcon
        message="点「小/中/大促价」格子直接改数字(=店铺实收价), 回车或点别处即保存; 右侧灰色「店铺宝系数」= 后端按你 Excel 口径当场反推(要填进淘宝店铺宝的那个数)。"
        description="口径: 小促系数 = 小促价 ÷ 日常价; 中促/大促系数 = 买家到手 ÷ (日常 × 88%), 买家到手 = 促价 ÷ (1−佣金)。改价即「手动定价」, 会覆盖该档成本加成价。"
      />
      <Input.Search
        placeholder="按 产品名 / 编码 / SKU 搜 (先搜到再改)" allowClear
        style={{ maxWidth: 360 }} onSearch={setQ}
      />
      <Table<ShopPriceRow>
        rowKey="id" size="small" loading={isLoading || saveMut.isPending}
        dataSource={rows} columns={columns}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 个 SKU` }}
        scroll={{ x: 1000 }}
      />
    </Space>
  );
}
