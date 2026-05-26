/**
 * 首页顶部 AI 每日经营简报 banner (Phase 8 Tier 1 #1).
 *
 * 展示昨日 AI 简报 + 高亮点 chips, 点击 chip 跳对应页.
 */
import { Alert, Button, Empty, Space, Tag, Typography } from 'antd';
import { ReloadOutlined, RobotOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchTodayBriefing, triggerBriefing } from '../api/client';

const LEVEL_COLOR: Record<string, string> = {
  risk: 'red', critical: 'red', warn: 'orange',
  opportunity: 'green', info: 'blue', action: 'purple',
};

export default function BriefingBanner() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: b } = useQuery({
    queryKey: ['today-briefing'], queryFn: fetchTodayBriefing,
    refetchInterval: 5 * 60 * 1000,
  });
  const genMut = useMutation({
    mutationFn: () => triggerBriefing(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['today-briefing'] }),
  });

  if (!b) {
    return (
      <Alert
        type="info"
        showIcon
        icon={<RobotOutlined />}
        message="AI 每日经营简报"
        description={
          <Space>
            <Typography.Text type="secondary">
              还没生成今日简报. 默认每天 09:30 自动生成, 或手动触发.
            </Typography.Text>
            <Button size="small" icon={<ReloadOutlined />}
                    loading={genMut.isPending}
                    onClick={() => genMut.mutate()}>
              立即生成
            </Button>
          </Space>
        }
      />
    );
  }

  return (
    <Alert
      type="success"
      showIcon
      icon={<RobotOutlined />}
      message={
        <Space>
          <span>AI 经营简报 · {b.for_date}</span>
          <Tag>{b.model}</Tag>
        </Space>
      }
      description={
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Paragraph style={{ marginBottom: 6, whiteSpace: 'pre-wrap' }}>
            {b.content}
          </Typography.Paragraph>
          {(b.highlights_json ?? []).length > 0 && (
            <Space wrap>
              {(b.highlights_json ?? []).map((h: any, i: number) => (
                <Tag
                  key={i}
                  color={LEVEL_COLOR[h.level] ?? 'default'}
                  style={{ cursor: 'pointer' }}
                  onClick={() => h.url && navigate(h.url)}
                >
                  {h.title}
                </Tag>
              ))}
            </Space>
          )}
        </Space>
      }
      action={
        <Button size="small" icon={<ReloadOutlined />}
                loading={genMut.isPending}
                onClick={() => genMut.mutate()}>
          重新生成
        </Button>
      }
    />
  );
}
