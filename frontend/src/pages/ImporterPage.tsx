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
  Popconfirm,
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
  CloseCircleOutlined,
  CloudUploadOutlined,
  ExperimentOutlined,
  ImportOutlined,
  InboxOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  EntityField,
  EntityType,
  ImportJob,
  ImportReport,
  ImporterPreviewResp,
  PostImportResult,
  SheetAnalysis,
  SheetPreview,
  SmartCommitReport,
  cancelImportJob,
  commitImporter,
  commitImporterAsync,
  fetchEntityTypes,
  fetchImportJob,
  previewImporter,
  smartAnalyzeExcel,
  smartCommitExcel,
} from '../api/client';

const ALIPAY_ACCOUNTS = ['企业号', '个体户私账', '爱群号', '佳宝号', '主力号'];

const { Dragger } = Upload;


export default function ImporterPage() {
  return (
    <Tabs
      defaultActiveKey="smart"
      items={[
        { key: 'smart', label: '智能导入 (任意 Excel, AI 自动分析)',
          children: <SmartImporter /> },
        { key: 'legacy', label: '手动模式 (单 sheet, 自选实体)',
          children: <LegacyImporter /> },
      ]}
    />
  );
}


function LegacyImporter() {
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
  const { data: job, refetch } = useQuery({
    queryKey: ['import-job', jobId],
    queryFn: () => fetchImportJob(jobId),
    refetchInterval: (q) => {
      const j = q.state.data as ImportJob | undefined;
      if (!j) return 2000;
      // done / failed / cancelled 都停止轮询
      return ['done', 'failed', 'cancelled'].includes(j.status) ? false : 2000;
    },
  });

  // 业务需求扩展: 取消作业 — worker 在下次 progress tick (≤50 行) 内退出
  const cancelMut = useMutation({
    mutationFn: () => cancelImportJob(jobId),
    onSuccess: () => {
      message.info('已请求取消, worker 会在 1-2 秒内停止');
      refetch();
    },
    onError: (e: any) =>
      message.error(e?.response?.data?.detail ?? '取消请求失败'),
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
  const isFinished = ['done', 'failed', 'cancelled'].includes(job.status);
  const canCancel = !isFinished;

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
          {canCancel && (
            <Popconfirm
              title="确定取消导入?"
              description="worker 会在下一次进度 tick (≤50 行) 时退出, 已入库的数据保留."
              okText="确定取消"
              okType="danger"
              onConfirm={() => cancelMut.mutate()}
            >
              <Button
                danger
                size="small"
                icon={<StopOutlined />}
                loading={cancelMut.isPending}
              >
                取消
              </Button>
            </Popconfirm>
          )}
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
          {isFinished && (
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
            : job.status === 'cancelled'
              ? 'normal'
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
      {job.status === 'cancelled' && (
        <Alert
          type="warning"
          showIcon
          icon={<CloseCircleOutlined />}
          style={{ marginTop: 8 }}
          message="作业已取消"
          description={job.error || `已处理 ${job.processed_rows}/${job.total_rows} 行, 后续行未入库`}
        />
      )}
      {job.status === 'failed' && job.error && (
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


// ============================ 智能导入 (Phase 14) ============================ //

const QUALITY_META: Record<string, { color: string; label: string; advice: string }> = {
  good: { color: 'green', label: '良好', advice: '直接导' },
  needs_review: { color: 'orange', label: '需确认', advice: '检查列映射后导入' },
  messy: { color: 'red', label: '太乱', advice: '建议员工先修 Excel 再导' },
  unknown: { color: 'default', label: '未知', advice: '' },
};

type SmartPlanState = {
  entity_type: string;
  mapping: Record<string, string>;
  sheet_account?: string;
};

function SmartImporter() {
  const [resp, setResp] = useState<{ file_b64: string; sheets: SheetAnalysis[] } | null>(null);
  const [editedPlan, setEditedPlan] = useState<Record<string, SmartPlanState>>({});
  const [commitResult, setCommitResult] = useState<{
    reports: SmartCommitReport[];
    post_import: PostImportResult;
  } | null>(null);

  const analyzeMut = useMutation({
    mutationFn: (file: File) => smartAnalyzeExcel(file),
    onSuccess: (r) => {
      setResp(r);
      // 初始化每个 sheet 的可编辑状态 = AI 建议
      const init: typeof editedPlan = {};
      r.sheets.forEach((s) => {
        init[s.sheet_name] = {
          entity_type: s.suggested_entity || 'unknown',
          mapping: { ...s.mapping },
        };
      });
      setEditedPlan(init);
      setCommitResult(null);
      message.success(`AI 分析了 ${r.sheets.length} 个 sheet`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '分析失败'),
  });

  const plannable = (s: SheetAnalysis) => {
    const e = editedPlan[s.sheet_name];
    if (!e || e.entity_type === 'unknown') return false;
    // 支付宝流水: 选了账户就能导 (账户列可缺)
    if (e.entity_type === 'alipay_flow' && e.sheet_account) return true;
    return Object.keys(e.mapping).length > 0;
  };

  const commitMut = useMutation({
    mutationFn: ({ dryRun, onConflict }: { dryRun: boolean; onConflict: 'ask' | 'overwrite' }) => {
      if (!resp) throw new Error('no analysis');
      const plan = resp.sheets.filter(plannable).map((s) => ({
        sheet_name: s.sheet_name,
        entity_type: editedPlan[s.sheet_name].entity_type,
        mapping: editedPlan[s.sheet_name].mapping,
        header_row: s.header_row,
        dry_run: dryRun,
        on_conflict: onConflict,
        sheet_account: editedPlan[s.sheet_name].sheet_account ?? null,
      }));
      if (plan.length === 0) throw new Error('没有可导入的 sheet');
      return smartCommitExcel({ file_b64: resp.file_b64, plan });
    },
    onSuccess: (r, vars) => {
      setCommitResult(r);
      const conflicts = (r.reports || []).reduce(
        (n, rep) => n + (rep.conflicts?.length ?? 0), 0);
      if (conflicts > 0 && vars.onConflict === 'ask') {
        message.warning(`${conflicts} 处与库内数据不同, 请在下方裁决`);
      } else {
        message.success(`${vars.dryRun ? '试运行' : '导入'} 完成: ${r.reports.length} 个 sheet`);
      }
    },
    onError: (e: any) => message.error(e?.message ?? e?.response?.data?.detail ?? '失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="智能模式: 上传任意 Excel, AI 自动识别每个 sheet 是哪类数据"
        description={
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li><b>🟢 良好</b>: 列名规范, 直接导</li>
            <li><b>🟡 需确认</b>: 大体能识别, 你确认下列映射就行</li>
            <li><b>🔴 太乱</b>: AI 给出具体哪几行/哪列有问题, 让员工先修 Excel 再导</li>
          </ul>
        }
      />
      <Card size="small">
        <Dragger
          accept=".xlsx,.xls"
          showUploadList={false}
          beforeUpload={(f) => { analyzeMut.mutate(f as File); return false; }}
          disabled={analyzeMut.isPending}
          multiple={false}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {analyzeMut.isPending ? 'AI 正在分析每个 sheet...' : '点击或拖入任意 Excel'}
          </p>
          <p className="ant-upload-hint">
            支持 26 个 sheet 一起分析, 每个 sheet 独立质量评分
          </p>
        </Dragger>
      </Card>

      {resp && (
        <>
          <Card size="small" title={`分析结果 (${resp.sheets.length} 个 sheet)`}
                extra={
                  <Space>
                    <Button onClick={() => commitMut.mutate({ dryRun: true, onConflict: 'ask' })}
                            loading={commitMut.isPending}>
                      试运行 (不入库)
                    </Button>
                    <Button type="primary"
                            onClick={() => commitMut.mutate({ dryRun: false, onConflict: 'ask' })}
                            loading={commitMut.isPending}>
                      一键全部导入 ({resp.sheets.filter(plannable).length} 个)
                    </Button>
                  </Space>
                }>
            <Tabs
              destroyInactiveTabPane={false}
              items={resp.sheets.map((s) => {
                const qm = QUALITY_META[s.quality] || QUALITY_META.unknown;
                const ed = editedPlan[s.sheet_name];
                return {
                  key: s.sheet_name,
                  label: (
                    <Space>
                      <Tag color={qm.color}>{qm.label}</Tag>
                      <span>{s.sheet_name}</span>
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {s.total_rows} 行
                      </Typography.Text>
                    </Space>
                  ),
                  children: (
                    <SmartSheetEditor
                      sheet={s}
                      state={ed}
                      onStateChange={(state) =>
                        setEditedPlan((p) => ({ ...p, [s.sheet_name]: state }))
                      }
                    />
                  ),
                };
              })}
            />
          </Card>

          {commitResult && (
            <Card size="small" title="导入报告">
              <Table size="small" rowKey={(r) => r.sheet_name}
                     pagination={false}
                     dataSource={commitResult.reports}
                     columns={[
                       { title: 'Sheet', dataIndex: 'sheet_name', width: 200 },
                       { title: '实体', dataIndex: 'entity_type', width: 140 },
                       { title: '总行', dataIndex: 'total_rows', width: 80 },
                       { title: '已入', dataIndex: 'inserted_parents', width: 80,
                         render: (v: number) => v ? <Tag color="green">{v}</Tag> : '-' },
                       { title: '子项', dataIndex: 'inserted_children', width: 80,
                         render: (v: number) => v ? <Tag>{v}</Tag> : '-' },
                       { title: '跳过', dataIndex: 'skipped_rows', width: 80,
                         render: (v: number) => v ? <Tag color="orange">{v}</Tag> : '-' },
                       { title: '冲突', dataIndex: 'conflicts', width: 80,
                         render: (v: any[]) => (v ?? []).length > 0 ?
                           <Tag color="volcano">{v.length}</Tag> : '-' },
                       { title: '错误', dataIndex: 'errors',
                         render: (v: string[]) => (v ?? []).length > 0 ?
                           <Tag color="red">{v.length} 条</Tag> : '-' },
                       { title: '其他', render: (r: any) =>
                         r.skipped ? <Tag color="orange">{r.reason}</Tag> :
                         r.error ? <Tag color="red">{r.error.slice(0, 60)}</Tag> : '-' },
                     ]} />
            </Card>
          )}

          {commitResult && <ConflictReview
            reports={commitResult.reports}
            onApplyNew={() => commitMut.mutate({ dryRun: false, onConflict: 'overwrite' })}
            applying={commitMut.isPending}
          />}

          {commitResult?.post_import &&
           (commitResult.post_import.logic_issues > 0 || commitResult.post_import.analysis) && (
            <Card size="small" title="AI 导入后核查 + 运营分析">
              {commitResult.post_import.logic_issues > 0 && (
                <Alert
                  type="warning" showIcon style={{ marginBottom: 8 }}
                  message={`AI 逻辑核查发现 ${commitResult.post_import.logic_issues} 条疑似异常`}
                  description="已写入「异常」页面, 请前往复核处理。"
                />
              )}
              {commitResult.post_import.analysis && (
                <Alert
                  type="info" showIcon
                  message="运营状况分析"
                  description={
                    <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                      {commitResult.post_import.analysis}
                    </Typography.Paragraph>
                  }
                />
              )}
            </Card>
          )}
        </>
      )}
    </Space>
  );
}

// ----------------------------- 冲突复核 (#9) ---------------------- //

function ConflictReview({ reports, onApplyNew, applying }: {
  reports: SmartCommitReport[];
  onApplyNew: () => void;
  applying: boolean;
}) {
  const rows = reports.flatMap((rep) =>
    (rep.conflicts ?? []).flatMap((c) =>
      c.diffs.map((d) => ({
        sheet: rep.sheet_name,
        pk: c.source_pk,
        table: c.source_table,
        field: d.field,
        old: d.old,
        new: d.new,
      })),
    ),
  );
  if (rows.length === 0) return null;
  return (
    <Card size="small" title={<Space>与此前数据不符 — 需要裁决<Tag color="volcano">{rows.length} 处</Tag></Space>}
          extra={
            <Popconfirm
              title="采用新值并覆盖?"
              description="会把表里的新值写入已有记录 (其余已存在的行不动)。"
              okText="采用新值覆盖"
              onConfirm={onApplyNew}
            >
              <Button type="primary" danger loading={applying}>采用新值 (覆盖)</Button>
            </Popconfirm>
          }>
      <Alert type="warning" showIcon style={{ marginBottom: 8 }}
             message="以下记录已存在且导入表里的值不同, 默认未覆盖。确认无误可点右上「采用新值」, 否则保持原值不动。" />
      <Table size="small" rowKey={(_r, i) => String(i)} pagination={{ pageSize: 10 }}
             dataSource={rows}
             columns={[
               { title: 'Sheet', dataIndex: 'sheet', width: 140 },
               { title: '记录', dataIndex: 'pk', width: 140 },
               { title: '字段', dataIndex: 'field', width: 120 },
               { title: '原值 (库内)', dataIndex: 'old',
                 render: (v: any) => <span style={{ color: '#999' }}>{v == null ? '空' : String(v)}</span> },
               { title: '新值 (导入)', dataIndex: 'new',
                 render: (v: any) => <strong>{v == null ? '空' : String(v)}</strong> },
             ]} />
    </Card>
  );
}


function SmartSheetEditor({ sheet, state, onStateChange }: {
  sheet: SheetAnalysis;
  state?: SmartPlanState;
  onStateChange: (s: SmartPlanState) => void;
}) {
  const { data: entityTypes = [] } = useQuery({
    queryKey: ['importer-entity-types'],
    queryFn: fetchEntityTypes,
  });
  const qm = QUALITY_META[sheet.quality] || QUALITY_META.unknown;
  const entityType = state?.entity_type ?? 'unknown';
  const mapping = state?.mapping ?? {};
  const sheetAccount = state?.sheet_account;
  const entity = entityTypes.find((e) => e.value === entityType);
  const fields = entity?.fields ?? [];

  const setMapping = (target: string, excelCol: string | undefined) => {
    const next = { ...mapping };
    if (excelCol) next[target] = excelCol;
    else delete next[target];
    onStateChange({ entity_type: entityType, mapping: next, sheet_account: sheetAccount });
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Row gutter={16}>
        <Col span={8}>
          <Card size="small" title="质量评分">
            <Statistic value={sheet.quality_score} suffix="/ 100"
                       valueStyle={{ color: { good: '#52c41a', needs_review: '#fa8c16',
                                              messy: '#cf1322' }[sheet.quality] || '#999' }} />
            <Tag color={qm.color}>{qm.label}</Tag> — {qm.advice}
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="AI 判断">
            <Space direction="vertical">
              <Space>
                <Tag color="blue">建议: {sheet.entity_label || '未知'}</Tag>
                <span style={{ fontSize: 12, color: '#999' }}>
                  置信度 {(sheet.confidence * 100).toFixed(0)}%
                </span>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                表头在第 {sheet.header_row} 行 · {sheet.columns.length} 列
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="实体类型 (可改)">
            <Select style={{ width: '100%' }}
                    value={entityType}
                    onChange={(v) => onStateChange({ entity_type: v, mapping: {}, sheet_account: undefined })}
                    options={[
                      { value: 'unknown', label: '— 不导这个 sheet —' },
                      ...entityTypes.map((e) => ({ value: e.value, label: e.label })),
                    ]} />
          </Card>
        </Col>
      </Row>

      {entityType === 'alipay_flow' && !mapping['account'] && (
        <Alert
          type="info" showIcon
          message="这张支付宝流水表没有「账户」列 — 请指定它属于哪个账户"
          description={
            <Space>
              <span>该 sheet 全部流水归到:</span>
              <Select
                style={{ width: 200 }}
                placeholder="选择账户"
                value={sheetAccount}
                onChange={(v) =>
                  onStateChange({ entity_type: entityType, mapping, sheet_account: v })}
                options={ALIPAY_ACCOUNTS.map((a) => ({ value: a, label: a }))}
              />
            </Space>
          }
        />
      )}

      {sheet.issues.length > 0 && (
        <Alert
          type={sheet.quality === 'messy' ? 'error' : 'warning'}
          showIcon
          message={`检测到 ${sheet.issues.length} 个数据问题`}
          description={
            <Table size="small" pagination={false}
                   rowKey={(_r, i) => String(i)}
                   dataSource={sheet.issues}
                   columns={[
                     { title: '位置', width: 120, render: (i: any) =>
                       i.row_offset === -1 ? '表头' :
                       `第 ${sheet.header_row + i.row_offset + 1} 行 · ${i.column}` },
                     { title: '原值', dataIndex: 'value', width: 150,
                       render: (v: any) => v == null ? <i style={{ color: '#999' }}>空</i> :
                         <code style={{ fontSize: 11 }}>{String(v).slice(0, 30)}</code> },
                     { title: '问题', dataIndex: 'problem' },
                     { title: '建议', dataIndex: 'fix' },
                   ]} />
          }
        />
      )}

      {sheet.notes.length > 0 && (
        <Alert type="info" message="其他提示"
               description={<ul>{sheet.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>} />
      )}

      {entityType !== 'unknown' && fields.length > 0 && (
        <Card size="small" title="列映射 (AI 已填, 可调)">
          <Table size="small" rowKey="name" pagination={false}
                 dataSource={fields}
                 columns={[
                   { title: '目标字段', dataIndex: 'name', width: 180,
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
                   { title: 'Excel 列', width: 280,
                     render: (_: any, f: EntityField) => (
                       <Select allowClear style={{ width: '100%' }}
                               value={mapping[f.name]}
                               onChange={(v) => setMapping(f.name, v)}
                               placeholder={f.required ? '(必选)' : '(可选)'}
                               status={f.required && !mapping[f.name] ? 'error' : undefined}
                               options={sheet.columns.map((c) => ({ value: c, label: c }))} />
                     ),
                   },
                 ]} />
        </Card>
      )}

      <Card size="small" title={`数据样本 (从第 ${sheet.header_row + 1} 行起)`}>
        <Table size="small" pagination={false} scroll={{ x: true }}
               dataSource={sheet.sample_rows.map((r, i) => ({
                 key: i, ...Object.fromEntries(sheet.columns.map((c, j) => [c, r[j]])),
               }))}
               columns={sheet.columns.map((c) => ({
                 title: c, dataIndex: c, ellipsis: true,
                 render: (v: any) => v == null ? '' : String(v),
               }))} />
      </Card>
    </Space>
  );
}
