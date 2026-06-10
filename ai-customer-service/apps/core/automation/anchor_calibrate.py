"""
v1.6.3 锚点校准（窗口+偏移预测）。

核心思想
--------
千牛主窗口被「窗口锁定」钉死在固定屏幕 rect 后，窗口内每个组件
（输入框 / 发送键 / 滚动点 / 会话列表 / 聊天OCR区 / 右侧OCR区 / 客服键）
都只是**相对窗口左上角的固定偏移**。因此：

  - 校准成功写盘时，顺手记下「采集时的窗口 rect」+「每个组件相对窗口
    左上角的偏移」，存为 anchor。
  - 下次校准不再满屏瞎搜——直接用「当前窗口左上角 + 偏移」预测出全部
    组件坐标，再交给 AI 复检；只有个别组件复检不过时，才退回满屏搜那一项。

为什么不纳入任务栏图标
--------------------
任务栏图标在窗口**外**，不随窗口移动，必须每次独立 UIA/OCR 重定位。

设计原则
--------
- 本模块**零外部依赖**（只用 dataclass + 纯算术），方便单测。
- 所有偏移都相对窗口左上角 (wl, wt)：
    * 点：  dx = x - wl,  dy = y - wt        →  预测 x = wl' + dx
    * 矩形：四条边都相对左上角
            dl = left - wl, dt = top - wt, dr = right - wl, db = bottom - wt
  这样窗口平移时组件完全跟随；窗口缩放时不自适应，但窗口锁定保证尺寸不变。
- auto_calibrate.py 负责 CalibrateCoords <-> dict 的转换，本模块只吃 dict，
  彻底避免循环 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 纳入"窗口+偏移"预测的组件名（任务栏图标 taskbar_icon_point 故意不在此列）
ANCHOR_POINT_KEYS: tuple[str, ...] = (
    "input_box_point",
    "send_button_point",
    "chat_scroll_point",
    "service_btn_point",
)
ANCHOR_RECT_KEYS: tuple[str, ...] = (
    "session_list_rect",
    "ocr_chat_rect",
    "ocr_right_rect",
)

#: 判定"窗口没动"的容忍像素（四个维度任一超过即视为窗口已变化）
DEFAULT_WINDOW_TOLERANCE_PX = 6


@dataclass(frozen=True, slots=True)
class CalibAnchor:
    """一次成功校准固化下来的锚点。"""
    #: 采集时的窗口 rect：(wl, wt, wr, wb)
    base_window: tuple[int, int, int, int]
    #: name -> (dx, dy)，相对窗口左上角
    point_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: name -> (dl, dt, dr, db)，相对窗口左上角
    rect_offsets: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


# ── 采集：组件坐标 → 偏移 ────────────────────────────────────────────────
def build_anchor(
    base_window: tuple[int, int, int, int],
    points: dict[str, tuple[int, int]],
    rects: dict[str, tuple[int, int, int, int]],
) -> CalibAnchor:
    """
    从一次校准结果构造 anchor。

    @param base_window  采集时窗口 (wl, wt, wr, wb)
    @param points       {name: (x, y)}，仅传需要纳入的、非 None 的点
    @param rects        {name: (left, top, right, bottom)}
    """
    wl, wt, _wr, _wb = base_window
    point_offsets: dict[str, tuple[int, int]] = {}
    for name, xy in points.items():
        if name not in ANCHOR_POINT_KEYS or xy is None:
            continue
        x, y = xy
        if x is None or y is None:
            continue
        point_offsets[name] = (int(x) - wl, int(y) - wt)

    rect_offsets: dict[str, tuple[int, int, int, int]] = {}
    for name, ltrb in rects.items():
        if name not in ANCHOR_RECT_KEYS or ltrb is None:
            continue
        left, top, right, bottom = ltrb
        if None in (left, top, right, bottom):
            continue
        rect_offsets[name] = (
            int(left) - wl, int(top) - wt,
            int(right) - wl, int(bottom) - wt,
        )

    return CalibAnchor(
        base_window=(int(base_window[0]), int(base_window[1]),
                     int(base_window[2]), int(base_window[3])),
        point_offsets=point_offsets,
        rect_offsets=rect_offsets,
    )


# ── 预测：偏移 + 当前窗口 → 组件坐标 ──────────────────────────────────────
def predict_points(
    anchor: CalibAnchor,
    current_window: tuple[int, int, int, int],
) -> dict[str, tuple[int, int]]:
    """用当前窗口左上角 + 偏移，预测全部点坐标。"""
    wl, wt = int(current_window[0]), int(current_window[1])
    return {
        name: (wl + dx, wt + dy)
        for name, (dx, dy) in anchor.point_offsets.items()
    }


def predict_rects(
    anchor: CalibAnchor,
    current_window: tuple[int, int, int, int],
) -> dict[str, tuple[int, int, int, int]]:
    """用当前窗口左上角 + 偏移，预测全部矩形坐标。"""
    wl, wt = int(current_window[0]), int(current_window[1])
    return {
        name: (wl + dl, wt + dt, wl + dr, wt + db)
        for name, (dl, dt, dr, db) in anchor.rect_offsets.items()
    }


# ── 窗口是否没动 ────────────────────────────────────────────────────────
def window_unchanged(
    base_window: tuple[int, int, int, int],
    current_window: tuple[int, int, int, int],
    tolerance_px: int = DEFAULT_WINDOW_TOLERANCE_PX,
) -> bool:
    """
    采集时窗口 与 当前窗口 的四个维度差是否都在容忍内。
    True → 窗口没动 → 组件坐标也不会变 → 重新校准是多余的（弹窗确认）。
    """
    if base_window is None or current_window is None:
        return False
    return all(
        abs(int(a) - int(b)) <= int(tolerance_px)
        for a, b in zip(base_window, current_window, strict=False)
    )


# ── yaml 序列化（存到 lightrat.yaml: qianniu.calib_anchor）────────────────
def to_yaml_dict(anchor: CalibAnchor) -> dict:
    """CalibAnchor → 适合 yaml.safe_dump 的纯 dict。"""
    wl, wt, wr, wb = anchor.base_window
    return {
        "base_window": {"left": wl, "top": wt, "right": wr, "bottom": wb},
        "point_offsets": {
            name: {"dx": dx, "dy": dy}
            for name, (dx, dy) in anchor.point_offsets.items()
        },
        "rect_offsets": {
            name: {"dl": dl, "dt": dt, "dr": dr, "db": db}
            for name, (dl, dt, dr, db) in anchor.rect_offsets.items()
        },
    }


def from_yaml_dict(d: dict | None) -> CalibAnchor | None:
    """yaml dict → CalibAnchor；结构不合法返回 None（调用方据此回退满屏搜）。"""
    if not isinstance(d, dict):
        return None
    bw = d.get("base_window")
    if not isinstance(bw, dict):
        return None
    try:
        base_window = (
            int(bw["left"]), int(bw["top"]),
            int(bw["right"]), int(bw["bottom"]),
        )
    except (KeyError, TypeError, ValueError):
        return None

    point_offsets: dict[str, tuple[int, int]] = {}
    for name, off in (d.get("point_offsets") or {}).items():
        if name not in ANCHOR_POINT_KEYS or not isinstance(off, dict):
            continue
        try:
            point_offsets[name] = (int(off["dx"]), int(off["dy"]))
        except (KeyError, TypeError, ValueError):
            continue

    rect_offsets: dict[str, tuple[int, int, int, int]] = {}
    for name, off in (d.get("rect_offsets") or {}).items():
        if name not in ANCHOR_RECT_KEYS or not isinstance(off, dict):
            continue
        try:
            rect_offsets[name] = (
                int(off["dl"]), int(off["dt"]),
                int(off["dr"]), int(off["db"]),
            )
        except (KeyError, TypeError, ValueError):
            continue

    if not point_offsets and not rect_offsets:
        return None
    return CalibAnchor(
        base_window=base_window,
        point_offsets=point_offsets,
        rect_offsets=rect_offsets,
    )
