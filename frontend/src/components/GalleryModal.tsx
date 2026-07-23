/**
 * 产品图库弹窗 (用户需求 2026-06-11):
 * 产品总表行点「图库」→ 自动匹配 D:\畔色 产品图库 下以该编码开头的文件夹,
 * 按 主图/SKU图/详情页 分组浏览。列表加载 320px WebP 缩略图 (秒开),
 * 点开预览加载 1280px 压缩版 — 外网访问带宽友好。
 * 每组分页渲染 (48/页) + 后端限并发压缩, 防大场景图夹一次性压垮弱 CPU NAS。
 */
import { useMemo, useState } from 'react';
import {
  AutoComplete, Button, Checkbox, Empty, Image, message, Modal, Select, Space, Spin, Tag, Typography, Upload,
} from 'antd';
import { ExpandOutlined, FolderOpenOutlined, UploadOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/base';
import {
  formatUploadBytes,
  galleryGroupNameError,
  GALLERY_MAX_SINGLE_BYTES,
  splitGalleryUploadBatches,
} from '../utils/galleryUpload';

const thumbUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&thumb=1`;
const previewUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&max_edge=1280`;
// 不带压缩参数 = 原图 (8MB/4000万像素), 仅「打开原图」按钮按需拉, 点图默认不碰
const originalUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}`;

// 一组先渲染这么多张, 其余点「加载更多」追加 — 防一夹 169 张一次性全请求, 压垮弱 CPU NAS。
const GROUP_PAGE_SIZE = 48;

const ROOT_GROUP = '(根目录)';
// 上传分组候选: 库内已有分组 + 常用分组, 去重
const COMMON_GROUPS = ['主图', 'SKU 图', '场景图', '详情页', ROOT_GROUP];

type GalleryImportResult = {
  added: number;
  skipped: number;
  invalid: number;
  too_large?: number;
  unsupported?: number;
  write_failed?: number;
};

type GalleryMoveResult = {
  moved: number;
  conflicts: number;
  missing: number;
  invalid: number;
  skipped_same: number;
  failed: number;
};

interface TreeGroup { group: string; images: string[] }

type GalleryGroupProps = TreeGroup & {
  organizing: boolean;
  selected: Set<string>;
  onToggle: (path: string) => void;
  onToggleAll: (paths: string[], selected: boolean) => void;
};

/**
 * 单个分组的缩略图墙, 自带分页 (用户 2026-06-25 优化):
 * 大场景图夹动辄 169 张, 一次性全渲染 = 169 个并发请求现场压缩, 把弱 CPU NAS 打爆、
 * 平板上全裂图。这里先渲染 48 张, 其余点「加载更多」按需追加; 配合后端限并发, 稳。
 */
function GalleryGroup({
  group, images, organizing, selected, onToggle, onToggleAll,
}: GalleryGroupProps) {
  const [shown, setShown] = useState(GROUP_PAGE_SIZE);
  const visible = images.slice(0, shown);
  const rest = images.length - visible.length;
  const allSelected = images.length > 0 && images.every((p) => selected.has(p));
  return (
    <div>
      <Space size={6} style={{ margin: '8px 0' }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          {group} <Tag>{images.length} 张</Tag>
        </Typography.Title>
        {organizing && (
          <Button size="small" onClick={() => onToggleAll(images, !allSelected)}>
            {allSelected ? '取消全选本组' : '全选本组'}
          </Button>
        )}
      </Space>
      <Image.PreviewGroup
        preview={organizing ? false : {
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
            <div
              key={p}
              role={organizing ? 'checkbox' : undefined}
              aria-checked={organizing ? selected.has(p) : undefined}
              onClick={organizing ? () => onToggle(p) : undefined}
              style={{
                position: 'relative', display: 'inline-flex', borderRadius: 6,
                cursor: organizing ? 'pointer' : undefined,
                outline: organizing && selected.has(p) ? '3px solid #1677ff' : undefined,
                outlineOffset: organizing && selected.has(p) ? 1 : undefined,
              }}
            >
              <Image
                width={120}
                height={120}
                style={{ objectFit: 'cover', borderRadius: 6, pointerEvents: organizing ? 'none' : undefined }}
                src={thumbUrl(p)}
                preview={organizing ? false : { src: previewUrl(p) }}
                loading="lazy"
                decoding="async"
              />
              {organizing && (
                <Checkbox
                  checked={selected.has(p)}
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => onToggle(p)}
                  style={{
                    position: 'absolute', top: 6, left: 6, padding: 4,
                    borderRadius: 4, background: 'rgba(255,255,255,.9)',
                  }}
                />
              )}
            </div>
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
  const [folderUploading, setFolderUploading] = useState(false);
  const [organizing, setOrganizing] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() => new Set());
  const [moveGroup, setMoveGroup] = useState('');
  const [moving, setMoving] = useState(false);
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

  const targetUploadGroup = () => {
    const group = uploadGroup.trim();
    const error = galleryGroupNameError(group);
    if (error) {
      message.warning(error);
      return null;
    }
    return group;
  };

  const toggleSelected = (path: string) => {
    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  const toggleGroup = (paths: string[], shouldSelect: boolean) => {
    setSelectedPaths((current) => {
      const next = new Set(current);
      paths.forEach((path) => {
        if (shouldSelect) next.add(path); else next.delete(path);
      });
      return next;
    });
  };

  const finishOrganizing = () => {
    setOrganizing(false);
    setSelectedPaths(new Set());
    setMoveGroup('');
  };

  const moveSelected = async () => {
    if (!activeFolder) {
      message.error('当前产品图库文件夹不存在');
      return;
    }
    if (!selectedPaths.size) {
      message.warning('请先勾选要整理的图片');
      return;
    }
    const targetGroup = moveGroup.trim();
    const error = galleryGroupNameError(targetGroup);
    if (error) {
      message.warning(error);
      return;
    }
    setMoving(true);
    try {
      const response = await api.post<GalleryMoveResult>('/api/gallery/move', {
        folder: activeFolder,
        paths: Array.from(selectedPaths),
        target_group: targetGroup,
      });
      const result = response.data;
      const issues = result.conflicts + result.missing + result.invalid + result.failed;
      const summary = `已移动 ${result.moved} 张到「${targetGroup}」`
        + (result.conflicts ? `，同名未覆盖 ${result.conflicts} 张` : '')
        + (result.missing ? `，原图不存在 ${result.missing} 张` : '')
        + (result.invalid ? `，非图片 ${result.invalid} 个` : '')
        + (result.failed ? `，移动失败 ${result.failed} 张` : '')
        + (result.skipped_same ? `，已在目标中 ${result.skipped_same} 张` : '');
      if (issues || result.skipped_same) message.warning(summary); else message.success(summary);
      setSelectedPaths(new Set());
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['gallery-folders', productCode] }),
        queryClient.invalidateQueries({ queryKey: ['gallery-tree', activeFolder] }),
      ]);
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      message.error(err.response?.data?.detail || err.message || '整理图片失败');
    } finally {
      setMoving(false);
    }
  };

  const doUpload = async (file: File) => {
    const targetGroup = targetUploadGroup();
    if (!targetGroup) return;
    if (file.size > GALLERY_MAX_SINGLE_BYTES) {
      message.error('「' + file.name + '」为 ' + formatUploadBytes(file.size) + '，超过单张图片 30 MB 上限');
      return;
    }
    const fd = new FormData();
    fd.append('file', file);
    // 有匹配文件夹用文件夹名; 没有则用产品编码让后端新建
    if (activeFolder) fd.append('folder', activeFolder);
    if (productCode) fd.append('product_code', productCode);
    fd.append('group', targetGroup);
    try {
      await api.post('/api/gallery/upload', fd, {
        headers: { 'Content-Type': undefined as unknown as string },
        timeout: 120000,
      });
      message.success(`已上传「${file.name}」到「${targetGroup}」`);
      // 刷新文件夹列表(图片数)与当前树
      queryClient.invalidateQueries({ queryKey: ['gallery-folders', productCode] });
      queryClient.invalidateQueries({ queryKey: ['gallery-tree', activeFolder] });
    } catch (e) {
      const err = e as {
        response?: { status?: number; data?: { detail?: string } | string };
        message?: string;
      };
      const detail = typeof err.response?.data === 'object' ? err.response.data.detail : undefined;
      const msg = err.response?.status === 413
        ? '「' + file.name + '」上传请求过大（' + formatUploadBytes(file.size) + '），服务器拒绝了请求'
        : detail || err.message || '上传失败';
      message.error(msg);
    }
  };

  // 上传整个文件夹: 一次选中相机直导的产品文件夹(几百张), 分批传后端, 同名【跳过】不覆盖不降质。
  const doFolderUpload = async (all: File[]) => {
    if (folderUploading) return;
    const targetGroup = targetUploadGroup();
    if (!targetGroup) return;
    const candidates = all.filter((f) => /\.(jpe?g|png|webp|gif|bmp)$/i.test(f.name));
    if (!candidates.length) {
      message.warning('该文件夹里没有支持的图片（jpg/png/webp/gif/bmp）');
      return;
    }
    const tooLargeFiles = candidates.filter((f) => f.size > GALLERY_MAX_SINGLE_BYTES);
    const imgs = candidates.filter((f) => f.size <= GALLERY_MAX_SINGLE_BYTES);
    if (!imgs.length) {
      message.error('没有可上传的图片：' + tooLargeFiles.length + ' 张均超过单张 30 MB 上限');
      return;
    }
    // 张数只能控制后端处理量，字节上限才真正防 413。80 MB 为线上 250 MB 代理保留充足余量。
    const batches = splitGalleryUploadBatches(imgs);
    const key = 'folder-import';
    let added = 0; let skipped = 0; let invalid = 0;
    let serverTooLarge = 0; let unsupported = 0; let writeFailed = 0;
    let processed = 0; let batchIndex = 0; let batchBytes = 0;
    setFolderUploading(true);
    message.open({
      key,
      type: 'loading',
      content: '导入中 0/' + imgs.length + '（共 ' + batches.length + ' 批）…',
      duration: 0,
    });
    try {
      for (batchIndex = 0; batchIndex < batches.length; batchIndex += 1) {
        const batch = batches[batchIndex];
        batchBytes = batch.reduce((sum, f) => sum + f.size, 0);
        const fd = new FormData();
        // 有匹配文件夹就精确投它; 否则给产品编码让后端定位/新建「编码 产品名」
        if (activeFolder) fd.append('folder', activeFolder);
        if (productCode) fd.append('product_code', productCode);
        // 与“上传新图”共用目标；输入新名称时后端会自动创建该分组文件夹。
        fd.append('group', targetGroup);
        batch.forEach((f) => fd.append('files', f));
        const r = await api.post<GalleryImportResult>(
          '/api/gallery/import-folder', fd,
          { headers: { 'Content-Type': undefined as unknown as string }, timeout: 300000 },
        );
        added += r.data.added; skipped += r.data.skipped; invalid += r.data.invalid;
        serverTooLarge += r.data.too_large ?? 0;
        unsupported += r.data.unsupported ?? 0;
        writeFailed += r.data.write_failed ?? 0;
        processed += batch.length;
        message.open({
          key, type: 'loading', duration: 0,
          content: '导入中 ' + processed + '/' + imgs.length + '（第 ' + (batchIndex + 1) + '/' + batches.length + ' 批，' + formatUploadBytes(batchBytes) + '）…',
        });
      }
      const rejected = tooLargeFiles.length + invalid;
      const details = [
        tooLargeFiles.length ? '本机拦截超 30 MB ' + tooLargeFiles.length : '',
        serverTooLarge ? '服务端判定超 30 MB ' + serverTooLarge : '',
        unsupported ? '格式不支持 ' + unsupported : '',
        writeFailed ? '写入失败 ' + writeFailed : '',
      ].filter(Boolean).join('，');
      message.open({
        key, type: rejected ? 'warning' : 'success', duration: 8,
        content: '已导入到「' + targetGroup + '」：新增 ' + added + '，跳过（已存在）' + skipped
          + (rejected ? '，未导入 ' + rejected + '（' + (details || '无效图片') + '）' : ''),
      });
      queryClient.invalidateQueries({ queryKey: ['gallery-folders', productCode] });
      queryClient.invalidateQueries({ queryKey: ['gallery-tree', activeFolder] });
    } catch (e) {
      const err = e as {
        response?: { status?: number; data?: { detail?: string } | string };
        message?: string;
      };
      const detail = typeof err.response?.data === 'object' ? err.response.data.detail : undefined;
      const reason = err.response?.status === 413
        ? '第 ' + (batchIndex + 1) + '/' + batches.length + ' 批仍超过服务器请求上限（该批 ' + formatUploadBytes(batchBytes) + '）'
        : detail || err.message || '未知错误';
      message.open({
        key,
        type: 'error',
        duration: 10,
        content: '导入中断：' + reason + '。已处理 ' + processed + '/' + imgs.length
          + ' 张（新增 ' + added + '，跳过 ' + skipped + '）；重新选择同一文件夹可安全续传',
      });
    } finally {
      setFolderUploading(false);
    }
  };

  const uploadBar = (
    <Space wrap>
      <Typography.Text type="secondary">上传到文件夹:</Typography.Text>
      <AutoComplete
        size="small"
        style={{ minWidth: 190 }}
        value={uploadGroup}
        onChange={setUploadGroup}
        options={groupOptions}
        placeholder="选择或输入新文件夹名"
        filterOption={(input, option) => String(option?.value ?? '')
          .toLowerCase().includes(input.toLowerCase())}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        输入新名称会在首次上传时自动创建
      </Typography.Text>
      <Upload
        multiple
        showUploadList={false}
        accept="image/*"
        beforeUpload={(file) => { void doUpload(file as File); return false; }}
      >
        <Button size="small" type="primary" icon={<UploadOutlined />}>上传新图</Button>
      </Upload>
      <Upload
        directory
        disabled={folderUploading}
        showUploadList={false}
        beforeUpload={(file, fileList) => {
          // directory 模式 beforeUpload 每文件触发一次; 只在首个文件时整批上传
          if (file === fileList[0]) void doFolderUpload(fileList as unknown as File[]);
          return false;
        }}
      >
        <Button size="small" loading={folderUploading} icon={<FolderOpenOutlined />}>上传整个文件夹</Button>
      </Upload>
    </Space>
  );

  const organizeBar = organizing ? (
    <Space wrap>
      <Tag color="blue">已选 {selectedPaths.size} 张</Tag>
      <Typography.Text type="secondary">移动到:</Typography.Text>
      <AutoComplete
        size="small"
        style={{ minWidth: 190 }}
        value={moveGroup}
        onChange={setMoveGroup}
        options={groupOptions}
        placeholder="选择或输入目标文件夹"
        filterOption={(input, option) => String(option?.value ?? '')
          .toLowerCase().includes(input.toLowerCase())}
      />
      <Button
        size="small"
        type="primary"
        loading={moving}
        disabled={!selectedPaths.size}
        onClick={() => { void moveSelected(); }}
      >
        移动到此文件夹
      </Button>
      <Button size="small" disabled={moving} onClick={finishOrganizing}>完成整理</Button>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>同名图片不会覆盖</Typography.Text>
    </Space>
  ) : (
    <Button size="small" onClick={() => setOrganizing(true)}>整理已上传图片</Button>
  );

  return (
    <Modal
      open={!!productCode}
      onCancel={() => { setFolder(undefined); finishOrganizing(); onClose(); }}
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
          {organizeBar}
          {loadingTree && <Spin />}
          {(tree ?? []).map((g) => (
            <GalleryGroup
              key={g.group}
              group={g.group}
              images={g.images}
              organizing={organizing}
              selected={selectedPaths}
              onToggle={toggleSelected}
              onToggleAll={toggleGroup}
            />
          ))}
        </Space>
      )}
    </Modal>
  );
}
