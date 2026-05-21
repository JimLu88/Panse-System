/**
 * 通用 Excel importer 页面 (业务需求).
 *
 * 流程:
 *   1. 上传 Excel (多 sheet 都吃)
 *   2. 后端 AI 推荐 entity_type + 列映射
 *   3. 每个 sheet 一个 tab, 用户调整 entity_type / mapping / 选项
 *   4. 点 "试运行" 看 ImportReport (不入库) → 没问题点 "正式入库"
 */
import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  ExperimentOutlined,
  ImportOutlined,
  InboxOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  EntityField,
  EntityType,
  ImportJob,
  ImportReport,
  ImporterPreviewResp,
  SheetPreview,
  commitImporter,
  commitImporterAsync,
  fetchEntityTypes,
  fetchImportJob,
  previewImporter,
} from '../api/client';

const { Dragger } = Upload;

export default function ImporterPage() {
  const { data: entityTypes } = useQuery({
    queryKey: ['importer-entity-types'],
    queryFn: fetchEntityTypes,
  });
  const [previewResp, setPreviewResp] = useState<ImporterPreviewResp | null>(null);
  const [reports, setReports] = useState<Record<string, ImportReport>>({});
  // per-sheet 状态: entity_type 选什么 + mapping
  const [sheetState, setSheetState] = useState<
    Record<string, { entity_type: string; mapping: Record<string, string> }>
  >({});

  const previewMut = useMutation({
    mutationFn: (file: File) => previewImporter(file),
    onSuccess: (r) => {
      setPreviewResp(r);
      // 初始化 per-sheet 状态
      const init: typeof sheetState = {};
      r.sheets.forEach((sh) => {
        init[sh.sheet_name] = {
          entity_type: sh.suggested_entity ?? 'delivery_note',
          mapping: { ...sh.suggested_mapping },
        };
      });
      setSheetState(init);
      setReports({});
      message.success(`解析了 ${r.sheets.length} 个 sheet`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '解析失败'),
  });

  const onUpload = (file: File) => {
    previewMut.mutate(file);
    return false;
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Card size="small" title="第 1 步: 上传 Excel">
        <Alert
          type="info"
          showIcon
          message="多 sheet 都能识别"
          description="不同供应商的对账表列名可能不一样, AI 会先扫一眼前 5 行帮你猜列映射。你可以在第 2 步逐个 sheet 调整。"
          style={{ marginBottom: 12 }}
        />
        <Dragger
          accept=".xlsx,.xls"
          showUploadList={false}
          beforeUpload={onUpload as any}
          disabled={previewMut.isPending}
          multiple={false}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">
            {previewMut.isPending ? '解析中, AI 正在扫描列结构...' : '点击或拖入 Excel 到此区域'}
          </p>
          <p className="ant-upload-hint">支持 .xlsx; AI 会自动推荐列映射, 你可调整</p>
        </Dragger>
      </Card>

      {previewResp && previewResp.sheets.length > 0 && (
        <Card size="small" title="第 2 步: 调整每个 sheet 的 entity 和 列映射">
          <Tabs
            destroyInactiveTabPane={false}
            items={previewResp.sheets
              .filter((sh) => sh.column_names.length > 0)
              .map((sh) => ({
                key: sh.sheet_name,
                label: (
                  <Space>
                    {sh.sheet_name}
                    <Tag>{sh.row_count} 行</Tag>
                    {reports[sh.sheet_name] && (
                      <CheckCircleOutlined style={{ color: '#52c41a' }} />
                    )}
                  </Space>
                ),
                children: (
                  <SheetEditor
                    sheet={sh}
                    fileB64={previewResp.file_b64}
                    entityTypes={entityTypes ?? []}
                    state={sheetState[sh.sheet_name]}
                    onStateChange={(s) =>
                      setSheetState((prev) => ({ ...prev, [sh.sheet_name]: s }))
                    }
                    report={reports[sh.sheet_name]}
                    onReport={(r) =>
                      setReports((prev) => ({ ...prev, [sh.sheet_name]: r }))
                    }
                  />
                ),
              }))}
          />
        </Card>
      )}
    </Space>
  );
}

// ----------------------------- 单 sheet 编辑器 ---------------------- //

function SheetEditor({
  sheet,
  fileB64,
  entityTypes,
  state,
  onStateChange,
  report,
  onReport,
}: {
  sheet: SheetPreview;
  fileB64: string;
  entityTypes: EntityType[];
  state?: { entity_type: string; mapping: Record<string, string> };
  onStateChange: (s: { entity_type: string; mapping: Record<string, string> }) => void;
  report?: ImportReport;
  onReport: (r: ImportReport) => void;
}) {
  const [autoCreate, setAutoCreate] = useState(true);
  const [autoMatch, setAutoMatch] = useState(true);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);

  const entityType = state?.entity_type ?? 'delivery_note';
  const mapping = state?.mapping ?? {};
  const entity = entityTypes.find((e) => e.value === entityType);
  const fields = entity?.fields ?? [];

  const requiredMissing = useMemo(
    () => fields.filter((f) => f.required && !mapping[f.name]).map((f) => f.name),
    [fields, mapping],
  );

  const setMapping = (target: string, excelCol: string | undefined) => {
    const next = { ...mapping };
    if (excelCol) next[target] = excelCol;
    else delete next[target];
    onStateChange({ entity_type: entityType, mapping: next });
  };

  const setEntity = (v: string) => {
    onStateChange({ entity_type: v, mapping: {} });
  };

  const commitMut = useMutation({
    mutationFn: (dryRun: boolean) =>
      commitImporter({
        file_b64: fileB64,
        sheet_name: sheet.sheet_name,
        entity_type: entityType,
        mapping,
        auto_create_suppliers: autoCreate,
        auto_match_orders: autoMatch,
        dry_run: dryRun,
      }),
    onSuccess: (r, dryRun) => {
      onReport(r);
      if (r.errors.length > 0) {
        message.warning(`${r.errors.length} 行有错误, 请看下面报告`);
      } else if (dryRun) {
        message.success(`试运行通过, 可入 ${r.inserted_parents} 条主记录`);
      } else {
        message.success(
          `已入库 ${r.inserted_parents} 主 + ${r.inserted_children} 行明细`,
        );
      }
    },
    onError: (e: any) =>
      message.error(e?.response?.data?.detail ?? '入库失败'),
  });

  // 业务需求 6: 大文件异步入库, 立即返回 job_id, 前端轮询进度
  const commitAsyncMut = useMutation({
    mutationFn: () =>
      commitImporterAsync({
        file_b64: fileB64,
        sheet_name: sheet.sheet_name,
        entity_type: entityType,
        mapping,
        auto_create_suppliers: autoCreate,
        auto_match_orders: autoMatch,
      }),
    onSuccess: (r) => {
      setActiveJobId(r.job_id);
      message.info(`已开始后台导入 (job #${r.job_id}), 进度面板会自动刷新`);
    },
    onError: (e: any) =>
      message.error(e?.response?.data?.detail ?? '提交失败'),
  });

  const colOptions = sheet.column_names.map((c) => ({ value: c, label: c }));
  const usedCols = new Set(Object.values(mapping));

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {sheet.notes.length > 0 && (
        <Alert
          type="info"
          showIcon
          message="解析提示"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {sheet.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          }
        />
      )}

      <Row gutter={16}>
        <Col span={8}>
          <Card size="small" title="实体类型">
            <Select
              style={{ width: '100%' }}
              value={entityType}
              onChange={setEntity}
              options={entityTypes.map((e) => ({
                value: e.value,
                label: e.label,
              }))}
            />
            {entity && (
              <Typography.Paragraph
                type="secondary"
                style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}
              >
                {entity.description}
              </Typography.Paragraph>
            )}
          </Card>
        </Col>
        <Col span={16}>
          <Card size="small" title="选项">
            <Space direction="vertical">
              <Checkbox checked={autoCreate} onChange={(e) => setAutoCreate(e.target.checked)}>
                供应商不存在时自动创建 (推荐)
              </Checkbox>
              <Checkbox checked={autoMatch} onChange={(e) => setAutoMatch(e.target.checked)}>
                入库后自动跑订单匹配 (delivery_note)
              </Checkbox>
            </Space>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="列映射 (左 = 目标字段, 右 = Excel 列)">
        <Table
          size="small"
          rowKey="name"
          pagination={false}
          dataSource={fields}
          columns={[
            {
              title: '目标字段',
              dataIndex: 'name',
              width: 180,
              render: (n: string, f: EntityField) => (
                <Space direction="vertical" size={0}>
                  <Space>
                    <strong>{n}</strong>
                    {f.required && <Tag color="red">必填</Tag>}
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {f.desc}
                  </Typography.Text>
                </Space>
              ),
            },
            { title: '类型', dataIndex: 'type', width: 80,
              render: (t: string) => <Tag>{t}</Tag> },
            {
              title: 'Excel 列',
              width: 280,
              render: (_: any, f: EntityField) => (
                <Select
                  allowClear
                  style={{ width: '100%' }}
                  value={mapping[f.name]}
                  onChange={(v) => setMapping(f.name, v)}
                  placeholder={f.required ? '(必选)' : '(可选, 留空跳过)'}
                  status={f.required && !mapping[f.name] ? 'error' : undefined}
                  options={colOptions.map((o) => ({
                    ...o,
                    label: (
                      <Space>
                        {o.label}
                        {usedCols.has(o.value) && mapping[f.name] !== o.value && (
                          <Tag color="orange">已用</Tag>
                        )}
                      </Space>
                    ),
                  }))}
                />
              ),
            },
            {
              title: '同义词提示',
              dataIndex: 'aliases',
              render: (xs: string[]) => (
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {(xs || []).join(' / ')}
                </Typography.Text>
              ),
            },
          ]}
        />
      </Card>

      <Card size="small" title="样本数据 (前 5 行)">
        <Table
          size="small"
          pagination={false}
          scroll={{ x: true }}
          dataSource={sheet.sample_rows.map((row, i) => ({
            key: i,
            ...Object.fromEntries(sheet.column_names.map((c, j) => [c, row[j]])),
          }))}
          columns={sheet.column_names.map((c) => ({
            title: c,
            dataIndex: c,
            ellipsis: true,
            render: (v: any) => (v === null || v === undefined ? '' : String(v)),
          }))}
        />
      </Card>

      <Space wrap>
        <Button
          icon={<ExperimentOutlined />}
          onClick={() => commitMut.mutate(true)}
          loading={commitMut.isPending}
          disabled={requiredMissing.length > 0}
        >
          试运行 (不入库)
        </Button>
        <Button
          type="primary"
          icon={<ImportOutlined />}
          onClick={() => commitMut.mutate(false)}
          loading={commitMut.isPending}
          disabled={requiredMissing.length > 0 || commitAsyncMut.isPending}
        >
          同步入库 (小文件)
        </Button>
        <Tooltip title="100MB / 上万行的大文件请用后台导入, 立即返回, 不会超时">
          <Button
            type="primary"
            ghost
            icon={<CloudUploadOutlined />}
            onClick={() => commitAsyncMut.mutate()}
            loading={commitAsyncMut.isPending}
            disabled={requiredMissing.length > 0 || activeJobId !== null}
          >
            后台入库 (大文件)
          </Button>
        </Tooltip>
        {requiredMissing.length > 0 && (
          <Typography.Text type="danger">
            必填字段还没选: {requiredMissing.join(', ')}
          </Typography.Text>
        )}
      </Space>

      {activeJobId !== null && (
        <JobProgress
          jobId={activeJobId}
          onClose={() => setActiveJobId(null)}
          onReport={onReport}
        />
      )}

      {report && <ImportReportView report={report} />}
    </Space>
  );
}

