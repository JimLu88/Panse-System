import { useEffect, useState } from 'react';
import { Button, Image, Modal, Space, Spin, Typography, message } from 'antd';
import {
  ProductDimensionDetail,
  api,
  getProductDimension,
  listProductDimensions,
} from '../api/client';

type ViewMode = 'image' | 'text';

export default function ProductDimensionFinalActions({
  productCode,
  assetCount,
}: {
  productCode: string;
  assetCount?: number;
}) {
  const [mode, setMode] = useState<ViewMode | null>(null);
  const [loading, setLoading] = useState(false);
  const [details, setDetails] = useState<ProductDimensionDetail[]>([]);
  const [imageUrls, setImageUrls] = useState<string[]>([]);

  useEffect(() => () => imageUrls.forEach(URL.revokeObjectURL), [imageUrls]);

  if (!assetCount) return null;

  const close = () => {
    setMode(null);
    setDetails([]);
    setImageUrls([]);
  };

  const open = async (nextMode: ViewMode) => {
    setMode(nextMode);
    setLoading(true);
    setDetails([]);
    setImageUrls([]);
    try {
      const listing = await listProductDimensions(productCode);
      const loaded = await Promise.all(
        listing.assets.map((asset) => getProductDimension(productCode, asset.id)),
      );
      setDetails(loaded);
      if (nextMode === 'image') {
        const blobs = await Promise.all(loaded.map(async (detail) => {
          if (!detail.preview_url) return '';
          const response = await api.get<Blob>(detail.preview_url, { responseType: 'blob' });
          return URL.createObjectURL(response.data);
        }));
        setImageUrls(blobs);
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '最终尺寸文件读取失败');
      setMode(null);
    } finally {
      setLoading(false);
    }
  };

  const finalText = (detail: ProductDimensionDetail) => {
    const text = detail.dimension_data?.final_text;
    if (typeof text === 'string' && text.trim()) return text;
    if (detail.size_detail?.trim()) return detail.size_detail;
    return detail.erp_dimensions
      .map((item) => `${item.label || '尺寸'}：${item.value || ''}`)
      .join('\n') || '该产品还没有文字说明。';
  };

  return (
    <>
      <Space size={4} wrap>
        <Button size="small" onClick={() => open('image')}>尺寸图</Button>
        <Button size="small" onClick={() => open('text')}>文字说明</Button>
      </Space>
      <Modal
        open={mode !== null}
        title={`${productCode} · ${mode === 'image' ? '最终尺寸图' : '尺寸文字说明'}`}
        footer={null}
        width={mode === 'image' ? 1000 : 760}
        onCancel={close}
        destroyOnClose
      >
        {loading ? <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div> : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {details.map((detail, index) => (
              <section key={detail.id}>
                {details.length > 1 && (
                  <Typography.Title level={5}>{detail.title}</Typography.Title>
                )}
                {mode === 'image' ? (
                  imageUrls[index]
                    ? <Image src={imageUrls[index]} width="100%" preview />
                    : <Typography.Text type="secondary">暂无最终尺寸图</Typography.Text>
                ) : (
                  <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', lineHeight: 1.75, margin: 0 }}>
                    {finalText(detail)}
                  </Typography.Paragraph>
                )}
              </section>
            ))}
          </Space>
        )}
      </Modal>
    </>
  );
}
