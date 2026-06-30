import { useEffect, useState } from 'react';
import { Card, Switch, InputNumber, Button, Table, Tag, message, Space, Typography, Spin } from 'antd';
import {
  getNpdSettings, listNpdStages, updateNpdSettings, editNpdStage,
  type NpdSettings, type NpdStage,
} from '../api/client';

const GROUP_LABEL: Record<string, string> = {
  plan: '立项', design: '设计', sourcing: '寻源', prototype: '打样',
  production: '量产', launch: '上架', review: '复盘',
};

export default function NpdSettingsPanel() {
  const [s, setS] = useState<NpdSettings | null>(null);
  const [stages, setStages] = useState<NpdStage[]>([]);
  const [loading, setLoading] = useState(true);
  const [minSup, setMinSup] = useState<number | null>(null);
  const [thr, setThr] = useState<string>('');

  const load = async () => {
    setLoading(true);
    try {
      const [st, stgs] = await Promise.all([getNpdSettings(), listNpdStages(true)]);
      setS(st); setStages(stgs);
      setMinSup(st.min_supplier_candidates);
      setThr(st.cost_overrun_threshold ?? '');
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '加载失败');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const toggleMP = async (v: boolean) => {
    try {
      await updateNpdSettings({ mass_production_enabled: v });
      message.success(v ? '已开启量产线(显示小批试产/量产阶段)' : '已关闭量产线');
      load();
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '失败'); }
  };
  const saveNums = async () => {
    try {
      const r = await updateNpdSettings({
        min_supplier_candidates: minSup ?? 2,
        cost_overrun_threshold: thr === '' ? 0 : Number(thr),
      });
      setS(r); message.success('已保存');
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '失败'); }
  };
  const saveStage = async (id: number, field: string, val: number, old?: number) => {
    if (val === old) return;
    try {
      await editNpdStage(id, { [field]: val });
      message.success('已更新');
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '失败'); }
  };

  if (loading) return <Spin />;

  const slaCol = (field: 'default_sla_days' | 'warn_days' | 'critical_days', title: string) => ({
    title, dataIndex: field, width: 96,
    render: (v: number, r: NpdStage) => (
      <InputNumber
        size="small" min={0} defaultValue={v} style={{ width: 68 }}
        onBlur={(e) => saveStage(r.id, field, Number((e.target as HTMLInputElement).value), v)}
      />
    ),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small" title="全局设置">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap>
            <Typography.Text strong>生产线(量产阶段)</Typography.Text>
            <Switch checked={!!s?.mass_production_enabled} onChange={toggleMP}
              checkedChildren="开" unCheckedChildren="关" />
            <Typography.Text type="secondary">开启后看板/流程显示「小批试产PVT / 量产」阶段(现状默认关=样品做完直发)</Typography.Text>
          </Space>
          <Space wrap>
            <span>后备供应商最少家数</span>
            <InputNumber min={1} value={minSup ?? undefined} onChange={(v) => setMinSup(v as number)} style={{ width: 90 }} />
            <span>成本上浮告警阈值(%)</span>
            <InputNumber min={0} value={thr === '' ? undefined : Number(thr)}
              onChange={(v) => setThr(v == null ? '' : String(v))} style={{ width: 110 }} />
            <Button type="primary" onClick={saveNums}>保存</Button>
          </Space>
        </Space>
      </Card>

      <Card size="small" title="阶段时间(SLA天数 / 提醒阈值,改完移开光标即存)">
        <Table size="small" rowKey="id" pagination={false} dataSource={stages}
          scroll={{ x: 560 }}
          columns={[
            {
              title: '阶段', dataIndex: 'name',
              render: (v: string, r: NpdStage) => (
                <span>{r.code} {v} {r.is_gate && <Tag color="gold">门</Tag>}
                  {r.requires_mass_production && <Tag color="red">量产</Tag>}</span>
              ),
            },
            { title: '组', dataIndex: 'group', width: 64, render: (g: string) => GROUP_LABEL[g] || g },
            slaCol('default_sla_days', 'SLA天'),
            slaCol('warn_days', 'warn天'),
            slaCol('critical_days', 'critical天'),
          ]} />
      </Card>
    </Space>
  );
}
