/**
 * 运营待办台账 — 每日 / 每周 / 每月 例行工作清单 + 完成勾选。
 * 完成状态按周期(日/周/月)记录, 进入新周期自动重置。
 */
import { Button, Card, Checkbox, Progress, Space, Tag, Typography, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { OpsGroup, fetchOpsChecklist, toggleOpsTask } from '../api/operations';
import AutomationStatusCard from '../components/AutomationStatusCard';   // #6 从首页移来

const FREQ_COLOR: Record<string, string> = { daily: 'blue', weekly: 'purple', monthly: 'orange' };

export default function OpsChecklistPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['ops-checklist'],
    queryFn: fetchOpsChecklist,
  });
  const mut = useMutation({
    mutationFn: ({ key, done }: { key: string; done: boolean }) => toggleOpsTask(key, done),
    onSuccess: (fresh) => qc.setQueryData(['ops-checklist'], fresh),
    onError: (e: any) => message.error(`更新失败: ${e?.response?.data?.detail ?? e?.message ?? '请稍后重试(可能后端正在重启)'}`),
  });

  if (isLoading || !data) return <Card loading />;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <div>
        <Typography.Title level={4} style={{ margin: 0 }}>运营待办台账</Typography.Title>
        <Typography.Text type="secondary">
          今天 {data.today} · 勾选记录完成情况;每个周期(日/周/月)自动重置,未做完的会标记。
        </Typography.Text>
      </div>
      <AutomationStatusCard />
      {data.login_status && data.login_status.length > 0 && (
        <Card size="small" title="各平台登录状态 (自动取数用)">
          <Space direction="vertical" style={{ width: '100%' }} size={6}>
            {data.login_status.map((p) => (
              <Space key={p.platform} size={8} wrap>
                <Tag color={p.need_scan ? 'red' : 'green'} style={{ minWidth: 130, textAlign: 'center' }}>{p.platform}</Tag>
                <span style={{ fontSize: 13, color: p.need_scan ? '#cf1322' : '#52c41a' }}>
                  {p.need_scan ? `⚠ 需扫码登录: ${p.message}` : p.message}
                </span>
                {p.last_ok && (
                  <span style={{ fontSize: 11, color: '#aaa' }}>上次成功取数 {String(p.last_ok).slice(0, 16).replace('T', ' ')}</span>
                )}
              </Space>
            ))}
          </Space>
        </Card>
      )}
      {data.groups.map((g: OpsGroup) => {
        const pending = g.total - g.done_count;
        const pct = g.total > 0 ? Math.round((g.done_count / g.total) * 100) : 0;
        return (
          <Card
            key={g.freq}
            size="small"
            title={
              <Space>
                <Tag color={FREQ_COLOR[g.freq]}>{g.label}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{g.period_key}</Typography.Text>
              </Space>
            }
            extra={
              <Space>
                <Progress percent={pct} size="small" style={{ width: 120 }} />
                <Tag color={pending === 0 ? 'success' : 'warning'}>
                  {g.done_count}/{g.total} 完成{pending > 0 ? ` · ${pending} 未做` : ''}
                </Tag>
              </Space>
            }
          >
            <Space direction="vertical" style={{ width: '100%' }} size={2}>
              {g.tasks.map((t) => (
                <div key={t.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '5px 0' }}>
                  <Checkbox
                    checked={t.done}
                    disabled={t.auto}
                    onChange={(e) => mut.mutate({ key: t.key, done: e.target.checked })}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, textDecoration: t.done ? 'line-through' : 'none', color: t.done ? '#aaa' : undefined }}>
                      {t.route ? <Link to={t.route}>{t.title}</Link> : t.title}
                      {t.auto && <Tag color="cyan" style={{ marginLeft: 6, fontSize: 11 }}>已自动完成</Tag>}
                    </div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t.detail}</Typography.Text>
                  </div>
                  {t.route && (
                    <Link to={t.route}>
                      <Button type="link" size="small" style={{ padding: 0 }}>去处理 →</Button>
                    </Link>
                  )}
                </div>
              ))}
            </Space>
          </Card>
        );
      })}
    </Space>
  );
}
