import { useState } from 'react';
import {
  Alert,
  Button,
  Modal,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlipayFlow,
  CsvImportReport,
  importAlipayCsv,
  listAlipayFlows,
} from '../api/client';

const ACCOUNTS = ['企业号', '个体户私账', '爱群号', '佳宝号', '主力号'];

export default function AlipayPage() {
  const qc = useQueryClient();
  const [account, setAccount] = useState<string>(ACCOUNTS[0]);
  const [importResult, setImportResult] = useState<CsvImportReport | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['alipay', account],
    queryFn: () => listAlipayFlows({ account, limit: 200 }),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importAlipayCsv(file, account),
    onSuccess: (r) => {
      setImportResult(r);
      qc.invalidateQueries({ queryKey: ['alipay'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  const columns = [
    { title: '时间', dataIndex: 'transaction_time', width: 150 },
    { title: '流水号', dataIndex: 'transaction_no', width: 230, ellipsis: true,
      render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code> },
    { title: '类型', dataIndex: 'transaction_type', width: 90 },
    { title: '对象', dataIndex: 'counterparty', ellipsis: true },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 110,
      align: 'right' as const,
      render: (v: string) => (
        <span style={{ color: Number(v) >= 0 ? '#3f8600' : '#cf1322', fontWeight: 600 }}>
          ¥{v}
        </span>
      ),
    },
    { title: '余额', dataIndex: 'balance', width: 110, align: 'right' as const },
    {
      title: '核销',
      dataIndex: 'reconciliation_type',
      width: 110,
      render: (v: string | null) => (v ? <Tag color="blue">{v}</Tag> : <Tag>未分类</Tag>),
    },
    { title: '关联订单', dataIndex: 'related_order_no', ellipsis: true, width: 160,
      render: (v: string | null) => v ? <code style={{ fontSize: 11 }}>{v}</code> : '-' },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          支付宝流水 (9a-9e)
        </Typography.Title>
        <Upload
          accept=".csv"
          showUploadList={false}
          beforeUpload={(file) => {
            importMut.mutate(file as File);
            return false;
          }}
        >
          <Button icon={<UploadOutlined />} loading={importMut.isPending}>
            CSV 导入到「{account}」
          </Button>
        </Upload>
      </Space>

      <Segmented
        value={account}
        onChange={(v) => setAccount(v as string)}
        options={ACCOUNTS.map((a) => ({ label: a, value: a }))}
      />

      <Table<AlipayFlow>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 30 }}
        size="middle"
      />

      <Modal
        open={!!importResult}
        title="CSV 导入结果"
        onCancel={() => setImportResult(null)}
        footer={[<Button key="ok" type="primary" onClick={() => setImportResult(null)}>知道了</Button>]}
      >
        {importResult && (
          <Space direction="vertical">
            <div>新增：<Tag color="green">{importResult.inserted}</Tag></div>
            <div>重复：<Tag>{importResult.skipped_duplicate}</Tag></div>
            <div>无效：<Tag color="red">{importResult.skipped_invalid}</Tag></div>
            {importResult.errors.length > 0 && (
              <Alert type="error" showIcon message={importResult.errors.join('\n')} />
            )}
          </Space>
        )}
      </Modal>
    </Space>
  );
}
