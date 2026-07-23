import { expect, test } from '@playwright/test';
import {
  galleryGroupNameError,
  GALLERY_BATCH_MAX_BYTES,
  GALLERY_BATCH_MAX_FILES,
  splitGalleryUploadBatches,
} from '../src/utils/galleryUpload';

const MB = 1024 * 1024;

test('图库上传同时按文件数和总字节数拆批', () => {
  const files = [
    { name: 'a.jpg', size: 35 * MB },
    { name: 'b.jpg', size: 35 * MB },
    { name: 'c.jpg', size: 20 * MB },
    ...Array.from({ length: 20 }, (_, i) => ({ name: 'tiny-' + i + '.jpg', size: 1 })),
  ];

  const batches = splitGalleryUploadBatches(files);

  expect(batches[0].map((f) => f.name)).toEqual(['a.jpg', 'b.jpg']);
  expect(batches[1][0].name).toBe('c.jpg');
  expect(batches.flat().map((f) => f.name)).toEqual(files.map((f) => f.name));
  for (const batch of batches) {
    expect(batch.length).toBeLessThanOrEqual(GALLERY_BATCH_MAX_FILES);
    expect(batch.reduce((sum, f) => sum + f.size, 0)).toBeLessThanOrEqual(GALLERY_BATCH_MAX_BYTES);
  }
});

test('单个异常大文件独占一批且不产生空批次', () => {
  const files = [
    { name: 'huge.jpg', size: 100 * MB },
    { name: 'small.jpg', size: 1 * MB },
  ];

  const batches = splitGalleryUploadBatches(files);

  expect(batches).toEqual([[files[0]], [files[1]]]);
  expect(batches.every((batch) => batch.length > 0)).toBeTruthy();
});

test('非法分批上限会明确报错', () => {
  expect(() => splitGalleryUploadBatches([{ size: 1 }], 0, 1))
    .toThrow('上传分批上限必须大于 0');
  expect(() => splitGalleryUploadBatches([{ size: 1 }], 1, 0))
    .toThrow('上传分批上限必须大于 0');
});

test('图库新文件夹名允许中文并拦截路径和空名称', () => {
  expect(galleryGroupNameError('  安装细节图  ')).toBeNull();
  expect(galleryGroupNameError('(根目录)')).toBeNull();
  expect(galleryGroupNameError('')).toBe('请先选择或输入目标文件夹名');
  expect(galleryGroupNameError('../别的商品')).toContain('不能包含');
  expect(galleryGroupNameError('a/b')).toContain('不能包含');
});
