import { useQuery } from '@tanstack/react-query';
import { Popover, Tag, Typography, Descriptions } from 'antd';
import { getVersion } from '../api/client';

/**
 * 版本标记 — 显示当前后端运行的代码 commit, 一眼判断「拉的代码是否最新」。
 *
 * - 鼠标悬停看完整信息 (commit / 分支 / commit 时间 / 部署时间 / commit 说明)
 * - 把这里显示的短哈希和 GitHub 上 main 最新 commit 对照, 一致就说明已同步
 * - source=runtime_git 表示开发环境直接读 git; build_file 表示容器内读部署文件
 */
export default function VersionTag() {
  const { data, isError } = useQuery({
    queryKey: ['version'],
    queryFn: getVersion,
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });

  if (isError) {
    return <Tag color="default" style={{ marginRight: 8 }}>版本未知</Tag>;
  }
  if (!data) return null;

  const commit = data.commit || 'unknown';
  const deployed = data.deployed_at || '(开发模式)';

  const content = (
    <Descriptions column={1} size="small" style={{ maxWidth: 420 }}>
      <Descriptions.Item label="Commit">
        <Typography.Text copyable code>{data.commit_full || commit}</Typography.Text>
      </Descriptions.Item>
      <Descriptions.Item label="分支">{data.branch || '—'}</Descriptions.Item>
      <Descriptions.Item label="提交说明">{data.commit_message || '—'}</Descriptions.Item>
      <Descriptions.Item label="提交时间">{data.commit_date || '—'}</Descriptions.Item>
      <Descriptions.Item label="部署时间">{deployed}</Descriptions.Item>
      <Descriptions.Item label="来源">
        {data.source === 'build_file' ? '部署文件 (容器)'
          : data.source === 'runtime_git' ? '运行时 git (开发)'
          : '未知'}
      </Descriptions.Item>
    </Descriptions>
  );

  return (
    <Popover
      title="当前运行版本"
      content={
        <>
          {content}
          <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0', maxWidth: 420 }}>
            把上方 Commit 短哈希和 GitHub 上 main 分支最新 commit 对照, 一致即已同步到最新。
          </Typography.Paragraph>
        </>
      }
    >
      <Tag color="geekblue" style={{ marginRight: 8, cursor: 'pointer', fontFamily: 'monospace' }}>
        {commit}
      </Tag>
    </Popover>
  );
}
