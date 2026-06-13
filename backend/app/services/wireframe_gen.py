# -*- coding: utf-8 -*-
"""产品等距(轴测)线框尺寸图生成器 — 方案 C。

纯 Python 按盒体尺寸程序化画等距线框 + 箭头尺寸标注 → SVG。
SVG 包进 HTML 后, 用 order_sheet_archive_service._html_to_png (wkhtmltoimage) 渲染成 PNG。
蓝本 GitHub pawelnosko/3ddrawing 模式 (投影器 + 画布 + arrowDimension);
投影数学参考 prideout/svg3d。零重依赖。

坐标: x=宽(向右) y=深(向后) z=高(向上), 单位 mm。
模板: render_cabinet(柜) / render_table(桌) / render_bed(床)。
"""
from __future__ import annotations

import math
from typing import Optional

# ---- 投影 -------------------------------------------------------------------
_ISO_DEG = 26.0                       # 等距角 (30=经典等距; 设计师偏浅, 取 26)
_COS = math.cos(math.radians(_ISO_DEG))
_SIN = math.sin(math.radians(_ISO_DEG))


def _project(x: float, y: float, z: float) -> tuple[float, float]:
    """3D(mm) → 2D 数学坐标(y 向上)。back/up 越大越高。"""
    return (x - y) * _COS, z + (x + y) * _SIN


# ---- 凸包 / 隐藏角 -----------------------------------------------------------
def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    lo: list = []
    for p in pts:
        while len(lo) >= 2 and _cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up: list = []
    for p in reversed(pts):
        while len(up) >= 2 and _cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _hidden_index(proj) -> int:
    """盒体 8 角投影后, 恰有 1 个落在凸包内部 = 被遮挡的背后角。返回其下标 (无则 -1)。"""
    hull = set(_hull(proj))
    for i, p in enumerate(proj):
        if p not in hull:
            return i
    return -1


# ---- 画布 -------------------------------------------------------------------
class _Canvas:
    def __init__(self) -> None:
        self.polys: list = []     # (pts2d, fill, stroke, sw) — 按插入顺序绘制(画家算法)
        self.lines: list = []     # (x1,y1,x2,y2,style)
        self.texts: list = []     # (x,y,text,font)

    def poly(self, pts, fill: str, stroke: str, sw: float = 1.7) -> None:
        self.polys.append((list(pts), fill, stroke, sw))

    def line(self, p1, p2, style: str) -> None:
        self.lines.append((p1[0], p1[1], p2[0], p2[1], style))

    def text(self, p, s: str, font: int) -> None:
        self.texts.append((p[0], p[1], s, font))


_STYLE = {
    "solid": 'stroke="#22312f" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"',
    "thin": 'stroke="#5b6470" stroke-width="1.5" stroke-linecap="round" fill="none"',
    "dim": 'stroke="#333333" stroke-width="1.6" stroke-linecap="round" fill="none"',
    "ext": 'stroke="#aab0b6" stroke-width="1.0" fill="none"',
}


def _emit(cv: _Canvas, w: float, h: float, caption: Optional[str]) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}">',
        f'<rect width="{w:.0f}" height="{h:.0f}" fill="#fbf9f4"/>',
    ]
    for pts, fill, stroke, sw in cv.polys:
        pstr = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        out.append(f'<polygon points="{pstr}" fill="{fill}" stroke="{stroke}" '
                   f'stroke-width="{sw}" stroke-linejoin="round"/>')
    for x1, y1, x2, y2, k in cv.lines:
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" {_STYLE[k]}/>')
    for x, y, s, fs in cv.texts:
        out.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="Noto Sans CJK SC, sans-serif" '
            f'font-size="{fs}" font-weight="600" fill="#1a1a1a" '
            f'text-anchor="middle" dominant-baseline="middle">{s}</text>')
    if caption:
        out.append(
            f'<text x="{w/2:.1f}" y="{h-22:.1f}" font-family="Noto Sans CJK SC, sans-serif" '
            f'font-size="20" fill="#888" text-anchor="middle">{caption}</text>')
    out.append('</svg>')
    return "\n".join(out)


# ---- 标注 -------------------------------------------------------------------
def _arrow(cv: _Canvas, tip, d, size: float = 9.0) -> None:
    ang = math.atan2(d[1], d[0])
    for off in (2.6, -2.6):                      # ±≈149°
        a = ang + off
        cv.line(tip, (tip[0] + size * math.cos(a), tip[1] + size * math.sin(a)), "dim")