// ----------------------------- 报告展示 ---------------------------- //

function ImportReportView({ report }: { report: ImportReport }) {
  const ok = report.errors.length === 0;
  return (
    <Card
      size="small"
      title={
        <Space>
          {ok ? (
            <CheckCircleOutlined style={{ color: '#52c41a' }} />
          ) : (
            <ReloadOutlined style={{ color: '#fa8c16' }} />
          )}
          导入报告
        </Space>
      }
    >
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={4}>
          <Statistic title="总行数" value={report.total_rows} />
        </Col>
        <Col span={4}>
          <Statistic
            title="入库主记录"
            value={report.inserted_parents}
            valueStyle={{ color: '#52c41a' }}
          />
        </Col>
        <Col span={4}>
          <Statistic title="入库明细行" value={report.inserted_children} />
        </Col>
        <Col span={4}>
          <Statistic
            title="跳过"
            value={report.skipped_rows}
            valueStyle={{ color: report.skipped_rows > 0 ? '#fa8c16' : undefined }}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="订单匹配命中"
            value={report.matched_lines}
            valueStyle={{ color: '#1677ff' }}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="自动创建供应商"
            value={report.auto_created_suppliers.length}
          />
        </Col>
      </Row>
      {report.auto_created_suppliers.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 8 }}
          message="新建了这些供应商, 记得去 供应商对账 页面补类型和支付宝关键字"
          description={
            <Space wrap>
              {report.auto_created_suppliers.map((s) => (
                <Tag key={s} color="blue">
                  {s}
                </Tag>
              ))}
            </Space>
          }
        />
      )}
      {report.errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 8 }}
          message={`${report.errors.length} 条错误`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18, maxHeight: 240, overflow: 'auto' }}>
              {report.errors.slice(0, 50).map((e, i) => (
                <li key={i} style={{ fontSize: 12 }}>
                  {e}
                </li>
              ))}
              {report.errors.length > 50 && <li>... 共 {report.errors.length} 条</li>}
            </ul>
          }
        />
      )}
      {report.warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`${report.warnings.length} 条警告`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18, maxHeight: 200, overflow: 'auto' }}>
              {report.warnings.slice(0, 50).map((w, i) => (
                <li key={i} style={{ fontSize: 12 }}>
                  {w}
                </li>
              ))}
            </ul>
          }
        />
      )}
    </Card>
  );
}

