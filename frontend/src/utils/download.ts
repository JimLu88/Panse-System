// 浏览器端 blob 下载小工具（xlsx 等二进制），从 ActivityAutoFillTab 抽出共用。
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export function triggerBlobDownload(data: BlobPart, filename: string, mime: string = XLSX_MIME): void {
  const url = URL.createObjectURL(new Blob([data], { type: mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
