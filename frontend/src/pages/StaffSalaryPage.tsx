/**
 * 人员工资 / 外包 (G) — 自由增减人员、改月工资。
 * 月度「外包成本」= Σ 当月在职人员月工资(替代写死 ¥10000),自动挂进利润/对账(order_financials.outsourcing_for_range)。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, DatePicker, Form, Input, InputNumber, Modal, Popconfirm,
  Space, Statistic, Table, Typography, message,
} from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  StaffSalary, StaffSalaryInput, fetchStaffSalaries,
  createStaffSalary, updateStaffSalary, deleteStaffSalary,
} from '../api/staffSalary';

export default function StaffSalaryPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['staff-salaries'], queryFn: fetchStaffSalaries });
  const [editing, setEditing] = useState<StaffSalary | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const invalidate = () => qc.invalidateQueries({ queryKey: ['staff-salaries'] });
  const createMut = useMutation({
    mutationFn: createStaffSalary,
    onSuccess: () => { message.success('已添加'); invalidate(); setOpen(false); },
    onError: (e: Error) => message.error(e.message),
  });
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<StaffSalaryInput> }) => updateStaffSalary(id, body),
    onSuccess: () => { message.success('已保存'); invalidate(); setOpen(false); },
    onError: (e: Error) => message.error(e.message),
  });
  const deleteMut = useMutation({
    mutationFn: deleteStaffSalary,
    onSuccess: () => { message.success('已删除'); invalidate(); },
    onError: (e: Error) => message.error(e.message),
  });

  const openAdd = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ active_from: dayjs().startOf('month') });
    setOpen(true);
  };
  const openEdit = (r: StaffSalary) => {
    setEditing(r);
    form.setFieldsValue({
      name: r.name, role: r.role, monthly_cost: r.monthly_cost,
      active_from: r.active_from ? dayjs(r.active_from) : null,
      active_to: r.active_to ? dayjs(r.active_to) : null, remark: r.remark,
    });
    setOpen(true);
  };
  const submit = async () => {
    const v = await form.validateFields();
    const body: StaffSalaryInput = {
      name: v.name, role: v.role || null, monthly_cost: v.monthly_cost,
      active_from: v.active_from.format('YYYY-MM-DD'),
      active_to: v.active_to ? v.active_to.format('YYYY-MM-DD') : null,
      remark: v.remark || null,
    };
    if (editing) updateMut.mutate({ id: editing.id, body });
    else createMut.mutate(body);
  };

  const columns = [
    { title: '姓名', dataIndex: 'name' },
    { title: '角色', dataIndex: 'role', render: (v: string | null) => v || '-' },
    { title: '月工资', dataIndex: 'monthly_cost',
      render: (v: number) => `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}` },
    { title: '在职起', dataIndex: 'active_from' },
    { title: '在职止', dataIndex: 'active_to', render: (v: string | null) => v || '至今' },
    { title: '备注', dataIndex: 'remark', render: (v: string | null) => v || '-' },
    { title: '操作', width: 110,
      render: (_: unknown, r: StaffSalary) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="删除该人员?" onConfirm={() => deleteMut.mutate(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>人员工资 / 外包</Typography.Title>
      <Alert
        type="info" showIcon
        message="人员工资 → 月度外包成本"
        description="在这里增减人员、改月工资。月度「外包成本」= Σ 当月在职人员月工资(替代之前写死的 ¥10000),自动挂进利润对账。在职止留空 = 至今。"
      />
      <Card size="small">
        <Space style={{ marginBottom: 12 }} align="end">
          <Statistic title="当月在职合计(每月)" value={data?.current_month_total ?? 0} precision={0} prefix="¥" />
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>加人员</Button>
        </Space>
        <Table rowKey="id" size="small" loading={isLoading} columns={columns}
               dataSource={data?.rows ?? []} pagination={false} />
      </Card>
      <Modal
        open={open} title={editing ? '编辑人员' : '加人员'} onOk={submit} onCancel={() => setOpen(false)}
        confirmLoading={createMut.isPending || updateMut.isPending} destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: '填姓名' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色(如 木工/打包/客服/外包)">
            <Input />
          </Form.Item>
          <Form.Item name="monthly_cost" label="月工资(元)" rules={[{ required: true, message: '填月工资' }]}>
            <InputNumber min={0} style={{ width: '100%' }} addonBefore="¥" />
          </Form.Item>
          <Form.Item name="active_from" label="在职起(按月)" rules={[{ required: true, message: '选在职起始月' }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="active_to" label="在职止(留空 = 至今)">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
