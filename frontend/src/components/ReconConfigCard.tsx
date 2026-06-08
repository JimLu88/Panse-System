/**
 * 对账/利润 口径设置 — 容差(百分比/最小金额) + 补贴税率 + 软件服务费率。
 * 全局默认 + 按店铺/渠道覆盖(未来渠道有差异时分别填)。费率以 % 展示, 存储为小数。
 */
import { useEffect, useState } from 'react';
import { Button, Card, Col, Input, InputNumber, Row, Space, Table, Typography, message } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReconConfig, updateReconConfig } from '../api/settlements';

const pct = (v: number | undefined) => (v == null ? 0 : Math.round(v * 1000) / 10); // 0.02 → 2(%)
const frac = (v: number) => Math.round((v / 100) * 100000) / 100000;               // 2(%) → 0.02

interface ShopRow { key: string; shop: string; subsidy_tax_rate: number; software_fee_rate: number }

export default function ReconConfigCard() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['recon-config'], queryFn: fetchReconConfig });

  const [taxPct, setTaxPct] = useState(2);
  const [feePct, setFeePct] = useState(0.6);
  const [tolPct, setTolPct] = useState(0.5);
  const [tolFloor, setTolFloor] = useState(5);
  const [shops, setShops] = useState<ShopRow[]>([]);

  useEffect(() => {
    if (!data) return;
    const d = data.defaults || {};
    setTaxPct(pct(d.subsidy_tax_rate));
    setFeePct(pct(d.software_fee_rate));
    setTolPct(pct(d.tolerance_pct));
    setTolFloor(d.tolerance_floor ?? 5);
    setShops(Object.entries(data.by_shop || {}).map(([shop, r], i) => ({
      key: `${shop}-${i}`, shop,
      subsidy_tax_rate: pct(r.subsidy_tax_rate ?? d.subsidy_tax_rate),
      software_fee_rate: pct(r.software_fee_rate ?? d.software_fee_rate),
    })));
  }, [data]);

  const save = useMutation({
    mutationFn: () => {
      const defaults = {
        subsidy_tax_rate: frac(taxPct), software_fee_rate: frac(feePct),
        tolerance_pct: frac(tolPct), tolerance_floor: tolFloor,
      };
      const by_shop: Record<string, Record<string, number>> = {};
      for (const s of shops) {
        if (!s.shop.trim()) continue;
        by_shop[s.shop.trim()] = {
          subsidy_tax_rate: frac(s.subsidy_tax_rate), software_fee_rate: frac(s.software_fee_rate),
        };
      }
      return updateReconConfig(defaults, by_shop);
    },
    onSuccess: () => { message.success('口径已保存,对账/利润即时生效'); qc.invalidateQueries({ queryKey: ['recon-config'] }); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  return (
    <Card size="small" title="对账 / 利润 口径设置（全局默认 + 按店铺覆盖）">
      <Row gutter={12} align="bottom">
        <Col><span>补贴税率</span><br /><InputNumber value={taxPct} min={0} max={20} step={0.1} addonAfter="%" onChange={(v) => setTaxPct(v ?? 0)} /></Col>
        <Col><span>软件服务费率</span><br /><InputNumber value={feePct} min={0} max={20} step={0.1} addonAfter="%" onChange={(v) => setFeePct(v ?? 0)} /></Col>
        <Col><span>对账容差·百分比</span><br /><InputNumber value={tolPct} min={0} max={20} step={0.1} addonAfter="%" onChange={(v) => setTolPct(v ?? 0)} /></Col>
        <Col><span>对账容差·最小额</span><br /><InputNumber value={tolFloor} min={0} step={1} addonAfter="元" onChange={(v) => setTolFloor(v ?? 0)} /></Col>
      </Row>

      <Typography.Paragraph type="secondary" style={{ margin: '12px 0 6px' }}>
        按店铺/渠道覆盖（不填则用上面的全局值；未来某渠道税费不同时在此加一行）：
      </Typography.Paragraph>
      <Table<ShopRow>
        rowKey="key" size="small" pagination={false} dataSource={shops}
        columns={[
          { title: '店铺/渠道', dataIndex: 'shop', render: (v, r) => (
            <Input value={v} placeholder="店铺名" style={{ width: 160 }}
              onChange={(e) => setShops((p) => p.map((x) => x.key === r.key ? { ...x, shop: e.target.value } : x))} /> ) },
          { title: '补贴税率%', dataIndex: 'subsidy_tax_rate', width: 130, render: (v, r) => (
            <InputNumber value={v} min={0} max={20} step={0.1} onChange={(val) => setShops((p) => p.map((x) => x.key === r.key ? { ...x, subsidy_tax_rate: val ?? 0 } : x))} /> ) },
          { title: '软件费率%', dataIndex: 'software_fee_rate', width: 130, render: (v, r) => (
            <InputNumber value={v} min={0} max={20} step={0.1} onChange={(val) => setShops((p) => p.map((x) => x.key === r.key ? { ...x, software_fee_rate: val ?? 0 } : x))} /> ) },
          { title: '', width: 50, render: (_, r) => (
            <Button size="small" type="text" danger icon={<DeleteOutlined />}
              onClick={() => setShops((p) => p.filter((x) => x.key !== r.key))} /> ) },
        ]}
      />
      <Space style={{ marginTop: 10 }}>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setShops((p) => [...p, { key: `new-${p.length}-${Math.round(taxPct)}`, shop: '', subsidy_tax_rate: taxPct, software_fee_rate: feePct }])}>加店铺覆盖</Button>
        <Button type="primary" size="small" loading={save.isPending} onClick={() => save.mutate()}>保存口径</Button>
      </Space>
    </Card>
  );
}
