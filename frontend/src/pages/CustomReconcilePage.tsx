/**
 * 定制单核对 (订单) — 分级混合推演工厂成本: 规则→本地AI→85%兜底(标红待人工)。
 * 工厂实际成本填入后全覆盖推演; 推演仅展示, 点「写回推演」才写理论成本。用户拍板 2026-06-17。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Input, Modal, Popconfirm, Segmented, Space, Statistic, Table, Tag,
  Tooltip, Typography, message,
} from 'antd';
import { RobotOutlined, SettingOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CustomReconcileRow, aiRecomputeCustom, applyProjectedCost, fetchCustomReconcile,
  getReconApiUrl, putReconApiUrl,
} from '../api/orders';

const { Title, Text, Paragraph } = Typography;

const yuan = (v: number | null | undefined) => (v == null ? '—' : `¥${v.toFixed(2)}`);
const CONF: Record<string, { color: string; label: string }> = {
  high: { color: 'green', label: '🟢高' }, mid: { color: 'orange', label: '🟡中' }, low: { color: 'red', label: '🔴低' },
};

export default function CustomReconcilePage() {
  const qc = useQueryClient();
  const [onlyMissing, setOnlyMissing] = useState(true);
  const [apiOpen, setApiOpen] = useState(false);
  const [apiUrl, setApiUrl] = useState('');

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['custom-reconcile', onlyMissing],
    queryFn: () => fetchCustomReconcile(onlyMissing),
  });

  const applyMut = useMutation({
    mutationFn: (orderId: number) => applyProjectedCost(orderId),
    onSuccess: (r) => {
      message.success(`已写回推演成本 ¥${r.written_theoretical_cost.toFixed(2)} (${r.method})`);
      qc.invalidateQueries({ queryKey: ['custom-reconcile'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '写回失败'),
  });

  // 一键 AI 重算兜底(后台): 把 85% 兜底的用本地 AI 重估并写回(规则算出的不动)。45 单跑 AI 要几分钟 → 异步。
  const recomputeMut = useMutation({
    mutationFn: aiRecomputeCustom,
    onSuccess: (r) => message.success(r.note ?? 'AI 重算已在后台开始, 完成后点刷新'),
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'AI 重算失败'),
  });

  const openApi = async () => {
    try { const r = await getReconApiUrl(); setApiUrl(r.url || ''); } catch { setApiUrl(''); }
    setApiOpen(true);
  };
  const saveApi = async () => {
    try {
      await putReconApiUrl(apiUrl.trim());
      message.success('本地 AI 地址已保存');
      setApiOpen(false);
      qc.invalidateQueries({ queryKey: ['custom-reconcile'] });
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '保存失败'); }
  };

  const cols: ColumnsType<CustomReconcileRow> = [
    { title: '订单号', dataIndex: 'order_no', width: 165, fixed: 'left',
      render: (v: string) => <Text copyable style={{ fontSize: 12 }}>{v}</Text> },
    { title: '产品', dataIndex: 'product_name', ellipsis: true, width: 140,
      render: (v: string | null, r) => v ?? r.product_code ?? '—' },
    { title: '实付', dataIndex: 'paid_amount', width: 85, align: 'right', render: yuan },
    { title: '备注 (定制需求)', dataIndex: 'remark', ellipsis: true,
      render: (v: string) => v ? <Tooltip title={v}><span>{v}</span></Tooltip>
        : <Text type="secondary">（无备注）</Text> },
    { title: '推演成本', dataIndex: 'projected_cost', width: 100, align: 'right',
      render: (v: number | null, r) => {
        if (r.actual_cost != null) return <Text delete type="secondary">{yuan(v)}</Text>;
        const danger = r.confidence === 'low';
        return <Text strong type={danger ? 'danger' : undefined}>{yuan(v)}</Text>;
      } },
    { title: '计算方式', dataIndex: 'method', width: 150,
      render: (v: string, r) => <Tag color={CONF[r.confidence]?.color ?? 'default'}>{v}</Tag> },
    { title: '置信', dataIndex: 'confidence', width: 70,
      render: (c: string) => <Tag color={CONF[c]?.color}>{CONF[c]?.label ?? c}</Tag> },
    { title: '推演毛利', dataIndex: 'projected_margin', width: 95, align: 'right',
      render: (v: number | null) => v == null ? '—'
        : <Text type={v >= 0 ? 'success' : 'danger'}>{yuan(v)}</Text> },
    { title: '工厂成本', dataIndex: 'actual_cost', width: 90, align: 'right',
      render: (v: number | null) => v == null
        ? <Text type="secondary">未填</Text> : <Tag color="green">{yuan(v)}</Tag> },
    {
      title: '操作', width: 130, fixed: 'right',
      render: (_: unknown, r) => {
        if (r.actual_cost != null) return <Text type="secondary">工厂成本已覆盖</Text>;
        return (
          <Space size={4}>
            {r.needs_review && <Tag color="red">待人工</Tag>}
            <Popconfirm title="把推演成本写回该单(作理论成本)?"
              description="工厂实际成本到位后会再覆盖它。" onConfirm={() => applyMut.mutate(r.order_id)}>
              <Button size="small" type="link" loading={applyMut.isPending}>写回推演</Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }}>
        <Title level={4} style={{ margin: 0 }}>定制单核对</Title>
        <Space>
          <Segmented value={onlyMissing ? 'missing' : 'all'}
            onChange={(v) => setOnlyMissing(v === 'missing')}
            options={[{ label: '只看缺工厂成本', value: 'missing' }, { label: '全部定制单', value: 'all' }]} />
          <Popconfirm title="一键 AI 重算兜底并写回?(后台跑)"
            description="把 85% 兜底的定制单(改尺寸/材质等)用本地大模型 qwen2.5vl 重估、写回理论成本(规则已算出的不动)。45 单约 1-3 分钟, 后台异步; 完成后点刷新。PC/Ollama 没开会飞书报警并维持 85%。"
            onConfirm={() => recomputeMut.mutate()}>
            <Button type="primary" icon={<RobotOutlined />} loading={recomputeMut.isPending}>AI 重算兜底(后台写回)</Button>
          </Popconfirm>
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>刷新</Button>
          <Tooltip title="本地 AI 模型地址 (Ollama OpenAI-compat); 留空用默认 PC Ollama">
            <Button icon={<SettingOutlined />} onClick={openApi}>本地AI地址</Button>
          </Tooltip>
        </Space>
      </Space>

      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        message="分级混合推演 — 工厂实际成本填入后全覆盖"
        description={
          <Paragraph style={{ marginBottom: 0 }}>
            规则先算: <b>写明成本/百分比</b>→直接取；<b>插座</b>→{data?.socket_material_code ?? 'AC-1007'}；
            都不中 → <b style={{ color: '#cf1322' }}>实付×85% 兜底(标红, 待人工/工厂价覆盖)</b>。
            复杂的（改尺寸/材质）点「<b>AI 重算兜底(后台写回)</b>」交本地大模型 qwen2.5vl 估算并写回(几分钟, 完成后刷新)；PC/Ollama 没开会飞书报警并维持 85%。
            推演<b>只展示不入账</b>，点「写回推演」才写成理论成本。
          </Paragraph>
        }
      />

      <Space size="large" style={{ marginBottom: 12 }}>
        <Statistic title="定制单" value={data?.count ?? 0} />
        <Statistic title="待人工核价 (85%兜底·标红)" value={data?.low_confidence_count ?? 0}
          valueStyle={{ color: (data?.low_confidence_count ?? 0) > 0 ? '#cf1322' : '#3f8600' }} />
        <Statistic title="本地AI已估算(写回)" value={data?.ai_count ?? 0}
          valueStyle={{ color: (data?.ai_count ?? 0) > 0 ? '#3f8600' : undefined }} />
      </Space>

      <Card size="small">
        <Table<CustomReconcileRow>
          rowKey="order_id" size="small" loading={isLoading}
          columns={cols} dataSource={data?.rows ?? []}
          scroll={{ x: 1250 }}
          pagination={{ pageSize: 50, showSizeChanger: true }}
        />
      </Card>

      <Modal title="本地 AI 模型地址" open={apiOpen} onCancel={() => setApiOpen(false)} onOk={saveApi}>
        <Paragraph type="secondary">
          复杂定制单用本地大模型(qwen2.5vl, Ollama)估算成本。这里填 Ollama 的 OpenAI 兼容地址，
          如 <code>http://192.168.31.91:11434/v1</code>。留空用默认(取数 PC)。
        </Paragraph>
        <Input placeholder="http://192.168.31.91:11434/v1 (留空=默认)" value={apiUrl}
          onChange={(e) => setApiUrl(e.target.value)} />
      </Modal>
    </div>
  );
}
