/**
 * 定制单核对 (订单) — 按订单备注「推演」工厂成本, 仅供参考。
 * 工厂实际成本填入后全覆盖推演; 复杂备注走「预留外部 API」或落「需系统运算(1.1)」。
 * 用户拍板 2026-06-17。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Input, Modal, Popconfirm, Segmented, Space, Statistic, Table, Tag,
  Tooltip, Typography, message,
} from 'antd';
import { SettingOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CustomReconcileRow, applyProjectedCost, fetchCustomReconcile, getReconApiUrl, putReconApiUrl,
} from '../api/orders';

const { Title, Text, Paragraph } = Typography;

const yuan = (v: number | null | undefined) => (v == null ? '—' : `¥${v.toFixed(2)}`);

// 计算方式来源 → 颜色
const METHOD_COLOR: Record<string, string> = {
  factory: 'green', surcharge: 'cyan', socket: 'blue', percent: 'geekblue',
  amount: 'purple', external: 'magenta', manual: 'red',
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

  const openApi = async () => {
    try {
      const r = await getReconApiUrl();
      setApiUrl(r.url || '');
    } catch { setApiUrl(''); }
    setApiOpen(true);
  };
  const saveApi = async () => {
    try {
      await putReconApiUrl(apiUrl.trim());
      message.success('预留 API 已保存');
      setApiOpen(false);
      qc.invalidateQueries({ queryKey: ['custom-reconcile'] });
    } catch (e: any) { message.error(e?.response?.data?.detail ?? '保存失败'); }
  };

  const cols: ColumnsType<CustomReconcileRow> = [
    { title: '订单号', dataIndex: 'order_no', width: 165, fixed: 'left',
      render: (v: string) => <Text copyable style={{ fontSize: 12 }}>{v}</Text> },
    { title: '产品', dataIndex: 'product_name', ellipsis: true, width: 150,
      render: (v: string | null, r) => v ?? r.product_code ?? '—' },
    { title: '实付', dataIndex: 'paid_amount', width: 90, align: 'right', render: yuan },
    { title: '备注 (定制需求)', dataIndex: 'remark', ellipsis: true,
      render: (v: string) => v ? <Tooltip title={v}><span>{v}</span></Tooltip>
        : <Text type="secondary">（拉取订单后显示）</Text> },
    { title: '推演成本', dataIndex: 'projected_cost', width: 100, align: 'right',
      render: (v: number | null, r) => r.actual_cost != null
        ? <Text delete type="secondary">{yuan(v)}</Text>
        : <Text strong>{yuan(v)}</Text> },
    { title: '计算方式', dataIndex: 'method', width: 130,
      render: (v: string, r) => <Tag color={METHOD_COLOR[r.source] ?? 'default'}>{v}</Tag> },
    { title: '推演毛利', dataIndex: 'projected_margin', width: 100, align: 'right',
      render: (v: number | null) => v == null ? '—'
        : <Text type={v >= 0 ? 'success' : 'danger'}>{yuan(v)}</Text> },
    { title: '工厂成本', dataIndex: 'actual_cost', width: 95, align: 'right',
      render: (v: number | null) => v == null
        ? <Text type="secondary">未填</Text>
        : <Tag color="green">{yuan(v)}</Tag> },
    {
      title: '操作', width: 110, fixed: 'right',
      render: (_: unknown, r) => {
        if (r.actual_cost != null) return <Text type="secondary">工厂成本已覆盖</Text>;
        if (r.projected_cost == null) return <Tag color="red">需系统运算</Tag>;
        return (
          <Popconfirm title="把推演成本写回该单(作理论成本)?"
            description="工厂实际成本到位后会再覆盖它。" onConfirm={() => applyMut.mutate(r.order_id)}>
            <Button size="small" type="link" loading={applyMut.isPending}>写回推演</Button>
          </Popconfirm>
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
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>刷新</Button>
          <Tooltip title="复杂备注交给外部服务推演(未来可接定制报价v1.1/AI); 留空则复杂单落『需系统运算』">
            <Button icon={<SettingOutlined />} onClick={openApi}>
              预留 API {data?.external_api_configured ? <Tag color="green" style={{ marginLeft: 4 }}>已配</Tag> : null}
            </Button>
          </Tooltip>
        </Space>
      </Space>

      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        message="推演成本仅供参考 — 工厂实际成本填入后全覆盖"
        description={
          <Paragraph style={{ marginBottom: 0 }}>
            按订单备注推演工厂成本: <b>插座</b>→物料库 {data?.socket_material_code ?? 'AC-1007'} 单价；
            <b>写了金额</b>→按金额；<b>写了百分比</b>→实付×比例；<b>复杂的</b>→走「预留 API」或落「需系统运算(1.1)」。
            这里<b>只展示推演</b>，不直接入账；点「写回推演」才会把它写成理论成本（工厂成本到位后再覆盖）。
          </Paragraph>
        }
      />

      <Space size="large" style={{ marginBottom: 12 }}>
        <Statistic title="定制单" value={data?.count ?? 0} />
        <Statistic title="需系统运算(复杂)" value={data?.needs_compute_count ?? 0}
          valueStyle={{ color: (data?.needs_compute_count ?? 0) > 0 ? '#cf1322' : '#3f8600' }} />
      </Space>

      <Card size="small">
        <Table<CustomReconcileRow>
          rowKey="order_id" size="small" loading={isLoading}
          columns={cols} dataSource={data?.rows ?? []}
          scroll={{ x: 1200 }}
          pagination={{ pageSize: 50, showSizeChanger: true }}
        />
      </Card>

      <Modal title="预留外部解析 API (复杂备注用)" open={apiOpen}
        onCancel={() => setApiOpen(false)} onOk={saveApi}>
        <Paragraph type="secondary">
          复杂/不规则备注可交给一个外部服务推演成本。系统会 POST
          <code> {'{order_no, paid_amount, remark, product_name, sku}'} </code>
          ，期望返回 <code>{'{cost, method, detail}'}</code>。留空 = 关闭(复杂单落「需系统运算」)。
        </Paragraph>
        <Input placeholder="http://… (留空关闭)" value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} />
      </Modal>
    </div>
  );
}
