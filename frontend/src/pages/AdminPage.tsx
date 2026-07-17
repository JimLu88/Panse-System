import React, { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tree,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudDownloadOutlined,
  DashboardOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  KeyOutlined,
  PoweroffOutlined,
  ReloadOutlined,
  SaveOutlined,
  TruckOutlined,
  UserAddOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import OpsToolsPage from './OpsToolsPage';   // 运维工具 (2026-06-12) 并入管理 → 作为标签页
import {
  AuditLog,
  HealthLog,
  IntegrationConfig,
  Integrations,
  MeUser,
  NotifyConfig,
  ResetDataResult,
  RuntimeLog,
  SchedulerJob,
  SchedulerRun,
  SystemEvent,
  SystemStatus,
  adminResetPassword,
  createUser,
  fetchHealthLogs,
  fetchRecentLogs,
  fetchIntegrations,
  fetchNotifyConfig,
  fetchRoles,
  fetchSchedulerJobs,
  fetchSchedulerRuns,
  fetchSystemEvents,
  fetchSystemStatus,
  listAuditLogs,
  listAuthUsers,
  fetchResetDataTables,
  resetBusinessData,
  BackupConfig,
  BackupFile,
  fetchBackupConfig,
  updateBackupConfig,
  fetchBackupList,
  exportAndDownload,
  downloadBackup,
  restartApi,
  testIntegration,
  testNotifyConfig,
  triggerSchedulerJob,
  updateSchedulerJob,
  updateIntegrations,
  updateNotifyConfig,
  updateUser,
  LogisticsConfig,
  fetchLogisticsConfig,
  updateLogisticsConfig,
  fetchCampaignAi,
  updateCampaignAi,
  testCampaignAi,
} from '../api/client';
import { syncAllShipments } from '../api/shipments';
import { useAuth } from '../auth/AuthProvider';
import { PERM_TREE, ALL_PERM_KEYS } from '../auth/permissions';

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
          { key: 'monitor', label: <Space><DashboardOutlined />系统监控 / 看门狗</Space>, children: <MonitorTab /> },
          { key: 'scheduler', label: '全自动任务清单 (业务需求 18)', children: <SchedulerTab /> },
          { key: 'audit', label: '操作审计', children: <AuditTab /> },
          { key: 'runtime-logs', label: <Space><WarningOutlined />运行日志 / 错误排查</Space>, children: <RuntimeLogsTab /> },
          { key: 'data-reset', label: <Space><DeleteOutlined />数据管理</Space>, children: <DataResetTab /> },
          { key: 'ops-tools', label: '运维工具', children: <OpsToolsPage /> },
        ]}
      />
    </Space>
  );
}

// 子账号「可见页面」选择器: 全部 / 仅指定页面(勾选权限树)。新建/编辑用户共用。
function PermPicker({ mode, setMode, checked, setChecked }: {
  mode: 'all' | 'custom';
  setMode: (m: 'all' | 'custom') => void;
  checked: string[];
  setChecked: (k: string[]) => void;
}) {
  const treeData = PERM_TREE.map((g) => ({
    key: g.key, title: g.label,
    children: g.children.map((c) => ({ key: c.key, title: c.label })),
  }));
  return (
    <>
      <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} style={{ marginBottom: 8 }}>
        <Radio value="all">全部页面 (不受限)</Radio>
        <Radio value="custom">仅指定页面</Radio>
      </Radio.Group>
      {mode === 'custom' && (
        <Tree
          checkable
          selectable={false}
          defaultExpandAll
          treeData={treeData}
          checkedKeys={checked}
          onCheck={(keys) =>
            setChecked((keys as React.Key[]).map(String).filter((k) => ALL_PERM_KEYS.includes(k)))
          }
          height={300}
          style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 8 }}
        />
      )}
    </>
  );
}

