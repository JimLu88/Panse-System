/**
 * 导入档案 — 每次导入归档的原始文件 (表格/图片) 列表, 可按类型/月份筛选 + 下载回溯。
 * 对账对不上时, 点开原始凭证核对。
 */
import { useState } from 'react';
import {
  Button, Card, Col, Row, Segmented, Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { ImportedFileRow, downloadImportFile, fetchImportFileSummary, fetchImportFiles } from '../api/imports';

const KIND_LABEL: Record<string, string> = {
  orders: '订单', taobao: '淘宝订单', alipay: '支付宝流水', settlement: '结算账单(billDetail)',
  wanshifu: '万师傅账单', logistics: '物流账单', promotion: '推广流水', aftersales: '售后',
  refill: '补单', account_balance: '账户余额', generic: '其它',
};

const KB = (n: number | null) => (n == null ? '-' : n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

function summaryText(s: Record<string, unknown> | null): string {
  if (!s) return '-';
  const parts: string[] = [];
  const pick = (k: string, label: string) => {
    const v = s[k];
    if (typeof v === 'number' && v) parts.push(`${label}${v}`);
  };
  pick('inserted', '新增'); pick('updated', '更新'); pick('backfilled', '回填');
  pick('skipped_duplicate', '重复'); pick('skipped_invalid', '无效');
  return parts.length ? parts.join(' / ') : '-';
}

export default function ImportArchivePage() {
  const [kind, setKind] = useState<string>('');

  const { data: sum } = useQuery({ queryKey: ['import-archive-summary'], queryFn: fetchImportFileSummary });
  const { data, isLoading } = useQuery({
    queryKey: ['import-archive', kind],
    queryFn: () => fetchImportFiles({ kind: kind || undefined, limit: 500 }),
  });

  const onDownload = async (r: ImportedFileRow) => {
    try {
      await downloadImportFile(r.id, r.original_filename || `import-${r.id}`);
    } catch {
      message.error('下载失败,原文件可能已不可读');
    }
  };

  const columns: ColumnsType<ImportedFileRow> = [
    { title: '类型', dataIndex: 'kind', width: 150, render: (v: string) => <Tag color="blue">{KIND_LABEL[v] ?? v}</Tag> },
    { title: '原文件名', dataIndex: 'original_filename', ellipsis: true, render: (v: string | null) => v || <span style={{ color: '#bbb' }}>(无名)</span> },
    { title: '导入结果', key: 'summary', width: 220, render: (_: unknown, r) => summaryText(r.row_summary) },
    { title: '大小', dataIndex: 'size_bytes', width: 90, align: 'right', render: KB },
    { title: '来源', dataIndex: 'source', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: '上传人', dataIndex: 'uploaded_by', width: 100, render: (v: string | null) => v || '-' },
    { title: '导入时间', dataIndex: 'created_at', width: 170, render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '-') },
    {
      title: '操作', key: 'act', width: 90, fixed: 'right' as const,
      render: (_: unknown, r) => <Button size="small" icon={<DownloadOutlined />} onClick={() => onDownload(r)}>下载</Button>,
    },
  ];

  const kindOptions = [{ label: '全部', value: '' }].concat(
    Object.keys(sum?.by_kind ?? {}).map((k) => ({ label: `${KIND_LABEL[k] ?? k} (${sum!.by_kind[k]})`, value: k })),
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>导入档案</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        每次导入的表格/图片原文件都自动按 类型/年/月 归档在此,可下载回溯。对账对不上时点开原始凭证核对。
      </Typography.Paragraph>

      {sum && (
        <Row gutter={12}>
          <Col span={6}><Card size="small"><Statistic title="归档文件总数" value={sum.total} /></Card></Col>
          <Col span={18}>
            <Card size="small">
              <Space wrap>
                {Object.entries(sum.by_kind).map(([k, n]) => (
                  <Tag key={k} color="geekblue">{KIND_LABEL[k] ?? k}: {n}</Tag>
                ))}
              </Space>
            </Card>
          </Col>
        </Row>
      )}

      <Card size="small">
        <Space wrap style={{ marginBottom: 12 }}>
          <span>类型:</span>
          <Segmented value={kind} onChange={(v) => setKind(v as string)} options={kindOptions} />
        </Space>
        <Table<ImportedFileRow>
          rowKey="id" size="small" loading={isLoading}
          dataSource={data?.files ?? []}
          pagination={{ pageSize: 50, showTotal: (t) => `共 ${t} 个文件` }}
          scroll={{ x: 1100 }}
          columns={columns}
        />
      </Card>
    </Space>
  );
}
