import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Empty,
  Image,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { UploadFile } from 'antd';
import { CameraOutlined, SearchOutlined, UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';

import type { FactoryCard } from '../api/client';
import {
  fetchInspectionGallery,
  inspectionImageUrl,
  uploadInspectionImages,
} from '../api/orders';

interface Props {
  open: boolean;
  onClose: () => void;
  orders: FactoryCard[];
}

export default function InspectionGalleryModal({ open, onClose, orders }: Props) {
  const [range, setRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null);
  const [productDraft, setProductDraft] = useState('');
  const [product, setProduct] = useState('');
  const [orderId, setOrderId] = useState<number | null>(null);
  const [capturedOn, setCapturedOn] = useState<dayjs.Dayjs>(dayjs());
  const [files, setFiles] = useState<UploadFile[]>([]);

  const params = useMemo(() => ({
    date_from: range?.[0]?.format('YYYY-MM-DD'),
    date_to: range?.[1]?.format('YYYY-MM-DD'),
    product: product || undefined,
  }), [range, product]);

  const gallery = useQuery({
    queryKey: ['inspection-gallery', params],
    queryFn: () => fetchInspectionGallery(params),
    enabled: open,
  });

  const uploadMut = useMutation({
    mutationFn: async () => {
      if (!orderId) throw new Error('请先选择对应订单');
      const originals = files.map((f) => f.originFileObj).filter(Boolean) as File[];
      if (!originals.length) throw new Error('请选择图片');
      return uploadInspectionImages(orderId, originals, capturedOn.format('YYYY-MM-DD'));
    },
    onSuccess: async (r) => {
      message.success(`已归档 ${r.uploaded} 张验货图`);
      setFiles([]);
      await gallery.refetch();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? e?.message ?? '上传失败'),
  });

  const orderOptions = useMemo(() => orders.map((order) => ({
    value: order.id,
    label: `${order.order_label || '待编号'}｜${order.product_name || '未命名产品'}｜${order.order_no}`,
  })), [orders]);

  return (
    <Modal
      title={<Space><CameraOutlined />工厂检查图库</Space>}
      open={open}
      onCancel={onClose}
      width="min(1180px, 96vw)"
      footer={<Button onClick={onClose}>关闭</Button>}
      destroyOnClose={false}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Alert
          type="info"
          showIcon
          message="图片按订单归档，可按日期和产品筛选"
          description="网页可直接上传；飞书中把图片与“验货 畔色321单”或完整平台订单号放在同一条消息里，也会自动进入这里。"
        />

        <Card size="small" title="上传验货图">
          <Space wrap align="start">
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择对应的工厂订单"
              style={{ width: 430 }}
              value={orderId}
              onChange={setOrderId}
              options={orderOptions}
            />
            <DatePicker value={capturedOn} onChange={(v) => v && setCapturedOn(v)} />
            <Upload
              accept="image/*"
              multiple
              fileList={files}
              beforeUpload={() => false}
              onChange={({ fileList }) => setFiles(fileList)}
              onRemove={(file) => {
                setFiles((current) => current.filter((item) => item.uid !== file.uid));
                return true;
              }}
            >
              <Button icon={<UploadOutlined />}>选择图片</Button>
            </Upload>
            <Button
              type="primary"
              icon={<UploadOutlined />}
              loading={uploadMut.isPending}
              disabled={!orderId || !files.length}
              onClick={() => uploadMut.mutate()}
            >
              上传归档
            </Button>
          </Space>
        </Card>

        <Space wrap>
          <DatePicker.RangePicker
            value={range}
            onChange={(v) => setRange(v as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null)}
            placeholder={['验货日期起', '验货日期止']}
          />
          <Input
            allowClear
            value={productDraft}
            onChange={(e) => setProductDraft(e.target.value)}
            onPressEnter={() => setProduct(productDraft.trim())}
            placeholder="产品名称 / 编码 / SKU"
            style={{ width: 260 }}
          />
          <Button icon={<SearchOutlined />} onClick={() => setProduct(productDraft.trim())}>筛选</Button>
          <Typography.Text type="secondary">共 {gallery.data?.length ?? 0} 张</Typography.Text>
        </Space>

        {gallery.isLoading ? (
          <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
        ) : !gallery.data?.length ? (
          <Empty description="暂无符合条件的验货图" />
        ) : (
          <Image.PreviewGroup>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))',
              gap: 12,
            }}>
              {gallery.data.map((item) => (
                <Card
                  key={item.id}
                  size="small"
                  cover={(
                    <Image
                      src={inspectionImageUrl(item.id)}
                      alt={`${item.factory_label || item.order_no || ''} 验货图`}
                      height={150}
                      style={{ objectFit: 'cover' }}
                    />
                  )}
                >
                  <Space direction="vertical" size={2} style={{ width: '100%' }}>
                    <Space size={4} wrap>
                      <Tag color="blue">{item.factory_label || item.order_no || '未编号'}</Tag>
                      <Tag>{item.captured_on || '未知日期'}</Tag>
                    </Space>
                    <Typography.Text strong ellipsis={{ tooltip: item.product_name || '' }}>
                      {item.product_name || item.product_code || '未命名产品'}
                    </Typography.Text>
                    <Typography.Text type="secondary" ellipsis={{ tooltip: item.sku || '' }}>
                      {item.sku || item.sku_code || '无 SKU'}
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {item.uploaded_by || item.source || '系统归档'}
                    </Typography.Text>
                  </Space>
                </Card>
              ))}
            </div>
          </Image.PreviewGroup>
        )}
      </Space>
    </Modal>
  );
}
