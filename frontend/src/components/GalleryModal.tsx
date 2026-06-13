/**
 * 产品图库弹窗 (用户需求 2026-06-11):
 * 产品总表行点「图库」→ 自动匹配 D:\畔色 产品图库 下以该编码开头的文件夹,
 * 按 主图/SKU图/详情页 分组浏览。列表加载 480px WebP 缩略图 (秒开),
 * 点开预览加载 1600px 压缩版 — 外网访问带宽友好。
 */
import { useState } from 'react';
import { Empty, Image, Modal, Select, Space, Spin, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/base';

const thumbUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&thumb=1`;
const previewUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&max_edge=1600`;

interface TreeGroup { group: string; images: string[] }

export default function GalleryModal({ productCode, onClose }: {
  productCode: string | null; onClose: () => void;
}) {
  const [folder, setFolder] = useState<string | undefined>(undefined);

  const { data: folders, isLoading: loadingFolders } = useQuery({
    queryKey: ['gallery-folders', productCode],
    queryFn: () => api.get<{ folders: string[] }>(
      `/api/gallery/by-product/${encodeURIComponent(productCode!)}`,
    ).then((r) => r.data.folders),
    enabled: !!productCode,
  });

  const activeFolder = folder ?? folders?.[0];
  const { data: tree, isLoading: loadingTree } = useQuery({
    queryKey: ['gallery-tree', activeFolder],
    queryFn: () => api.get<{ groups: TreeGroup[] }>(
      '/api/gallery/tree', { params: { folder: activeFolder } },
    ).then((r) => r.data.groups),
    enabled: !!activeFolder,
  });

  return (
    <Modal
      open={!!productCode}
      onCancel={() => { setFolder(undefined); onClose(); }}
      footer={null}
      width={980}
      title={`产品图库 — ${productCode ?? ''}`}
    >
      {loadingFolders && <Spin />}
      {folders && folders.length === 0 && (
        <Empty description={`图库里没有以 ${productCode} 开头的文件夹 (按「产品名称+编码」命名即可自动匹配)`} />
      )}
      {folders && folders.length > 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          {folders.length > 1 && (
            <Select
              style={{ minWidth: 360 }}
              value={activeFolder}
              onChange={setFolder}
              options={folders.map((f) => ({ value: f, label: f }))}
            />
          )}
          {loadingTree && <Spin />}
          {(tree ?? []).map((g) => (
            <div key={g.group}>
              <Typography.Title level={5} style={{ margin: '8px 0' }}>
                {g.group} <Tag>{g.images.length} 张</Tag>
              </Typography.Title>
              <Image.PreviewGroup>
                <Space wrap size={8}>
                  {g.images.map((p) => (
                    <Image
                      key={p}
                      width={120}
                      height={120}
                      style={{ objectFit: 'cover', borderRadius: 6 }}
                      src={thumbUrl(p)}
                      preview={{ src: previewUrl(p) }}
                      loading="lazy"
                    />
                  ))}
                </Space>
              </Image.PreviewGroup>
            </div>
          ))}
        </Space>
      )}
    </Modal>
  );
}
