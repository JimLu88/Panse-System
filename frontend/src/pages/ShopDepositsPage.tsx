/**
 * 平台保证金 — 多店铺手动加条目 (平台/店铺/金额/备注)。
 * 合计自动并入 财务→剩余流水 的「平台保证金」加项 (有条目时取条目合计, 否则回退旧单值)。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Space,
  Statistic, Table, Typography, message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ShopDeposit, createShopDeposit, deleteShopDeposit, listShopDeposits, updateShopDeposit,
} from '../api/shopDeposits';

export default function ShopDepositsPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<ShopDeposit | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery({ queryKey: ['shop-deposits'], queryFn: listShopDeposits });
  const refresh = () => qc.invalidateQueries({ queryKey: ['shop-deposits'] });

  const saveMut = useMutation({
    mutationFn: (vals: any) => editing
      ? updateShopDeposit(editing.id, vals)
      : createShopDeposit(vals),
    onSuccess: () => {
      message.success(editing ? '已保存' : '已新增');
      setOpen(false);
      setEditing(null);
      form.resetFields();
      refresh();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => deleteShopDeposit(id),
    onSuccess: () => { message.success('已删除'); refresh(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '删除失败'),
  });

  const openNew = () => { setEditing(null); form.resetFields(); setOpen(true); };
  const openEdit = (d: ShopDeposit) => {
    setEditing(d);
    form.setFieldsValue({ platform: d.platform, shop_name: d.shop_name, amount: d.amount, remark: d.remark });
    setOpen(true);
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>平台保证金</Typography.Title>
      <Alert
        type="info" showIcon
        message="多店铺/平台保证金, 手动维护"
        description="很多平台店铺都有保证金, 各记各的。合计会自动并入 财务→剩余流水 的「平台保证金」加项 (有条目时按条目合计, 没条目时回退旧的单值设置)。"
      />

      <Space>
        <Card size="small"><Statistic title="保证金合计" value={data?.total ?? 0} precision={0} prefix="¥" /></Card>
        <Card size="small"><Statistic title="店铺条目数" value={data?.count ?? 0} /></Card>
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>新增保证金条目</Button>
      </Space>

      <Card size="small">
        <Table<ShopDeposit>
          rowKey="id" size="small" loading={isLoading} dataSource={data?.rows ?? []}
          pagination={false}
          columns={[
            { title: '平台', dataIndex: 'platform', width: 120, render: (v) => v || '-' },
            { title: '店铺名', dataIndex: 'shop_name', width: 200 },
            { title: '保证金额', dataIndex: 'amount', width: 140, align: 'right' as const, render: (v: number) => `¥${v.toFixed(2)}` },
            { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v) => v || '-' },
            { title: '操作', width: 130, render: (_, row) => (
              <Space>
                <Button size="small" type="link" onClick={() => openEdit(row)}>编辑</Button>
                <Popconfirm title="删除该保证金条目?" onConfirm={() => delMut.mutate(row.id)}>
                  <Button size="small" type="link" danger>删除</Button>
                </Popconfirm>
              </Space>
            ) },
          ]}
        />
      </Card>

      <Modal
        title={editing ? '编辑保证金条目' : '新增保证金条目'}
        open={open}
        onCancel={() => { setOpen(false); setEditing(null); }}
        onOk={() => form.validateFields().then((vals) => saveMut.mutate(vals))}
        confirmLoading={saveMut.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="shop_name" label="店铺名" rules={[{ required: true, message: '请填写店铺名' }]}>
            <Input placeholder="如: 畔色旗舰店" />
          </Form.Item>
          <Form.Item name="platform" label="平台">
            <Input placeholder="如: 淘宝 / 抖音 / 拼多多" />
          </Form.Item>
          <Form.Item name="amount" label="保证金额" rules={[{ required: true, message: '请填写金额' }]}>
            <InputNumber style={{ width: '100%' }} min={0} precision={2} prefix="¥" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
