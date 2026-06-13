/**
 * 产品缩略图 + 缺图占位 —— 统一的「吃豆人幽灵」像素占位。
 * 用户拍板 (2026-06-12): 幽灵替代史莱姆, 放右下角、透明底、无文字、有色版(冰蓝)。
 * 缺图(null)或图片加载失败(404/商品页链接)都显示同一个占位, 不再出现难看的"裂图"。
 * 所有需要展示产品图的地方都用 <ProductThumb src=...> 或 import { CUTE_IMG }。
 */
import { Image } from 'antd';

// Pac-Man 幽灵 8×8 像素网格 (像素占位图预览.html 选项 5)
const GHOST_GRID = [
  '..gggg..',
  '.gggggg.',
  'gggggggg',
  'gWWggWWg',
  'gWPggWPg',
  'gggggggg',
  'gggggggg',
  'g.gg.gg.',
];
// 中间蓝版 (用户 2026-06-12 二次微调): 身=AntD blue-5 #4096ff (比主蓝 #1677ff 浅一档、比浅蓝深, 居中) / 白眼 / 深蓝瞳 — 全站占位统一这一处
const GHOST_PALETTE: Record<string, string> = { g: '#4096ff', W: '#ffffff', P: '#003a8c' };

function ghostPlaceholder(): string {
  // 格宽 7.5px = 半尺寸(5px) 的 1.5 倍 (用户 2026-06-12); 8×8 格 = 60×60, 贴右下角留 8px 边距
  const canvas = 120, cell = 7.5, margin = 8;
  const cols = GHOST_GRID[0].length, rows = GHOST_GRID.length;
  const ox = canvas - margin - cell * cols;
  const oy = canvas - margin - cell * rows;
  let rects = '';
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < GHOST_GRID[y].length; x++) {
      const f = GHOST_PALETTE[GHOST_GRID[y][x]];
      if (f) rects += `<rect x='${ox + x * cell}' y='${oy + y * cell}' width='${cell}' height='${cell}' fill='${f}'/>`;
    }
  }
  // 透明底, 无文字 (用户 2026-06-12: 去掉"暂无图片")
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120' shape-rendering='crispEdges'>" +
    rects + '</svg>',
  );
}

// 缺图/裂图统一占位 (冰蓝幽灵 + 透明底)
export const CUTE_IMG = ghostPlaceholder();

export default function ProductThumb({
  src, size = 40, preview = true,
}: { src?: string | null; size?: number; preview?: boolean }) {
  if (!src) {
    return <img src={CUTE_IMG} width={size} height={size} alt="暂无图片" style={{ display: 'block' }} />;
  }
  return (
    <Image
      src={src}
      width={size}
      height={size}
      style={{ objectFit: 'cover', borderRadius: 4 }}
      fallback={CUTE_IMG}
      preview={preview}
    />
  );
}
