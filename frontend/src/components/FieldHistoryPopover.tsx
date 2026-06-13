/**
 * 字段级悬浮历史 (方向2): 点击 ⏱ 图标 → 该字段最近 30 份修改记录。
 * 懒加载: 打开时才请求, 不拖慢表格/编辑器。
 */
import { useState } from 'react';
import { Popover, Spin, Tag, Typography } from 'antd';
import { HistoryOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { fetchFieldHistory } from '../api/client';

export default function FieldHistoryPopover({ table, pk, field, label }: {
  table: string; pk: string; field: string; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const { data: rows, isLoading } = useQuery({
    queryKey: ['field-history', table, pk, field],
    queryFn: () => fetchFieldHistory(table, pk, field),
    enabled: open,
    staleTime: 30_000,
  });
  const content = (
    <div style={{ maxHeight: 320, overflowY: 'auto', width: 360 }}>
      {isLoading && <Spin size="small" />}
      {rows && rows.length === 0 && (
        <Typography.Text type="secondary">该字段还没有人工修改记录</Typography.Text>
      )}
      {(rows ?? []).map((r) => (
        <div key={r.id} style={{ borderBottom: '1px solid #f0f0f0', padding: '6px 0', fontSize: 12 }}>
          <div>
            <Typography.Text type="secondary">
              {r.created_at ? r.created_at.slice(0, 16).replace('T', ' ') : '—'}
            </Typography.Text>
            {' '}
            <Tag color={r.source === 'feishu' ? 'purple' : 'blue'} style={{ marginLeft: 4 }}>
              {r.source_label}
            </Tag>
            <Typography.Text>{r.actor || '未记录账号'}</Typography.Text>
          </div>
          <div>
            <Typography.Text delete type="secondary">{r.old_value ?? '空'}</Typography.Text>
            {' → '}
            <Typography.Text strong>{r.new_value ?? '空'}</Typography.Text>
          </div>
        </div>
      ))}
    </div>
  );
  return (
    <Popover
      trigger="click" open={open} onOpenChange={setOpen}
      content={content} title={`${label || field} — 修改历史 (最近 30 份)`}
    >
      <HistoryOutlined style={{ color: '#999', cursor: 'pointer' }} title="查看修改历史" />
    </Popover>
  );
}
