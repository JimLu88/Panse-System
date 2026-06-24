/**
 * 供应商对账模块 (业务需求扩展).
 *
 * 左: 供应商列表 (含 + 新增)
 * 右: 选中供应商 → 月份选择 + 上传 OCR + 送货单列表 + 月度对账下载 + 文件夹查看
 *     点击一条送货单 → 抽屉 (Drawer) 展示原图 + 行明细 + 订单匹配下拉 + 状态机
 */
import { useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Drawer,
  Empty,
  Form,
  Image,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';
import type { UploadFile } from 'antd/es/upload/interface';
import {
  CameraOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  DollarOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FolderOpenOutlined,
  ImportOutlined,
  PlusOutlined,
  ReloadOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  DeliveryLine,
  DeliveryNote,
  PaymentMatch,
  ReconcileSummary,
  Supplier,
  applyManualPaymentMatch,
  createSupplier,
  deliveryFileRawUrl,
  getDeliveryNote,
  listDeliveryNotes,
  listSupplierFolder,
  listSuppliers,
  getAlipaySupplierCandidates,
  getPurchaseSupplierCandidates,
  autoCreateSuppliers,
  patchLineMatch,
  patchSupplier,
  reconcilePayments,
  rematchNote,
  sourceImageUrl,
  statementHtmlUrl,
  statementXlsxUrl,
  updateDeliveryNote,
  uploadDeliveryNote,
} from '../api/client';

const SUPPLIER_TYPE_LABEL: Record<string, string> = {
  woodwork: '木作',
  rock_slab: '岩板',
  glass: '玻璃',
  hardware: '五金',
  logistics: '物流',
  veneer: '贴皮',
  power_track: '电力轨道',
  beech_wood: '榉木木材',
  plywood: '多层板',
  finish_panel: '饰面板',
  other: '其他',
};
const SUPPLIER_TYPE_COLOR: Record<string, string> = {
  woodwork: 'magenta',
  rock_slab: 'gold',
  glass: 'cyan',
  hardware: 'geekblue',
  logistics: 'purple',
  veneer: 'orange',
  power_track: 'red',
  beech_wood: 'lime',
  plywood: 'green',
  finish_panel: 'blue',
  other: 'default',
};
const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审',
  confirmed: '已确认',
  billed: '已开账',
  paid: '已付款',
  disputed: '争议中',
};
const STATUS_COLOR: Record<string, string> = {
  pending_review: 'orange',
  confirmed: 'blue',
  billed: 'cyan',
  paid: 'green',
  disputed: 'red',
};

export default function SuppliersPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [period, setPeriod] = useState<Dayjs>(() => dayjs());
  const [drawerNoteId, setDrawerNoteId] = useState<number | null>(null);
  const [folderOpen, setFolderOpen] = useState(false);
  const [reconcileOpen, setReconcileOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data: suppliers } = useQuery({
    queryKey: ['suppliers'],
    queryFn: () => listSuppliers(true),
  });

  // 默认选第一家
  const activeId = selectedId ?? suppliers?.[0]?.id ?? null;
  const activeSupplier = suppliers?.find((s) => s.id === activeId) ?? null;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列 (送货单)', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="delivery_note" />}
      {viewMode === 'curated' && (
    <Row gutter={16}>
      <Col flex="280px">
        <SupplierListPanel
          suppliers={suppliers ?? []}
          selectedId={activeId}
          onSelect={setSelectedId}
          onEdit={setEditingSupplier}
          onOpenReconcile={() => setReconcileOpen(true)}
        />
      </Col>
      <Col flex="auto">
        {activeSupplier ? (
          <SupplierDetailPanel
            supplier={activeSupplier}
            period={period}
            onPeriodChange={setPeriod}
            onOpenNote={setDrawerNoteId}
            onOpenFolder={() => setFolderOpen(true)}
          />
        ) : (
          <Empty description="左侧选一家供应商, 或点 + 新建" />
        )}
        {activeSupplier && folderOpen && (
          <FolderModal
            supplierId={activeSupplier.id}
            year={period.year()}
            month={period.month() + 1}
            onClose={() => setFolderOpen(false)}
            onOpenNote={(noteId) => {
              setFolderOpen(false);
              setDrawerNoteId(noteId);
            }}
          />
        )}
      </Col>
      <Drawer
        title="送货单详情"
        width={920}
        open={drawerNoteId !== null}
        onClose={() => setDrawerNoteId(null)}
        destroyOnClose
      >
        {drawerNoteId && <DeliveryNoteDetail noteId={drawerNoteId} />}
      </Drawer>
      {reconcileOpen && (
        <ReconcileModal onClose={() => setReconcileOpen(false)} />
      )}
      {editingSupplier && (
        <SupplierEditModal
          supplier={editingSupplier}
          onClose={() => setEditingSupplier(null)}
        />
      )}
    </Row>
      )}
    </Space>
  );
}

