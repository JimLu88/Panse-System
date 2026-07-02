/**
 * 改价台 (2026-07-02) — 复刻用户 Excel List 表: 改「定价基数」(0.86/0.88/0.9 这个除数),
 * 促价 = ROUNDUP(成本 ÷ 基数, 进位到10) 自动算出来; 右侧附带反推的「店铺宝系数」(填淘宝用)。
 * 你改基数, 价格立刻变(和你原表一样); 价格/店铺宝系数都是只读输出。
 */
import { useEffect, useRef, useState } from 'react';
import { Alert, Image, Input, InputNumber, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CUTE_IMG } from '../components/ProductThumb';
import { fetchShopPriceBoard, updateShopPrice, type ShopPriceRow } from '../api/catalog';

const yuan = (v?: number | null) =>
  v == null ? '—' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const pct = (v?: number | null) => (v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`);

type BaseTier = 'base_small' | 'base_mid' | 'base_big';

// 「定价基数」单元格: 点着改(0.86 这种小数), 回车/失焦保存(只在真变了才存)。
// ⚠后端 Decimal 序列化成字符串("0.8600"), 必须转数字比较, 否则每次失焦都误判"变了"→重复保存(触发幂等 409)。
function BaseCell({ value, onSave }: { value?: number | string | null; onSave: (v: number) => void }) {
  const num = (x: unknown) => (x == null || x === '' ? null : Number(x));
  const [v, setV] = useState<number | null>(num(value));
  const sent = useRef<number | null>(null);   // 本轮已发出的值, 防 onPressEnter+onBlur 双发
  useEffect(() => { setV(num(value)); sent.current = null; }, [value]);
  const commit = () => {
    const nv = num(v);
    const cur = num(value);
    if (nv != null && nv > 0 && nv !== cur && nv !== sent.current) {
      sent.current = nv;
      onSave(nv);
    } else if (nv == null || nv <= 0) {
      setV(cur);   // 空/非正 → 回退, 不发
    }
  };
  return (
    <InputNumber
      value={v} onChange={(x) => setV(x as number)}
      onPressEnter={commit} onBlur={commit}
      controls={false} min={0.01} max={5} step={0.01} style={{ width: '100%' }}
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
    mutationFn: ({ id, tier, value }: { id: number; tier: BaseTier; value: number }) =>
      updateShopPrice(id, { [tier]: value }),
    onSuccess: (row) => {
      setRows((rs) => rs.map((r) => (r.id === row.id ? row : r)));   // 回值刷新该行(价格+系数)
      message.success({ content: '已改基数, 价格与系数已联动', key: 'sp', duration: 1.4 });
    },
    onError: (err: unknown) => {
      // 409 = 幂等重复(前一次其实已成功), 忽略, 别吓用户
      const st = (err as { response?: { status?: number } })?.response?.status;
      if (st === 409) return;
      message.error({ content: '保存失败', key: 'sp' });
    },
  });

  const baseCol = (title: string, tier: BaseTier): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: tier, width: 96, align: 'right',
    render: (v: number | null, row) => (
      <BaseCell value={v} onSave={(nv) => saveMut.mutate({ id: row.id, tier, value: nv })} />
    ),
  });
  const priceCol = (title: string, key: keyof ShopPriceRow): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: key as string, width: 92, align: 'right',
    render: (v: number | null) => <span style={{ fontWeight: 500 }}>{yuan(v)}</span>,
  });
  const rateCol = (title: string, key: keyof ShopPriceRow): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: key as string, width: 88, align: 'right',
    render: (v: number | null) => <span style={{ color: '#94a3b8' }}>{pct(v)}</span>,
  });

  // 大促利润(实时): = 大促价 −(物理成本 + 平台费0.6% + 税2%); 改基数→价格变→利润当场联动。
  // 红=亏/≤0, 橙=薄利<10%, 绿=正常。金额大字 + 利润率小字。
  const marginCol: ColumnsType<ShopPriceRow>[number] = {
    title: '大促利润', dataIndex: 'big_promo_margin', width: 116, align: 'right',
    render: (v: number | null, row) => {
      if (v == null) return <span style={{ color: '#cbd5e1' }}>—</span>;
      const rate = row.gross_margin_rate;
      const color = v <= 0 ? '#dc2626' : rate != null && rate < 0.1 ? '#d97706' : '#16a34a';
      return (
        <div style={{ lineHeight: 1.15 }}>
          <div style={{ fontWeight: 700, color }}>{yuan(v)}</div>
          {rate != null ? <div style={{ fontSize: 12, color }}>{pct(rate)}</div> : null}
        </div>
      );
    },
  };

  const columns: ColumnsType<ShopPriceRow> = [
    {
      title: '图片', dataIndex: 'image', width: 60, align: 'center', fixed: 'left',
      render: (src: string | null) =>
        <Image src={src || CUTE_IMG} fallback={CUTE_IMG} width={46} height={46}
          style={{ objectFit: 'cover', borderRadius: 8 }} />,
    },
    {
      title: '产品', dataIndex: 'product_name', width: 190, ellipsis: true, fixed: 'left',
      render: (v: string, row) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v || '(未命名)'}</div>
          <Tag style={{ marginTop: 2 }}>{row.product_code}</Tag>
        </div>
      ),
    },
    {
      title: 'SKU', dataIndex: 'sku', width: 160, ellipsis: true,
      render: (v: string, row) => (
        <div>
          <div>{v || '默认'}</div>
          {row.size_info ? <div style={{ fontSize: 12, color: '#94a3b8' }}>{row.size_info}</div> : null}
        </div>
      ),
    },
    { title: '日常价', dataIndex: 'daily_price', width: 84, align: 'right',
      render: (v: number | null) => <span style={{ color: '#94a3b8' }}>{yuan(v)}</span> },
    baseCol('小促基数', 'base_small'),
    baseCol('中促基数', 'base_mid'),
    baseCol('大促基数', 'base_big'),
    priceCol('小促价', 'small_promo'),
    priceCol('中促价', 'mid_promo'),
    priceCol('大促价', 'big_promo'),
    marginCol,
    rateCol('小促店铺宝', 'shop_promo_rate'),
    rateCol('中促店铺宝', 'mid_shop_rate'),
    rateCol('大促店铺宝', 'big_shop_rate'),
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>改价台</Typography.Title>
      <Alert
        type="info" showIcon
        message="点「小/中/大促基数」格子改数字(0.86 / 0.88 / 0.9 这种除数), 回车即存; 「促价」= ROUNDUP(成本 ÷ 基数, 进位到10) 自动算(和你 Excel List 表一致, 会以 0 结尾)。"
        description="「大促利润」= 大促价 −(物理成本 + 平台费0.6% + 税2%), 改基数当场联动(绿=正常 / 橙=薄利<10% / 红=亏)。最右三列「店铺宝」= 价格反推出的、填进淘宝店铺宝工具的系数。基数越小价格越高。"
      />
      <Input.Search
        placeholder="按 产品名 / 编码 / SKU 搜 (先搜到再改)" allowClear
        style={{ maxWidth: 360 }} onSearch={setQ}
      />
      <Table<ShopPriceRow>
        rowKey="id" size="small" loading={isLoading || saveMut.isPending}
        dataSource={rows} columns={columns}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 个 SKU` }}
        scroll={{ x: 1300 }}
      />
    </Space>
  );
}
