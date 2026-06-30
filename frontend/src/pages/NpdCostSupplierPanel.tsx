import { useState } from 'react';
import {
  Card, Tag, Typography, Space, InputNumber, Input, Button, Table, message, Checkbox,
} from 'antd';
import {
  saveNpdCostGate, addNpdCraftIssue, addNpdSupplier, type NpdProjectDetail,
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

  return (
    <>
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