// ----------------------------- 异步作业进度 (业务需求 6) ----------- //

function JobProgress({
  jobId,
  onClose,
  onReport,
}: {
  jobId: number;
  onClose: () => void;
  onReport: (r: ImportReport) => void;
}) {
  const { data: job } = useQuery({
    queryKey: ['import-job', jobId],
    queryFn: () => fetchImportJob(jobId),
    refetchInterval: (q) => {
      const j = q.state.data as ImportJob | undefined;
      if (!j) return 2000;
      return j.status === 'done' || j.status === 'failed' ? false : 2000;
    },
  });

  if (!job) {
    return (
      <Card size="small" title={`后台作业 #${jobId}`}>
        加载中...
      </Card>
    );
  }

  const statusColor: Record<string, string> = {
    pending: 'default',
    running: 'processing',
    done: 'success',
    failed: 'error',
    cancelled: 'warning',
  };

  return (
    <Card
      size="small"
      title={
        <Space>
          <span>后台导入 #{job.id}</span>
          <Tag color={statusColor[job.status] ?? 'default'}>{job.status.toUpperCase()}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {job.sheet_name} → {job.entity_type}
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          {job.status === 'done' && job.report && (
            <Button
              type="primary"
              size="small"
              onClick={() => {
                onReport(job.report as ImportReport);
                onClose();
              }}
            >
              查看完整报告
            </Button>
          )}
          {(job.status === 'done' || job.status === 'failed') && (
            <Button size="small" onClick={onClose}>关闭</Button>
          )}
        </Space>
      }
    >
      <Progress
        percent={Math.round(job.progress_pct)}
        status={
          job.status === 'failed'
            ? 'exception'
            : job.status === 'done'
              ? 'success'
              : 'active'
        }
        format={() =>
          job.total_rows
            ? `${job.processed_rows} / ${job.total_rows} 行`
            : (job.status === 'done' ? '完成' : '准备中...')
        }
      />
      {job.error && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 8 }}
          message="导入失败"
          description={
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 11, maxHeight: 200, overflow: 'auto' }}>
              {job.error}
            </pre>
          }
        />
      )}
      {job.status === 'done' && job.report && (
        <Alert
          type="success"
          showIcon
          style={{ marginTop: 8 }}
          message={`已入库 ${job.report.inserted_parents} 主 + ${job.report.inserted_children ?? 0} 行明细`}
          description={
            (job.report.auto_created_suppliers?.length ?? 0) > 0
              ? `新建供应商: ${job.report.auto_created_suppliers.join(', ')}`
              : undefined
          }
        />
      )}
    </Card>
  );
}
