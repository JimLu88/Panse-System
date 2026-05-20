import { Button, Segmented, Space, Table, Tag, Typography, message } from 'antd';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DataException, listExceptions, resolveException } from '../api/client';

const severityColor: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  error: 'red',
};

export default function ExceptionsPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<'open' | 'resolved' | 'ignored'>('open');

  const { data, isLoading } = useQuery({
    queryKey: ['exceptions', status],
    queryFn: () => listExceptions(status),
  });

  const resolveMut = useMutation({
    mutationFn: ({ id, s }: { id: number; s: 'resolved' | 'ignored' }) =>
      resolveException(id, s),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  const columns = [
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      render: (v: string) => <Tag color={severityColor[v] ?? 'default'}>{v}</Tag>,
    },
    { title: '来源表', dataIndex: 'source_table', width: 120 },
    {
      title: '主键',
      dataIndex: 'source_pk',
      width: 110,
      render: (v: string | null) => (v ? <Tag>{v}</Tag> : '-'),
    },
    { title: '异常类型', dataIndex: 'exception_type', width: 220 },
    { title: '描述', dataIndex: 'description', ellipsis: false },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 160,
      render: (_: unknown, row: DataException) =>
        row.status === 'open' ? (
          <Space>
            <Button
              size="small"
              type="primary"
              onClick={() => resolveMut.mutate({ id: row.id, s: 'resolved' })}
            >
              标记已处理
            </Button>
            <Button
              size="small"
              onClick={() => resolveMut.mutate({ id: row.id, s: 'ignored' })}
            >
              忽略
            </Button>
          </Space>
        ) : (
          <Tag color={row.status === 'resolved' ? 'green' : 'default'}>{row.status}</Tag>
        ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          异常处理 (Phase 3.5 入口)
        </Typography.Title>
        <Segmented
          value={status}
          onChange={(v) => setStatus(v as typeof status)}
          options={[
            { label: '未处理', value: 'open' },
            { label: '已处理', value: 'resolved' },
            { label: '已忽略', value: 'ignored' },
          ]}
        />
      </Space>
      <Table<DataException>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
        size="middle"
      />
    </Space>
  );
}
