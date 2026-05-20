import { useState } from 'react';
import {
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Material, listMaterials, updateMaterial } from '../api/client';

type FilterKey = 'all' | 'standard' | 'custom';

export default function MaterialsPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [q, setQ] = useState('');
  const [editing, setEditing] = useState<Material | null>(null);
  const [form] = Form.useForm();

  const isCustom = filter === 'custom' ? true : filter === 'standard' ? false : undefined;

  const { data, isLoading } = useQuery({
    queryKey: ['materials', q, isCustom],
    queryFn: () => listMaterials(q || undefined, isCustom),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<Material> }) =>
      updateMaterial(id, patch),
    onSuccess: () => {
      message.success('已保存');
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['materials'] });
    },
  });

  const columns = [
    {
      title: '编码',
      dataIndex: 'code',
      width: 110,
      render: (v: string, row: Material) =>
        row.is_custom ? <Tag color="orange">{v}</Tag> : v,
    },
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { title: '尺寸类型', dataIndex: 'size_type', width: 100 },
    { title: '单位', dataIndex: 'unit', width: 80 },
    {
      title: '价格',
      dataIndex: 'price',
      width: 110,
      render: (v: string | null) =>
        v == null ? <Tag color="red">待补</Tag> : <span>{v}</span>,
    },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, row: Material) => (
        <a
          onClick={() => {
            setEditing(row);
            form.setFieldsValue(row);
          }}
        >
          编辑
        </a>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          物料单价库 (3b)
        </Typography.Title>
        <Space>
          <Input.Search
            placeholder="按编码或名称搜索"
            allowClear
            style={{ width: 280 }}
            onSearch={setQ}
          />
          <Segmented<FilterKey>
            value={filter}
            onChange={(v) => setFilter(v as FilterKey)}
            options={[
              { label: '全部', value: 'all' },
              { label: '标准', value: 'standard' },
              { label: '定制 (AC≥1000)', value: 'custom' },
            ]}
          />
        </Space>
      </Space>

      <Table<Material>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
        size="middle"
      />

      <Modal
        title={editing ? `编辑物料 ${editing.code}` : ''}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        confirmLoading={updateMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => editing && updateMut.mutate({ id: editing.id, patch: v })}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="size_type" label="尺寸类型">
            <Input placeholder="如 组合 / 个数 / 长度" />
          </Form.Item>
          <Form.Item name="unit" label="单位">
            <Input placeholder="如 条/个/套" />
          </Form.Item>
          <Form.Item name="price" label="价格">
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
