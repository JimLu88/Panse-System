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
import { InboxOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UploadProps } from 'antd';
import {
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

  const uploadProps: UploadProps = {
    multiple: false,
    showUploadList: false,
    accept: 'image/*,.pdf',
    beforeUpload: (file) => {
      uploadMut.mutate(file as File);
      return false; // 阻止 antd 自动上传, 走我们的 mutation
    },
  };

  const columns = [
    { title: '采购单号', dataIndex: 'purchase_no', key: 'purchase_no', width: 150 },
    { title: '供应商', dataIndex: 'supplier', key: 'supplier' },
    { title: '购买日期', dataIndex: 'purchase_date', key: 'purchase_date', width: 110 },
    { title: '配件名称', dataIndex: 'material_name', key: 'material_name' },
    { title: '规格', dataIndex: 'spec', key: 'spec' },
    { title: '数量', dataIndex: 'qty', key: 'qty', width: 80 },
    {
      title: '单价',
      dataIndex: 'unit_price',
      key: 'unit_price',
      width: 90,
      render: (v: number | null) => (v == null ? '-' : v),
    },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 90,
      render: (v: number | null) => (v == null ? '-' : v),
    },
    { title: '快递单号', dataIndex: 'tracking_no', key: 'tracking_no' },
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
            {uploadMut.isPending ? '识别中 (可能需 30-120 秒)...' : '点击或拖拽发票图片到此处'}
          </p>
          <p className="ant-upload-hint">支持 jpg/png/pdf, 上限 15MB</p>
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
        <Table
          rowKey="id"
          loading={isLoading}
          dataSource={rows}
          columns={columns}
          size="small"
          scroll={{ x: 1100 }}
          pagination={{ pageSize: 20 }}
        />
      </Card>
      </>)}
    </div>
  );
}