def _dim(cv: _Canvas, p1, p2, center, label: str, *, offset: float = 58.0, font: int = 25) -> None:
    """在 p1→p2 这条边外侧画尺寸线(双箭头)+ 延伸线 + 水平数字。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux                              # 法线
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    if (mid[0] + nx - center[0]) ** 2 + (mid[1] + ny - center[1]) ** 2 < \
       (mid[0] - center[0]) ** 2 + (mid[1] - center[1]) ** 2:
        nx, ny = -nx, -ny                         # 法线朝远离盒体中心
    gap = 7.0
    a1 = (p1[0] + nx * gap, p1[1] + ny * gap); b1 = (p1[0] + nx * offset, p1[1] + ny * offset)
    a2 = (p2[0] + nx * gap, p2[1] + ny * gap); b2 = (p2[0] + nx * offset, p2[1] + ny * offset)
    cv.line(a1, b1, "ext"); cv.line(a2, b2, "ext")
    cv.line(b1, b2, "dim")
    _arrow(cv, b1, (ux, uy)); _arrow(cv, b2, (-ux, -uy))
    lp = ((b1[0] + b2[0]) / 2 + nx * 26, (b1[1] + b2[1]) / 2 + ny * 26)  # 数字推到尺寸线外侧, 不压线
    cv.text(lp, label, font)


# ---- 几何辅助 ---------------------------------------------------------------
_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
          (0, 4), (1, 5), (2, 6), (3, 7)]
_TOP_XY = {4: (0, 0), 5: (1, 0), 6: (1, 1), 7: (0, 1)}   # 顶面 4 角 (x,y) 归一


def _setup(w, d, h, target_w, margin, mirror):
    """按外包围盒 (w,d,h) 算投影器 P + 画布尺寸。mirror=True 让宽在右、深在左。"""
    corners = [(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0),
               (0, 0, h), (w, 0, h), (w, d, h), (0, d, h)]
    proj = [_project(*c) for c in corners]
    mxs = [p[0] for p in proj]; mys = [p[1] for p in proj]
    min_mx, max_mx = min(mxs), max(mxs)
    min_my, max_my = min(mys), max(mys)
    scale = (target_w - 2 * margin) / (max_mx - min_mx)
    cw = float(target_w)
    ch = (max_my - min_my) * scale + 2 * margin

    def P(x, y, z):
        mx, my = _project(x, y, z)
        sx = (max_mx - mx) if mirror else (mx - min_mx)
        return (margin + sx * scale, margin + (max_my - my) * scale)

    return P, cw, ch


def _box_pts(P, x0, y0, z0, x1, y1, z1):
    cs = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
          (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    scr = [P(*c) for c in cs]
    return scr, _hidden_index(scr)


def _draw_box(cv, scr, hid, style="solid", skip_hidden=True):
    for a, b in _EDGES:
        if skip_hidden and (a == hid or b == hid):
            continue
        cv.line(scr[a], scr[b], style)


def _center(scr):
    return (sum(p[0] for p in scr) / 8, sum(p[1] for p in scr) / 8)


def _draw_dims(cv, scr, center, w, d, h):
    """顶峰角引出 宽/深; 左侧竖边引出 高。scr 为外包围盒 8 角屏幕坐标。"""
    top = [4, 5, 6, 7]
    peak = min(top, key=lambda i: scr[i][1])      # 屏幕最高 (sy 最小)
    pxn = _TOP_XY[peak]
    width_nb = next(i for i in top if i != peak and _TOP_XY[i][1] == pxn[1])
    depth_nb = next(i for i in top if i != peak and _TOP_XY[i][0] == pxn[0])
    _dim(cv, scr[peak], scr[width_nb], center, str(int(round(w))))
    _dim(cv, scr[peak], scr[depth_nb], center, str(int(round(d))))
    left_top = min(top, key=lambda i: scr[i][0])
    _dim(cv, scr[left_top], scr[left_top - 4], center, str(int(round(h))))


# ---- 模板: 柜 ---------------------------------------------------------------
def render_cabinet(w, d, h, *, drawer=None, niche=None, mirror=True,
                   caption=None, target_w=560, margin=108):
    """柜体: drawer=(z0,z1) 嵌入式抽屉面; niche=(z0,z1) 开放格(可看进凹槽)。"""
    P, cw, ch = _setup(w, d, h, target_w, margin, mirror)
    cv = _Canvas()
    scr, hid = _box_pts(P, 0, 0, 0, w, d, h)
    _draw_box(cv, scr, hid, "solid")
    center = _center(scr)
    fr = min(w, d) * 0.05
    x0, x1 = fr, w - fr
    rec = d * 0.42
    if niche is not None:
        z0, z1 = niche
        op = [P(x0, 0, z0), P(x1, 0, z0), P(x1, 0, z1), P(x0, 0, z1)]
        for i in range(4):
            cv.line(op[i], op[(i + 1) % 4], "thin")
        cv.line(P(x0, 0, z0), P(x0, rec, z0), "thin")
        cv.line(P(x1, 0, z0), P(x1, rec, z0), "thin")
        cv.line(P(x0, rec, z0), P(x1, rec, z0), "thin")
        cv.line(P(x0, rec, z0), P(x0, rec, z1), "thin")
        cv.line(P(x1, rec, z0), P(x1, rec, z1), "thin")
        cv.line(P(x0, rec, z1), P(x1, rec, z1), "thin")
    if drawer is not None:
        z0, z1 = drawer
        op = [P(x0, 0, z0), P(x1, 0, z0), P(x1, 0, z1), P(x0, 0, z1)]
        for i in range(4):
            cv.line(op[i], op[(i + 1) % 4], "thin")
        g = fr * 0.5
        df = [P(x0 + g, 0, z0 + g), P(x1 - g, 0, z0 + g),
              P(x1 - g, 0, z1 - g), P(x0 + g, 0, z1 - g)]
        for i in range(4):
            cv.line(df[i], df[(i + 1) % 4], "thin")
        hy = z0 + (z1 - z0) * 0.7
        cv.line(P(w * 0.42, 0, hy), P(w * 0.58, 0, hy), "thin")
    _draw_dims(cv, scr, center, w, d, h)
    return _emit(cv, cw, ch, caption)


# ---- 模板: 桌 ---------------------------------------------------------------
def render_table(w, d, h, *, top_th=None, mirror=True,
                 caption=None, target_w=560, margin=108):
    """桌: 薄桌面板 + 4 条细方腿。"""
    top_th = top_th or max(28.0, h * 0.05)
    lt = min(w, d) * 0.06                          # 腿截面
    li = lt * 1.2                                   # 腿内缩
    P, cw, ch = _setup(w, d, h, target_w, margin, mirror)
    cv = _Canvas()
    outer, _ = _box_pts(P, 0, 0, 0, w, d, h)
    center = _center(outer)
    legs = [(li, li), (w - li - lt, li), (w - li - lt, d - li - lt), (li, d - li - lt)]
    for lx, ly in legs:                            # 先画腿
        s, hh = _box_pts(P, lx, ly, 0, lx + lt, ly + lt, h - top_th)
        _draw_box(cv, s, hh, "thin")
    s, hh = _box_pts(P, 0, 0, h - top_th, w, d, h)  # 桌面板压在腿上
    _draw_box(cv, s, hh, "solid")
    _draw_dims(cv, outer, center, w, d, h)
    return _emit(cv, cw, ch, caption)


# ---- 模板: 床 ---------------------------------------------------------------
def render_bed(w, d, h, *, frame_h=None, hb_th=None, mirror=True,
               caption=None, target_w=560, margin=108):
    """床: 低床架 + 内缩床垫 + 背面床头板 (高到 h)。w=宽 d=长 h=床头板总高。"""
    frame_h = frame_h or min(h * 0.45, 360.0)
    hb_th = hb_th or min(w, d) * 0.05
    P, cw, ch = _setup(w, d, h, target_w, margin, mirror)
    cv = _Canvas()
    outer, _ = _box_pts(P, 0, 0, 0, w, d, h)
    center = _center(outer)
    s, hh = _box_pts(P, 0, 0, 0, w, d, frame_h)                  # 床架
    _draw_box(cv, s, hh, "solid")
    mi = min(w, d) * 0.03
    s, hh = _box_pts(P, mi, mi, frame_h, w - mi, d - mi, frame_h + min(w, d) * 0.06)
    _draw_box(cv, s, hh, "thin")                                 # 床垫
    s, hh = _box_pts(P, 0, d - hb_th, 0, w, d, h)                # 床头板
    _draw_box(cv, s, hh, "solid")
    _draw_dims(cv, outer, center, w, d, h)
    return _emit(cv, cw, ch, caption)


# ---- 模板: 柜 (配方驱动 v4 — 实体板厚 + 仅画朝相机面的画家算法; 侧板落地) -----
# 朝向相机的方向 (与投影一致): 法线·此向 > 0 的面才可见 → 背面/底面不画, 无漏线。
_CAM = (-_COS, -_COS, 2 * _COS * _SIN)
# 盒 8 角的 6 个面 (顶点序) + 各面外法线
_FACE_N = [
    ((0, 1, 2, 3), (0, 0, -1)),   # 底
    ((4, 5, 6, 7), (0, 0, 1)),    # 顶
    ((0, 1, 5, 4), (0, -1, 0)),   # 前 (y=y0)
    ((1, 2, 6, 5), (1, 0, 0)),    # 右 (x=x1)
    ((2, 3, 7, 6), (0, 1, 0)),    # 后 (y=y1)
    ((3, 0, 4, 7), (-1, 0, 0)),   # 左 (x=x0)
]


def render_cabinet_recipe(recipe: dict, *, caption: Optional[str] = None,
                          target_w: int = 560, margin: int = 116) -> str:
    """配方驱动柜体。设计师填: w/d/h(mm)、panel_t(板厚)、kick(底档高,0=侧板直接落地)、
    segments=[{kind,h}] (从下到上, kind: drawer齐平抽屉 / door齐平门 / open开放格)。

    画法: 把每块板建成实体盒(真实板厚), 只画「朝相机的面」(背面/底面不画→无漏线),
    白色不透明填充 + 画家算法(远的先画)做遮挡 → 顶板/侧板/搁板都有厚度双线,
    侧板落地、接缝干净。开放格不画正面板, 自然看进内腔。"""
    w = float(recipe["w"]); d = float(recipe["d"]); h = float(recipe["h"])
    t = float(recipe.get("panel_t", 18))
    kick = float(recipe.get("kick", recipe.get("plinth_h", 0)))   # 底档高; 侧板始终 z=0 落地
    segs = recipe.get("segments", [])
    P, cw, ch = _setup(w, d, h, target_w, margin, mirror=True)
    cv = _Canvas()
    WHITE, EDGE = "#ffffff", "#26312e"
    panels: list = []

    def pan(x0, y0, z0, x1, y1, z1, fill=WHITE):
        panels.append((x0, y0, z0, x1, y1, z1, fill))

    # 外壳板件 (侧板/背板/底板 都 z=0 落地; 顶板在顶)
    pan(0, 0, 0, t, d, h)                       # 左侧板 (落地)
    pan(w - t, 0, 0, w, d, h)                   # 右侧板 (落地)
    pan(0, 0, h - t, w, d, h)                   # 顶板
    pan(0, 0, 0, w, d, t)                       # 底板
    pan(0, d - t, 0, w, d, h)                   # 背板
    if kick > t:                                # 底档(踢脚)前脸: 落地侧板间的下横档
        pan(t, 0, 0, w - t, t, kick)

    inner0, inner1 = t, w - t
    drawer_fronts: list = []
    z = max(kick, t)
    n = len(segs)
    for i, seg in enumerate(segs):
        sh = float(seg.get("h", 0)); kind = seg.get("kind", "open")
        z0, z1 = z, z + sh
        if kind in ("drawer", "door"):          # 齐平面板 (厚 t, 贴正面)
            pan(inner0, 0, z0 + 1.5, inner1, t, z1 - 1.5)
            if kind == "drawer":
                drawer_fronts.append((z0, z1))
        if i < n - 1:                            # 段间分隔搁板 (厚 t)
            pan(inner0, 0, z1, inner1, d - t, z1 + t)
            z = z1 + t
        else:
            z = z1

    # 仅画朝相机的面 + 画家算法 (白底不透明遮挡)
    faces: list = []
    for (x0, y0, z0, x1, y1, z1, fill) in panels:
        cs = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
              (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for idx, nrm in _FACE_N:
            if nrm[0] * _CAM[0] + nrm[1] * _CAM[1] + nrm[2] * _CAM[2] <= 0.01:
                continue                         # 背向相机 → 不画 (杜绝漏线)
            pts = [cs[i] for i in idx]
            cx = sum(p[0] for p in pts) / 4
            cy = sum(p[1] for p in pts) / 4
            cz = sum(p[2] for p in pts) / 4
            key = _CAM[0] * cx + _CAM[1] * cy + _CAM[2] * cz   # 越大越近 → 升序排, 远先画
            faces.append((key, [P(*p) for p in pts], fill))
    faces.sort(key=lambda fr: fr[0])
    for _, pts, fill in faces:
        cv.poly(pts, fill, EDGE, 1.7)

    for z0, z1 in drawer_fronts:                 # 抽屉拉手凹槽短线
        hz = z0 + (z1 - z0) * 0.6
        cv.line(P(w * 0.40, 0, hz), P(w * 0.60, 0, hz), "thin")

    outer, _ = _box_pts(P, 0, 0, 0, w, d, h)
    _draw_dims(cv, outer, _center(outer), w, d, h)
    return _emit(cv, cw, ch, caption)


def svg_to_html(svg: str) -> str:
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<style>*{margin:0;padding:0}body{background:#fbf9f4}</style>'
            f'</head><body>{svg}</body></html>')
