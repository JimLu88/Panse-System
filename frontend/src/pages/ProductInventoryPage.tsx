import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ProductInventoryRow,
  addProductInventoryRow,
  listProductInventory,
  listProducts,
  updateProductInventory,
} from '../api/client';

export default function ProductInventoryPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [productSearch, setProductSearch] = useState('');
  const [editingQty, setEditingQty] = useState<Record<number, number>>({});
  const [savingId, setSavingId] = useState<number | null>(null);

  async function saveQty(id: number) {
    const qty = editingQty[id];
    if (qty === undefined) return;
    setSavingId(id);
    try {
      await updateProductInventory(id, { qty });
      message.success('库存已更新');
      qc.invalidateQueries({ queryKey: ['product-inventory'] });
      setEditingQty(prev => { const n = { ...prev }; delete n[id]; return n; });
    } catch {
      message.error('保存失败');
    } finally {
      setSavingId(null);
    }
  }

  const { data, isLoading } = useQuery({
    queryKey: ['product-inventory'],
    queryFn: listProductInventory,
  });

  const { data: products } = useQuery({
    queryKey: ['products', productSearch],
    queryFn: () => listProducts(productSearch || undefined),
    enabled: open,
  });

  const addMut = useMutation({
    mutationFn: addProductInventoryRow,
    onSuccess: () => {
      message.success('已加入成品库存');
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['product-inventory'] });
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '录入失败'),
  });

  const columns = [
    { title: '仓库', dataIndex: 'warehouse', width: 110 },
    { title: '产品编码', dataIndex: 'product_code', width: 160 },
    { title: 'SKU', dataIndex: 'sku', ellipsis: true },
    { title: '规格', dataIndex: 'spec', ellipsis: true },
    { title: '单位', dataIndex: 'unit', width: 60 },
    {
      title: '物理库存',
      dataIndex: 'physical_qty',
      width: 140,
      render: (v: number, row: ProductInventoryRow) => (
        <Space size={4}>
          <InputNumber
            size="small"
            min={0}
            value={editingQty[row.id] ?? v}
            onChange={(val) =>
              setEditingQty(prev => ({ ...prev, [row.id]: val ?? 0 }))
            }
            style={{ width: 70 }}
          />
          {editingQty[row.id] !== undefined && (
            <Button
              size="small"
              type="primary"
              loading={savingId === row.id}
              onClick={() => saveQty(row.id)}
            >
              存
            </Button>
          )}
        </Space>
      ),
    },
    { title: '锁定', dataIndex: 'locked_qty', width: 70 },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          成品库存 (4a)
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          录入一行
        </Button>
      </Space>

      <Alert
        type="info"
        showIcon
        message="成品库存按精确产品编码匹配。如果引用了不存在的产品编码，会被记入「异常处理」页（不会自动建产品）。"
      />

      <Table<ProductInventoryRow>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
      />

      <Modal
        title="录入一条成品库存"
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
          initialValues={{ warehouse: '江西仓库', unit: '个', physical_qty: 1, locked_qty: 0 }}
        >
          <Form.Item name="warehouse" label="仓库" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]}>
            <AutoComplete
              onSearch={setProductSearch}
              options={(products ?? []).map((p) => ({ value: p.code, label: `${p.code} ${p.name}` }))}
              placeholder="按编码或名称搜索"
            />
          </Form.Item>
          <Form.Item name="sku" label="SKU">
            <Input placeholder="如 榉木餐桌-1.4米" />
          </Form.Item>
          <Form.Item name="spec" label="规格">
            <Input />
          </Form.Item>
          <Space style={{ width: '100%' }}>
            <Form.Item name="physical_qty" label="物理库存">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="locked_qty" label="锁定库存">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="unit" label="单位">
              <Input />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
