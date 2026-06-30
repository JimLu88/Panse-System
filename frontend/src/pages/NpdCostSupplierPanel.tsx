import { useState } from 'react';
import {
  Card, Tag, Typography, Space, InputNumber, Input, Button, Table, message, Checkbox,
  Modal, Popconfirm,
} from 'antd';
import {
  saveNpdCostGate, addNpdCraftIssue, addNpdSupplier,
  addNpdBomLine, deleteNpdBomLine, materializeNpdProject,
  type NpdProjectDetail,
} from '../api/client';

export default function NpdCostSupplierPanel(
  { detail, projectId, onChange }: { detail: NpdProjectDetail; projectId: number; onChange: () => void },
) {
  const cg = detail.cost_gate;
  const [proto, setProto] = useState<string>(cg?.prototype_cost ?? '');
  const [est, setEst] = useState<string>(cg?.est_mass_cost ?? '');
  const [savingCg, setSavingCg] = useState(false);
  const verdict = cg?.verdict;
  const verdictTag = verdict === 'pass' ? <Tag color="green">通过 ✓</Tag>
    : verdict === 'fail' ? <Tag color="red">未通过 ✗</Tag> : <Tag>待算</Tag>;

  const pct = (v: string | null | undefined) => (v != null ? `${(Number(v) * 100).toFixed(0)}%` : '-');

  const saveCg = async () => {
    setSavingCg(true);
    try {
      await saveNpdCostGate(projectId, {
        prototype_cost: proto === '' ? null : proto,
        est_mass_cost: est === '' ? null : est,
      });
      message.success('已算成本门');
      onChange();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSavingCg(false);
    }
  };

  const [ciTitle, setCiTitle] = useState('');
  const [ciCost, setCiCost] = useState<string>('');
  const addCi = async () => {
    if (!ciTitle.trim()) { message.warning('填问题标题'); return; }
    try {
      await addNpdCraftIssue(projectId, { title: ciTitle.trim(), cost_impact: ciCost === '' ? null : ciCost });
      setCiTitle(''); setCiCost('');
      message.success('已加工艺问题'); onChange();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '失败');
    }
  };

  const [spName, setSpName] = useState('');
  const [spCat, setSpCat] = useState('');
  const [spQuote, setSpQuote] = useState<string>('');
  const [spBackup, setSpBackup] = useState(false);
  const addSp = async () => {
    if (!spName.trim()) { message.warning('填供应商名'); return; }
    try {
      await addNpdSupplier(projectId, {
        supplier_name: spName.trim(), material_category: spCat || null,
        quote_amount: spQuote === '' ? null : spQuote, is_backup: spBackup,
      });
      setSpName(''); setSpCat(''); setSpQuote(''); setSpBackup(false);
      message.success('已加供应商'); onChange();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '失败');
    }
  };

  const proj = detail.project;
  const [bmName, setBmName] = useState('');
  const [bmCode, setBmCode] = useState('');
  const [bmCat, setBmCat] = useState('');
  const [bmUnit, setBmUnit] = useState('');
  const [bmQty, setBmQty] = useState<string>('1');
  const [bmPrice, setBmPrice] = useState<string>('');
  const addBom = async () => {
    if (!bmCode.trim() && !bmName.trim()) { message.warning('填物料名或已有编码'); return; }
    try {
      await addNpdBomLine(projectId, {
        material_code: bmCode.trim() || null, material_name: bmName.trim() || null,
        category: bmCat || null, unit: bmUnit || null,
        qty: bmQty === '' ? 1 : bmQty, unit_price: bmPrice === '' ? null : bmPrice,
        is_new: !bmCode.trim(),
      });
      setBmName(''); setBmCode(''); setBmCat(''); setBmUnit(''); setBmQty('1'); setBmPrice('');
      message.success('已加 BOM 行'); onChange();
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '失败'); }
  };
  const delBom = async (id: number) => {
    try { await deleteNpdBomLine(id); onChange(); }
    catch (e: any) { message.error(e?.response?.data?.detail ?? '删除失败'); }
  };
  const [matOpen, setMatOpen] = useState(false);
  const [brand, setBrand] = useState('');
  const [catCode, setCatCode] = useState('');
  const [materializing, setMaterializing] = useState(false);
  const doMaterialize = async () => {
    if (!/^[A-Za-z]{2}$/.test(brand.trim())) { message.warning('品牌码=2位字母'); return; }
    if (!/^\d{2}$/.test(catCode.trim())) { message.warning('类目码=2位数字'); return; }
    setMaterializing(true);
    try {
      const r = await materializeNpdProject(projectId, { brand: brand.trim(), category_code: catCode.trim() });
      message.success(`已生成产品档案 ${r.product_code} (新建配件 ${r.materials_created} 个)`);
      setMatOpen(false); onChange();
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '生成失败'); }
    finally { setMaterializing(false); }
  };

  return (
    <>
      <Card
        size="small" title="设计 BOM → 自动建档" style={{ marginTop: 16 }}
        extra={proj.product_code
          ? <Tag color="green">已生成 {proj.product_code}</Tag>
          : <Button type="primary" disabled={!detail.bom_lines.length} onClick={() => setMatOpen(true)}>生成产品档案</Button>}
      >
        {!proj.product_code && (
          <Space wrap style={{ marginBottom: 8 }}>
            <Input placeholder="物料名(新配件)" value={bmName} onChange={(e) => setBmName(e.target.value)} style={{ width: 170 }} />
            <Input placeholder="或已有编码" value={bmCode} onChange={(e) => setBmCode(e.target.value)} style={{ width: 110 }} />
            <Input placeholder="分类" value={bmCat} onChange={(e) => setBmCat(e.target.value)} style={{ width: 90 }} />
            <Input placeholder="单位" value={bmUnit} onChange={(e) => setBmUnit(e.target.value)} style={{ width: 64 }} />
            <InputNumber placeholder="数量" min={0} style={{ width: 84 }}
              value={bmQty === '' ? null : Number(bmQty)} onChange={(v) => setBmQty(v == null ? '' : String(v))} />
            <InputNumber placeholder="新配件单价¥" min={0} style={{ width: 130 }}
              value={bmPrice === '' ? null : Number(bmPrice)} onChange={(v) => setBmPrice(v == null ? '' : String(v))} />
            <Button onClick={addBom}>加BOM</Button>
          </Space>
        )}
        <Table size="small" rowKey="id" pagination={false} dataSource={detail.bom_lines}
          locale={{ emptyText: '暂无 BOM,先录设计物料清单' }}
          columns={[
            { title: '物料', dataIndex: 'material_name', render: (v: string, r: any) => v || r.material_code || '-' },
            { title: '编码', dataIndex: 'material_code', width: 110, render: (v: string) => v || <Tag color="orange">新</Tag> },
            { title: '分类', dataIndex: 'category', width: 90 },
            { title: '数量', dataIndex: 'qty', width: 70 },
            { title: '单价', dataIndex: 'unit_price', width: 90 },
            ...(proj.product_code ? [] : [{
              title: '', key: 'del', width: 46,
              render: (_: unknown, r: any) => (
                <Popconfirm title="删除?" onConfirm={() => delBom(r.id)}>
                  <Button type="link" size="small" danger>删</Button>
                </Popconfirm>
              ),
            }]),
          ]} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          生成后: 新配件按单价自动入物料库、建产品+BOM、按 BOM 成本生成定价表草稿(价取价位靶, 到定价页细化)。
        </Typography.Text>
      </Card>

      <Modal title="生成产品档案" open={matOpen} confirmLoading={materializing}
        onOk={doMaterialize} onCancel={() => setMatOpen(false)} destroyOnClose>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">将建 产品+BOM、新配件入库、定价表草稿。产品编码需:</Typography.Text>
          <Input addonBefore="品牌码 2位字母(PS/FG)" value={brand} maxLength={2}
            onChange={(e) => setBrand(e.target.value.toUpperCase())} />
          <Input addonBefore="类目码 2位数字(如33)" value={catCode} maxLength={2}
            onChange={(e) => setCatCode(e.target.value)} />
        </Space>
      </Modal>

      <Card size="small" title="成本门 G3 (量产成本 vs 价位靶)" style={{ marginTop: 16 }}>
        <Space wrap>
          <Typography.Text type="secondary">目标售价 {cg?.target_price ?? detail.project.target_price ?? '-'}</Typography.Text>
          <Typography.Text type="secondary">目标毛利 {pct(cg?.target_margin ?? detail.project.target_margin_rate)}</Typography.Text>
        </Space>
        <Space wrap style={{ marginTop: 8 }}>
          <InputNumber addonBefore="打样成本" min={0} style={{ width: 190 }}
            value={proto === '' ? null : Number(proto)} onChange={(v) => setProto(v == null ? '' : String(v))} />
          <InputNumber addonBefore="量产成本" min={0} style={{ width: 230 }} placeholder="留空=打样+未解决工艺上浮"
            value={est === '' ? null : Number(est)} onChange={(v) => setEst(v == null ? '' : String(v))} />
          <Button type="primary" loading={savingCg} onClick={saveCg}>算 / 保存</Button>
        </Space>
        <div style={{ marginTop: 8 }}>
          结果 {verdictTag}
          {cg?.actual_margin != null && (
            <Typography.Text style={{ marginLeft: 8 }}>实算毛利 {(Number(cg.actual_margin) * 100).toFixed(1)}%</Typography.Text>
          )}
          {verdict === 'fail' && (
            <Typography.Text type="danger" style={{ marginLeft: 8 }}>
              成本超靶 → 不建议加价,扩大供应商池 / 启用后备降本
            </Typography.Text>
          )}
        </div>
      </Card>

      <Card size="small" title="工艺问题台账 (打样成本上浮)" style={{ marginTop: 16 }}>
        <Space wrap style={{ marginBottom: 8 }}>
          <Input placeholder="问题(如 封边开胶需改工艺)" value={ciTitle}
            onChange={(e) => setCiTitle(e.target.value)} style={{ width: 280 }} />
          <InputNumber placeholder="成本上浮¥" min={0} value={ciCost === '' ? null : Number(ciCost)}
            onChange={(v) => setCiCost(v == null ? '' : String(v))} style={{ width: 130 }} />
          <Button onClick={addCi}>添加</Button>
        </Space>
        <Table size="small" rowKey="id" pagination={false} dataSource={detail.craft_issues}
          locale={{ emptyText: '暂无' }}
          columns={[
            { title: '问题', dataIndex: 'title' },
            { title: '成本上浮', dataIndex: 'cost_impact', width: 100 },
            { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => (v === 'solved' ? <Tag color="green">已解决</Tag> : <Tag color="orange">未解决</Tag>) },
            { title: '选定供应商', dataIndex: 'chosen_supplier', width: 130 },
          ]} />
      </Card>

      <Card size="small" title="供应商候选 (≥2家 + 后备对齐工艺)" style={{ marginTop: 16 }}>
        <Space wrap style={{ marginBottom: 8 }}>
          <Input placeholder="供应商名" value={spName} onChange={(e) => setSpName(e.target.value)} style={{ width: 160 }} />
          <Input placeholder="材料类(岩板/五金…)" value={spCat} onChange={(e) => setSpCat(e.target.value)} style={{ width: 150 }} />
          <InputNumber placeholder="报价¥" min={0} value={spQuote === '' ? null : Number(spQuote)}
            onChange={(v) => setSpQuote(v == null ? '' : String(v))} style={{ width: 110 }} />
          <Checkbox checked={spBackup} onChange={(e) => setSpBackup(e.target.checked)}>后备</Checkbox>
          <Button onClick={addSp}>添加</Button>
        </Space>
        <Table size="small" rowKey="id" pagination={false} dataSource={detail.suppliers}
          locale={{ emptyText: '暂无' }}
          columns={[
            { title: '供应商', dataIndex: 'supplier_name' },
            { title: '材料类', dataIndex: 'material_category', width: 110 },
            { title: '报价', dataIndex: 'quote_amount', width: 90 },
            { title: '后备', dataIndex: 'is_backup', width: 60, render: (v: boolean) => (v ? <Tag>后备</Tag> : '') },
            { title: '能解工艺', dataIndex: 'can_solve_craft_issue', width: 80, render: (v: boolean) => (v ? <Tag color="blue">能</Tag> : '') },
          ]} />
      </Card>
    </>
  );
}