function UsersTab() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<MeUser | null>(null);
  const [pwdFor, setPwdFor] = useState<MeUser | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [pwdForm] = Form.useForm();
  // 子账号页面权限: 新建/编辑弹窗共用 (同一时刻只开一个)
  const [permMode, setPermMode] = useState<'all' | 'custom'>('custom');
  const [checkedPerms, setCheckedPerms] = useState<string[]>([]);
  const createRole = Form.useWatch('role', form);
  const editRole = Form.useWatch('role', editForm);
  const buildPagePerms = (role?: string): string[] | null =>
    role === 'admin' || permMode === 'all' ? null : checkedPerms;
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

  const updateMut = useMutation({
    mutationFn: (v: { id: number; payload: any }) => updateUser(v.id, v.payload),
    onSuccess: () => {
      message.success('已保存');
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const pwdMut = useMutation({
    mutationFn: (v: { id: number; pwd: string }) => adminResetPassword(v.id, v.pwd),
    onSuccess: () => {
      message.success('密码已重置');
      setPwdFor(null);
      pwdForm.resetFields();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '重置失败'),
  });

  function openEdit(u: MeUser) {
    setEditing(u);
    const restricted = u.page_perms != null;   // null=不受限, 数组=受限
    setPermMode(restricted ? 'custom' : 'all');
    setCheckedPerms(restricted ? (u.page_perms || []) : []);
    editForm.setFieldsValue({
      username: u.username,
      display_name: u.display_name,
      role: u.role,
      is_active: u.is_active,
    });
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message={`系统支持 3 个角色: ${rolesInfo?.roles.join(' / ')}`}
        />
        <Button
          type="primary"
          icon={<UserAddOutlined />}
          onClick={() => { setPermMode('custom'); setCheckedPerms([]); setOpen(true); }}
        >
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
          {
            title: '可见页面',
            dataIndex: 'page_perms',
            width: 100,
            render: (v: string[] | null | undefined, u: MeUser) =>
              u.role === 'admin' || v == null
                ? <Tag color="blue">全部</Tag>
                : <Tag color="orange">{v.length} 个页面</Tag>,
          },
          {
            title: '操作',
            width: 160,
            render: (_: any, u: MeUser) => (
              <Space>
                <Button size="small" onClick={() => openEdit(u)}>
                  编辑
                </Button>
                <Button size="small" icon={<KeyOutlined />} onClick={() => setPwdFor(u)}>
                  改密
                </Button>
              </Space>
            ),
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
          onFinish={(v) => createMut.mutate({ ...v, page_perms: buildPagePerms(v.role) })}
          initialValues={{ role: 'viewer' }}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码 (至少 8 位)" rules={[{ required: true, min: 8 }]}>
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
          {createRole !== 'admin' && (
            <Form.Item
              label="可见页面 (子账号权限)"
              tooltip="只勾选的页面对该账号可见, 其余菜单隐藏、直接访问显示「程序错误」。选「全部页面」= 不受限。admin 角色恒不受限。"
            >
              <PermPicker mode={permMode} setMode={setPermMode} checked={checkedPerms} setChecked={setCheckedPerms} />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={`编辑用户 ${editing?.username ?? ''}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        confirmLoading={updateMut.isPending}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(v) => editing && updateMut.mutate({ id: editing.id, payload: { ...v, page_perms: buildPagePerms(v.role) } })}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3 }]}>
            <Input />
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
          <Form.Item name="is_active" label="状态">
            <Select
              options={[
                { value: true, label: '启用' },
                { value: false, label: '停用' },
              ]}
            />
          </Form.Item>
          {editRole !== 'admin' && (
            <Form.Item
              label="可见页面 (子账号权限)"
              tooltip="只勾选的页面对该账号可见, 其余菜单隐藏、直接访问显示「程序错误」。选「全部页面」= 不受限。admin 角色恒不受限。"
            >
              <PermPicker mode={permMode} setMode={setPermMode} checked={checkedPerms} setChecked={setCheckedPerms} />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={`重置密码 — ${pwdFor?.username ?? ''}`}
        open={!!pwdFor}
        onCancel={() => setPwdFor(null)}
        onOk={() => pwdForm.submit()}
        confirmLoading={pwdMut.isPending}
        destroyOnClose
      >
        <Form
          form={pwdForm}
          layout="vertical"
          onFinish={(v) => pwdFor && pwdMut.mutate({ id: pwdFor.id, pwd: v.new_password })}
        >
          <Form.Item name="new_password" label="新密码 (至少 8 位)" rules={[{ required: true, min: 8 }]}>
            <Input.Password />
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
      pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
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

// 运行日志查看 — 把后端内存环形日志直接搬到界面, 排查飞书同步/导入等错误不用敲 docker logs
function RuntimeLogsTab() {
  const [level, setLevel] = useState<string>('WARNING');
  const [contains, setContains] = useState('');
  const [loggerPrefix, setLoggerPrefix] = useState<string>('');

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['runtime-logs', level, contains, loggerPrefix],
    queryFn: () =>
      fetchRecentLogs({
        limit: 500,
        level: level || undefined,
        contains: contains || undefined,
        logger_prefix: loggerPrefix || undefined,
      }),
    refetchInterval: 15000, // 15s 自动刷新, 方便边操作边看
  });

  const levelColor: Record<string, string> = {
    DEBUG: 'default',
    INFO: 'blue',
    WARNING: 'orange',
    ERROR: 'red',
    CRITICAL: 'magenta',
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        message="这里能看到后端最近的运行日志 (内存保留约 3000 条, 重启后清空)。"
        description="同步失败、导入报错等都会记在这里。默认只看 WARNING 及以上; 排查飞书同步选模块「panse.feishu_sync」。"
      />
      <Space wrap>
        <span>级别:</span>
        <Select
          value={level}
          style={{ width: 130 }}
          onChange={setLevel}
          options={[
            { value: '', label: '全部' },
            { value: 'INFO', label: 'INFO 及以上' },
            { value: 'WARNING', label: 'WARNING 及以上' },
            { value: 'ERROR', label: 'ERROR 及以上' },
          ]}
        />
        <span>模块:</span>
        <Select
          value={loggerPrefix}
          style={{ width: 200 }}
          onChange={setLoggerPrefix}
          options={[
            { value: '', label: '全部模块' },
            { value: 'panse.feishu_sync', label: '飞书同步' },
            { value: 'panse.smart_import', label: '智能导入' },
            { value: 'panse.scheduler', label: '定时任务' },
            { value: 'panse.error', label: '未处理异常' },
            { value: 'panse.request', label: '请求' },
          ]}
        />
        <Input.Search
          placeholder="关键字过滤"
          allowClear
          style={{ width: 220 }}
          onSearch={setContains}
        />
        <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
          刷新
        </Button>
      </Space>
      <Table<RuntimeLog>
        rowKey={(_, i) => String(i)}
        loading={isLoading}
        dataSource={data}
        size="small"
        pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        columns={[
          { title: '时间', dataIndex: 'ts', width: 160, render: (v: string) => <span style={{ fontSize: 12 }}>{v}</span> },
          {
            title: '级别',
            dataIndex: 'level',
            width: 90,
            render: (v: string) => <Tag color={levelColor[v] ?? 'default'}>{v}</Tag>,
          },
          { title: '模块', dataIndex: 'logger', width: 170, render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code> },
          { title: '内容', dataIndex: 'msg', render: (v: string) => <span style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{v}</span> },
        ]}
      />
    </Space>
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
            <span style={{ color: '#fa8c16' }}>
              只填一处即可: 两个槽位会<b>互相自动复用</b> Key/模型。比如只在"异常诊断"填了 Key,
              "OCR 送货单"和截图录单也能直接用 (反之亦然)。
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
      <IntegrationForm
        kind="custom"
        title="定制报价 AI (分类/识图)"
        current={data.custom}
        providers={data.supported_providers}
        onSaved={() => qc.invalidateQueries({ queryKey: ['integrations'] })}
      />
      <CampaignAiForm />
      <LogisticsForm />
    </Space>
  );
}

// 活动系统 AI (DeepSeek/千问, 2026-07-17): 活动发现日期兜底 + 核对失败原因归类。
// key 加密落库; 读取只回「已配置 + 尾4位」, 绝不回明文; 提交后清空输入框。
function CampaignAiForm() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['campaign-ai'],
    queryFn: fetchCampaignAi,
  });
  const [form] = Form.useForm();
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const saveMut = useMutation({
    mutationFn: (v: { provider: string; model: string; api_key?: string }) =>
      updateCampaignAi({
        provider: v.provider,
        model: v.model,
        api_key: v.api_key ? v.api_key : undefined, // 留空 = 不改
      }),
    onSuccess: () => {
      message.success('活动系统 AI 配置已保存');
      form.setFieldValue('api_key', ''); // 提交后清空密码框, 不留明文
      qc.invalidateQueries({ queryKey: ['campaign-ai'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const clearKeyMut = useMutation({
    mutationFn: () => updateCampaignAi({ api_key: '__CLEAR__' }),
    onSuccess: () => {
      message.success('API Key 已清除');
      qc.invalidateQueries({ queryKey: ['campaign-ai'] });
    },
  });

  const testMut = useMutation({
    mutationFn: testCampaignAi,
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

  if (isLoading || !data) return null;

  return (
    <Card
      size="small"
      title={
        <Space>
          <ExperimentOutlined />
          活动系统 AI (DeepSeek/千问)
          <Tag color="purple">campaign</Tag>
        </Space>
      }
      extra={
        <Button
          icon={<ExperimentOutlined />}
          loading={testMut.isPending}
          disabled={!data.api_key_set || data.provider === 'none'}
          onClick={() => testMut.mutate()}
        >
          测试联通
        </Button>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="活动发现/核对的可选 LLM 兜底"
        description="千牛活动列表抓回来但规则解析不出档期日期时, 用云端 LLM 从原文抽取; 核对失败原因也可归类。选「关闭」则一切保持原行为。Key 加密存储, 只显示尾 4 位。"
      />
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="当前 Provider">
          {data.providers.find((p) => p.value === data.provider)?.label ?? data.provider}
        </Descriptions.Item>
        <Descriptions.Item label="当前模型">{data.model || '-'}</Descriptions.Item>
        <Descriptions.Item label="API Key" span={2}>
          {data.api_key_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>
                已配置 ****{data.api_key_tail || '????'}
              </Tag>
              <Button size="small" type="link" danger onClick={() => clearKeyMut.mutate()}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未配置</Tag>
          )}
        </Descriptions.Item>
      </Descriptions>

      <Form
        form={form}
        layout="vertical"
        initialValues={{ provider: data.provider, model: data.model, api_key: '' }}
        onFinish={(v) => saveMut.mutate(v)}
      >
        <Form.Item name="provider" label="Provider" rules={[{ required: true }]}>
          <Select
            options={data.providers.map((p) => ({ value: p.value, label: p.label }))}
            onChange={(val) => {
              const def = data.providers.find((p) => p.value === val)?.default_model;
              if (def) form.setFieldValue('model', def);
            }}
          />
        </Form.Item>
        <Form.Item
          name="model"
          label={
            <Space>
              模型名
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                DeepSeek 默认 deepseek-chat / 千问默认 qwen-plus
              </Typography.Text>
            </Space>
          }
          rules={[{ required: true }]}
        >
          <Input placeholder="deepseek-chat / qwen-plus" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="API Key (留空 = 不修改)"
          extra="加密存储; 保存后不再回显, 仅显示尾 4 位"
        >
          <Input.Password placeholder={data.api_key_set ? '(已配置, 留空保留原值)' : '请输入'} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={saveMut.isPending}>
            保存
          </Button>
        </Form.Item>
      </Form>

      {testResult && (
        <Alert
          style={{ marginTop: 8 }}
          type={testResult.ok ? 'success' : 'error'}
          showIcon
          message={testResult.text}
        />
      )}
    </Card>
  );
}

function LogisticsForm() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['logistics-config'],
    queryFn: fetchLogisticsConfig,
  });

  const [customer, setCustomer] = useState('');
  const [key, setKey] = useState('');
  const [kdniaoId, setKdniaoId] = useState('');
  const [kdniaoKey, setKdniaoKey] = useState('');

  const saveMut = useMutation({
    mutationFn: (payload: {
      customer?: string;
      key?: string;
      kdniao_ebusiness_id?: string;
      kdniao_key?: string;
    }) => updateLogisticsConfig(payload),
    onSuccess: () => {
      message.success('物流配置已保存');
      setCustomer('');
      setKey('');
      setKdniaoId('');
      setKdniaoKey('');
      qc.invalidateQueries({ queryKey: ['logistics-config'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const providerMut = useMutation({
    mutationFn: (provider: string) => updateLogisticsConfig({ provider }),
    onSuccess: () => {
      message.success('已切换 provider');
      qc.invalidateQueries({ queryKey: ['logistics-config'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '切换失败'),
  });

  const clearMut = useMutation({
    mutationFn: (field: 'customer' | 'key' | 'kdniao_ebusiness_id' | 'kdniao_key') =>
      updateLogisticsConfig({ [field]: '__CLEAR__' }),
    onSuccess: () => {
      message.success('已清除');
      qc.invalidateQueries({ queryKey: ['logistics-config'] });
    },
  });

  const syncMut = useMutation({
    mutationFn: () => syncAllShipments(),
    onSuccess: (r) => {
      if (r.skipped) {
        message.warning(`物流未配置: ${r.skipped}`);
      } else {
        message.success(
          `同步完成: 检查 ${r.checked} 单, 新建 ${r.synced ?? 0}, 已签收 ${r.signed}, 失败 ${r.errors}`,
        );
      }
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '同步失败'),
  });

  if (isLoading || !data) return null;

  return (
    <Card
      size="small"
      title={
        <Space>
          <TruckOutlined />
          物流追踪 (快递100 / 快递鸟)
          <Tag color="orange">logistics</Tag>
        </Space>
      }
      extra={
        <Space size={4} style={{ fontSize: 12 }}>
          <Typography.Link href="https://api.kuaidi100.com/" target="_blank">
            快递100 Key
          </Typography.Link>
          <span>/</span>
          <Typography.Link href="https://www.kdniao.com/" target="_blank">
            快递鸟 Key →
          </Typography.Link>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="实时查快递 (配件清单 / 订单等的「实时刷新物流」)"
        description={
          <Space direction="vertical" size={2}>
            <span>
              支持 <b>快递100</b> (免费约 100 次/天) 与 <b>快递鸟</b> (即时查询免费档)。
              填任一家即可; 用下方「启用 provider」选择走哪家 (auto = 谁配了用谁, 优先快递100)。
            </span>
            <span style={{ color: '#999' }}>
              到对应开放平台获取凭证后填入下方保存即可, 无需重启。
            </span>
          </Space>
        }
      />
      <Space style={{ marginBottom: 12 }} wrap>
        <Button icon={<ReloadOutlined />} loading={syncMut.isPending} onClick={() => syncMut.mutate()}>
          立即同步全部物流
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          扫描所有在途订单/售后/工厂单/补单/采购的快递单号并立即批量刷新 (无需等定时任务)
        </Typography.Text>
      </Space>
      <Form.Item label="启用 provider" style={{ marginBottom: 12 }}>
        <Select
          size="small"
          style={{ width: 240 }}
          value={data.provider || 'auto'}
          onChange={(v) => providerMut.mutate(v)}
          options={[
            { value: 'auto', label: 'auto (谁配了用谁, 优先快递100)' },
            { value: 'kuaidi100', label: '快递100' },
            { value: 'kdniao', label: '快递鸟' },
          ]}
        />
      </Form.Item>
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="快递100 Customer">
          {data.customer_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>{data.customer}</Tag>
              <Button size="small" type="link" danger onClick={() => clearMut.mutate('customer')}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未设置</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="快递100 Key">
          {data.key_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>{data.key_masked}</Tag>
              <Button size="small" type="link" danger onClick={() => clearMut.mutate('key')}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未设置</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="快递鸟 EBusinessID">
          {data.kdniao_ebusiness_id_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>{data.kdniao_ebusiness_id}</Tag>
              <Button size="small" type="link" danger onClick={() => clearMut.mutate('kdniao_ebusiness_id')}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未设置</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="快递鸟 ApiKey">
          {data.kdniao_key_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>{data.kdniao_key_masked}</Tag>
              <Button size="small" type="link" danger onClick={() => clearMut.mutate('kdniao_key')}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未设置</Tag>
          )}
        </Descriptions.Item>
      </Descriptions>
      <Form
        layout="inline"
        onFinish={() =>
          saveMut.mutate({
            customer: customer || undefined,
            key: key || undefined,
            kdniao_ebusiness_id: kdniaoId || undefined,
            kdniao_key: kdniaoKey || undefined,
          })
        }
      >
        <Form.Item label="快递100 Customer">
          <Input
            value={customer}
            onChange={(e) => setCustomer(e.target.value)}
            placeholder="填新值 (留空=不改)"
            style={{ width: 160 }}
          />
        </Form.Item>
        <Form.Item label="快递100 Key">
          <Input.Password
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="填新值 (留空=不改)"
            style={{ width: 180 }}
          />
        </Form.Item>
        <Form.Item label="快递鸟 ID">
          <Input
            value={kdniaoId}
            onChange={(e) => setKdniaoId(e.target.value)}
            placeholder="EBusinessID (留空=不改)"
            style={{ width: 160 }}
          />
        </Form.Item>
        <Form.Item label="快递鸟 Key">
          <Input.Password
            value={kdniaoKey}
            onChange={(e) => setKdniaoKey(e.target.value)}
            placeholder="ApiKey (留空=不改)"
            style={{ width: 180 }}
          />
        </Form.Item>
        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<SaveOutlined />}
            loading={saveMut.isPending}
            disabled={!customer && !key && !kdniaoId && !kdniaoKey}
          >
            保存
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}

function IntegrationForm({
  kind,
  title,
  current,
  providers,
  onSaved,
}: {
  kind: 'diagnose' | 'ocr' | 'custom';
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

// ----------------------------- 系统监控 / 看门狗 (业务需求) ---------- //

const STATUS_COLOR: Record<string, string> = { ok: 'green', warn: 'orange', fail: 'red' };
const STATUS_ICON: Record<string, React.ReactNode> = {
  ok: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  warn: <WarningOutlined style={{ color: '#fa8c16' }} />,
  fail: <CloseCircleOutlined style={{ color: '#cf1322' }} />,
};
const CHECK_LABEL: Record<string, string> = {
  db_ping: '数据库连通',
  disk: '磁盘空间',
  memory: '内存',
  migrations: '数据库迁移',
  ai_config: 'AI 配置',
};

function fmtUptime(sec: number): string {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}天 ${h}时 ${m}分`;
  if (h > 0) return `${h}时 ${m}分`;
  return `${m}分 ${sec % 60}秒`;
}

function MonitorTab() {
  const qc = useQueryClient();
  const { data: status, isLoading } = useQuery({
    queryKey: ['system-status'],
    queryFn: fetchSystemStatus,
    refetchInterval: 10000,   // 每 10s 自动刷新
  });
  const { data: logs } = useQuery({
    queryKey: ['system-health-logs'],
    queryFn: () => fetchHealthLogs(50),
    refetchInterval: 30000,
  });

  const restartMut = useMutation({
    mutationFn: restartApi,
    onSuccess: () => {
      message.success('已发送重启信号; 后端将在 1-3 秒内由 Docker 自动拉起, 页面 5 秒后请手动刷新');
      // 5 秒后强制刷新 status query
      setTimeout(() => qc.invalidateQueries({ queryKey: ['system-status'] }), 6000);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '重启请求失败'),
  });

  if (isLoading || !status) {
    return <Card loading />;
  }

  const overallStatus: 'ok' | 'warn' | 'fail' = status.recent_checks.some((c) => c.status === 'fail')
    ? 'fail'
    : status.recent_checks.some((c) => c.status === 'warn')
      ? 'warn'
      : 'ok';

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type={overallStatus === 'ok' ? 'success' : overallStatus === 'warn' ? 'warning' : 'error'}
        showIcon
        message={
          overallStatus === 'ok'
            ? '系统状态正常'
            : overallStatus === 'warn'
              ? '系统有警告项, 建议关注'
              : '系统有失败项, 请立即处理'
        }
        description={`版本 ${status.version_sha} | Python ${status.python_version} | 已运行 ${fmtUptime(status.uptime_sec)}`}
        action={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => qc.invalidateQueries({ queryKey: ['system-status'] })}
            >
              手动刷新
            </Button>
            <Popconfirm
              title="确认重启后端 API ?"
              description="进程会在 ~1 秒内退出, Docker 自动拉起新进程, 期间所有请求会失败 ~5 秒。"
              okText="确认重启"
              okType="danger"
              onConfirm={() => restartMut.mutate()}
            >
              <Button
                danger
                icon={<PoweroffOutlined />}
                loading={restartMut.isPending}
              >
                重启 API
              </Button>
            </Popconfirm>
          </Space>
        }
      />

      <Row gutter={12}>
        <Col xs={12} sm={8} md={6}>
          <Card size="small">
            <Statistic
              title="数据库"
              value={status.db_ok ? '在线' : '离线'}
              valueStyle={{ color: status.db_ok ? '#3f8600' : '#cf1322' }}
              suffix={status.db_latency_ms != null ? `${status.db_latency_ms}ms` : undefined}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6}>
          <Card size="small">
            <Statistic
              title="磁盘"
              value={status.disk_used_pct.toFixed(1)}
              suffix="%"
              valueStyle={{
                color:
                  status.disk_used_pct >= 95 ? '#cf1322' : status.disk_used_pct >= 85 ? '#fa8c16' : '#3f8600',
              }}
            />
            <Progress
              percent={status.disk_used_pct}
              size="small"
              status={status.disk_used_pct >= 95 ? 'exception' : 'normal'}
              showInfo={false}
            />
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              空闲 {status.disk_free_gb.toFixed(1)} / {status.disk_total_gb.toFixed(1)} GB
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6}>
          <Card size="small">
            <Statistic
              title="内存"
              value={status.mem_used_pct.toFixed(1)}
              suffix="%"
              valueStyle={{
                color:
                  status.mem_used_pct >= 95 ? '#cf1322' : status.mem_used_pct >= 85 ? '#fa8c16' : '#3f8600',
              }}
            />
            <Progress
              percent={status.mem_used_pct}
              size="small"
              status={status.mem_used_pct >= 95 ? 'exception' : 'normal'}
              showInfo={false}
            />
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              可用 {status.mem_available_mb} / {status.mem_total_mb} MB
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6}>
          <Card size="small">
            <Statistic
              title="送货单原图占用"
              value={status.storage_used_mb}
              suffix="MB"
            />
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              ./storage 目录
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="当前健康检查">
        <List
          size="small"
          dataSource={status.recent_checks}
          renderItem={(c) => (
            <List.Item>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space>
                  {STATUS_ICON[c.status]}
                  <strong>{CHECK_LABEL[c.name] ?? c.name}</strong>
                  <Tag color={STATUS_COLOR[c.status]}>{c.status.toUpperCase()}</Tag>
                </Space>
                <Space>
                  <Typography.Text style={{ fontSize: 12 }}>{c.detail}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {c.duration_ms}ms
                  </Typography.Text>
                </Space>
              </Space>
            </List.Item>
          )}
        />
      </Card>

      <NotifyConfigCard />

      <SystemEventsCard />

      <Card
        size="small"
        title={
          <Space>
            最近健康日志 (看门狗每 60s 自动写)
            <Badge count={(logs ?? []).filter((l) => l.status !== 'ok').length} />
          </Space>
        }
      >
        <Table<HealthLog>
          size="small"
          rowKey="id"
          dataSource={logs ?? []}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 160,
              render: (v: string) => new Date(v).toLocaleString('zh-CN'),
            },
            {
              title: '检查项',
              dataIndex: 'check_name',
              width: 140,
              filters: Object.entries(CHECK_LABEL).map(([v, l]) => ({ value: v, text: l })),
              onFilter: (v, r) => r.check_name === v,
              render: (v: string) => CHECK_LABEL[v] ?? v,
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 90,
              filters: [
                { value: 'ok', text: 'OK' },
                { value: 'warn', text: 'WARN' },
                { value: 'fail', text: 'FAIL' },
              ],
              onFilter: (v, r) => r.status === v,
              render: (v: string) => (
                <Tag color={STATUS_COLOR[v]} icon={STATUS_ICON[v]}>
                  {v.toUpperCase()}
                </Tag>
              ),
            },
            { title: '详情', dataIndex: 'detail', ellipsis: true },
            {
              title: '耗时',
              dataIndex: 'duration_ms',
              width: 80,
              align: 'right',
              render: (v: number | null) => (v != null ? `${v}ms` : '-'),
            },
          ]}
        />
      </Card>
    </Space>
  );
}

