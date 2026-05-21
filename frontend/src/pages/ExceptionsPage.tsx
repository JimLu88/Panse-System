import {
  Alert,
  Button,
  Modal,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AiDiagnoseResult,
  DataException,
  aiDiagnose,
  listExceptions,
  resolveException,
  runAllScanners,
} from '../api/client';

const severityColor: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  error: 'red',
};

export default function ExceptionsPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<'open' | 'resolved' | 'ignored'>('open');
  const [diagnoseOpen, setDiagnoseOpen] = useState<{ exc: DataException; result?: AiDiagnoseResult } | null>(null);

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

  const diagnoseMut = useMutation({
    mutationFn: (id: number) => aiDiagnose(id),
    onSuccess: (result) => {
      setDiagnoseOpen((prev) => (prev ? { ...prev, result } : null));
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'AI 调用失败'),
  });

  const scanMut = useMutation({
    mutationFn: () => runAllScanners(false),
    onSuccess: (res) => {
      const total = Object.values(res).reduce((s, r) => s + r.written, 0);
      const skipped = Object.values(res).reduce((s, r) => s + r.skipped_duplicate, 0);
      message.success(`扫描完成：新增 ${total} 条，去重 ${skipped} 条`);
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  const handleDiagnose = (exc: DataException) => {
    setDiagnoseOpen({ exc });
    diagnoseMut.mutate(exc.id);
  };

  const columns = [
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 80,
      render: (v: string) => <Tag color={severityColor[v] ?? 'default'}>{v}</Tag>,
    },
    { title: '来源表', dataIndex: 'source_table', width: 120 },
    {
      title: '主键',
      dataIndex: 'source_pk',
      width: 130,
      render: (v: string | null) => (v ? <code style={{ fontSize: 11 }}>{v}</code> : '-'),
    },
    { title: '异常类型', dataIndex: 'exception_type', width: 220 },
    { title: '描述', dataIndex: 'description', ellipsis: false },
    {
      title: '操作',
      width: 230,
      render: (_: unknown, row: DataException) =>
        row.status === 'open' ? (
          <Space size="small">
            <Button
              size="small"
              icon={<RobotOutlined />}
              onClick={() => handleDiagnose(row)}
            >
              AI 分析
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={() => resolveMut.mutate({ id: row.id, s: 'resolved' })}
            >
              已处理
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
          异常处理 (Phase 3.5)
        </Typography.Title>
        <Space>
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => scanMut.mutate()}
            loading={scanMut.isPending}
          >
            全量扫描
          </Button>
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
      </Space>

      <Table<DataException>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
        size="middle"
      />

      <Modal
        title={
          <Space>
            <RobotOutlined />
            <span>AI 诊断 — 异常 #{diagnoseOpen?.exc.id}</span>
          </Space>
        }
        open={!!diagnoseOpen}
        onCancel={() => setDiagnoseOpen(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setDiagnoseOpen(null)}>
            关闭
          </Button>,
        ]}
        width={700}
      >
        {diagnoseOpen && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Alert
              type="info"
              message={diagnoseOpen.exc.description}
              description={
                <Space size="small" wrap>
                  <Tag color={severityColor[diagnoseOpen.exc.severity]}>{diagnoseOpen.exc.severity}</Tag>
                  <Tag>{diagnoseOpen.exc.source_table}</Tag>
                  <code>{diagnoseOpen.exc.source_pk}</code>
                </Space>
              }
            />
            {diagnoseMut.isPending ? (
              <div style={{ textAlign: 'center', padding: 24 }}>
                <Spin tip="AI 分析中...">
                  <div style={{ minHeight: 40 }} />
                </Spin>
              </div>
            ) : diagnoseOpen.result ? (
              diagnoseOpen.result.error ? (
                <Alert type="warning" showIcon message="AI 暂不可用" description={diagnoseOpen.result.error} />
              ) : (
                <>
                  <div
                    style={{
                      whiteSpace: 'pre-wrap',
                      background: '#f7f7f7',
                      padding: 12,
                      borderRadius: 6,
                    }}
                  >
                    {diagnoseOpen.result.text}
                  </div>
                  <div style={{ fontSize: 12, color: '#999' }}>
                    模型: {diagnoseOpen.result.model} · in={diagnoseOpen.result.input_tokens}
                    {' '}out={diagnoseOpen.result.output_tokens}
                    {' '}cache_read={diagnoseOpen.result.cache_read_tokens}
                  </div>
                </>
              )
            ) : null}
          </Space>
        )}
      </Modal>
    </Space>
  );
}
