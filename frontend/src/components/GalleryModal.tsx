/**
 * 产品图库弹窗 (用户需求 2026-06-11):
 * 产品总表行点「图库」→ 自动匹配 D:\畔色 产品图库 下以该编码开头的文件夹,
 * 按 主图/SKU图/详情页 分组浏览。列表加载 320px WebP 缩略图 (秒开),
 * 点开预览加载 1280px 压缩版 — 外网访问带宽友好。
 * 每组分页渲染 (48/页) + 后端限并发压缩, 防大场景图夹一次性压垮弱 CPU NAS。
 */
import { useMemo, useState } from 'react';
import {
  Button, Empty, Image, message, Modal, Select, Space, Spin, Tag, Typography, Upload,
} from 'antd';
import { ExpandOutlined, UploadOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/base';

const thumbUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&thumb=1`;
const previewUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&max_edge=1280`;
// 不带压缩参数 = 原图 (8MB/4000万像素), 仅「打开原图」按钮按需拉, 点图默认不碰
const originalUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}`;

// 一组先渲染这么多张, 其余点「加载更多」追加 — 防一夹 169 张一次性全请求, 压垮弱 CPU NAS。
const GROUP_PAGE_SIZE = 48;

const ROOT_GROUP = '(根目录)';
// 上传分组候选: 库内已有分组 + 常用分组, 去重
const COMMON_GROUPS = ['主图', 'SKU 图', '场景图', '详情页', ROOT_GROUP];

interface TreeGroup { group: string; images: string[] }

/**
 * 单个分组的缩略图墙, 自带分页 (用户 2026-06-25 优化):
 * 大场景图夹动辄 169 张, 一次性全渲染 = 169 个并发请求现场压缩, 把弱 CPU NAS 打爆、
 * 平板上全裂图。这里先渲染 48 张, 其余点「加载更多」按需追加; 配合后端限并发, 稳。
 */
function GalleryGroup({ group, images }: TreeGroup) {
  const [shown, setShown] = useState(GROUP_PAGE_SIZE);
  const visible = images.slice(0, shown);
  const rest = images.length - visible.length;
  return (
    <div>
      <Typography.Title level={5} style={{ margin: '8px 0' }}>
        {group} <Tag>{images.length} 张</Tag>
      </Typography.Title>
      <Image.PreviewGroup
        preview={{
          // 预览工具条加「打开原图」: 点图看的是 1280 清晰预览(秒开), 要抠细节再点这个拉原图 (用户 2026-06-25)。
          // 2026-06-26: 工具条加深色药丸底+白图标(原默认太暗看不清), 不换行横滑(手机端原会挤到下一排);
          //   「打开原图」改实心高亮(原 ghost 透明蓝字在暗条上看不清)。样式见 global.css / mobile.css。
          toolbarRender: (originalNode, { current }) => (
            <div className="gallery-preview-toolbar">
              {originalNode}
              <Button
                type="primary"
                icon={<ExpandOutlined />}
                className="gallery-open-original-btn"
                onClick={() => {
                  const p = visible[current];
                  if (p) window.open(originalUrl(p), '_blank', 'noopener,noreferrer');
                }}
              >
                打开原图
              </Button>
            </div>
          ),
        }}
      >
        <Space wrap size={8}>
          {visible.map((p) => (
            <Image
              key={p}
              width={120}
              height={120}
              style={{ objectFit: 'cover', borderRadius: 6 }}
              src={thumbUrl(p)}
              preview={{ src: previewUrl(p) }}
              loading="lazy"
              decoding="async"
            />
          ))}
        </Space>
      </Image.PreviewGroup>
      {rest > 0 && (
        <div style={{ marginTop: 8 }}>
          <Button size="small" onClick={() => setShown((s) => s + GROUP_PAGE_SIZE)}>
            加载更多（剩余 {rest} 张）
          </Button>
        </div>
      )}
    </div>
  );
}

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
            <GalleryGroup key={g.group} group={g.group} images={g.images} />
          ))}
        </Space>
      )}
    </Modal>
  );
}
