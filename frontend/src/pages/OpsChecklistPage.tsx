/**
 * 运营待办台账 — 每日 / 每周 / 每月 例行工作清单 + 完成勾选。
 * 完成状态按周期(日/周/月)记录, 进入新周期自动重置。
 */
import { Card, Checkbox, Progress, Space, Tag, Typography, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { OpsGroup, fetchOpsChecklist, toggleOpsTask } from '../api/operations';

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
    onError: () => message.error('更新失败'),
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
                    onChange={(e) => mut.mutate({ key: t.key, done: e.target.checked })}
                  />
                  <div>
                    <div style={{ fontWeight: 600, textDecoration: t.done ? 'line-through' : 'none', color: t.done ? '#aaa' : undefined }}>
                      {t.title}
                    </div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t.detail}</Typography.Text>
                  </div>
                </div>
              ))}
            </Space>
          </Card>
        );
      })}
    </Space>
  );
}
