import { useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { UserAddOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AuditLog,
  MeUser,
  createUser,
  fetchRoles,
  listAuditLogs,
  listAuthUsers,
} from '../api/client';
import { useAuth } from '../auth/AuthProvider';

export default function AdminPage() {
  const { user } = useAuth();
  if (user?.role !== 'admin') {
    return (
      <Alert
        type="warning"
        showIcon
        message="权限不足"
        description={`此页仅限 admin 访问。你的角色：${user?.role}`}
      />
    );
  }
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        管理员控制台 (Phase 6)
      </Typography.Title>
      <Tabs
        items={[
          { key: 'users', label: '用户管理', children: <UsersTab /> },
          { key: 'audit', label: '操作审计', children: <AuditTab /> },
        ]}
      />
    </Space>
  );
}

function UsersTab() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { data: users, isLoading } = useQuery({ queryKey: ['users'], queryFn: listAuthUsers });
  const { data: rolesInfo } = useQuery({ queryKey: ['roles'], queryFn: fetchRoles });

  const createMut = useMutation({
    mutationFn: createUser,
    onSuccess: (u) => {
      message.success(`已创建 ${u.username}`);
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message={`系统支持 3 个角色: ${rolesInfo?.roles.join(' / ')}`}
        />
        <Button type="primary" icon={<UserAddOutlined />} onClick={() => setOpen(true)}>
          新建用户
        </Button>
      </Space>

      <Table<MeUser>
        rowKey="id"
        loading={isLoading}
        dataSource={users}
        size="middle"
        pagination={false}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 60 },
          { title: '用户名', dataIndex: 'username', width: 140 },
          { title: '显示名', dataIndex: 'display_name' },
          {
            title: '角色',
            dataIndex: 'role',
            width: 110,
            render: (v: string) => (
              <Tag color={{ admin: 'red', operator: 'blue', viewer: 'default' }[v] ?? 'default'}>
                {v}
              </Tag>
            ),
          },
          {
            title: '状态',
            dataIndex: 'is_active',
            width: 80,
            render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
          },
        ]}
      />

      <Modal
        title="新建用户"
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
          initialValues={{ role: 'viewer' }}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 6 }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select
              options={(rolesInfo?.roles ?? []).map((r) => ({
                value: r,
                label: `${r} — ${rolesInfo?.descriptions[r] ?? ''}`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function AuditTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['audit-logs'],
    queryFn: () => listAuditLogs({ limit: 200 }),
  });
  return (
    <Table<AuditLog>
      rowKey="id"
      loading={isLoading}
      dataSource={data}
      size="small"
      pagination={{ pageSize: 30 }}
      columns={[
        {
          title: '时间',
          dataIndex: 'created_at',
          width: 170,
          render: (v: string) => new Date(v).toLocaleString('zh-CN'),
        },
        {
          title: '用户',
          dataIndex: 'username',
          width: 110,
          render: (v: string | null) => v ?? <span style={{ color: '#999' }}>anon</span>,
        },
        {
          title: '方法',
          dataIndex: 'method',
          width: 80,
          render: (v: string) => (
            <Tag
              color={
                {
                  POST: 'green',
                  PATCH: 'blue',
                  PUT: 'blue',
                  DELETE: 'red',
                }[v] ?? 'default'
              }
            >
              {v}
            </Tag>
          ),
        },
        {
          title: '路径',
          dataIndex: 'path',
          render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code>,
        },
        {
          title: '状态',
          dataIndex: 'status_code',
          width: 70,
          render: (v: number | null) =>
            v == null ? '-' : (
              <Tag color={v >= 400 ? 'red' : v >= 300 ? 'orange' : 'green'}>{v}</Tag>
            ),
        },
        { title: 'IP', dataIndex: 'ip', width: 110 },
      ]}
    />
  );
}
