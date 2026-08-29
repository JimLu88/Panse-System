import { useState } from 'react';
import {
  Alert, Badge, Button, Card, Descriptions, Input, InputNumber, message,
  Modal, Popconfirm, Space, Table, Tag, Tooltip, Typography,
} from 'antd';
import { CloudDownloadOutlined, QrcodeOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AgentFreshness, IngestFile, getWebAgentSettings, getWebAgentStatus,
  ingestNow, putWebAgentSettings, resumeWebAgentScans, runWebAgentNow,
  submitShippingPassword,
} from '../api/webAgent';

const { Title, Text } = Typography;

const CATEGORY_LABEL: Record<string, string> = {
  taobao_report: '淘宝订单报表',
  settlement: '聚合账单',
  promotion: '推广流水(万相台)',
  wanshifu: '万师傅订单',
  balance: '账户余额截图',
};

const FRESH_META: Record<AgentFreshness['status'], { color: string; label: string }> = {
  fresh: { color: 'green', label: '新鲜' },
  due: { color: 'orange', label: '到期未更' },
  stale: { color: 'red', label: '已过期' },
  missing: { color: 'default', label: '从未成功' },
};

const FILE_STATUS_META: Record<string, { color: string; label: string }> = {
  imported: { color: 'green', label: '已导入' },
  pending_password: { color: 'orange', label: '待口令' },
  pending_read: { color: 'blue', label: '待读数' },
  unsupported: { color: 'default', label: '仅归档' },
  error: { color: 'red', label: '导入失败' },
};

