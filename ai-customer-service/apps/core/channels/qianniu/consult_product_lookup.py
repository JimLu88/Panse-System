"""
v1.6.14 商品卡片 → 咨询宝贝悬停读编码 → 查产品库答尺寸。

链路（仅在 base_settings.card_consult_lookup_enabled=True 且坐标已标定时执行）：
  1. 点「咨询宝贝」标签（consult_tab_point）让面板展开
  2. 鼠标悬停商品缩略图（consult_hover_point），等浮层弹出
  3. OCR 浮层区域（consult_popup_rect），抽取 编码 PFG\\d+
  4. 用编码查 products 表，返回 (product_code, size_details, name)

设计原则（默认关、零标定不动鼠标）：
  - 三个坐标任一未标定 → 直接返回 None，绝不点击/悬停（不干扰界面）
  - 任何异常只 log + 返回 None，绝不影响接待主流程
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from apps.core.configs.loader import ShopConfig

LogFn = Callable[[str], None]

# 产品编码：PFG 开头 + 数字（如 PFG25250011225）。大小写兼容。
# 不用前导 \b：编码常紧跟中文"编码PFG..."，中文与字母间无 ASCII 词边界。
_PRODUCT_CODE_RE = re.compile(r"PFG\d{6,}", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ConsultLookupResult:
    product_code: str
    name: str
    size_details: str
    customization_scope: str


def _extract_code_from_spans(spans: list) -> str:
    for s in spans:
        t = (getattr(s, "text", "") or "")
        m = _PRODUCT_CODE_RE.search(t.replace(" ", ""))
        if m:
            return m.group(0).upper()
    # 跨 span 兜底：拼整段再搜一次
    blob = "".join((getattr(s, "text", "") or "") for s in spans).replace(" ", "")
    m = _PRODUCT_CODE_RE.search(blob)
    return m.group(0).upper() if m else ""


def read_product_code_via_consult(shop: ShopConfig, log: LogFn) -> str:
    """点咨询宝贝→悬停→OCR 浮层，返回 PFG 编码；失败/未标定返回 ""。"""
    tab = getattr(shop, "consult_tab_point", None)
    hover = getattr(shop, "consult_hover_point", None)
    popup = getattr(shop, "consult_popup_rect", None)
    if tab is None or hover is None or popup is None:
        log("咨询宝贝读编码：坐标未标定（consult_tab/hover/popup 缺失），跳过（不点击）")
        return ""

    try:
        import uiautomation as auto
    except Exception as e:
        log(f"咨询宝贝读编码：uiautomation 不可用，跳过：{e!r}")
        return ""

    try:
        from apps.core.capture.screen import ScreenCapture
        from apps.core.ocr.dual_engine import get_dual_ocr_engine
    except Exception as e:
        log(f"咨询宝贝读编码：依赖不可用，跳过：{e!r}")
        return ""

    try:
        # 1) 点「咨询宝贝」标签
        auto.Click(int(tab.x), int(tab.y))
        time.sleep(0.6)
        # 2) 悬停商品缩略图（移动到该点，不点击）
        try:
            auto.SetCursorPos(int(hover.x), int(hover.y))
        except Exception:
            # 退化：用 MoveTo（部分版本 API 名不同）
            try:
                auto.MoveTo(int(hover.x), int(hover.y))
            except Exception:
                pass
        time.sleep(0.7)  # 等浮层渲染
        # 3) OCR 浮层区域
        rgb = ScreenCapture().grab_rgb(popup)
        result = get_dual_ocr_engine().recognize(rgb)
        code = _extract_code_from_spans(result.spans)
        if code:
            log(f"咨询宝贝读编码：✓ 识别到编码 {code}")
        else:
            log("咨询宝贝读编码：浮层未识别到 PFG 编码（可能悬停点/浮层区需重标定）")
        return code
    except Exception as e:
        log(f"咨询宝贝读编码异常（已忽略）：{e!r}")
        return ""


def lookup_product_by_consult(
    shop: ShopConfig,
    *,
    brand_id: str,
    shop_id: str,
    db_path,
    log: LogFn,
) -> ConsultLookupResult | None:
    """完整链路：读编码 → 查产品库。任一步失败返回 None。"""
    code = read_product_code_via_consult(shop, log)
    if not code:
        return None
    try:
        from apps.core.crm.db import connect
        from apps.core.ai.rag_kb import retrieve_product_snippets

        conn = connect(db_path)
        try:
            rows = retrieve_product_snippets(
                conn, brand_id=brand_id, shop_id=shop_id, query=code, limit=1
            )
        finally:
            conn.close()
        if not rows:
            log(f"咨询宝贝读编码：编码 {code} 在产品库未匹配到记录")
            return None
        name, product_code, size_details, customization_scope = rows[0]
        log(f"咨询宝贝读编码：编码 {code} → 命中产品「{name}」")
        return ConsultLookupResult(
            product_code=product_code,
            name=name,
            size_details=size_details,
            customization_scope=customization_scope,
        )
    except Exception as e:
        log(f"咨询宝贝查产品库异常（已忽略）：{e!r}")
        return None
