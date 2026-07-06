/**
 * 改价台 (2026-07-02) — 复刻用户 Excel List 表: 改「定价基数」(0.86/0.88/0.9 这个除数),
 * 促价 = ROUNDUP(成本 ÷ 基数, 进位到10) 自动算出来; 右侧附带反推的「单品立减系数」(填淘宝用)。
 * 你改基数, 价格立刻变(和你原表一样); 价格/单品立减系数都是只读输出。
 */
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, Key, ReactNode } from 'react';
import { Alert, Button, Collapse, Image, Input, InputNumber, Popconfirm, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CUTE_IMG } from '../components/ProductThumb';
import {
  fetchShopPriceBoard, updateShopPrice, bulkUpdateShopPrice,
  type ShopPriceRow,
} from '../api/catalog';

const yuan = (v?: number | null) =>
  v == null ? '—' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const pct = (v?: number | null) => (v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`);

// 页面顶部「名词说明」样式
const helpBox: CSSProperties = { fontSize: 13, lineHeight: 1.9, color: '#334155' };
const helpP: CSSProperties = { margin: '4px 0' };

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

  // ── 批量改基数 (筛选后勾选/全选, 填一次基数一键套用, 免逐个点) ──
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const [bSmall, setBSmall] = useState<number | null>(null);
  const [bMid, setBMid] = useState<number | null>(null);
  const [bBig, setBBig] = useState<number | null>(null);
  const hasBulkValue = [bSmall, bMid, bBig].some((v) => v != null && v > 0);
  const bulkMut = useMutation({
    mutationFn: (ids: number[]) => {
      const patch: { base_small?: number; base_mid?: number; base_big?: number } = {};
      if (bSmall != null && bSmall > 0) patch.base_small = bSmall;
      if (bMid != null && bMid > 0) patch.base_mid = bMid;
      if (bBig != null && bBig > 0) patch.base_big = bBig;
      return bulkUpdateShopPrice(ids, patch);
    },
    onSuccess: (updated) => {
      const map = new Map(updated.map((r) => [r.id, r]));
      setRows((rs) => rs.map((r) => map.get(r.id) ?? r));   // 回值刷新各行(价格+系数+利润)
      setSelectedKeys([]);
      message.success({ content: `已批量改 ${updated.length} 个 SKU, 价格/系数/利润已联动`, key: 'spb', duration: 2 });
    },
    onError: () => message.error({ content: '批量保存失败', key: 'spb' }),
  });
  const applyBulk = () => {
    if (!selectedKeys.length || !hasBulkValue) return;
    bulkMut.mutate(selectedKeys.map(Number));
  };

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

  // 单品立减 (加法口径): 淘宝该填的『折扣 + 立减金额』。折 0.792 → 显示 7.92折; 立减金额取整到元。
  // 折为空 = 官方立减已≥目标价, 该档单品立减不适用 (显示 —)。
  const discountCol = (
    title: ReactNode, dKey: keyof ShopPriceRow, amtKey: keyof ShopPriceRow,
  ): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: dKey as string, width: 106, align: 'right',
    render: (d: number | null, row) => {
      if (d == null)
        return <span style={{ color: '#cbd5e1' }} title="官方立减已≥目标价, 该档不需再叠单品立减">—</span>;
      const amt = row[amtKey] as number | null;
      return (
        <div style={{ lineHeight: 1.15 }}>
          <div style={{ fontWeight: 700, color: '#0f766e' }}>{(d * 10).toFixed(2)}折</div>
          {amt != null
            ? <div style={{ fontSize: 12, color: '#0f766e' }}>减{yuan(amt)}</div>
            : null}
        </div>
      );
    },
  });

  // ── 中促/小促「当前不启用」列: 灰底 + 灰字 + 只读(能选中复制, 不能编辑); 未来启用中促时再放开 ──
  const GRAY_LOCK = '#a3adba';
  const lockedCell = () => ({ style: { background: '#f6f7f9' } });
  const lockText = (children: ReactNode) => (
    <span style={{ color: GRAY_LOCK, userSelect: 'text' }} title="当前不启用, 可复制、不可改">{children}</span>
  );
  const baseColLocked = (title: string, tier: BaseTier): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: tier, width: 96, align: 'right', onCell: lockedCell,
    render: (v: number | null) => lockText(v == null ? '—' : Number(v)),
  });
  const priceColLocked = (title: string, key: keyof ShopPriceRow): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: key as string, width: 92, align: 'right', onCell: lockedCell,
    render: (v: number | null) => lockText(yuan(v)),
  });
  const rateColLocked = (title: string, key: keyof ShopPriceRow): ColumnsType<ShopPriceRow>[number] => ({
    title, dataIndex: key as string, width: 88, align: 'right', onCell: lockedCell,
    render: (v: number | null) => lockText(pct(v)),
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
    baseColLocked('小促基数', 'base_small'),
    baseColLocked('中促基数', 'base_mid'),
    baseCol('大促基数', 'base_big'),
    priceColLocked('小促价', 'small_promo'),
    priceColLocked('中促价', 'mid_promo'),
    priceCol('大促价', 'big_promo'),
    marginCol,
    // ── 报名价模型 (2026-07-03: 大促锚不动, 只动中促) ──
    { title: <span>88VIP大促<br />报名价</span>, dataIndex: 'report_price', width: 108, align: 'right',
      render: (v: number | null) => <span style={{ color: '#1a73e8', fontWeight: 700 }}>{yuan(v)}</span> },
    { title: <span>超大促报名价<br />(618/双11)</span>, dataIndex: 'report_price_618', width: 116, align: 'right',
      render: (v: number | null) => <span style={{ color: '#7c3aed', fontWeight: 500 }}>{yuan(v)}</span> },
    { title: <span>大促到手<br />(买家实付)</span>, dataIndex: 'big_buyer_price', width: 96, align: 'right',
      render: (v: number | null) => <span style={{ color: '#64748b' }}>{yuan(v)}</span> },
    { title: '空档价红线', dataIndex: 'gap_floor', width: 96, align: 'right',
      render: (v: number | null) => <span style={{ color: '#94a3b8' }}>{yuan(v)}</span> },
    // ── 单品立减 (加法口径, 淘宝直接填): 折 + 立减金额, 三档场次力度 10/12/15% ──
    discountCol(<span>中促单品立减<br />(日常10%)</span>, 'mid_discount', 'mid_deduct'),
    discountCol(<span>大促单品立减<br />(88VIP 12%)</span>, 'big_discount', 'big_deduct'),
    discountCol(<span>超大促单品立减<br />(618·双11 15%)</span>, 'big618_discount', 'big618_deduct'),
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>改价台</Typography.Title>
      <Alert
        type="info" showIcon
        message="改「大促基数」(0.86 / 0.88 / 0.9 这种除数), 回车即存 → 大促价 = 进位到10(成本 ÷ 基数) 自动算 → 报名价/单品立减 全联动。三档促价存的都是「店铺实收」(88VIP佣金后到账), 不是买家到手价。"
        description="最右三列「单品立减」= 淘宝直接填的『折扣 + 立减金额』(加法口径, 到手=日常−官方立减−单品立减)。看不懂点开下面「📖 名词说明」。"
      />
      <Collapse
        size="small"
        items={[
          {
            key: 'mid',
            label: '📖 ① 为什么小促 / 中促各列是灰的? 还有用吗?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>现在只有「大促」这一档在用</b>: 大促基数可改 → 大促价、大促利润、两个报名价都跟着算。<b>小促 / 中促 各列已全部灰色只读</b>(能选中复制、不能改), 因为现阶段用不到; 未来要用中促时再放开。</p>
                <p style={helpP}><b>中促基数为什么很多是空的:</b>系统之前把这些 SKU 的中促价锁在了刚好合规的精确值(如 ¥532、¥1319.32), 不再由基数派生 —— 空着是对的, 别去补。</p>
                <p style={helpP}><b>中促、小促这两档没废, 只是角色变了:</b> 以前是你分别报给淘宝的价; 现在你只报<b>两个大促报名价</b>(88VIP大促 / 超大促), 平台按场次自动打折。中促/小促现在的用途 = ① 算报名价 + 判合规的锚 ② 空档期底线(空档价红线 = 中促到手)。</p>
              </div>
            ),
          },
          {
            key: 'report',
            label: '📖 ② 两个报名价(88VIP大促 / 超大促618双11)是什么? 为什么不一样?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>「报名价」= 你填进淘宝「超级立减」报名表的那个数</b>, 不是买家看到的价。淘宝在这个数上按场次力度打折, <b>买家到手 = 报名价 ×(1 − 该场力度)</b>。两个场次分别报, 不合并成一个。</p>
                <p style={helpP}><b>「88VIP大促报名价」= 大促到手 ÷ 0.88</b>(88VIP 月度大促场打 12%)。</p>
                <p style={helpP}><b>「超大促报名价(618/双11)」= 大促到手 ÷ 0.85</b>(618/双11 场打 15%, 打得更深)。</p>
                <p style={helpP}><b>为什么两个不一样:</b> 两个场次打折力度不同(12% vs 15%), 但都要让买家最终落到<b>同一个「大促到手价」</b>。折扣越深的场(15%)要报越高的数去抵, 所以 超大促报名价 &gt; 88VIP大促报名价 —— 这是对的, 不是算错。</p>
                <p style={helpP}>这两档力度(12% / 15%)目前系统固定, 不在页面上调。</p>
              </div>
            ),
          },
          {
            key: 'terms',
            label: '📖 ③ 单品立减(折/立减金额) 怎么填? 空档价红线是什么?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>淘宝是"加法": 到手 = 日常价 − 官方立减 − 单品立减。</b> 官方立减是各场固定力度(日常 10% / 88VIP大促 12% / 618·双11 15%), 单品立减是你自己再叠的那部分。</p>
                <p style={helpP}><b>公式: 单品立减折 = 目标到手 ÷ 日常价 + 官方力度。</b> 最右三列已按这个算好 —— <b>「折」直接填淘宝单品立减/单品补贴, 或用下面的「减¥」立减金额(更精确, 到分)</b>。别再用旧的乘法系数(填了会差几百块, 就是同事那个 353 元的问题)。</p>
                <p style={helpP}><b>三档不一样</b>: 官方力度越深, 单品立减打得越浅(超大促比大促浅 3 个点)。<b>每换一个活动力度都要重填</b>。折为「—」= 官方立减已够, 该档不用叠单品立减。</p>
                <p style={helpP}><b>空档价红线 = 中促到手价。</b> 没活动的空档期, 单品立减做价<b>不能低于这条线</b>, 否则砸穿淘宝近 15 天最低价线, 下个大活动被冷却(要 15 天洗回)。</p>
              </div>
            ),
          },
          {
            key: 'big',
            label: '📖 ④ 我的大促价会被改吗?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>不会。</b>你改大促基数时只联动大促这一档(大促价、大促利润、两个报名价), 不碰其它档; 系统对中促做过的合规微调也是只抬中促、大促价一分不动(已逐条校验、可回滚)。</p>
              </div>
            ),
          },
        ]}
      />
      <Input.Search
        placeholder="按 产品名 / 编码 / SKU 搜 (先搜到再改)" allowClear
        style={{ maxWidth: 360 }} onSearch={setQ}
      />
      {/* 批量改基数: 筛选 → 全选/勾选 → 填一次基数 → 一键套用 (比逐个点快, 还能跨页) */}
      <Space wrap style={{ background: '#f5f7fa', padding: '8px 12px', borderRadius: 8, width: '100%' }}>
        <span style={{ fontWeight: 500 }}>批量改基数：</span>
        <InputNumber value={bSmall} onChange={(x) => setBSmall(x as number)} disabled placeholder="小促基数(停用)"
          controls={false} min={0.01} max={5} step={0.01} style={{ width: 118 }} />
        <InputNumber value={bMid} onChange={(x) => setBMid(x as number)} disabled placeholder="中促基数(停用)"
          controls={false} min={0.01} max={5} step={0.01} style={{ width: 118 }} />
        <InputNumber value={bBig} onChange={(x) => setBBig(x as number)} placeholder="大促基数"
          controls={false} min={0.01} max={5} step={0.01} style={{ width: 104 }} />
        <Popconfirm title={`把填的基数套用到选中的 ${selectedKeys.length} 个 SKU?`}
          onConfirm={applyBulk} okText="套用" cancelText="取消"
          disabled={!selectedKeys.length || !hasBulkValue}>
          <Button type="primary" disabled={!selectedKeys.length || !hasBulkValue} loading={bulkMut.isPending}>
            应用到选中 {selectedKeys.length} 行
          </Button>
        </Popconfirm>
        <Button onClick={() => setSelectedKeys(rows.map((r) => r.id))} disabled={!rows.length}>
          全选筛选结果 ({rows.length})
        </Button>
        {selectedKeys.length > 0 && <Button type="link" onClick={() => setSelectedKeys([])}>清空选择</Button>}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>留空的档不改；套用后自动记入工厂调价历史</Typography.Text>
      </Space>
      <Table<ShopPriceRow>
        rowKey="id" size="small" loading={isLoading || saveMut.isPending || bulkMut.isPending}
        rowSelection={{ selectedRowKeys: selectedKeys, onChange: setSelectedKeys, preserveSelectedRowKeys: true }}
        dataSource={rows} columns={columns}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 个 SKU` }}
        scroll={{ x: 1560 }}
      />

    </Space>
  );
}
