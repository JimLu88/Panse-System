/**
 * 改价台 (2026-07-02) — 复刻用户 Excel List 表: 改「定价基数」(0.86/0.88/0.9 这个除数),
 * 促价 = ROUNDUP(成本 ÷ 基数, 进位到10) 自动算出来; 右侧附带反推的「单品立减系数」(填淘宝用)。
 * 你改基数, 价格立刻变(和你原表一样); 价格/单品立减系数都是只读输出。
 */
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, Key } from 'react';
import { Alert, Button, Collapse, Image, Input, InputNumber, Modal, Popconfirm, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CUTE_IMG } from '../components/ProductThumb';
import {
  fetchShopPriceBoard, updateShopPrice, bulkUpdateShopPrice, fixMidCompliance,
  type ShopPriceRow, type FixMidComplianceResult, type FixMidComplianceChange,
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

  // ── 一键微升中促合规 (报名价模型): 先 dry-run 拉不合规清单前后对比给你看, 确认后才落库(大促价一分不动) ──
  const qc = useQueryClient();
  const [fixPreview, setFixPreview] = useState<FixMidComplianceResult | null>(null);
  const dryRunMut = useMutation({
    mutationFn: () => fixMidCompliance(false),
    onSuccess: (res) => setFixPreview(res),
    onError: () => message.error('验算失败'),
  });
  const applyFixMut = useMutation({
    mutationFn: () => fixMidCompliance(true),
    onSuccess: (res) => {
      setFixPreview(null);
      qc.invalidateQueries({ queryKey: ['shop-price-board'] });
      message.success(`已微升中促 ${res.changed} 个 SKU, 大促价一分未动`, 3);
    },
    onError: () => message.error('落库失败'),
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
    // ── 报名价模型 (2026-07-03: 大促锚不动, 只动中促) ──
    {
      title: '报名价A', dataIndex: 'report_price', width: 100, align: 'right',
      render: (v: number | null, row) => {
        const ok = row.report_compliant;
        const color = ok === false ? '#dc2626' : '#1a73e8';
        return (
          <div style={{ lineHeight: 1.15 }}>
            <div style={{ fontWeight: 700, color }}>{yuan(v)}</div>
            {ok === false
              ? <div style={{ fontSize: 11, color: '#dc2626' }}>需微升中促</div>
              : ok === true ? <div style={{ fontSize: 11, color: '#16a34a' }}>✓合规</div> : null}
          </div>
        );
      },
    },
    { title: '618报名价', dataIndex: 'report_price_618', width: 92, align: 'right',
      render: (v: number | null) => <span style={{ color: '#7c3aed', fontWeight: 500 }}>{yuan(v)}</span> },
    { title: '空档价红线', dataIndex: 'gap_floor', width: 96, align: 'right',
      render: (v: number | null) => <span style={{ color: '#94a3b8' }}>{yuan(v)}</span> },
    {
      title: '合规 g', dataIndex: 'compliance_g', width: 92, align: 'center',
      render: (v: number | null, row) => {
        if (v == null) return <span style={{ color: '#cbd5e1' }}>—</span>;
        const ok = row.report_compliant !== false;
        return <Tag color={ok ? 'green' : 'red'}>{Number(v).toFixed(4)}</Tag>;
      },
    },
    rateCol('小促单品立减', 'shop_promo_rate'),
    rateCol('中促单品立减', 'mid_shop_rate'),
    rateCol('大促单品立减', 'big_shop_rate'),
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>改价台</Typography.Title>
      <Alert
        type="info" showIcon
        message="改「基数」(0.86 / 0.88 / 0.9 这种除数), 回车即存 → 促价 = 进位到10(成本 ÷ 基数) 自动算(和你 Excel List 表一致, 以 0 结尾)。三档促价存的都是「店铺实收」(88VIP佣金后到账), 不是买家到手价。"
        description="看不懂这些列(报名价A / 618报名价 / 空档价红线 / 合规 g / 中促基数为什么空)? 点开下面的「📖 名词说明」, 每一项都有大白话解释。"
      />
      <Collapse
        size="small"
        items={[
          {
            key: 'mid',
            label: '📖 ① 中促基数为什么很多是空的? 中促 / 小促还有用吗?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>中促基数空着 = 故意锁定, 别去补。</b>「一键微升中促合规」把这些 SKU 的<b>中促价锁死在了刚好合规的精确值</b>(如 ¥532、¥1319.32), 不再由基数派生。你一旦填回中促基数, 系统重算会把中促价拉回基数算的值, <b>合规就被冲掉</b>。没被动过的 SKU(如柚色餐边柜 0.78)中促基数还在, 照常派生。</p>
                <p style={helpP}><b>小促基数 / 大促基数 照常用</b>, 正常派生小促价 / 大促价。</p>
                <p style={helpP}><b>中促、小促这两档没废, 是角色变了:</b> 以前是你分别报给淘宝的价; 现在你只报<b>一个报名价A</b>, 平台按场次自动打折。中促/小促现在的用途 = ① 算报名价A + 判合规的锚 ② 空档期底线(空档价红线 = 中促到手)③ 小促 = 空档/小活动用单品立减做的浅折价。</p>
              </div>
            ),
          },
          {
            key: 'report',
            label: '📖 ② 报名价A 是什么? 为什么和 618 报名价不一样?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>报名价A = 你填进淘宝「超级立减」报名表的那个数</b>, 不是买家看到的价。淘宝在这个数上按场次力度打折, <b>买家到手 = 报名价A ×(1 − 场次力度)</b>。</p>
                <p style={helpP}>报名价A = <b>大促到手 ÷ 0.88</b>(0.88 = 1 − 大促力度12%)。锚在大促, 因为大促是你最深、也是绝不能动的那档; 这样大促场(打12%)买家正好落到大促到手价。</p>
                <p style={helpP}><b>618 报名价更高的原因:</b> 618/双11 平台折扣更深(15% vs 大促12%)。要让买家在两种场次<b>都落到同一个大促到手价</b>, 越深的场越要报高一点去抵 —— 大促场报 ÷0.88、618 场报 ÷0.85(更高)。<b>两个数不同, 纯粹是两个场次打折力度不同, 不是算错。</b></p>
                <p style={helpP}>想「一个报名价走天下」也行 —— 只有各场力度一样时两个才相等。三档力度(中促10% / 大促12% / 618 15%)在右上角<b>「报价参数设置」可调</b>; 把 618 改成 12%, 两个报名价就一样了。</p>
              </div>
            ),
          },
          {
            key: 'terms',
            label: '📖 ③ 空档价红线 / 合规 g / 单品立减系数 是什么?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>空档价红线 = 中促到手价。</b> 没活动的空档期, 你用单品立减做价<b>不能低于这条线</b>; 否则砸穿淘宝近15天最低价线, 下个大活动被冷却 / 报不进(要15天才洗回)。就是空档期你能做的最低价。</p>
                <p style={helpP}><b>合规 g = 中促到手 ÷ 大促到手。</b> 判断「你的中促够不够高, 让同一个报名价A 在中促场也报得进」。需 <b>g ≥ 1.0227</b>(=0.90/0.88)才合规(绿); 低于就红 = 中促做太低、报名价在中促场会破线 → 点「一键微升中促合规」把中促抬到刚好 g=1.0227(<b>大促不动</b>)。</p>
                <p style={helpP}><b>最右三列「单品立减系数」</b> = 空档期用单品立减把价做到目标水平时, 反推出来的那个系数(直接填淘宝单品立减用)。</p>
              </div>
            ),
          },
          {
            key: 'big',
            label: '📖 ④ 我的大促价会被改吗?',
            children: (
              <div style={helpBox}>
                <p style={helpP}><b>不会。</b>「一键微升中促合规」只抬<b>中促</b>, 大促价一分不动(落库前后逐条校验大促未变, 变了整体回滚)。你改基数时也只按你改的那一档联动, 不碰其它档。</p>
              </div>
            ),
          },
        ]}
      />
      {/* 一键微升中促合规: 先 dry-run 拉不合规清单前后对比(大促价一分不动), 确认后才落库 */}
      <Space wrap style={{ background: '#fff7ed', padding: '8px 12px', borderRadius: 8, width: '100%' }}>
        <span style={{ fontWeight: 500, color: '#c2410c' }}>报名价合规：</span>
        <Button danger onClick={() => dryRunMut.mutate()} loading={dryRunMut.isPending}>
          一键微升中促合规（大促价不动）
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          扫全表, 把「中促到手÷大促到手 &lt; 0.90/0.88」的 SKU 抬中促令报名价能在中促场报得进; 点开先看清单再落库。
        </Typography.Text>
      </Space>
      <Input.Search
        placeholder="按 产品名 / 编码 / SKU 搜 (先搜到再改)" allowClear
        style={{ maxWidth: 360 }} onSearch={setQ}
      />
      {/* 批量改基数: 筛选 → 全选/勾选 → 填一次基数 → 一键套用 (比逐个点快, 还能跨页) */}
      <Space wrap style={{ background: '#f5f7fa', padding: '8px 12px', borderRadius: 8, width: '100%' }}>
        <span style={{ fontWeight: 500 }}>批量改基数：</span>
        <InputNumber value={bSmall} onChange={(x) => setBSmall(x as number)} placeholder="小促基数"
          controls={false} min={0.01} max={5} step={0.01} style={{ width: 104 }} />
        <InputNumber value={bMid} onChange={(x) => setBMid(x as number)} placeholder="中促基数"
          controls={false} min={0.01} max={5} step={0.01} style={{ width: 104 }} />
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
        scroll={{ x: 1300 }}
      />

      {/* 微升中促 dry-run 验算弹窗: 落库前给你看清单(大促价一分不动) */}
      <Modal
        open={!!fixPreview}
        title="微升中促合规 — 验算清单（落库前核对）"
        width={860}
        onCancel={() => setFixPreview(null)}
        footer={[
          <Button key="cancel" onClick={() => setFixPreview(null)}>取消（不落库）</Button>,
          <Popconfirm
            key="apply"
            title={`确认微升 ${fixPreview?.changed ?? 0} 个 SKU 的中促价？大促价一分不动。`}
            onConfirm={() => applyFixMut.mutate()} okText="确认落库" cancelText="再想想"
            disabled={!fixPreview?.changed}
          >
            <Button type="primary" danger loading={applyFixMut.isPending} disabled={!fixPreview?.changed}>
              确认落库（微升 {fixPreview?.changed ?? 0} 个，大促不动）
            </Button>
          </Popconfirm>,
        ]}
      >
        {fixPreview && (
          <>
            <Alert
              type={fixPreview.changed ? 'warning' : 'success'} showIcon style={{ marginBottom: 12 }}
              message={fixPreview.changed
                ? `扫描 ${fixPreview.scanned} 个 SKU，其中 ${fixPreview.changed} 个不合规需微升中促（大促价全部不动）。`
                : `扫描 ${fixPreview.scanned} 个 SKU，全部已合规，无需改动。`}
            />
            <Table<FixMidComplianceChange>
              rowKey="sku_code" size="small" dataSource={fixPreview.changes}
              pagination={{ pageSize: 12, showTotal: (t) => `共 ${t} 条` }}
              scroll={{ y: 360 }}
              columns={[
                { title: '产品', dataIndex: 'product_name', ellipsis: true,
                  render: (v: string, r) => <div><div>{v || '(未命名)'}</div>
                    <span style={{ fontSize: 12, color: '#94a3b8' }}>{r.product_code} · {r.sku || '默认'}</span></div> },
                { title: '大促价(不变)', dataIndex: 'big_promo', width: 110, align: 'right',
                  render: (v: number) => <span style={{ color: '#16a34a', fontWeight: 600 }}>{yuan(v)}</span> },
                { title: '中促价 前→后', width: 150, align: 'right',
                  render: (_: unknown, r) => (
                    <span><span style={{ color: '#94a3b8' }}>{yuan(r.mid_before)}</span>
                      <span style={{ color: '#dc2626' }}> → {yuan(r.mid_after)}</span></span>) },
                { title: 'g 现值 / 需达', width: 130, align: 'center',
                  render: (_: unknown, r) => (
                    <span><Tag color="red">{r.g_before.toFixed(4)}</Tag>→<Tag color="green">{r.g_min.toFixed(4)}</Tag></span>) },
              ]}
            />
          </>
        )}
      </Modal>
    </Space>
  );
}
