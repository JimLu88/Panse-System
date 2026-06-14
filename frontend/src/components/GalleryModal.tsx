/**
 * 产品图库弹窗 (用户需求 2026-06-11):
 * 产品总表行点「图库」→ 自动匹配 D:\畔色 产品图库 下以该编码开头的文件夹,
 * 按 主图/SKU图/详情页 分组浏览。列表加载 480px WebP 缩略图 (秒开),
 * 点开预览加载 1600px 压缩版 — 外网访问带宽友好。
 */
import { useMemo, useState } from 'react';
import {
  Button, Empty, Image, message, Modal, Select, Space, Spin, Tag, Typography, Upload,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/base';

const thumbUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&thumb=1`;
const previewUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&max_edge=1600`;

const ROOT_GROUP = '(根目录)';
// 上传分组候选: 库内已有分组 + 常用分组, 去重
const COMMON_GROUPS = ['主图', 'SKU 图', '场景图', '详情页', ROOT_GROUP];

interface TreeGroup { group: string; images: string[] }

export default function GalleryModal({ productCode, onClose }: {
  productCode: string | null; onClose: () => void;
}) {
  const [folder, setFolder] = useState<string | undefined>(undefined);
  const [uploadGroup, setUploadGroup] = useState<string>('主图');
  const queryClient = useQueryClient();

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

  // 分组下拉选项: 已有分组优先, 再补常用分组
  const groupOptions = useMemo(() => {
    const seen = new Set<string>();
    const opts: { value: string; label: string }[] = [];
    for (const g of (tree ?? []).map((t) => t.group)) {
      if (!seen.has(g)) { seen.add(g); opts.push({ value: g, label: g }); }
    }
    for (const g of COMMON_GROUPS) {
      if (!seen.has(g)) { seen.add(g); opts.push({ value: g, label: g }); }
    }
    return opts;
  }, [tree]);

  const doUpload = async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    // 有匹配文件夹用文件夹名; 没有则用产品编码让后端新建
    if (activeFolder) fd.append('folder', activeFolder);
    if (productCode) fd.append('product_code', productCode);
    fd.append('group', uploadGroup);
    try {
      await api.post('/api/gallery/upload', fd, {
        headers: { 'Content-Type': undefined as unknown as string },
        timeout: 120000,
      });
      message.success(`已上传「${file.name}」到「${uploadGroup}」`);
      // 刷新文件夹列表(图片数)与当前树
      queryClient.invalidateQueries({ queryKey: ['gallery-folders', productCode] });
      queryClient.invalidateQueries({ queryKey: ['gallery-tree', activeFolder] });
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } }; message?: string })
        ?.response?.data?.detail
        || (e as { message?: string })?.message || '上传失败';
      message.error(msg);
    }
  };

  const uploadBar = (
    <Space wrap>
      <Typography.Text type="secondary">上传到:</Typography.Text>
      <Select
        size="small"
        style={{ minWidth: 120 }}
        value={uploadGroup}
        onChange={setUploadGroup}
        options={groupOptions}
      />
      <Upload
        multiple
        showUploadList={false}
        accept="image/*"
        beforeUpload={(file) => { void doUpload(file as File); return false; }}
      >
        <Button size="small" type="primary" icon={<UploadOutlined />}>上传新图</Button>
      </Upload>
    </Space>
  );

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
        <Space direction="vertical" style={{ width: '100%' }}>
          <Empty description={`图库里没有以 ${productCode} 开头的文件夹 — 上传第一张图会自动建文件夹`} />
          {uploadBar}
        </Space>
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
          {uploadBar}
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
