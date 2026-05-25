import { useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  PartInventory,
  addPartInventoryRow,
  listPartInventory,
  updatePartInventory,
} from '../api/client';
import { FirstVisitTip } from '../components/FirstVisitTip';

export default function PartInventoryPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['part-inventory'],
    queryFn: listPartInventory,
  });
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [edits, setEdits] = useState<Record<number, { physical_qty?: number; locked_qty?: number }>>({});
  const [savingId, setSavingId] = useState<number | null>(null);

  function setEdit(id: number, patch: { physical_qty?: number; locked_qty?: number }) {
    setEdits(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  async function saveRow(id: number) {
    const patch = edits[id];
    if (!patch) return;
    setSavingId(id);
    try {
      await updatePartInventory(id, patch);
      message.success('库存已更新');
      qc.invalidateQueries({ queryKey: ['part-inventory'] });
      setEdits(prev => { const n = { ...prev }; delete n[id]; return n; });
    } catch {
      message.error('保存失败');
    } finally {
      setSavingId(null);
    }
  }

  const addMut = useMutation({
    mutationFn: addPartInventoryRow,
    onSuccess: (res) => {
      if (res.material_created) {
        Modal.success({
          title: '自动建档定制物料',
          content: (
            <div>
              <p>
                录入名称在物料库中不存在，已自动分配编码{' '}
                <Tag color="orange">{res.material_code}</Tag>
              </p>
              <p>新建物料名：{res.material_name}</p>
              <p style={{ color: '#888' }}>
                价格 / 单位 / 尺寸类型已留空，请到「异常处理」页补全。
              </p>
            </div>
          ),
        });
      } else {
        message.success(`已加入 ${res.material_code} 的库存`);
      }
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['part-inventory'] });
      qc.invalidateQueries({ queryKey: ['materials'] });
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
    onError: (e: any) => {
      message.error(e?.response?.data?.detail ?? '录入失败');
    },
  });

  const columns = [
    { title: '仓库', dataIndex: 'warehouse', width: 110 },
    {
      title: '物料编码',
      dataIndex: 'material_code',
      width: 110,
      render: (v: string) =>
        v.startsWith('AC-') && parseInt(v.slice(3), 10) >= 1000 ? (
          <Tag color="orange">{v}</Tag>
        ) : (
          v
        ),
    },
    { title: '规格', dataIndex: 'spec', ellipsis: true },
    { title: '单位', dataIndex: 'unit', width: 70 },
    {
      title: '物理库存',
      dataIndex: 'physical_qty',
      width: 100,
      render: (v: number, row: PartInventory) => (
        <InputNumber
          size="small"
          min={0}
          value={edits[row.id]?.physical_qty ?? v}
          onChange={(val) => setEdit(row.id, { physical_qty: val ?? 0 })}
          style={{ width: 80 }}
        />
      ),
    },
    {
      title: '锁定',
      dataIndex: 'locked_qty',
      width: 90,
      render: (v: number, row: PartInventory) => (
        <InputNumber
          size="small"
          min={0}
          value={edits[row.id]?.locked_qty ?? v}
          onChange={(val) => setEdit(row.id, { locked_qty: val ?? 0 })}
          style={{ width: 70 }}
        />
      ),
    },
    { title: '可用', dataIndex: 'available_qty', width: 70 },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
    {
      title: '操作',
      width: 70,
      render: (_: unknown, row: PartInventory) =>
        edits[row.id] !== undefined ? (
          <Button size="small" type="primary" loading={savingId === row.id} onClick={() => saveRow(row.id)}>
            存
          </Button>
        ) : null,
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <FirstVisitTip
        storageKey="part-inventory"
        title="配件库存录入指南"
        description={
          <ol style={{ marginBottom: 0 }}>
            <li>「物料名称」必填；如果库里没有该名字, 系统自动建一条「定制物料」(AC-1000+) 并写入异常表</li>
            <li>「物料编码」可空 — 选填用于精确指定既有物料 (如 AC-0172)</li>
            <li>定制物料价格 / 单位需要事后到「物料单价库」补全</li>
          </ol>
        }
      />
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          配件库存 (4b)
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          录入一行
        </Button>
      </Space>

      <Alert
        type="info"
        showIcon
        message="录入时如果物料名在「物料单价库」里没有，系统会自动建一条定制物料（AC-1000+），并把它丢进「异常处理」页等你补齐价格。"
      />

      <Table<PartInventory>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
        size="middle"
      />

      <Modal
        title="录入一条配件库存"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={addMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => addMut.mutate(v)}
          initialValues={{ warehouse: '江西仓库', physical_qty: 1, locked_qty: 0 }}
        >
          <Form.Item name="warehouse" label="仓库" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="material_name"
            label="物料名称"
            tooltip="如果留空就必须填编码；如果填名字但物料库里没有，系统自动建定制物料"
          >
            <Input placeholder="例如：电力轨道-Xpower-T25-黑色-1.358-2插座" />
          </Form.Item>
          <Form.Item name="material_code" label="物料编码（选填）">
            <Input placeholder="如已知，例如 AC-0172" />
          </Form.Item>
          <Space style={{ width: '100%' }}>
            <Form.Item name="physical_qty" label="物理库存">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="locked_qty" label="锁定库存">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="unit" label="单位">
              <Input placeholder="如 条/个" />
            </Form.Item>
          </Space>
          <Form.Item name="spec" label="规格">
            <Input />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
