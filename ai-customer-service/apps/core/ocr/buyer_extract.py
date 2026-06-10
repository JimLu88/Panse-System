"""
从 OCR spans 中启发式提取买家气泡文本（基于纵向位置：偏下的气泡视为更近）。

extract_buyer_message_block：新函数，将同一气泡的多行 span 合并后返回完整文本。
extract_latest_buyer_line：保留用于兼容旧调用，内部委托到新函数。
"""

from __future__ import annotations

import re

from apps.core.ocr.chat_time_align import is_chat_timestamp_text
from apps.core.ocr.models import OCRSpan

_LIST_UI_SUFFIX = re.compile(r"\s*未读\s*$")
_READ_RECEIPT_SUFFIX = re.compile(r"\s*已读\s*$")


def _usable_text(raw: str, *, exclude_timestamps: bool = True) -> str | None:
    t = (raw or "").strip()
    if not t:
        return None
    t = _LIST_UI_SUFFIX.sub("", t).strip()
    t = _READ_RECEIPT_SUFFIX.sub("", t).strip()
    if not t:
        return None
    if exclude_timestamps and is_chat_timestamp_text(t):
        return None
    return t


def _group_spans_into_bubbles(
    spans: list[OCRSpan],
    *,
    gap_px: float = 25.0,
) -> list[list[OCRSpan]]:
    """将 Y 轴相近的 spans 归入同一气泡组。gap_px 为气泡间最小间距阈值。"""
    if not spans:
        return []
    sorted_spans = sorted(spans, key=lambda s: (s.bbox[1] + s.bbox[3]) / 2.0)
    groups: list[list[OCRSpan]] = []
    current: list[OCRSpan] = [sorted_spans[0]]
    for s in sorted_spans[1:]:
        prev_y2 = max(sp.bbox[3] for sp in current)
        curr_y1 = s.bbox[1]
        if curr_y1 - prev_y2 <= gap_px:
            current.append(s)
        else:
            groups.append(current)
            current = [s]
    groups.append(current)
    return groups


def extract_buyer_message_block(
    spans: list[OCRSpan],
    *,
    roi_height: int,
    roi_width: int | None = None,
    buyer_x_ratio: float = 0.50,
    exclude_timestamps: bool = True,
    bottom_cutoff_ratio: float = 0.58,
    bottom_exclude_ratio: float = 0.95,
    bubble_gap_px: float = 25.0,
) -> str:
    """
    返回最新买家气泡的完整文本（合并同一气泡内的多行 span）。

    改进：
    - 将 Y 邻近的 spans 合并为整条气泡，而非只取最后一行
    - buyer_x_ratio 可通过 yaml 配置，不再硬编码 0.50
    - bottom_exclude_ratio：排除底部工具栏区域（默认 0.95，即最底部 5% 为工具栏）
      千牛聊天窗口底部有图标工具栏，OCR 会将图标误读为"田""?"等字符
    """
    if not spans:
        return ""

    buyer_x_max = float(roi_width) * buyer_x_ratio if roi_width and roi_width > 80 else None
    toolbar_y_min = roi_height * bottom_exclude_ratio

    eligible: list[OCRSpan] = []
    for s in spans:
        t = _usable_text(s.text or "", exclude_timestamps=exclude_timestamps)
        if not t:
            continue
        x1, y1, x2, y2 = s.bbox
        cx = (x1 + x2) / 2.0
        if buyer_x_max is not None and cx > buyer_x_max:
            continue
        # 排除底部工具栏区域的误识别（图标被 OCR 识别为"田""?""R"等）
        if y1 > toolbar_y_min:
            continue
        eligible.append(s)

    if not eligible:
        return ""

    # 先在 span 级别做底部切割（而不是分组后按组过滤），
    # 防止千牛连续消息因间距小而合并成一个超大组导致全部被选中
    cutoff = roi_height * bottom_cutoff_ratio
    bottom_eligible = [
        s for s in eligible
        if (s.bbox[1] + s.bbox[3]) / 2.0 >= cutoff
    ]
    if not bottom_eligible:
        bottom_eligible = eligible

    groups = _group_spans_into_bubbles(bottom_eligible, gap_px=bubble_gap_px)

    # 取最靠下的气泡组（最大中心 Y）
    best_group = max(
        groups,
        key=lambda g: max((s.bbox[1] + s.bbox[3]) / 2.0 for s in g),
    )

    sorted_group = sorted(best_group, key=lambda s: (s.bbox[1], s.bbox[0]))
    texts = [
        t for s in sorted_group
        if (t := _usable_text(s.text or "", exclude_timestamps=exclude_timestamps))
    ]
    return " ".join(texts) if texts else ""


def extract_latest_buyer_line(
    spans: list[OCRSpan],
    *,
    roi_height: int,
    roi_width: int | None = None,
    exclude_timestamps: bool = True,
) -> str:
    """向后兼容接口；委托到 extract_buyer_message_block。"""
    return extract_buyer_message_block(
        spans,
        roi_height=roi_height,
        roi_width=roi_width,
        exclude_timestamps=exclude_timestamps,
    )
