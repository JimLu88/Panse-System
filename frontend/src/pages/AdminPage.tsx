import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
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
import { ExperimentOutlined, KeyOutlined, UserAddOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AuditLog,
  IntegrationConfig,
  Integrations,
  MeUser,
  createUser,
  fetchIntegrations,
  fetchRoles,
  listAuditLogs,
  listAuthUsers,
  testIntegration,
  updateIntegrations,
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
          { key: 'integrations', label: 'AI 集成 / OCR 配置', children: <IntegrationsTab /> },
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

function IntegrationsTab() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['integrations'],
    queryFn: fetchIntegrations,
  });

  if (isLoading || !data) {
    return <Alert type="info" message="加载中..." />;
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        message="按用途分别配置 AI 模型"
        description={
          <Space direction="vertical">
            <span>
              <b>异常诊断</b>: 给 ERP 中的数据异常自动写人话分析 (token 消耗低, 推荐 sonnet/haiku 等小模型)。
            </span>
            <span>
              <b>OCR 送货单</b>: 拍照识别供应商送货单 (token 消耗高, 需要 vision 模型)。
            </span>
            <span style={{ color: '#999' }}>
              API Key 加密存储于数据库。后台改完<b>无需重启</b>, 下一次调用即生效。环境变量
              <code>ANTHROPIC_API_KEY</code> / <code>AI_MODEL</code> 作为回退默认值。
            </span>
          </Space>
        }
      />
      <IntegrationForm
        kind="diagnose"
        title="异常诊断"
        current={data.diagnose}
        providers={data.supported_providers}
        onSaved={() => qc.invalidateQueries({ queryKey: ['integrations'] })}
      />
      <IntegrationForm
        kind="ocr"
        title="OCR 送货单"
        current={data.ocr}
        providers={data.supported_providers}
        onSaved={() => qc.invalidateQueries({ queryKey: ['integrations'] })}
      />
    </Space>
  );
}

function IntegrationForm({
  kind,
  title,
  current,
  providers,
  onSaved,
}: {
  kind: 'diagnose' | 'ocr';
  title: string;
  current: IntegrationConfig;
  providers: Integrations['supported_providers'];
  onSaved: () => void;
}) {
  const [form] = Form.useForm();
  const [provider, setProvider] = useState(current.provider);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const saveMut = useMutation({
    mutationFn: (payload: {
      provider: string;
      base_url: string;
      api_key?: string;
      model: string;
    }) => updateIntegrations({ [kind]: payload }),
    onSuccess: () => {
      message.success(`${title} 配置已保存`);
      form.setFieldValue('api_key', '');
      onSaved();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const testMut = useMutation({
    mutationFn: () => testIntegration(kind),
    onSuccess: (r) => {
      if (r.ok) {
        setTestResult({ ok: true, text: `${r.provider} / ${r.model}: ${r.sample ?? ''}` });
        message.success('调用成功');
      } else {
        setTestResult({ ok: false, text: r.error ?? '调用失败' });
      }
    },
    onError: (e: any) => {
      setTestResult({ ok: false, text: e?.response?.data?.detail ?? '调用失败' });
    },
  });

  const clearKeyMut = useMutation({
    mutationFn: () => updateIntegrations({ [kind]: { api_key: '__CLEAR__' } }),
    onSuccess: () => {
      message.success('API Key 已清除');
      onSaved();
    },
  });

  const providerHint = providers.find((p) => p.value === provider);

  return (
    <Card
      size="small"
      title={
        <Space>
          <ExperimentOutlined />
          {title}
          <Tag color="blue">{kind}</Tag>
        </Space>
      }
      extra={
        <Space>
          <Button
            icon={<ExperimentOutlined />}
            loading={testMut.isPending}
            disabled={!current.api_key_set}
            onClick={() => testMut.mutate()}
          >
            测试联通
          </Button>
        </Space>
      }
    >
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="当前 Provider">{current.provider || '-'}</Descriptions.Item>
        <Descriptions.Item label="当前模型">{current.model || '-'}</Descriptions.Item>
        <Descriptions.Item label="API Key">
          {current.api_key_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>
                {current.api_key_masked}
              </Tag>
              <Button size="small" type="link" danger onClick={() => clearKeyMut.mutate()}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未设置 (走环境变量)</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="Base URL">{current.base_url || '官方默认'}</Descriptions.Item>
      </Descriptions>

      <Form
        form={form}
        layout="vertical"
        initialValues={{
          provider: current.provider,
          base_url: current.base_url,
          model: current.model,
          api_key: '',
        }}
        onFinish={(v) => saveMut.mutate(v)}
      >
        <Form.Item name="provider" label="Provider" rules={[{ required: true }]}>
          <Select
            options={providers.map((p) => ({ value: p.value, label: p.label }))}
            onChange={setProvider}
          />
        </Form.Item>
        <Form.Item
          name="model"
          label={
            <Space>
              模型名
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {providerHint?.model_hint}
              </Typography.Text>
            </Space>
          }
          rules={[{ required: true }]}
        >
          <Input placeholder="如 claude-sonnet-4-6 或 qwen-vl-max" />
        </Form.Item>
        <Form.Item
          name="base_url"
          label={
            <Space>
              Base URL (可选)
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {providerHint?.base_url_hint}
              </Typography.Text>
            </Space>
          }
        >
          <Input placeholder="留空 = 官方默认 / 代理填代理地址" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="API Key (留空 = 不修改)"
          extra="加密存储, 显示时仅保留前 3 后 4 位"
        >
          <Input.Password placeholder={current.api_key_set ? '(已设置, 留空保留原值)' : '请输入'} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={saveMut.isPending}>
            保存
          </Button>
        </Form.Item>
      </Form>

      {testResult && (
        <Alert
          type={testResult.ok ? 'success' : 'error'}
          showIcon
          message={testResult.ok ? '联通正常' : '联通失败'}
          description={<code style={{ fontSize: 12 }}>{testResult.text}</code>}
          closable
          onClose={() => setTestResult(null)}
        />
      )}
    </Card>
  );
}