// ----------------------------- 重启 / 看门狗事件 (业务需求 5) ----------- //

const EVENT_KIND_LABEL: Record<string, string> = {
  process_started: '进程启动',
  restart_requested: '收到重启请求',
  watchdog_triggered: '看门狗触发',
  orphan_killed: '孤立进程已 kill',
  restart_failed: '重启失败',
};
const EVENT_KIND_COLOR: Record<string, string> = {
  process_started: 'green',
  restart_requested: 'orange',
  watchdog_triggered: 'red',
  orphan_killed: 'volcano',
  restart_failed: 'red',
};

function SystemEventsCard() {
  const { data: events } = useQuery({
    queryKey: ['system-events'],
    queryFn: () => fetchSystemEvents(50),
    refetchInterval: 15000,
  });

  if (!events) return null;

  // 把含 snapshot_json 的 events 反向 (旧 → 新), 给 TrendChart 画折线
  const trendPoints = [...events]
    .filter((e) => e.snapshot_json)
    .reverse()
    .map((e) => ({
      ts: new Date(e.created_at).getTime(),
      mem: Number((e.snapshot_json as any)?.mem_used_pct ?? 0),
      db: Number((e.snapshot_json as any)?.db_latency_ms ?? 0),
      kind: e.kind,
    }));

  // 找最近一次 restart_requested → 下一个 process_started, 计算 diff
  const lastRestart = events.find((e) => e.kind === 'restart_requested');
  const lastStart = events.find((e) => e.kind === 'process_started');
  let restartDiff: React.ReactNode = null;
  if (lastRestart && lastStart && new Date(lastStart.created_at) > new Date(lastRestart.created_at)) {
    const before = lastRestart.snapshot_json as any;
    const after = lastStart.snapshot_json as any;
    restartDiff = (
      <Alert
        type="success"
        showIcon
        message={`上次重启完成: ${new Date(lastStart.created_at).toLocaleString('zh-CN')} (由 ${lastRestart.actor ?? '?'} 触发)`}
        description={
          <Row gutter={16} style={{ marginTop: 4 }}>
            <Col xs={12} sm={8} md={6}>
              <Statistic
                title="内存变化"
                value={`${(before?.mem_used_pct ?? 0).toFixed(1)} → ${(after?.mem_used_pct ?? 0).toFixed(1)} %`}
                valueStyle={{
                  fontSize: 16,
                  color: (after?.mem_used_pct ?? 0) < (before?.mem_used_pct ?? 0) ? '#3f8600' : '#cf1322',
                }}
              />
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Statistic
                title="DB 延迟变化"
                value={`${before?.db_latency_ms ?? '?'} → ${after?.db_latency_ms ?? '?'} ms`}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Statistic
                title="fail 数变化"
                value={`${before?.fail_count ?? 0} → ${after?.fail_count ?? 0}`}
                valueStyle={{
                  fontSize: 16,
                  color: (after?.fail_count ?? 0) < (before?.fail_count ?? 0) ? '#3f8600' : '#cf1322',
                }}
              />
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Statistic
                title="重启原因"
                value={lastRestart.detail ?? '-'}
                valueStyle={{ fontSize: 12 }}
              />
            </Col>
          </Row>
        }
      />
    );
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      {restartDiff}
      {trendPoints.length >= 2 && (
        <Card size="small" title="运行趋势 (取自历史事件 snapshot)">
          <TrendChart points={trendPoints} />
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            蓝线: 内存使用率 (%, 左轴) — 橙线: DB 延迟 (ms, 右轴) — 每点对应一次进程启动/重启事件
          </Typography.Text>
        </Card>
      )}
      <Card size="small" title={<Space>重启 / 看门狗事件 <Badge count={events.length} /></Space>}>
        <Table<SystemEvent>
          size="small"
          rowKey="id"
          dataSource={events}
          pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 160,
              render: (v: string) => new Date(v).toLocaleString('zh-CN'),
            },
            {
              title: '事件',
              dataIndex: 'kind',
              width: 150,
              render: (v: string) => (
                <Tag color={EVENT_KIND_COLOR[v] ?? 'default'}>
                  {EVENT_KIND_LABEL[v] ?? v}
                </Tag>
              ),
              filters: Object.entries(EVENT_KIND_LABEL).map(([v, l]) => ({ value: v, text: l })),
              onFilter: (v, r) => r.kind === v,
            },
            {
              title: '触发者',
              dataIndex: 'actor',
              width: 100,
              render: (v: string | null) => v ?? '-',
            },
            { title: '详情', dataIndex: 'detail', ellipsis: true },
            {
              title: '快照',
              width: 200,
              render: (_: any, r: SystemEvent) => {
                const s = r.snapshot_json as any;
                if (!s) return '-';
                return (
                  <Typography.Text style={{ fontSize: 11 }} type="secondary">
                    mem {s.mem_used_pct?.toFixed?.(0)}% / disk {s.disk_used_pct?.toFixed?.(0)}% /
                    db {s.db_ok ? '✓' : '✗'} / fail {s.fail_count ?? 0}
                  </Typography.Text>
                );
              },
            },
          ]}
        />
      </Card>
    </Space>
  );
}

