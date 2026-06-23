"""徐晶晶 Y 佣金合并备注解析 (2026-06-23).

多日佣金并一笔转时备注作 "3.21.22-Y"(=3.21+3.22) 或 "3.21-3.22-Y", 旧正则 r"...(\\d)\\.(\\d)-Y"
只认单日 → 整笔漏算、那几天误报"没付"。放宽后抓首个日期定月份/业务日; b流水(订单额)无 -Y 应被排除。
"""
from app.services.reconciliation_service import _XJJ_Y_RE


def test_single_day():
    m = _XJJ_Y_RE.match("3.2-Y")
    assert m and m.group(1) == "3" and m.group(2) == "2"


def test_combined_dot():
    # 3.21.22-Y = 3.21 与 3.22 合并 → 取首日 21, 月份 3
    m = _XJJ_Y_RE.match("3.21.22-Y")
    assert m and m.group(1) == "3" and m.group(2) == "21"


def test_combined_range():
    m = _XJJ_Y_RE.match("3.21-3.22-Y")
    assert m and m.group(1) == "3" and m.group(2) == "21"


def test_trailing_suffix_tolerated():
    assert _XJJ_Y_RE.match("5.4-Y红薯")


def test_b_liushui_excluded():
    # 订单额(b流水)不含 -Y, 不能被当成佣金
    assert _XJJ_Y_RE.match("3.21-3.22-b流水") is None
    assert _XJJ_Y_RE.match("3.2-b流水") is None
