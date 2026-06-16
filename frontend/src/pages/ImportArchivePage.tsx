/**
 * 导入档案 — 每次导入归档的原始文件 (表格/图片) 列表, 可按类型筛 + 下载回溯。
 * 导入结果显示是否成功; 上传人取飞书发图人; 「文件夹」给出主机归档路径(PC 可复制后在资源管理器打开)。
 */
import { useState } from 'react';
import {
  Button, Card, Col, Modal, Popconfirm, Row, Segmented, Space, Statistic, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { CloudUploadOutlined, DownloadOutlined, FolderOpenOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ImportedFileRow, downloadImportFile, fetchImportFileSummary, fetchImportFiles,
  fetchOrderSheetPushStatus, pushOrderSheets,
} from '../api/imports';
import PresetTable from '../components/PresetTable';

const KIND_LABEL: Record<string, string> = {
  orders: '订单', taobao: '淘宝订单', alipay: '支付宝流水', settlement: '结算账单(billDetail)',
  wanshifu: '万师傅账单', wanshifu_orders: '万师傅订单档案',
  logistics: '物流账单', promotion: '推广流水', aftersales: '售后',
  refill: '补单表', account_balance: '账户余额',
  factory_recon: '工厂对账', purchase: '采购单', screenshot: '截图录入',
  // 系统生成档案 (2026-06-11: 下单图/作废图/页面导出 单独分类入口)
  order_sheet: '工厂下单图', order_sheet_void: '工厂作废图', page_export: '页面导出',
  full_export: '全量导出',
  generic: '其它',
};

const KB = (n: number | null) => (n == null ? '-' : n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

// 导入结果摘要文字 (新增/更新/重复/...; 没有计数则用 note)
function summaryDetail(s: Record<string, unknown> | null): string {
  if (!s) return '';
  const parts: string[] = [];
  const pick = (k: string, label: string) => { const v = s[k]; if (typeof v === 'number' && v) parts.push(`${label}${v}`); };
  pick('inserted', '新增'); pick('updated', '更新'); pick('backfilled', '回填');
  pick('skipped_duplicate', '重复'); pick('skipped_invalid', '无效');
  if (!parts.length && typeof s.note === 'string' && s.note) parts.push(s.note as string);
  return parts.join(' / ');
}

// 导入结果状态: 成功 / 失败 / 未导入(仅归档) / 已处理(无新增)。
// 工厂下单图特殊: 显示飞书推送态 (已推飞书 / 待推飞书 / 历史·待补推)。
function renderResult(s: Record<string, unknown> | null, kind?: string) {
  if (kind === 'order_sheet') {
    if (s?.pushed === true) return <Tag color="cyan">已推飞书</Tag>;
    if (s?.baseline === true) return <Tag color="gold">历史·待补推</Tag>;
    return <Tag color="orange">待推飞书</Tag>;
  }
  if (!s) return <Tag>未导入</Tag>;
  const detail = summaryDetail(s);
  const note = <span style={{ fontSize: 12, color: '#999' }}>{detail}</span>;
  if (s.ok === false) return <Space size={4}><Tag color="red">失败</Tag>{note}</Space>;
  const hasCounts = ['inserted', 'updated', 'backfilled'].some((k) => typeof s[k] === 'number' && (s[k] as number) > 0);
  if (s.ok === true || hasCounts) return <Space size={4}><Tag color="green">成功</Tag>{note}</Space>;
  return <Space size={4}><Tag>已处理</Tag><span style={{ fontSize: 12, color: '#999' }}>{detail || '无新增'}</span></Space>;
}

function showFolder(path: string | null) {
  if (!path) { message.info('该文件没有归档路径'); return; }
  Modal.info({
    title: '归档文件夹（PC 端复制后在资源管理器打开）',
    width: 560,
    content: (
      <div>
        <Typography.Paragraph copyable={{ text: path }} style={{ wordBreak: 'break-all', marginBottom: 8 }}>{path}</Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          后端跑在 Docker 里无法直接弹出资源管理器；点右侧复制图标，粘到「此电脑」地址栏即可打开。
        </Typography.Text>
      </div>
    ),
  });
}

export default function ImportArchivePage() {
  const [kind, setKind] = useState<string>('');
  const qc = useQueryClient();

  const { data: sum } = useQuery({ queryKey: ['import-archive-summary'], queryFn: fetchImportFileSummary });
  const { data, isLoading } = useQuery({
    queryKey: ['import-archive', kind],
    queryFn: () => fetchImportFiles({ kind: kind || undefined, limit: 1000 }),
  });
  const { data: pushStatus } = useQuery({
    queryKey: ['order-sheet-push-status'],
    queryFn: fetchOrderSheetPushStatus,
  });
  const pushMut = useMutation({
    mutationFn: () => pushOrderSheets(20),
    onSuccess: (r) => {
      if (r.reason === 'no_chat_id') {
        message.warning('飞书推送群未配置：到「管理 → 飞书」设置 feishu_push_chat_id（推送群会话ID）');
        return;
      }
      message.success(
        `已推送 ${r.pushed} 张下单图到飞书工厂群`
        + (r.failed ? `，失败 ${r.failed} 张` : '')
        + (r.remaining ? `，还剩 ${r.remaining} 张未推（可再次点击续推）` : '，已全部推完'),
      );
      qc.invalidateQueries({ queryKey: ['order-sheet-push-status'] });
      qc.invalidateQueries({ queryKey: ['import-archive'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '推送失败'),
  });

  const onDownload = async (r: ImportedFileRow) => {
    try {
      await downloadImportFile(r.id, r.original_filename || `import-${r.id}`);
    } catch {
      message.error('下载失败,原文件可能已不可读');
    }
  };

  const columns: ColumnsType<ImportedFileRow> = [
    { title: '类型', dataIndex: 'kind', width: 130, render: (v: string) => <Tag color="blue">{KIND_LABEL[v] ?? v}</Tag> },
    { title: '原文件名', dataIndex: 'original_filename', ellipsis: true, render: (v: string | null) => v || <span style={{ color: '#bbb' }}>(无名)</span> },
    { title: '导入结果', key: 'summary', width: 220, render: (_: unknown, r) => renderResult(r.row_summary, r.kind) },
    { title: '大小', dataIndex: 'size_bytes', width: 80, align: 'right', render: KB },
    { title: '来源', dataIndex: 'source', width: 70, render: (v: string) => <Tag>{v}</Tag> },
    { title: '上传人', dataIndex: 'uploaded_by', width: 130, ellipsis: true, render: (v: string | null) => v || '-' },
    { title: '导入时间', dataIndex: 'created_at', width: 160, render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '-') },
    {
      title: '操作', key: 'act', width: 150, fixed: 'right' as const,
      render: (_: unknown, r) => (
        <Space size={4}>
          <Button size="small" icon={<DownloadOutlined />} onClick={() => onDownload(r)}>下载</Button>
          <Button size="small" icon={<FolderOpenOutlined />} onClick={() => showFolder(r.folder)}>文件夹</Button>
        </Space>
      ),
    },
  ];

  const kindOptions = [{ label: '全部', value: '' }].concat(
    Object.keys(sum?.by_kind ?? {}).map((k) => ({ label: `${KIND_LABEL[k] ?? k} (${sum!.by_kind[k]})`, value: k })),
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>资料存档库</Typography.Title>
        <Space>
          {pushStatus && (
            <Popconfirm
              title="推送工厂下单图到飞书工厂群"
              description={`把还没推过的下单图渲染成图片发到飞书工厂群，每次最多 20 张（当前待推 ${pushStatus.pending_total} 张）。`}
              okText="推送" cancelText="取消"
              disabled={!pushStatus.configured || pushStatus.pending_total === 0 || pushMut.isPending}
              onConfirm={() => pushMut.mutate()}
            >
              <Tooltip title={
                !pushStatus.configured ? '飞书推送群未配置：到「管理 → 飞书」设置 feishu_push_chat_id'
                  : pushStatus.pending_total === 0 ? '没有待推送的下单图（都已推过）' : ''
              }>
                <Button
                  type="primary" icon={<CloudUploadOutlined />} loading={pushMut.isPending}
                  disabled={!pushStatus.configured || pushStatus.pending_total === 0}
                >
                  推送下单图到飞书{pushStatus.pending_total ? ` (待推 ${pushStatus.pending_total})` : ''}
                </Button>
              </Tooltip>
            </Popconfirm>
          )}
          {sum?.imports_root && (
            <Button icon={<FolderOpenOutlined />} onClick={() => showFolder(sum.imports_root || null)}>归档目录</Button>
          )}
        </Space>
      </Space>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        每次导入的表格/图片原文件都自动按 类型/年/月 归档在此,可下载回溯。对账对不上时点开原始凭证核对。
      </Typography.Paragraph>

      {sum && (
        <Row gutter={12}>
          <Col span={6}><Card size="small"><Statistic title="归档文件总数" value={sum.total} /></Card></Col>
          <Col span={18}>
            <Card size="small" title={<span style={{ fontSize: 13 }}>分类入口 (点击进入该分类)</span>}>
              <Space wrap>
                {Object.entries(sum.by_kind).map(([k, n]) => (
                  <Tag
                    key={k}
                    color={kind === k ? 'green' : k.startsWith('order_sheet') || k === 'page_export' ? 'volcano' : 'geekblue'}
                    style={{ cursor: 'pointer', padding: '2px 10px' }}
                    onClick={() => setKind(kind === k ? '' : k)}
                  >
                    {KIND_LABEL[k] ?? k}: {n}
                  </Tag>
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
        <PresetTable<ImportedFileRow>
          tableKey="import_archive"
          rowKey="id" size="small" loading={isLoading}
          dataSource={data?.files ?? []}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [50, 100, 200], showTotal: (t) => `共 ${t} 个文件` }}
          scroll={{ x: 1180 }}
          columns={columns}
        />
      </Card>
    </Space>
  );
}
