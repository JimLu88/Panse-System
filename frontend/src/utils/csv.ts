// 表格行导出成 CSV — Excel 直接打开不乱码 (UTF-8 BOM)。
// 纯函数 buildCsv 便于单测; downloadCsv 负责浏览器下载。

/** 单元格转义: 含逗号/引号/换行时用引号包裹, 内部引号翻倍 (RFC 4180)。 */
function escapeCell(v: unknown): string {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 构造 CSV 文本 (不含 BOM)。 */
export function buildCsv(headers: string[], rows: unknown[][]): string {
  const head = headers.map(escapeCell).join(',');
  const body = rows.map((r) => r.map(escapeCell).join(',')).join('\r\n');
  return body ? `${head}\r\n${body}` : head;
}

/** 触发浏览器下载一个 CSV 文件 (UTF-8 BOM, 防中文乱码)。 */
export function downloadCsv(filename: string, headers: string[], rows: unknown[][]): void {
  const blob = new Blob([`﻿${buildCsv(headers, rows)}`], {
    type: 'text/csv;charset=utf-8;',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
