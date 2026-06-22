/**
 * 自动化任务 · 今日状态 (用户拍板 2026-06-12: 把可自动的指标放一个框, 每天一眼看状态)。
 * 数据源: 既有 /api/scheduler/jobs (任务+下次执行) + /api/scheduler/runs (最近运行日志)。
 * 失败/未跑置顶, 绿=上次成功, 红=上次失败, 灰=还没跑过。
 */
import { Badge, Card, Empty, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchSchedulerJobs, fetchSchedulerRuns } from '../api/system';

const OK = new Set(['ok', 'success', 'done', 'succeeded']);

function fmtTime(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function AutomationStatusCard() {
  const jobsQ = useQuery({ queryKey: ['sched-jobs'], queryFn: fetchSchedulerJobs, refetchInterval: 120000 });
  const runsQ = useQuery({ queryKey: ['sched-runs-300'], queryFn: () => fetchSchedulerRuns(300), refetchInterval: 120000 });

  const jobs = jobsQ.data ?? [];
  const runs = runsQ.data ?? [];
  const latest: Record<string, (typeof runs)[number]> = {};
  runs.forEach((r) => { if (!latest[r.job_id]) latest[r.job_id] = r; });

  const rows = jobs.map((j) => {
    const run = latest[j.job_id];
    const state = !run ? 'never' : OK.has((run.status || '').toLowerCase()) ? 'ok' : 'fail';
    // 排序: 异常0 > 应跑未跑(下次时间已过)1 > 正常2 > 未跑但还没到点3 (挪到最后, 免得看着像没完成)
    const overdue = state === 'never' && j.next_run_at != null && new Date(j.next_run_at) < new Date();
    const rank = state === 'fail' ? 0 : overdue ? 1 : state === 'ok' ? 2 : 3;
    return { ...j, run, state, rank };
  }).sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label));

  const failN = rows.filter((r) => r.state === 'fail').length;
  const okN = rows.filter((r) => r.state === 'ok').length;

  if (jobsQ.isLoading) return <Card size="small" title="自动化任务 · 今日状态"><Spin /></Card>;

  return (
    <Card
      size="small"
      title={
        <span>自动化任务 · 今日状态{' '}
          <Tag color="green">正常 {okN}</Tag>
          {failN > 0 ? <Tag color="red">异常 {failN}</Tag> : <Tag>全部正常</Tag>}
        </span>
      }
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>共 {rows.length} 项 · 每2分钟刷新</Typography.Text>}
    >
      {rows.length === 0 ? <Empty description="暂无自动任务" /> : (
        <Table
          rowKey="job_id"
          dataSource={rows}
          size="small"
          pagination={false}
          scroll={{ y: 360 }}
          columns={[
            {
              title: '状态', dataIndex: 'state', width: 70,
              render: (s: string) =>
                s === 'ok' ? <Badge status="success" text="正常" />
                  : s === 'fail' ? <Badge status="error" text="异常" />
                    : <Badge status="default" text="未跑" />,
            },
            { title: '自动任务', dataIndex: 'label', ellipsis: true },
            {
              title: '上次运行', dataIndex: 'run', width: 120,
              render: (run: any) => run
                ? <Tooltip title={run.error || (run.result_summary ? JSON.stringify(run.result_summary) : '')}>
                    {fmtTime(run.completed_at || run.created_at)}
                  </Tooltip>
                : <span style={{ color: '#bbb' }}>—</span>,
            },
            { title: '下次', dataIndex: 'next_run_at', width: 110, render: (v: string | null) => fmtTime(v) },
          ]}
        />
      )}
    </Card>
  );
}