// ----------------------------- 全自动任务清单 (业务需求 18) ----------- //

function fmtCron(s: Record<string, any>): string {
  if (s.interval_minutes) return `每 ${s.interval_minutes} 分钟`;
  const h = s.hour, m = s.minute;
  if (h !== undefined && m !== undefined) {
    return `每天 ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  }
  return JSON.stringify(s);
}

function SchedulerTab() {
  const qc = useQueryClient();
  const { data: jobs = [] } = useQuery({
    queryKey: ['scheduler-jobs'],
    queryFn: fetchSchedulerJobs,
    refetchInterval: 30000,
  });
  const { data: runs = [] } = useQuery({
    queryKey: ['scheduler-runs'],
    queryFn: () => fetchSchedulerRuns(100),
    refetchInterval: 30000,
  });

  const triggerMut = useMutation({
    mutationFn: (id: string) => triggerSchedulerJob(id),
    onSuccess: (_r, id) => {
      message.success(`已触发 ${id}, 1-2 秒后看运行结果`);
      setTimeout(() => qc.invalidateQueries({ queryKey: ['scheduler-runs'] }), 2000);
    },
  });

  const enableMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateSchedulerJob(id, { enabled }),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['scheduler-jobs'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });

  const [editJob, setEditJob] = useState<SchedulerJob | null>(null);
  const [editHour, setEditHour] = useState(9);
  const [editMinute, setEditMinute] = useState(0);
  const [editInterval, setEditInterval] = useState(60);

  const openEdit = (j: SchedulerJob) => {
    setEditJob(j);
    if (j.kind === 'cron') {
      setEditHour(Number(j.schedule?.hour ?? 9));
      setEditMinute(Number(j.schedule?.minute ?? 0));
    } else {
      setEditInterval(Number(j.schedule?.interval_minutes ?? 60));
    }
  };

  const scheduleMut = useMutation({
    mutationFn: (j: SchedulerJob) =>
      updateSchedulerJob(
        j.job_id,
        j.kind === 'cron'
          ? { cron: { hour: editHour, minute: editMinute } }
          : { interval_minutes: editInterval },
      ),
    onSuccess: () => {
      message.success('定时已更新, 即时生效');
      setEditJob(null);
      qc.invalidateQueries({ queryKey: ['scheduler-jobs'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
             message="业务需求 18: 所有自动跑的任务"
             description="包含: 17:00 退款检查、库存预警扫描、远期订单激活、财务公式核对 等. 'next_run_at' 是下一次自动跑的时间. 立即按钮可手工触发一次." />
      <Card size="small" title="对账与同步 · 手动执行" type="inner">
        <Space wrap>
          {[
            { id: 'daily_0940_alipay_match', label: '流水↔订单 对账匹配', primary: true },
            { id: 'daily_10_data_reconcile', label: '总额对账(写异常)' },
            { id: 'daily_08_data_quality', label: '数据异常扫描' },
            { id: 'email_poll_alipay_6h', label: '拉取支付宝流水' },
            { id: 'feishu_sync_30min', label: '飞书同步' },
          ].map((j) => (
            <Button
              key={j.id}
              type={j.primary ? 'primary' : undefined}
              loading={triggerMut.isPending}
              onClick={() => triggerMut.mutate(j.id)}
            >
              {j.label}
            </Button>
          ))}
        </Space>
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          这些任务每天会自动跑;点按钮可立刻手动执行一次,结果见下方「最近执行记录」。
          「流水↔订单 对账匹配」= 归类 → 按订单号回填 → 按金额匹配(4规则)。
        </div>
      </Card>
      <Card size="small" title="已注册定时任务">
        <Table<SchedulerJob>
          size="small" rowKey="job_id" dataSource={jobs} pagination={false}
          columns={[
            { title: '任务名', dataIndex: 'label' },
            { title: 'job_id', dataIndex: 'job_id', width: 240,
              render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code>,
            },
            { title: '频率', dataIndex: 'schedule', width: 180,
              render: (v: any) => fmtCron(v) },
            { title: '下次执行', dataIndex: 'next_run_at', width: 200,
              render: (v: string | null) => v ?
                new Date(v).toLocaleString('zh-CN') : '-',
            },
            { title: '启用', dataIndex: 'enabled', width: 70,
              render: (v: boolean, r: SchedulerJob) => (
                <Switch size="small" checked={v} loading={enableMut.isPending}
                        onChange={(checked) => enableMut.mutate({ id: r.job_id, enabled: checked })} />
              ),
            },
            { title: '操作', width: 180,
              render: (_: any, r: SchedulerJob) => (
                <Space size="small">
                  <Button size="small" onClick={() => openEdit(r)}>改时间</Button>
                  <Button size="small" loading={triggerMut.isPending}
                          onClick={() => triggerMut.mutate(r.job_id)}>
                    立即执行
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        open={!!editJob}
        title={editJob ? `定时设置 · ${editJob.label}` : ''}
        onCancel={() => setEditJob(null)}
        onOk={() => editJob && scheduleMut.mutate(editJob)}
        confirmLoading={scheduleMut.isPending}
        destroyOnClose
      >
        {editJob?.kind === 'cron' ? (
          <Space>
            每天
            <InputNumber min={0} max={23} value={editHour} onChange={(v) => setEditHour(Number(v ?? 0))} addonAfter="时" />
            <InputNumber min={0} max={59} value={editMinute} onChange={(v) => setEditMinute(Number(v ?? 0))} addonAfter="分" />
            执行
          </Space>
        ) : (
          <Space>
            每隔
            <InputNumber min={1} max={10080} value={editInterval} onChange={(v) => setEditInterval(Number(v ?? 1))} addonAfter="分钟" />
            执行 (60=1时, 360=6时, 1440=1天)
          </Space>
        )}
      </Modal>
      <Card size="small" title="最近执行记录">
        <Table<SchedulerRun>
          size="small" rowKey="id" dataSource={runs}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '时间', dataIndex: 'started_at', width: 170,
              render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-',
            },
            { title: '任务', dataIndex: 'job_label', width: 180 },
            { title: '状态', dataIndex: 'status', width: 80,
              render: (v: string) => (
                <Tag color={v === 'ok' ? 'green' : v === 'fail' ? 'red' : 'default'}>
                  {v}
                </Tag>
              ),
            },
            { title: '耗时', dataIndex: 'duration_ms', width: 90,
              render: (v: number | null) => v != null ? `${v} ms` : '-',
            },
            { title: '结果', dataIndex: 'result_summary',
              render: (v: any) => v ? (
                <code style={{ fontSize: 11 }}>{JSON.stringify(v)}</code>
              ) : '-',
            },
            { title: '错误', dataIndex: 'error',
              render: (v: string | null) => v ? (
                <Typography.Text type="danger" style={{ fontSize: 11 }}>
                  {v.slice(0, 120)}
                </Typography.Text>
              ) : '-',
            },
          ]}
        />
      </Card>
    </Space>
  );
}

// ----------------------------- 运行趋势折线图 (业务需求扩展) ------------ //

interface TrendPoint {
  ts: number;
  mem: number;
  db: number;
  kind: string;
}

function TrendChart({ points }: { points: TrendPoint[] }) {
  // 内联 SVG, 不引第三方 chart 库 — 折线足够看趋势
  const w = 720;
  const h = 180;
  const padL = 36;
  const padR = 44;
  const padT = 12;
  const padB = 28;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;

  const memMax = Math.max(100, ...points.map((p) => p.mem));
  const dbMax = Math.max(50, ...points.map((p) => p.db));

  const x = (i: number) =>
    padL + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const yMem = (v: number) => padT + innerH - (v / memMax) * innerH;
  const yDb = (v: number) => padT + innerH - (v / dbMax) * innerH;

  const memPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${yMem(p.mem)}`).join(' ');
  const dbPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${yDb(p.db)}`).join(' ');

  // Y 轴刻度
  const memTicks = [0, 50, 100].filter((t) => t <= memMax);
  const dbTicks = [0, Math.round(dbMax / 2), Math.round(dbMax)];

  // X 轴时间标 — 最多 5 个
  const xTickIdxs: number[] = [];
  const tickCount = Math.min(5, points.length);
  for (let i = 0; i < tickCount; i++) {
    xTickIdxs.push(Math.round((i / (tickCount - 1 || 1)) * (points.length - 1)));
  }

  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: 'block' }}>
      {/* 网格线 + Y 左轴 (mem%) */}
      {memTicks.map((t) => (
        <g key={`mt${t}`}>
          <line
            x1={padL}
            x2={w - padR}
            y1={yMem(t)}
            y2={yMem(t)}
            stroke="#f0f0f0"
            strokeWidth={1}
          />
          <text x={padL - 4} y={yMem(t) + 3} fontSize={10} textAnchor="end" fill="#1677ff">
            {t}%
          </text>
        </g>
      ))}
      {/* Y 右轴 (db ms) */}
      {dbTicks.map((t) => (
        <text
          key={`dt${t}`}
          x={w - padR + 4}
          y={yDb(t) + 3}
          fontSize={10}
          fill="#fa8c16"
        >
          {t}ms
        </text>
      ))}
      {/* 折线 */}
      <path d={memPath} fill="none" stroke="#1677ff" strokeWidth={2} />
      <path d={dbPath} fill="none" stroke="#fa8c16" strokeWidth={2} />
      {/* 点 */}
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={yMem(p.mem)} r={2.5} fill="#1677ff" />
          <circle cx={x(i)} cy={yDb(p.db)} r={2.5} fill="#fa8c16" />
          {p.kind === 'watchdog_triggered' && (
            <circle cx={x(i)} cy={padT + 4} r={4} fill="#cf1322">
              <title>{`看门狗触发: ${new Date(p.ts).toLocaleString('zh-CN')}`}</title>
            </circle>
          )}
        </g>
      ))}
      {/* X 轴时间 */}
      {xTickIdxs.map((i) => (
        <text
          key={`xt${i}`}
          x={x(i)}
          y={h - padB + 14}
          fontSize={10}
          textAnchor="middle"
          fill="#999"
        >
          {new Date(points[i].ts).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </text>
      ))}
    </svg>
  );
}

// ----------------------------- 通知配置 (业务需求扩展) ----------------- //

const NOTIFY_PROVIDER_HINT: Record<string, string> = {
  none: '关闭通知',
  slack: 'Slack incoming webhook, 形如 https://hooks.slack.com/services/T0/B0/...',
  wechat_work: '企业微信群机器人, 形如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
  dingtalk: '钉钉群机器人, 形如 https://oapi.dingtalk.com/robot/send?access_token=...',
  feishu: '飞书群机器人, 形如 https://open.feishu.cn/open-apis/bot/v2/hook/...',
};

function NotifyConfigCard() {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const { data: cfg, isLoading } = useQuery<NotifyConfig>({
    queryKey: ['notify-config'],
    queryFn: fetchNotifyConfig,
  });

  const saveMut = useMutation({
    mutationFn: (payload: { provider?: string; webhook?: string; text_channels?: string }) => updateNotifyConfig(payload),
    onSuccess: () => {
      message.success('通知配置已保存');
      form.setFieldValue('webhook', '');
      qc.invalidateQueries({ queryKey: ['notify-config'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const testMut = useMutation({
    mutationFn: testNotifyConfig,
    onSuccess: (r) => {
      setTestResult({ ok: r.ok, text: r.detail });
      if (r.ok) message.success('已发出测试消息, 请到群里确认');
      else message.warning('未发出: ' + r.detail);
    },
    onError: (e: any) => setTestResult({ ok: false, text: e?.response?.data?.detail ?? '请求失败' }),
  });

  const clearMut = useMutation({
    mutationFn: () => updateNotifyConfig({ webhook: '__CLEAR__' }),
    onSuccess: () => {
      message.success('Webhook 已清除');
      qc.invalidateQueries({ queryKey: ['notify-config'] });
    },
  });

  if (isLoading || !cfg) {
    return <Card size="small" title="运维通知" loading />;
  }

  return (
    <Card
      size="small"
      title={<Space>运维通知 <Tag color="blue">看门狗触发自动推送</Tag></Space>}
      extra={
        <Button
          icon={<ExperimentOutlined />}
          size="small"
          loading={testMut.isPending}
          disabled={cfg.provider === 'none' || !cfg.webhook_set}
          onClick={() => testMut.mutate()}
        >
          测试通知
        </Button>
      }
    >
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="当前 Provider">
          <Tag color={cfg.provider === 'none' ? 'default' : 'green'}>{cfg.provider}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Webhook">
          {cfg.webhook_set ? (
            <Space>
              <Tag color="green" icon={<KeyOutlined />}>
                {cfg.webhook_masked}
              </Tag>
              <Button size="small" type="link" danger onClick={() => clearMut.mutate()}>
                清除
              </Button>
            </Space>
          ) : (
            <Tag color="default">未设置</Tag>
          )}
        </Descriptions.Item>
      </Descriptions>

      <Form
        form={form}
        layout="vertical"
        initialValues={{ provider: cfg.provider, webhook: '', text_channels: (cfg.text_channels || 'feishu,webhook').split(',') }}
        onFinish={(v) => saveMut.mutate({ ...v, text_channels: Array.isArray(v.text_channels) ? v.text_channels.join(',') : v.text_channels })}
      >
        <Form.Item name="provider" label="通知平台" rules={[{ required: true }]}>
          <Select
            options={cfg.supported_providers.map((p) => ({ value: p.value, label: p.label }))}
          />
        </Form.Item>
        <Form.Item
          shouldUpdate
          noStyle
        >
          {() => (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: -8 }}>
              {NOTIFY_PROVIDER_HINT[form.getFieldValue('provider') ?? cfg.provider]}
            </Typography.Paragraph>
          )}
        </Form.Item>
        <Form.Item
          name="webhook"
          label="Webhook URL (留空 = 不修改)"
          extra="加密存储, 仅显示前 3 后 4 位"
        >
          <Input.Password placeholder={cfg.webhook_set ? '(已设置, 留空保留)' : '请输入完整 URL'} />
        </Form.Item>
        <Form.Item
          name="text_channels"
          label="纯文本通知推送到 (可多选 = 双推)"
          extra="富内容(下单图/工厂单ZIP/交互卡片)始终走飞书; 此项只管纯文本提醒与日报(NPD/评价资产/体检/对账等)"
        >
          <Select
            mode="multiple"
            options={[{ label: '飞书群', value: 'feishu' }, { label: '企业微信', value: 'webhook' }]}
          />
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
          message={testResult.ok ? '已发送测试消息' : '发送失败'}
          description={<code style={{ fontSize: 12 }}>{testResult.text}</code>}
          closable
          onClose={() => setTestResult(null)}
        />
      )}
    </Card>
  );
}

// ----------------------------- 数据管理 / 清空导入数据 ---------------- //

function DataResetTab() {
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="large">
      <BackupSection />
      <Divider style={{ margin: 0 }} />
      <ResetSection />
    </Space>
  );
}

function BackupSection() {
  const qc = useQueryClient();
  const { data: cfg } = useQuery({ queryKey: ['backup-config'], queryFn: fetchBackupConfig });
  const { data: files } = useQuery({ queryKey: ['backup-list'], queryFn: fetchBackupList });

  // 本地编辑态 (随配置加载初始化)
  const [enabled, setEnabled] = useState<boolean>(true);
  const [interval, setIntervalDays] = useState<number>(7);
  const [dir, setDir] = useState<string>('');
  const [startDate, setStartDate] = useState<string>('');
  React.useEffect(() => {
    if (cfg) {
      setEnabled(cfg.auto_enabled);
      setIntervalDays(cfg.interval_days);
      setDir(cfg.dir);
      setStartDate(cfg.start_date ?? '');
    }
  }, [cfg]);

  const exportMut = useMutation({
    mutationFn: () => exportAndDownload(),
    onSuccess: (r) => {
      message.success(`已导出并下载 ${r.file} (${r.size_mb} MB)`);
      qc.invalidateQueries({ queryKey: ['backup-list'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导出失败'),
  });

  const saveMut = useMutation({
    mutationFn: () =>
      updateBackupConfig({
        auto_enabled: enabled,
        interval_days: interval,
        dir: dir.trim(),
        start_date: startDate.trim(),
      }),
    onSuccess: () => {
      message.success('自动备份配置已保存');
      qc.invalidateQueries({ queryKey: ['backup-config'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        message="数据备份 / 导出"
        description="一键把系统所有数据导出成一个 Excel（每张表一个工作表），并可设置每隔若干天自动备份一份到指定目录。导入前、清空前强烈建议先导出一份。"
      />

      <Card size="small" title="📤 一键全量导出">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Button
            type="primary"
            icon={<CloudDownloadOutlined />}
            loading={exportMut.isPending}
            onClick={() => exportMut.mutate()}
          >
            立即导出并下载 Excel
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            导出的文件同时会存到备份目录（{cfg?.dir ?? '...'}），最多保留 {cfg?.max_backups ?? 60} 份。
          </Typography.Text>
        </Space>
      </Card>

      <Card size="small" title="⏰ 定时自动备份">
        <Form layout="vertical" style={{ maxWidth: 520 }}>
          <Form.Item label="开启自动备份" style={{ marginBottom: 12 }}>
            <Switch checked={enabled} onChange={setEnabled} />
          </Form.Item>
          <Form.Item label="每隔多少天备份一次" style={{ marginBottom: 12 }}>
            <InputNumber
              min={1}
              max={365}
              value={interval}
              onChange={(v) => setIntervalDays(Number(v) || 7)}
              addonAfter="天"
              disabled={!enabled}
            />
          </Form.Item>
          <Form.Item label="起始日期（可填，留空则从今天起算）" style={{ marginBottom: 12 }}>
            <Input
              placeholder="YYYY-MM-DD，如 2026-06-10"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              disabled={!enabled}
              style={{ maxWidth: 240 }}
            />
          </Form.Item>
          <Form.Item label="备份保存目录" style={{ marginBottom: 12 }}>
            <Input
              placeholder="/data/backups"
              value={dir}
              onChange={(e) => setDir(e.target.value)}
            />
          </Form.Item>
          <Space>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saveMut.isPending}
              onClick={() => saveMut.mutate()}
            >
              保存配置
            </Button>
            {cfg?.last_run_at && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                上次自动备份：{new Date(cfg.last_run_at).toLocaleString()}
              </Typography.Text>
            )}
            {cfg?.next_run_at && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                下次预计：{new Date(cfg.next_run_at).toLocaleDateString()}
              </Typography.Text>
            )}
          </Space>
        </Form>
      </Card>

      <Card size="small" title={<span>🗂 已有备份（{files?.length ?? 0} 份）</span>}>
        <List
          size="small"
          dataSource={files ?? []}
          locale={{ emptyText: '暂无备份文件' }}
          renderItem={(f: BackupFile) => (
            <List.Item
              actions={[
                <Button
                  key="dl"
                  type="link"
                  size="small"
                  icon={<DownloadOutlined />}
                  onClick={() => downloadBackup(f.filename)}
                >
                  下载
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={<span style={{ fontSize: 13 }}>{f.filename}</span>}
                description={
                  <span style={{ fontSize: 12 }}>
                    {f.size_mb} MB · {new Date(f.created_at).toLocaleString()}
                  </span>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </Space>
  );
}

function ResetSection() {
  const qc = useQueryClient();
  // 三次确认的步骤: 0 = 未开始, 1/2 = 前两次确认, 3 = 输入密码
  const [step, setStep] = useState(0);
  const [password, setPassword] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [clearFeishu, setClearFeishu] = useState(false);
  const [confirmFeishu, setConfirmFeishu] = useState('');
  const [result, setResult] = useState<ResetDataResult | null>(null);

  const { data: tables } = useQuery({
    queryKey: ['reset-data-tables'],
    queryFn: fetchResetDataTables,
  });

  const resetMut = useMutation({
    mutationFn: () => resetBusinessData(password, { clearFeishu }),
    onSuccess: (r) => {
      setResult(r);
      if (r.feishu_error) {
        message.warning(`本地已清空 ${r.total_deleted} 行；飞书清空失败：${r.feishu_error}`);
      } else if (r.feishu_cleared) {
        const fcount = Object.values(r.feishu_deleted).reduce((a, b) => a + b, 0);
        message.success(`已清空本地 ${r.total_deleted} 行 + 飞书云端 ${fcount} 条`);
      } else {
        message.success(`已清空 ${r.total_deleted} 行业务数据`);
      }
      closeFlow();
      // 业务数据全变了, 失效所有缓存
      qc.invalidateQueries();
    },
    onError: (e: any) => {
      message.error(e?.response?.data?.detail ?? '清空失败');
    },
  });

  const closeFlow = () => {
    setStep(0);
    setPassword('');
    setConfirmText('');
    setClearFeishu(false);
    setConfirmFeishu('');
  };

  const okDisabled =
    !password ||
    confirmText !== 'DELETE' ||
    (clearFeishu && confirmFeishu !== 'DELETE FEISHU');

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="warning"
        showIcon
        message="清空导入数据"
        description={
          <Space direction="vertical" size={4}>
            <span>此操作会删除<b>所有导入的业务数据</b>（订单、流水、产品、物料、BOM、库存、对账、售后、异常等），让你可以重新导入一份干净的总表。</span>
            <span style={{ color: '#cf1322' }}><b>删除不可恢复，请先自行备份数据库。</b></span>
          </Space>
        }
      />

      <Card size="small" title="✅ 以下内容会被【保留】，绝不删除">
        <Space wrap>
          <Tag color="green">登录账号 (users)</Tag>
          <Tag color="green">系统设置 (system_settings)</Tag>
          <Tag color="green">AI / OCR 配置</Tag>
          <Tag color="green">飞书表绑定 (feishu_table_bindings)</Tag>
          <Tag color="green">飞书同步映射 (feishu_sync_map)</Tag>
        </Space>
      </Card>

      <Card size="small" title={<span>🗑 以下 {tables?.length ?? 0} 张业务数据表会被清空</span>}>
        <Space wrap size={[4, 4]}>
          {(tables ?? []).map((t) => (
            <Tag key={t} color="volcano" style={{ fontSize: 11 }}>{t}</Tag>
          ))}
        </Space>
      </Card>

      <Button danger type="primary" icon={<DeleteOutlined />} onClick={() => setStep(1)}>
        清空导入数据…
      </Button>

      {result && (
        <Alert
          type="success"
          showIcon
          closable
          onClose={() => setResult(null)}
          message={`清空完成：本地共删除 ${result.total_deleted} 行${
            result.feishu_cleared
              ? `，飞书云端删除 ${Object.values(result.feishu_deleted).reduce((a, b) => a + b, 0)} 条`
              : ''
          }`}
          description={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              账号、设置、配置均已保留。
              {result.feishu_error ? `（飞书清空失败：${result.feishu_error}）` : ''}
              现在可以去「导入」页上传新的总表了。
            </Typography.Text>
          }
        />
      )}

      {/* 第 1 次确认 */}
      <Modal
        open={step === 1}
        title="第 1 / 3 次确认"
        okText="我确定，继续"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        onOk={() => setStep(2)}
        onCancel={closeFlow}
      >
        <p>你即将清空<b>所有导入的业务数据</b>。账号和设置会保留，但订单、流水等数据将被永久删除。</p>
        <p>确定要继续吗？</p>
      </Modal>

      {/* 第 2 次确认 */}
      <Modal
        open={step === 2}
        title="第 2 / 3 次确认"
        okText="确认，进入最后一步"
        cancelText="返回"
        okButtonProps={{ danger: true }}
        onOk={() => setStep(3)}
        onCancel={closeFlow}
      >
        <Alert
          type="error"
          showIcon
          message="此操作不可恢复"
          description="删除后无法撤销。如果还没备份数据库，请先取消并备份。"
        />
      </Modal>

      {/* 第 3 次确认: 密码 + 输入 DELETE */}
      <Modal
        open={step === 3}
        title="第 3 / 3 次确认 — 输入密码"
        okText="清空数据"
        cancelText="取消"
        okButtonProps={{
          danger: true,
          disabled: okDisabled,
          loading: resetMut.isPending,
        }}
        onOk={() => resetMut.mutate()}
        onCancel={closeFlow}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>请输入你的<b>管理员登录密码</b>以确认身份：</Typography.Text>
          <Input.Password
            placeholder="当前管理员密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
          <Typography.Text>再在下方输入大写 <code>DELETE</code> 以最终确认：</Typography.Text>
          <Input
            placeholder="DELETE"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
          />

          <Divider style={{ margin: '8px 0' }} />
          <Checkbox checked={clearFeishu} onChange={(e) => setClearFeishu(e.target.checked)}>
            <b style={{ color: '#cf1322' }}>同时清空飞书云端数据</b>
          </Checkbox>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            勾选后会删除所有已绑定飞书多维表格里的<b>全部记录</b>（云端数据也一并清空，不可恢复）。
            不勾选则只清本地——但飞书每 30 分钟会把云端数据同步回来，这通常就是「清了又出现」的原因。
          </Typography.Text>
          {clearFeishu && (
            <>
              <Typography.Text>请输入 <code>DELETE FEISHU</code> 以确认连飞书云端一起清空：</Typography.Text>
              <Input
                placeholder="DELETE FEISHU"
                value={confirmFeishu}
                onChange={(e) => setConfirmFeishu(e.target.value)}
              />
            </>
          )}
        </Space>
      </Modal>
    </Space>
  );
}