export default function WebAgentPage() {
  const qc = useQueryClient();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [ivOrders, setIvOrders] = useState<number | null>(null);
  const [ivBalance, setIvBalance] = useState<number | null>(null);
  const [schedTime, setSchedTime] = useState<string | null>(null);  // 每日触发时刻 HH:MM
  const [token, setToken] = useState('');
  const [shippingPassword, setShippingPassword] = useState('');

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['web-agent-status'],
    queryFn: getWebAgentStatus,
    refetchInterval: 30000,
  });
  const { data: settings } = useQuery({
    queryKey: ['web-agent-settings'],
    queryFn: getWebAgentSettings,
  });

  const runMut = useMutation({
    mutationFn: runWebAgentNow,
    onSuccess: () => {
      message.success('已开始取数 (后台运行, 本页 30 秒自动刷新进度)');
      qc.invalidateQueries({ queryKey: ['web-agent-status'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '触发失败'),
  });
  const ingestMut = useMutation({
    mutationFn: ingestNow,
    onSuccess: (r) => {
      message.success(`扫描完成: 新导入 ${r?.imported ?? 0} 份`);
      qc.invalidateQueries();   // 扫描导入了新数据 → 失效全部缓存, 大盘/各列表即时刷新 (用户 2026-06-24)
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '扫描失败'),
  });
  const scanMut = useMutation({
    mutationFn: resumeWebAgentScans,
    onSuccess: () => {
      message.success('登录恢复流程已开始，二维码会直接发到企业微信');
      qc.invalidateQueries({ queryKey: ['web-agent-status'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '登录恢复启动失败'),
  });
  const passwordMut = useMutation({
    mutationFn: submitShippingPassword,
    onSuccess: (r) => {
      setShippingPassword('');
      if (r.imported > 0) {
        message.success(`口令已生效：导入 ${r.imported} 份报表，更新 ${r.updated} 单`);
      } else if (r.failure_reason) {
        message.warning(`口令已收到，但未匹配报表：${r.failure_reason}`);
      } else {
        message.success('口令已保存，后续报表会自动尝试解密');
      }
      qc.invalidateQueries({ queryKey: ['web-agent-status'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '口令提交失败'),
  });
  const settingsMut = useMutation({
    mutationFn: putWebAgentSettings,
    onSuccess: () => {
      message.success('设置已保存');
      setSettingsOpen(false);
      setToken('');
      setSchedTime(null);
      qc.invalidateQueries({ queryKey: ['web-agent-settings'] });
      qc.invalidateQueries({ queryKey: ['web-agent-status'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const agent = data?.agent;
  const running = Boolean(data?.orchestration?.running);
  const pendingManual = (data?.orchestration?.pending_manual as { task: string; reason: string }[]) ?? [];
  const noSession = (data?.tasks ?? []).filter((t) => !t.has_session && !t.skip_reason);

  return (
    <div style={{ padding: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }}>
        <Title level={4} style={{ margin: 0 }}>
          自动取数{' '}
          <Badge
            status={agent?.online ? 'success' : 'error'}
            text={agent?.online ? '取数服务在线' : '取数服务离线'}
          />
          {settings?.schedule_enabled !== false && (
            <Text type="secondary" style={{ fontSize: 13, marginLeft: 10 }}>
              · 每天 <b>{settings?.schedule_time ?? '—'}</b> 自动取数（点「更新间隔设置」可改）
            </Text>
          )}
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
          <Button icon={<SettingOutlined />} onClick={() => setSettingsOpen(true)}>更新间隔设置</Button>
          <Button icon={<QrcodeOutlined />} loading={scanMut.isPending}
            onClick={() => scanMut.mutate()}>
            开始扫码 / 重新登录
          </Button>
          <Tooltip title="只扫描共享目录里已下载的文件并导入, 不开浏览器">
            <Button loading={ingestMut.isPending} onClick={() => ingestMut.mutate()}>扫描导入</Button>
          </Tooltip>
          <Popconfirm
            title="立即取数?"
            description="会在 Windows 上弹出浏览器逐个跑任务 (约 10-30 分钟), 期间请勿操作那台电脑的浏览器。"
            onConfirm={() => runMut.mutate()}
          >
            <Button type="primary" icon={<CloudDownloadOutlined />}
              loading={runMut.isPending || running} disabled={!agent?.online}>
              {running ? '取数进行中…' : '立即取数'}
            </Button>
          </Popconfirm>
        </Space>
      </Space>

      {!agent?.online && (
        <Alert
          type="error" showIcon style={{ marginBottom: 12 }}
          message="取数服务 (Panse-Web-Agent :8500) 不在线"
          description={`请在 Windows 上启动: D:\\Panse-Web-Agent → run.ps1。错误: ${agent?.error ?? ''}`}
        />
      )}
      {agent?.online && !agent.token_configured && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="未配置 Web-Agent token"
          description="在「更新间隔设置」里粘贴 token (Web-Agent 控制台首页可复制), 否则无法触发任务。"
        />
      )}

      {/* 待人工卡片 */}
      {(pendingManual.length > 0 || noSession.length > 0) && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message={`待人工 ${pendingManual.length + noSession.length} 项`}
          description={
            <ul style={{ marginBottom: 0, paddingLeft: 18 }}>
              {noSession.map((t) => (
                <li key={t.id}>
                  <b>{t.title}</b>：登录态缺失 —
                  <Button type="link" size="small" icon={<QrcodeOutlined />}
                    loading={scanMut.isPending} onClick={() => scanMut.mutate()}>
                    开始扫码 / 重新登录
                  </Button>
                </li>
              ))}
              {pendingManual.map((p, i) => <li key={i}><b>{p.task}</b>：{p.reason}</li>)}
            </ul>
          }
        />
      )}

      {/* 发货报表口令在 ERP 内提交，飞书订单群只保留订单图片。 */}
      {data?.shipping_password && (
        <Card size="small" title="发货报表口令" style={{ marginBottom: 12 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text type={data.shipping_password.configured ? 'success' : 'secondary'}>
              {data.shipping_password.configured
                ? `最近一次口令收到于 ${(data.shipping_password.received_at ?? '').replace('T', ' ').slice(0, 19)}`
                : '当前没有可用口令'}
            </Text>
            <Text type="secondary">{data.shipping_password.hint}</Text>
            <Space.Compact style={{ maxWidth: 520, width: '100%' }}>
              <Input.Password value={shippingPassword} placeholder="输入淘宝发来的发货报表口令"
                onChange={(e) => setShippingPassword(e.target.value)}
                onPressEnter={() => shippingPassword.trim() && passwordMut.mutate(shippingPassword)} />
              <Button type="primary" loading={passwordMut.isPending}
                disabled={!shippingPassword.trim()}
                onClick={() => passwordMut.mutate(shippingPassword)}>
                提交并自动解密
              </Button>
            </Space.Compact>
          </Space>
        </Card>
      )}

      {/* 未就绪项 (用户已知: 支付宝 API/个人号) */}
      <Card size="small" title="暂未接入 (等外部条件)" style={{ marginBottom: 12 }}>
        <ul style={{ marginBottom: 0, paddingLeft: 18 }}>
          {(data?.not_ready ?? []).map((n, i) => (
            <li key={i}><b>{n.item}</b> — <Text type="secondary">{n.reason}</Text></li>
          ))}
        </ul>
      </Card>

      {/* 数据新鲜度 */}
      <Card size="small" title="数据新鲜度 (按更新间隔)" style={{ marginBottom: 12 }}>
        <Space wrap>
          {(data?.freshness ?? []).map((f) => {
            const meta = FRESH_META[f.status];
            return (
              <Card key={f.category} size="small" style={{ width: 210 }}>
                <Space direction="vertical" size={2}>
                  <Text strong>{CATEGORY_LABEL[f.category] ?? f.category}</Text>
                  <Tag color={meta.color}>{meta.label}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    最近成功: {f.last_success ? f.last_success.replace('T', ' ') : '—'}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>间隔 {f.interval_days} 天</Text>
                </Space>
              </Card>
            );
          })}
        </Space>
      </Card>

      {/* 平台登录态 */}
      <Card size="small" title="平台任务与登录态" style={{ marginBottom: 12 }}>
        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          dataSource={data?.tasks ?? []}
          pagination={false}
          columns={[
            { title: '任务', dataIndex: 'title' },
            {
              title: '登录态', dataIndex: 'has_session', width: 110,
              render: (v: boolean, r) => r.skip_reason
                ? <Tag>暂不编排</Tag>
                : v ? <Tag color="green">已登录</Tag> : <Tag color="red">需扫码</Tag>,
            },
            {
              title: '说明', dataIndex: 'skip_reason', ellipsis: true,
              render: (v: string | null) => v ?? '',
            },
          ]}
        />
        <div style={{ marginTop: 8 }}>
          <Button icon={<QrcodeOutlined />} href="http://127.0.0.1:8500" target="_blank">
            打开取数控制台 (扫码 / 录制 / 高级设置)
          </Button>
          <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
            二维码有效期约 2 分钟, 需扫码时现开现扫; 淘宝登录态可用很久, 失效会在上面亮红灯。
          </Text>
        </div>
      </Card>

      {/* 最近一次扫描结果 — 三段式: 下载 → 导入 → 异常 */}
      <Card
        size="small"
        title={`最近扫描结果 (扫描 ${data?.last_ingest?.scanned ?? 0} / 新导入 ${data?.last_ingest?.imported ?? 0} / 已知跳过 ${data?.last_ingest?.skipped_known ?? 0} / 待人工 ${data?.last_ingest?.pending ?? 0} / 错误 ${data?.last_ingest?.errors ?? 0})`}
      >
        <Table<IngestFile>
          rowKey="path"
          size="small"
          dataSource={data?.last_ingest?.files ?? []}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          columns={[
            { title: '文件', dataIndex: 'path', ellipsis: true },
            {
              title: '类别', dataIndex: 'category', width: 140,
              render: (v: string) => CATEGORY_LABEL[v] ?? v,
            },
            {
              title: '状态', dataIndex: 'status', width: 100,
              render: (v: string) => {
                const m = FILE_STATUS_META[v] ?? { color: 'default', label: v };
                return <Tag color={m.color}>{m.label}</Tag>;
              },
            },
            {
              title: '结果', dataIndex: 'summary', ellipsis: true,
              render: (s: Record<string, unknown> | undefined) => {
                if (!s) return '';
                if (s.note) return String(s.note);
                const parts: string[] = [];
                if (s.inserted !== undefined) parts.push(`新增 ${s.inserted}`);
                if (s.updated !== undefined) parts.push(`更新 ${s.updated}`);
                if (s.parsed !== undefined) parts.push(`解析 ${s.parsed}`);
                if (Array.isArray(s.errors) && s.errors.length) parts.push(`错误: ${s.errors[0]}`);
                return parts.join(' / ');
              },
            },
          ]}
        />
      </Card>

      {/* 设置弹窗 */}
      <Modal
        title="自动取数 · 更新间隔设置"
        open={settingsOpen}
        onCancel={() => setSettingsOpen(false)}
        onOk={() => settingsMut.mutate({
          interval_orders_days: ivOrders ?? settings?.interval_orders_days,
          interval_balance_days: ivBalance ?? settings?.interval_balance_days,
          schedule_time: schedTime ?? settings?.schedule_time,
          ...(token ? { token } : {}),
        })}
        confirmLoading={settingsMut.isPending}
      >
        <Descriptions column={1} size="small" style={{ marginBottom: 12 }}>
          <Descriptions.Item label="取数服务地址">{settings?.agent_url}</Descriptions.Item>
        </Descriptions>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Text style={{ width: 160, display: 'inline-block' }}>每日自动取数时刻</Text>
            <Input placeholder="HH:MM 如 17:30" style={{ width: 120 }}
              value={schedTime ?? settings?.schedule_time}
              onChange={(e) => setSchedTime(e.target.value)} />
            <Text type="secondary">每天这个点自动拉订单+余额</Text>
          </Space>
          <Space>
            <Text style={{ width: 160, display: 'inline-block' }}>订单更新间隔 (天)</Text>
            <InputNumber min={1} max={30}
              value={ivOrders ?? settings?.interval_orders_days}
              onChange={(v) => setIvOrders(v)} />
            <Text type="secondary">默认 1 天</Text>
          </Space>
          <Space>
            <Text style={{ width: 160, display: 'inline-block' }}>余额/流水更新间隔 (天)</Text>
            <InputNumber min={1} max={30}
              value={ivBalance ?? settings?.interval_balance_days}
              onChange={(v) => setIvBalance(v)} />
            <Text type="secondary">默认 3 天</Text>
          </Space>
          <Space>
            <Text style={{ width: 160, display: 'inline-block' }}>
              Web-Agent token {settings?.token_configured ? <Tag color="green">已配置</Tag> : <Tag color="red">未配置</Tag>}
            </Text>
            <Input.Password placeholder="粘贴后保存 (不回显)" value={token}
              onChange={(e) => setToken(e.target.value)} style={{ width: 240 }} />
          </Space>
        </Space>
      </Modal>
    </div>
  );
}