// ----------------------------- 供应商列表 ----------------------------- //

function SupplierListPanel({
  suppliers,
  selectedId,
  onSelect,
  onEdit,
  onOpenReconcile,
}: {
  suppliers: Supplier[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onEdit: (s: Supplier) => void;
  onOpenReconcile: () => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [acOpen, setAcOpen] = useState(false);
  const [acSource, setAcSource] = useState<'alipay' | 'purchase'>('alipay');
  const [form] = Form.useForm();
  const createMut = useMutation({
    mutationFn: createSupplier,
    onSuccess: (s) => {
      message.success(`已添加 ${s.name}`);
      qc.invalidateQueries({ queryKey: ['suppliers'] });
      setOpen(false);
      form.resetFields();
      onSelect(s.id);
    },
    onError: (e: any) =>
      message.error(e?.response?.data?.detail ?? '新增失败'),
  });

  return (
    <Card
      size="small"
      title="供应商"
      extra={
        <Space>
          <Button size="small" icon={<ImportOutlined />} onClick={() => { setAcSource('alipay'); setAcOpen(true); }}>从支付宝建</Button>
          <Button size="small" icon={<ImportOutlined />} onClick={() => { setAcSource('purchase'); setAcOpen(true); }}>从采购记录建</Button>
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => setOpen(true)}
          >
            新增
          </Button>
        </Space>
      }
    >
      <List
        size="small"
        dataSource={suppliers}
        locale={{ emptyText: '尚无供应商, 点新增' }}
        renderItem={(s) => (
          <List.Item
            onClick={() => onSelect(s.id)}
            style={{
              cursor: 'pointer',
              background: s.id === selectedId ? '#e6f4ff' : undefined,
              padding: '6px 10px',
              borderRadius: 4,
            }}
            actions={[
              <Button
                key="edit"
                type="link"
                size="small"
                icon={<EditOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  onEdit(s);
                }}
              />,
            ]}
          >
            <Space direction="vertical" size={0}>
              <Space>
                <Tag color={SUPPLIER_TYPE_COLOR[s.supplier_type] ?? 'default'}>
                  {SUPPLIER_TYPE_LABEL[s.supplier_type] ?? s.supplier_type}
                </Tag>
                <strong>{s.name}</strong>
                {s.latest_score != null && (
                  <Tooltip title={`月度评分 · 期 ${s.score_period ?? '-'}`}>
                    <Tag color={Number(s.latest_score) >= 80 ? 'green' : Number(s.latest_score) >= 60 ? 'orange' : 'red'}>
                      {s.latest_rank != null ? `#${s.latest_rank} ` : ''}评分 {Number(s.latest_score).toFixed(0)}
                    </Tag>
                  </Tooltip>
                )}
              </Space>
              {(s.alipay_counterparty_keywords?.length ?? 0) > 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                  支付宝关键字: {(s.alipay_counterparty_keywords ?? []).join(' / ')}
                </Typography.Text>
              )}
            </Space>
          </List.Item>
        )}
      />
      <Button
        block
        icon={<DollarOutlined />}
        onClick={onOpenReconcile}
        style={{ marginTop: 12 }}
      >
        支付宝自动对账
      </Button>

      <AlipaySupplierModal open={acOpen} onClose={() => setAcOpen(false)} source={acSource} />

      <Modal
        title="新增供应商"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ supplier_type: 'woodwork', payment_terms: '月结' }}
          onFinish={(v) => createMut.mutate(v)}
        >
          <Form.Item name="name" label="供应商名" rules={[{ required: true }]}>
            <Input placeholder="如 X 木作工厂" />
          </Form.Item>
          <Form.Item name="supplier_type" label="类型" rules={[{ required: true }]}>
            <Select
              options={Object.entries(SUPPLIER_TYPE_LABEL).map(([v, l]) => ({
                value: v,
                label: l,
              }))}
            />
          </Form.Item>
          <Form.Item name="contact" label="联系人">
            <Input />
          </Form.Item>
          <Form.Item name="phone" label="电话">
            <Input />
          </Form.Item>
          <Form.Item name="payment_terms" label="付款方式">
            <Select
              options={['月结', '现付', '预付', '半月结'].map((x) => ({
                value: x,
                label: x,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="alipay_counterparty_keywords"
            label="支付宝对手方关键字"
            tooltip="付款时支付宝流水里这家供应商的名字。多个关键字逐一回车; 命中任一即可。"
          >
            <Select mode="tags" placeholder="如 X 木业 / 佛山X木业有限公司" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

// ----------------------------- 主面板 -------------------------------- //

function SupplierDetailPanel({
  supplier,
  period,
  onPeriodChange,
  onOpenNote,
  onOpenFolder,
}: {
  supplier: Supplier;
  period: Dayjs;
  onPeriodChange: (d: Dayjs) => void;
  onOpenNote: (id: number) => void;
  onOpenFolder: () => void;
}) {
  const year = period.year();
  const month = period.month() + 1;
  const { data: notes, isLoading, refetch } = useQuery({
    queryKey: ['delivery-notes', supplier.id, year, month],
    queryFn: () => listDeliveryNotes(supplier.id, { year, month }),
  });

  const stats = useMemo(() => {
    const list = notes ?? [];
    const total = list.reduce((acc, n) => acc + (n.total_amount ?? 0), 0);
    const paid = list
      .filter((n) => n.status === 'paid')
      .reduce((acc, n) => acc + (n.total_amount ?? 0), 0);
    const lowConf = list.filter(
      (n) => n.ocr_confidence !== null && n.ocr_confidence < 70,
    ).length;
    return { count: list.length, total, paid, unpaid: total - paid, lowConf };
  }, [notes]);

  return (
    <Card
      size="small"
      title={
        <Space>
          <Tag color={SUPPLIER_TYPE_COLOR[supplier.supplier_type] ?? 'default'}>
            {SUPPLIER_TYPE_LABEL[supplier.supplier_type] ?? supplier.supplier_type}
          </Tag>
          <span>{supplier.name}</span>
          {supplier.payment_terms && (
            <Tag color="default">付款: {supplier.payment_terms}</Tag>
          )}
        </Space>
      }
      extra={
        <Space>
          <DatePicker.MonthPicker
            value={period}
            onChange={(v) => v && onPeriodChange(v)}
            format="YYYY-MM"
            allowClear={false}
          />
          <Button icon={<FolderOpenOutlined />} onClick={onOpenFolder}>
            查看本月图片文件夹
          </Button>
        </Space>
      }
    >
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={4}>
          <Statistic title="单据数" value={stats.count} />
        </Col>
        <Col span={5}>
          <Statistic title="合计" value={stats.total} precision={2} prefix="¥" />
        </Col>
        <Col span={5}>
          <Statistic
            title="已付"
            value={stats.paid}
            precision={2}
            prefix="¥"
            valueStyle={{ color: '#3f8600' }}
          />
        </Col>
        <Col span={5}>
          <Statistic
            title="未付"
            value={stats.unpaid}
            precision={2}
            prefix="¥"
            valueStyle={{ color: '#cf1322' }}
          />
        </Col>
        <Col span={5}>
          <Statistic
            title="OCR 低置信"
            value={stats.lowConf}
            valueStyle={{ color: stats.lowConf > 0 ? '#cf1322' : '#999' }}
            suffix="条"
          />
        </Col>
      </Row>

      <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }}>
        <UploadButton
          supplierId={supplier.id}
          onUploaded={(note) => {
            message.success(`OCR 完成: 单号 ${note.note_no ?? '(空)'} 共 ${note.lines.length} 行`);
            refetch();
            // 自动打开抽屉给用户复核
            onOpenNote(note.id);
          }}
        />
        <Space>
          <Button
            icon={<CloudDownloadOutlined />}
            href={statementXlsxUrl(supplier.id, year, month)}
            target="_blank"
          >
            Excel 对账单
          </Button>
          <Button
            icon={<FilePdfOutlined />}
            href={statementHtmlUrl(supplier.id, year, month)}
            target="_blank"
          >
            PDF 打印版
          </Button>
        </Space>
      </Space>

      <PresetTable<DeliveryNote>
        tableKey="delivery_note"
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={notes ?? []}
        pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        onRow={(r) => ({
          onClick: () => onOpenNote(r.id),
          style: { cursor: 'pointer' },
        })}
        columns={[
          { title: '单号', dataIndex: 'note_no', width: 130, render: (v: string | null) => v ?? <Tag color="warning">未识别</Tag> },
          {
            title: '送货日期',
            dataIndex: 'delivery_date',
            width: 110,
            render: (v: string | null) => v ?? '-',
          },
          {
            title: '金额',
            dataIndex: 'total_amount',
            width: 110,
            render: (v: number | null) =>
              v != null ? `¥ ${v.toFixed(2)}` : '-',
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (v: string) => (
              <Tag color={STATUS_COLOR[v] ?? 'default'}>{STATUS_LABEL[v] ?? v}</Tag>
            ),
          },
          {
            title: 'OCR 置信',
            dataIndex: 'ocr_confidence',
            width: 110,
            render: (v: number | null) => {
              if (v == null) return <Tag>--</Tag>;
              const color = v >= 90 ? 'green' : v >= 70 ? 'orange' : 'red';
              return <Tag color={color}>{v.toFixed(0)}%</Tag>;
            },
          },
          {
            title: '提示',
            dataIndex: 'ocr_warnings',
            render: (ws: string[]) =>
              ws && ws.length ? (
                <Tooltip title={ws.join(' / ')}>
                  <Badge count={ws.length} style={{ backgroundColor: '#faad14' }}>
                    <WarningOutlined />
                  </Badge>
                </Tooltip>
              ) : (
                <Tag color="success" icon={<CheckCircleOutlined />}>无</Tag>
              ),
          },
        ]}
      />
    </Card>
  );
}

// ----------------------------- 上传按钮 ------------------------------- //

function UploadButton({
  supplierId,
  onUploaded,
}: {
  supplierId: number;
  onUploaded: (n: DeliveryNote) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const onPick = (file: File) => {
    setUploading(true);
    uploadDeliveryNote(supplierId, file)
      .then((n) => {
        if (n.ocr_warnings && n.ocr_warnings.length > 0) {
          Modal.warning({
            title: 'OCR 识别有警告',
            content: (
              <div>
                <p>请逐条核对下面的提示, 然后到详情页修正:</p>
                <ul>
                  {n.ocr_warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            ),
          });
        }
        onUploaded(n);
      })
      .catch((e) => {
        message.error(e?.response?.data?.detail ?? '上传/OCR 失败');
      })
      .finally(() => setUploading(false));
    return false; // 阻止 antd 自动上传
  };
  return (
    <Upload
      accept="image/*,.pdf"
      showUploadList={false}
      beforeUpload={onPick as any}
      disabled={uploading}
    >
      <Button
        type="primary"
        icon={<CameraOutlined />}
        loading={uploading}
        size="large"
      >
        拍照上传送货单 (自动 OCR)
      </Button>
    </Upload>
  );
}

// ----------------------------- 送货单详情 ---------------------------- //

function DeliveryNoteDetail({ noteId }: { noteId: number }) {
  const qc = useQueryClient();
  const { data: note, isLoading, refetch } = useQuery({
    queryKey: ['delivery-note', noteId],
    queryFn: () => getDeliveryNote(noteId),
  });

  const updateMut = useMutation({
    mutationFn: (payload: Parameters<typeof updateDeliveryNote>[1]) =>
      updateDeliveryNote(noteId, payload),
    onSuccess: () => {
      message.success('已保存');
      refetch();
      qc.invalidateQueries({ queryKey: ['delivery-notes'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const rematchMut = useMutation({
    mutationFn: () => rematchNote(noteId),
    onSuccess: () => {
      message.success('已重跑匹配 (手动改过的行不会被覆盖)');
      refetch();
    },
  });

  if (isLoading || !note) return <Alert type="info" message="加载中..." />;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Row gutter={16}>
        <Col span={10}>
          <Card size="small" title={<><FileImageOutlined /> 原图</>}>
            {note.source_file_id ? (
              <Image
                src={sourceImageUrl(note.id)}
                fallback="/static/no-image.png"
                style={{ maxWidth: '100%', maxHeight: 480 }}
              />
            ) : (
              <Empty description="未保留原图" />
            )}
          </Card>
        </Col>
        <Col span={14}>
          <Card size="small" title="OCR 结果">
            <Form
              layout="vertical"
              initialValues={{
                note_no: note.note_no ?? '',
                delivery_date: note.delivery_date ?? '',
                total_amount: note.total_amount,
                remark: note.remark ?? '',
              }}
              onValuesChange={() => {}}
            >
              <Row gutter={8}>
                <Col span={8}>
                  <Form.Item label="单号">
                    <Input
                      defaultValue={note.note_no ?? ''}
                      onBlur={(e) =>
                        updateMut.mutate({ note_no: e.target.value })
                      }
                    />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label="送货日期">
                    <Input
                      defaultValue={note.delivery_date ?? ''}
                      placeholder="YYYY-MM-DD"
                      onBlur={(e) =>
                        updateMut.mutate({ delivery_date: e.target.value })
                      }
                    />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label="合计金额">
                    <Input
                      type="number"
                      defaultValue={note.total_amount ?? undefined}
                      addonBefore="¥"
                      onBlur={(e) =>
                        updateMut.mutate({
                          total_amount: parseFloat(e.target.value) || 0,
                        })
                      }
                    />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="状态">
                <Select
                  value={note.status}
                  style={{ width: 200 }}
                  onChange={(v) => updateMut.mutate({ status: v })}
                  options={Object.entries(STATUS_LABEL).map(([v, l]) => ({
                    value: v,
                    label: l,
                  }))}
                />
                {note.ocr_model && (
                  <Tag style={{ marginLeft: 8 }}>模型: {note.ocr_model}</Tag>
                )}
                {note.ocr_confidence !== null && (
                  <Tag color={note.ocr_confidence >= 90 ? 'green' : note.ocr_confidence >= 70 ? 'orange' : 'red'}>
                    OCR {note.ocr_confidence.toFixed(0)}%
                  </Tag>
                )}
              </Form.Item>
              <Form.Item label="备注">
                <Input.TextArea
                  rows={2}
                  defaultValue={note.remark ?? ''}
                  onBlur={(e) => updateMut.mutate({ remark: e.target.value })}
                />
              </Form.Item>
              {note.ocr_warnings && note.ocr_warnings.length > 0 && (
                <Alert
                  type="warning"
                  icon={<ExclamationCircleOutlined />}
                  showIcon
                  message={`OCR 警告 ${note.ocr_warnings.length} 条`}
                  description={
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {note.ocr_warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  }
                />
              )}
            </Form>
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        title={`明细行 (${note.lines.length})`}
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => rematchMut.mutate()}
            loading={rematchMut.isPending}
          >
            重跑订单匹配
          </Button>
        }
      >
        <Table<DeliveryLine>
          rowKey="id"
          size="small"
          dataSource={note.lines}
          pagination={false}
          columns={[
            { title: '行', dataIndex: 'line_no', width: 40 },
            { title: '品名', dataIndex: 'item_name', render: (v: string | null) => v ?? '-' },
            { title: '规格', dataIndex: 'spec', width: 130 },
            { title: '单位', dataIndex: 'unit', width: 60 },
            {
              title: '数量',
              dataIndex: 'qty',
              width: 70,
              render: (v: number) => v?.toString(),
            },
            {
              title: '单价',
              dataIndex: 'unit_price',
              width: 90,
              render: (v: number | null) => (v != null ? `¥${v.toFixed(2)}` : '-'),
            },
            {
              title: '金额',
              dataIndex: 'amount',
              width: 100,
              render: (v: number | null) => (v != null ? `¥${v.toFixed(2)}` : '-'),
            },
            {
              title: '关联订单',
              width: 280,
              render: (_: any, line: DeliveryLine) => (
                <OrderMatchPicker
                  noteId={note.id}
                  line={line}
                  onChanged={() => refetch()}
                />
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}

// ----------------------------- 订单匹配下拉 -------------------------- //

function OrderMatchPicker({
  noteId,
  line,
  onChanged,
}: {
  noteId: number;
  line: DeliveryLine;
  onChanged: () => void;
}) {
  const mut = useMutation({
    mutationFn: (payload: { matched_order_no: string | null }) =>
      patchLineMatch(noteId, line.id, payload),
    onSuccess: (resp) => {
      onChanged();
      if (resp.match_method === 'manual') {
        message.success(`已锁定 ${resp.matched_order_no}`);
      }
    },
    onError: () => message.error('匹配修改失败'),
  });

  const onPick = (val: string | null) => {
    if (val === line.matched_order_no) return;
    if (line.match_confidence !== null && line.match_confidence < 60 && val) {
      // 业务需求: 匹配错误 (低置信) 时弹警告确认
      Modal.confirm({
        title: '匹配度较低, 请确认',
        icon: <ExclamationCircleOutlined />,
        content: `这条候选订单的匹配度只有 ${line.match_confidence?.toFixed(0)}%。确认要绑定吗?`,
        onOk: () => mut.mutate({ matched_order_no: val }),
      });
      return;
    }
    mut.mutate({ matched_order_no: val });
  };

  const opts = line.match_candidates.map((c) => ({
    value: c.order_no,
    label: (
      <Space>
        <span>{c.order_no}</span>
        <Tag color={c.confidence >= 90 ? 'green' : c.confidence >= 70 ? 'blue' : 'orange'}>
          {c.confidence.toFixed(0)}%
          {c.confidence >= 99.5 && ' ✓100'}
        </Tag>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {c.method}
        </Typography.Text>
      </Space>
    ),
  }));

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={4}>
      <Select
        size="small"
        style={{ width: '100%' }}
        value={line.matched_order_no ?? undefined}
        placeholder={line.match_candidates.length ? '从候选中选' : '无候选'}
        allowClear
        onChange={(v) => onPick(v ?? null)}
        options={opts}
        notFoundContent="无候选"
      />
      {line.matched_order_no && (
        <Space size={4}>
          <Tag
            color={
              line.match_method === 'manual'
                ? 'purple'
                : (line.match_confidence ?? 0) >= 90
                ? 'green'
                : (line.match_confidence ?? 0) >= 70
                ? 'blue'
                : 'orange'
            }
          >
            {line.match_confidence?.toFixed(0)}%
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {line.match_method === 'manual'
              ? '手动锁定'
              : line.match_method === 'ai'
              ? 'AI 兜底'
              : '模糊匹配'}
          </Typography.Text>
        </Space>
      )}
    </Space>
  );
}

// ----------------------------- 文件夹 Modal -------------------------- //

function FolderModal({
  supplierId,
  year,
  month,
  onClose,
  onOpenNote,
}: {
  supplierId: number;
  year: number;
  month: number;
  onClose: () => void;
  onOpenNote: (noteId: number) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['folder', supplierId, year, month],
    queryFn: () => listSupplierFolder(supplierId, year, month),
  });

  return (
    <Modal
      open
      width={900}
      title={`${year}-${month.toString().padStart(2, '0')} 图片文件夹 (${data?.file_count ?? 0} 张)`}
      onCancel={onClose}
      footer={null}
    >
      {isLoading || !data ? (
        <Alert type="info" message="加载中..." />
      ) : data.files.length === 0 ? (
        <Empty description="本月还没上传过送货单" />
      ) : (
        <Row gutter={[12, 12]}>
          {data.files.map((f) => (
            <Col span={6} key={f.id}>
              <Card
                size="small"
                cover={
                  <Image
                    src={deliveryFileRawUrl(f.id)}
                    style={{ height: 160, objectFit: 'cover' }}
                    fallback="/static/no-image.png"
                    preview={{ src: deliveryFileRawUrl(f.id) }}
                  />
                }
              >
                <Card.Meta
                  title={f.note_no ?? '(未识别单号)'}
                  description={
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {f.original_name}
                      </Typography.Text>
                      {f.delivery_note_id && (
                        <Button
                          size="small"
                          type="link"
                          icon={<ImportOutlined />}
                          onClick={() => onOpenNote(f.delivery_note_id!)}
                        >
                          打开送货单
                        </Button>
                      )}
                    </Space>
                  }
                />
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </Modal>
  );
}

// ----------------------------- 编辑供应商 ---------------------------- //

function SupplierEditModal({
  supplier,
  onClose,
}: {
  supplier: Supplier;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const mut = useMutation({
    mutationFn: (payload: Partial<Supplier>) => patchSupplier(supplier.id, payload),
    onSuccess: () => {
      message.success('已保存');
      qc.invalidateQueries({ queryKey: ['suppliers'] });
      onClose();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  return (
    <Modal
      open
      title={`编辑供应商 — ${supplier.name}`}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={mut.isPending}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={supplier}
        onFinish={(v) => mut.mutate(v)}
      >
        <Form.Item name="name" label="供应商名" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="supplier_type" label="类型" rules={[{ required: true }]}>
          <Select
            options={Object.entries(SUPPLIER_TYPE_LABEL).map(([v, l]) => ({
              value: v,
              label: l,
            }))}
          />
        </Form.Item>
        <Form.Item name="contact" label="联系人">
          <Input />
        </Form.Item>
        <Form.Item name="phone" label="电话">
          <Input />
        </Form.Item>
        <Form.Item name="payment_terms" label="付款方式">
          <Select
            options={['月结', '现付', '预付', '半月结'].map((x) => ({
              value: x,
              label: x,
            }))}
          />
        </Form.Item>
        <Form.Item
          name="alipay_counterparty_keywords"
          label="支付宝对手方关键字"
          tooltip="付款时支付宝流水里这家供应商的名字。多个关键字逐一回车; 命中任一即可。"
        >
          <Select mode="tags" placeholder="如 X 木业 / 佛山 X 木业有限公司" />
        </Form.Item>
        <Form.Item name="alipay_account" label="主要付款账号">
          <Select
            allowClear
            options={['企业号', '个体户私账', '爱群号', '佳宝号', '主力号'].map((x) => ({
              value: x,
              label: x,
            }))}
          />
        </Form.Item>
        <Form.Item name="remark" label="备注">
          <Input.TextArea rows={2} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ----------------------------- 支付宝自动对账 Modal (业务需求 2) ----- //

const DECISION_LABEL: Record<PaymentMatch['decision'], string> = {
  exact: '精确匹配',
  combo: '合并匹配',
  needs_review: '待手动复核',
  no_supplier: '未识别供应商',
  no_candidates: '无候选单据',
  skipped: '跳过',
};
const DECISION_COLOR: Record<PaymentMatch['decision'], string> = {
  exact: 'green',
  combo: 'cyan',
  needs_review: 'orange',
  no_supplier: 'default',
  no_candidates: 'default',
  skipped: 'default',
};

// #3: 从支付宝流水挖"多次打过款、还不是供应商"的对手方, 勾选批量建档(自动加支付宝关键字)
function AlipaySupplierModal({ open, onClose, source = 'alipay' }: { open: boolean; onClose: () => void; source?: 'alipay' | 'purchase' }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [type, setType] = useState('other');
  const { data, isFetching } = useQuery({
    queryKey: ['supplier-candidates', source],
    queryFn: () => (source === 'purchase' ? getPurchaseSupplierCandidates(1) : getAlipaySupplierCandidates(2)),
    enabled: open,
  });
  const mut = useMutation({
    mutationFn: () => autoCreateSuppliers(selected, type),
    onSuccess: (r) => {
      message.success(`已建 ${r.count} 个供应商`);
      setSelected([]);
      qc.invalidateQueries({ queryKey: ['suppliers'] });
      onClose();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '建供应商失败'),
  });
  const cands = data?.candidates ?? [];
  return (
    <Modal
      title={source === 'purchase' ? '从采购记录建供应商' : '从支付宝流水自动建供应商'}
      open={open}
      onCancel={onClose}
      width={680}
      okText={`建 ${selected.length} 个`}
      okButtonProps={{ disabled: selected.length === 0, loading: mut.isPending }}
      onOk={() => mut.mutate()}
      destroyOnClose
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={source === 'purchase'
          ? '下面是配件采购记录里的供应商(还不是档案)。这是真实付款对象, 比支付宝准。勾选建档。'
          : '下面是支付宝里多次给它打过款、但还不是供应商的对手方。勾选真正的供应商建档——会自动加支付宝关键字，下次「自动对账」就能命中。'}
      />
      <Space style={{ marginBottom: 8 }}>
        <span>类型</span>
        <Select
          size="small"
          style={{ width: 130 }}
          value={type}
          onChange={setType}
          options={[
            { value: 'woodwork', label: '木作工厂' },
            { value: 'rock_slab', label: '岩板厂' },
            { value: 'glass', label: '玻璃厂' },
            { value: 'hardware', label: '五金/配件' },
            { value: 'logistics', label: '物流' },
            { value: 'veneer', label: '贴皮' },
            { value: 'power_track', label: '电力轨道' },
            { value: 'beech_wood', label: '榉木木材' },
            { value: 'plywood', label: '多层板' },
            { value: 'finish_panel', label: '饰面板' },
            { value: 'other', label: '其他' },
          ]}
        />
      </Space>
      <Table
        size="small"
        rowKey="counterparty"
        loading={isFetching}
        dataSource={cands}
        pagination={false}
        scroll={{ y: 360 }}
        locale={{ emptyText: '没有候选（流水里多次付款的对手方都已是供应商）' }}
        rowSelection={{ selectedRowKeys: selected, onChange: (k) => setSelected(k as string[]) }}
        columns={[
          { title: '对手方', dataIndex: 'counterparty', ellipsis: true },
          { title: '打款次数', dataIndex: 'payment_count', width: 90, align: 'right' as const },
          {
            title: '累计金额',
            dataIndex: 'total_paid',
            width: 120,
            align: 'right' as const,
            render: (v: number) => `¥${Number(v).toLocaleString()}`,
          },
        ]}
      />
    </Modal>
  );
}


function ReconcileModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [account, setAccount] = useState<string | undefined>(undefined);
  const [sinceDays, setSinceDays] = useState(90);
  const [preview, setPreview] = useState<ReconcileSummary | null>(null);

  const dryRunMut = useMutation({
    mutationFn: () =>
      reconcilePayments({ account, since_days: sinceDays, dry_run: true }),
    onSuccess: (r) => setPreview(r),
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '预览失败'),
  });

  const commitMut = useMutation({
    mutationFn: () =>
      reconcilePayments({ account, since_days: sinceDays, dry_run: false }),
    onSuccess: (r) => {
      message.success(
        `已落盘: ${r.matched_count} 笔精确/合并匹配, ${r.needs_review} 笔待复核`,
      );
      setPreview(r);
      qc.invalidateQueries({ queryKey: ['delivery-notes'] });
      qc.invalidateQueries({ queryKey: ['delivery-note'] });
    },
  });

  const manualMut = useMutation({
    mutationFn: ({ flowId, noteIds }: { flowId: number; noteIds: number[] }) =>
      applyManualPaymentMatch(flowId, noteIds),
    onSuccess: () => {
      message.success('已手动绑定');
      dryRunMut.mutate();
      qc.invalidateQueries({ queryKey: ['delivery-notes'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '绑定失败'),
  });

  const hasCommitable = (preview?.matched_count ?? 0) > 0;

  return (
    <Modal
      open
      width={1100}
      title="支付宝流水 → 供应商送货单 自动对账"
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button
            onClick={() => dryRunMut.mutate()}
            loading={dryRunMut.isPending}
          >
            重新预览
          </Button>
          <Popconfirm
            title={`确认把 ${preview?.matched_count ?? 0} 笔自动匹配落盘?`}
            description="待复核的笔不会被自动落盘"
            okText="确认落盘"
            disabled={!hasCommitable}
            onConfirm={() => commitMut.mutate()}
          >
            <Button
              type="primary"
              disabled={!hasCommitable}
              loading={commitMut.isPending}
            >
              确认匹配 ({preview?.matched_count ?? 0} 笔)
            </Button>
          </Popconfirm>
        </Space>
      }
    >
      <Space style={{ marginBottom: 12 }}>
        <span>账户:</span>
        <Select
          allowClear
          placeholder="全部账户"
          style={{ width: 160 }}
          value={account}
          onChange={setAccount}
          options={['企业号', '个体户私账', '爱群号', '佳宝号', '主力号'].map((x) => ({
            value: x,
            label: x,
          }))}
        />
        <span>近</span>
        <Select
          value={sinceDays}
          onChange={setSinceDays}
          options={[30, 60, 90, 180].map((d) => ({ value: d, label: `${d} 天` }))}
          style={{ width: 100 }}
        />
        <Button
          type="primary"
          icon={<DollarOutlined />}
          onClick={() => dryRunMut.mutate()}
          loading={dryRunMut.isPending}
        >
          开始预览
        </Button>
      </Space>

      {preview && (
        <>
          <Row gutter={8} style={{ marginBottom: 16 }}>
            <Col span={4}>
              <Statistic title="扫描流水" value={preview.scanned} />
            </Col>
            <Col span={4}>
              <Statistic
                title="自动匹配"
                value={preview.matched_count}
                valueStyle={{ color: '#3f8600' }}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="待复核"
                value={preview.needs_review}
                valueStyle={{ color: '#fa8c16' }}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="未识别供应商"
                value={preview.no_supplier}
                valueStyle={{ color: '#bfbfbf' }}
              />
            </Col>
            <Col span={4}>
              <Statistic title="无候选" value={preview.no_candidates} />
            </Col>
            <Col span={4}>
              <Statistic title="跳过" value={preview.skipped} />
            </Col>
          </Row>

          {preview.no_supplier > 0 && (
            <Alert
              style={{ marginBottom: 8 }}
              type="info"
              showIcon
              message={`${preview.no_supplier} 笔未识别供应商`}
              description="去供应商编辑里加 “支付宝对手方关键字”, 再来重新预览即可命中。"
            />
          )}

          <Table<PaymentMatch>
            size="small"
            rowKey="flow_id"
            dataSource={preview.matches}
            pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
            columns={[
              {
                title: '决策',
                dataIndex: 'decision',
                width: 100,
                filters: Object.entries(DECISION_LABEL).map(([v, l]) => ({
                  value: v,
                  text: l,
                })),
                onFilter: (v, r) => r.decision === v,
                render: (v: PaymentMatch['decision']) => (
                  <Tag color={DECISION_COLOR[v]}>{DECISION_LABEL[v]}</Tag>
                ),
              },
              {
                title: '流水时间',
                dataIndex: 'flow_time',
                width: 140,
                render: (v: string | null) =>
                  v ? new Date(v).toLocaleString('zh-CN') : '-',
              },
              {
                title: '对手方',
                dataIndex: 'counterparty',
                ellipsis: true,
              },
              {
                title: '金额',
                dataIndex: 'flow_amount',
                width: 90,
                align: 'right',
                render: (v: number) => (
                  <span style={{ color: v < 0 ? '#cf1322' : '#3f8600' }}>
                    ¥ {Math.abs(v).toFixed(2)}
                  </span>
                ),
              },
              {
                title: '供应商',
                dataIndex: 'supplier_name',
                width: 110,
                render: (v: string | null) =>
                  v ?? <Typography.Text type="secondary">-</Typography.Text>,
              },
              {
                title: '匹配单据',
                dataIndex: 'matched_note_nos',
                render: (nos: string[], r: PaymentMatch) =>
                  nos.length === 0 ? (
                    <Typography.Text type="secondary">{r.reason}</Typography.Text>
                  ) : (
                    <Space wrap size={4}>
                      {nos.map((no) => (
                        <Tag key={no} color="blue">
                          {no}
                        </Tag>
                      ))}
                      {r.decision === 'needs_review' && (
                        <Tooltip title="把这 N 张单据一起绑定到这笔流水">
                          <Button
                            size="small"
                            type="primary"
                            ghost
                            onClick={() =>
                              Modal.confirm({
                                title: '把这些单据一起绑定到这笔流水?',
                                content: `${nos.join(' + ')} 合计应 = ¥${Math.abs(r.flow_amount).toFixed(2)}`,
                                onOk: () =>
                                  manualMut.mutateAsync({
                                    flowId: r.flow_id,
                                    noteIds: r.matched_note_ids,
                                  }),
                              })
                            }
                          >
                            手动绑定
                          </Button>
                        </Tooltip>
                      )}
                    </Space>
                  ),
              },
              {
                title: '说明',
                dataIndex: 'reason',
                width: 220,
                render: (v: string) => (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {v}
                  </Typography.Text>
                ),
              },
            ]}
          />
        </>
      )}
    </Modal>
  );
}
