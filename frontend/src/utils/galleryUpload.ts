export const GALLERY_MAX_SINGLE_BYTES = 30 * 1024 * 1024;
export const GALLERY_BATCH_MAX_BYTES = 80 * 1024 * 1024;
export const GALLERY_BATCH_MAX_FILES = 20;

type SizedFile = { size: number };

// Keep each multipart request below both a file-count and a byte ceiling.
// Camera originals vary widely, so count-only batching is not safe: the
// 2026-07-21 failure put 20 files / 350,452,963 bytes in one request and hit
// nginx's 250 MB limit.
export function splitGalleryUploadBatches<T extends SizedFile>(
  files: readonly T[],
  maxFiles = GALLERY_BATCH_MAX_FILES,
  maxBytes = GALLERY_BATCH_MAX_BYTES,
): T[][] {
  if (maxFiles <= 0 || maxBytes <= 0) {
    throw new Error('上传分批上限必须大于 0');
  }

  const batches: T[][] = [];
  let batch: T[] = [];
  let batchBytes = 0;

  for (const file of files) {
    const wouldOverflow = batch.length > 0
      && (batch.length >= maxFiles || batchBytes + file.size > maxBytes);
    if (wouldOverflow) {
      batches.push(batch);
      batch = [];
      batchBytes = 0;
    }
    batch.push(file);
    batchBytes += file.size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

export function formatUploadBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

export function galleryGroupNameError(rawName: string): string | null {
  const name = rawName.trim();
  if (!name) return '请先选择或输入目标文件夹名';
  if (name === '.' || name === '..') return '文件夹名不能是 . 或 ..';
  if (/[\\/:*?"<>|\x00-\x1f]/.test(name)) {
    return '文件夹名不能包含 \\ / : * ? " < > |';
  }
  if (name.startsWith('.')) return '文件夹名不能以 . 开头';
  if (name.length > 60) return '文件夹名最多 60 个字符';
  return null;
}
