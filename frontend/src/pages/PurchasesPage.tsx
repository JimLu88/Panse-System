import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Image,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import FullColumnView from '../components/FullColumnView';
import { CUTE_IMG } from '../components/ProductThumb';
import ShipmentTracker from '../components/ShipmentTracker';
import PresetTable from '../components/PresetTable';
import UrgentShortageGate from '../components/UrgentShortageGate';
import { InboxOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UploadProps } from 'antd';
import {
  importPurchasesTable,
  listPurchases,
  uploadPurchaseOcr,
  purchaseSourceImageUrl,
  type PurchaseOcrResult,
  type PurchaseRow,
} from '../api/client';

const { Title, Text, Paragraph } = Typography;

export default function PurchasesPage() {
  const qc = useQueryClient();
  const [lastResult, setLastResult] = useState<PurchaseOcrResult | null>(null);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['purchases'],
    queryFn: () => listPurchases(),
  });

  const uploadMut = useMutation({
    mutationFn: (file: File) => uploadPurchaseOcr(file, true),
    onSuccess: (res) => {
      setLastResult(res);
      if (res.warnings.length === 0) {
        message.success(`识别成功, 入库 ${res.created_purchase_ids.length} 条明细`);
      } else {
        message.warning(`识别完成, 但有 ${res.warnings.length} 条提示, 请核对`);
      }
      qc.invalidateQueries({ queryKey: ['purchases'] });
    },
    onError: (e: any) => {
      message.error(`上传/识别失败: ${e?.response?.data?.detail || e?.message || e}`);
    },
  });

  // 表格 (Excel/CSV) 走结构化导入, 图片/PDF 走 OCR — 同一个拖拽框按扩展名分流
  const tableMut = useMutation({
    mutationFn: (file: File) => importPurchasesTable(file),
    onSuccess: (res) => {
      message[res.inserted ? 'success' : 'warning'](res.message
        + (res.skipped_duplicate ? ` · 重复跳过 ${res.skipped_duplicate}` : '')
        + (res.skipped_invalid ? ` · 无效跳过 ${res.skipped_invalid}` : ''));
      qc.invalidateQueries({ queryKey: ['purchases'] });
    },
    onError: (e: any) => {
      message.error(`表格导入失败: ${e?.response?.data?.detail || e?.message || e}`);
    },
  });

  const uploadProps: UploadProps = {
    multiple: false,
    showUploadList: false,
    accept: 'image/*,.pdf,.xlsx,.xls,.csv',
    beforeUpload: (file) => {
      const name = (file.name || '').toLowerCase();
      if (/\.(xlsx|xls|csv)$/.test(name)) {
        tableMut.mutate(file as File);
      } else {
        uploadMut.mutate(file as File);
      }
      return false; // 阻止 antd 自动上传, 走我们的 mutation
    },
  };

  const columns = [
    { title: '采购单号', dataIndex: 'purchase_no', key: 'purchase_no', width: 150 },
    { title: '供应商', dataIndex: 'supplier', key: 'supplier' },
    { title: '购买日期', dataIndex: 'purchase_date', key: 'purchase_date', width: 110 },
    { title: '配件名称', dataIndex: 'material_name', key: 'material_name' },
    { title: '规格', dataIndex: 'spec', key: 'spec' },
    // 数字格式统一 (用户拍板): 数量去尾零 (1.0000→1), 金额 ¥ 整数无小数
    { title: '数量', dataIndex: 'qty', key: 'qty', width: 80,
      render: (v: number | string | null) => (v == null ? '-' : String(Number(v))) },
    {
      title: '单价',
      dataIndex: 'unit_price',
      key: 'unit_price',
      width: 90,
      render: (v: number | null) => (v == null ? '-' : `¥${Math.round(Number(v)).toLocaleString()}`),
    },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 100,
      render: (v: number | null) => (v == null ? '-' : `¥${Math.round(Number(v)).toLocaleString()}`),
    },
    { title: '快递单号', dataIndex: 'tracking_no', key: 'tracking_no' },
    { title: '物流', key: 'shipment', width: 150, render: (_: any, r: any) => <ShipmentTracker entityType="part_purchase" entityId={r.id} /> },
    {
      title: '发票原图',
      key: 'image',
      width: 90,
      render: (_: unknown, r: PurchaseRow) =>
        r.source_file_id ? (
          <Image
            width={48}
            height={48}
            style={{ objectFit: 'cover' }}
            src={purchaseSourceImageUrl(r.id)}
            placeholder
            fallback={CUTE_IMG}
          />
        ) : (
          <Text type="secondary">无</Text>
        ),
    },
    {
      title: 'OCR 提示',
      dataIndex: 'ocr_warnings',
      key: 'ocr_warnings',
      render: (w: string[]) =>
        w && w.length > 0 ? <Tag color="orange">{w.length} 条</Tag> : <Tag color="green">无</Tag>,
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <UrgentShortageGate />
      <Title level={3}>配件采购 (拍照识别入库)</Title>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
        style={{ marginBottom: 16 }}
      />
      {viewMode === 'full' && <FullColumnView entity="part_purchase" />}
      {viewMode === 'curated' && (<>
      <Paragraph type="secondary">
        上传配件采购发票/单据照片, 系统自动 OCR 识别供应商、明细、金额、快递单号并入库。
        原图永久留存, 可在列表中点击查看。
      </Paragraph>

      <Card style={{ marginBottom: 16 }}>
        <Upload.Dragger {...uploadProps} disabled={uploadMut.isPending}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">
            {uploadMut.isPending ? '识别中 (可能需 30-120 秒)...'
              : tableMut.isPending ? '表格导入中...'
              : '点击或拖拽 发票图片 / Excel / CSV 到此处'}
          </p>
          <p className="ant-upload-hint">图片/PDF 走 OCR 识别; Excel/CSV 按列名直接导入 (供应商/购买日期/配件名称/数量/单价/金额/快递单号)。上限 15MB</p>
        </Upload.Dragger>
      </Card>

      {lastResult && (
        <Card size="small" title="最近一次识别结果" style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text>
              供应商: <b>{lastResult.supplier || '未识别'}</b> · 购买日期:{' '}
              {lastResult.purchase_date || '未识别'} · 合计:{' '}
              {lastResult.total_amount ?? '-'} · 置信度: {lastResult.confidence}%
            </Text>
            <Text>
              入库明细: {lastResult.created_purchase_ids.length} 条 · 快递单号:{' '}
              {lastResult.tracking_no || '-'}
            </Text>
            {lastResult.warnings.length > 0 && (
              <Alert
                type="warning"
                message="OCR 提示 (请核对)"
                description={
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {lastResult.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                }
              />
            )}
          </Space>
        </Card>
      )}

      <Card title="采购记录">
        <PresetTable
          tableKey="purchases"
          rowKey="id"
          loading={isLoading}
          dataSource={rows}
          columns={columns}
          size="small"
          scroll={{ x: 1100 }}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        />
      </Card>
      </>)}
    </div>
  );
}
