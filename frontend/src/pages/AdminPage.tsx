import React, { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  KeyOutlined,
  PoweroffOutlined,
  ReloadOutlined,
  UserAddOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AuditLog,
  HealthLog,
  IntegrationConfig,
  Integrations,
  MeUser,
  SystemEvent,
  SystemStatus,
  createUser,
  fetchHealthLogs,
  fetchIntegrations,
  fetchRoles,
  fetchSystemEvents,
  fetchSystemStatus,
  listAuditLogs,
  listAuthUsers,
  restartApi,
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
          { key: 'monitor', label: <Space><DashboardOutlined />系统监控 / 看门狗</Space>, children: <MonitorTab /> },
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
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="数据库"
              value={status.db_ok ? '在线' : '离线'}
              valueStyle={{ color: status.db_ok ? '#3f8600' : '#cf1322' }}
              suffix={status.db_latency_ms != null ? `${status.db_latency_ms}ms` : undefined}
            />
          </Card>
        </Col>
        <Col span={6}>
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
        <Col span={6}>
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
        <Col span={6}>
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
          pagination={{ pageSize: 20 }}
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
            <Col span={6}>
              <Statistic
                title="内存变化"
                value={`${(before?.mem_used_pct ?? 0).toFixed(1)} → ${(after?.mem_used_pct ?? 0).toFixed(1)} %`}
                valueStyle={{
                  fontSize: 16,
                  color: (after?.mem_used_pct ?? 0) < (before?.mem_used_pct ?? 0) ? '#3f8600' : '#cf1322',
                }}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="DB 延迟变化"
                value={`${before?.db_latency_ms ?? '?'} → ${after?.db_latency_ms ?? '?'} ms`}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="fail 数变化"
                value={`${before?.fail_count ?? 0} → ${after?.fail_count ?? 0}`}
                valueStyle={{
                  fontSize: 16,
                  color: (after?.fail_count ?? 0) < (before?.fail_count ?? 0) ? '#3f8600' : '#cf1322',
                }}
              />
            </Col>
            <Col span={6}>
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
      <Card size="small" title={<Space>重启 / 看门狗事件 <Badge count={events.length} /></Space>}>
        <Table<SystemEvent>
          size="small"
          rowKey="id"
          dataSource={events}
          pagination={{ pageSize: 10 }}
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
