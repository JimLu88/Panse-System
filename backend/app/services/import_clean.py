"""导入清洗共用 helper (Plan C14) — 真实 Excel 缺陷的统一处理。

excel_importer 里已有的序列号日期/表头探测逻辑抽出来给 CSV 导入器复用:
  - Excel 日期序列号 (46175 → 2026-06-08, 纪元 1899-12-30)
  - 淘宝导出电话带虚拟分机后缀 (139xxxxxxxx-1234 → 139xxxxxxxx)
  - 运单号/订单号混入 tab / 全角空格
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

# Excel 序列号合理范围: 1954-09-19(20000) ~ 2117-12-15(80000), 防止把金额当日期
_SERIAL_MIN, _SERIAL_MAX = 20000, 80000
_EXCEL_EPOCH = datetime(1899, 12, 30)

_PHONE_EXT_RE = re.compile(r"^(\d{7,15})-\d{1,6}$")
_WS_RE = re.compile(r"[\s　\t]+")


def excel_serial_to_date(v: Any) -> Optional[date]:
    """数字形态的 Excel 日期序列号 → date; 不在合理范围返回 None。"""
    try:
        n = float(str(v).strip())
    except (ValueError, TypeError):
        return None
    if not (_SERIAL_MIN <= n <= _SERIAL_MAX):
        return None
    try:
        return (_EXCEL_EPOCH + timedelta(days=n)).date()
    except (ValueError, OverflowError):
        return None


def clean_phone(v: Any) -> Optional[str]:
    """电话去淘宝虚拟分机后缀 (-NNNN); 其余原样保留 (座机带区号不动)。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = _PHONE_EXT_RE.match(s)
    return m.group(1) if m else s


def clean_no(v: Any) -> Optional[str]:
    """运单号/订单号: 去掉所有空白(含 tab/全角空格)。"""
    if v is None:
        return None
    s = _WS_RE.sub("", str(v))
    return s or None
