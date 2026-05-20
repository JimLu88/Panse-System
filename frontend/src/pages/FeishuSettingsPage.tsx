import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FeishuBinding, createFeishuBinding, feishuStatus, listFeishuBindings } from '../api/client';

const SYSTEM_TABLES = [
  'materials',
  'products',
  'bom_lines',
  'part_inventory',
  'product_inventory',
  'orders',
  'alipay_flows',
];

export default function FeishuSettingsPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const { data: bindings, isLoading } = useQuery({
    queryKey: ['feishu-bindings'],
    queryFn: listFeishuBindings,
  });
  const { data: status } = useQuery({ queryKey: ['feishu-status'], queryFn: feishuStatus });

  const createMut = useMutation({
    mutationFn: createFeishuBinding,
    onSuccess: () => {
      message.success('绑定已创建');
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['feishu-bindings'] });
      qc.invalidateQueries({ queryKey: ['feishu-status'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const columns = [
    { title: '系统表', dataIndex: 'system_table', width: 180 },
    { title: '飞书 App Token', dataIndex: 'feishu_app_token', ellipsis: true },
    { title: '飞书 Table ID', dataIndex: 'feishu_table_id', width: 200 },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 140,
      render: (v: string) =>
        ({
          in: <Tag color="green">仅入 (飞书→系统)</Tag>,
          out: <Tag color="blue">仅出 (系统→飞书)</Tag>,
          bidirectional: <Tag color="purple">双向</Tag>,
        }[v] || v),
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 80,
      render: (v: boolean) => <Switch checked={v} disabled />,
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          飞书同步设置
        </Typography.Title>
        <Button type="primary" onClick={() => setOpen(true)}>
          新增绑定
        </Button>
      </Space>

      <Alert
        type="warning"
        showIcon
        message="同步引擎骨架"
        description={
          <span>
            Phase 1 当前仅支持绑定关系配置；实际的双向 pull/push 还在 plan §5.3 计划中实现。
            可以先把表绑定关系填好，等引擎开通后会按这里的配置跑。
          </span>
        }
      />

      <Typography.Title level={5} style={{ marginBottom: 0 }}>
        当前绑定
      </Typography.Title>
      <Table<FeishuBinding>
        rowKey="id"
        loading={isLoading}
        dataSource={bindings}
        columns={columns as any}
        size="middle"
        pagination={false}
      />

      <Typography.Title level={5} style={{ marginBottom: 0 }}>
        同步状态
      </Typography.Title>
      <Table
        rowKey={(r) => `${r.system_table}-${r.feishu_table_id}`}
        dataSource={status}
        size="small"
        pagination={false}
        columns={[
          { title: '系统表', dataIndex: 'system_table' },
          { title: '飞书 Table ID', dataIndex: 'feishu_table_id' },
          { title: '方向', dataIndex: 'direction' },
          { title: '已映射行数', dataIndex: 'mapped_rows' },
          {
            title: '启用',
            dataIndex: 'enabled',
            render: (v: boolean) => (v ? <Tag color="green">on</Tag> : <Tag>off</Tag>),
          },
        ]}
      />

      <Modal
        title="新增飞书绑定"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => createMut.mutate(v)}
          initialValues={{ direction: 'bidirectional', enabled: false }}
        >
          <Form.Item name="system_table" label="系统表" rules={[{ required: true }]}>
            <Select options={SYSTEM_TABLES.map((t) => ({ value: t, label: t }))} showSearch />
          </Form.Item>
          <Form.Item name="feishu_app_token" label="飞书 App Token" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="feishu_table_id" label="飞书 Table ID" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="direction" label="同步方向">
            <Select
              options={[
                { value: 'in', label: '仅入 (飞书 → 系统)' },
                { value: 'out', label: '仅出 (系统 → 飞书)' },
                { value: 'bidirectional', label: '双向' },
              ]}
            />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
