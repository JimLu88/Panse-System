"""可配置定价公式引擎 eval_safe 的回归测试.

公式引擎驱动定价/报价, 之前无直接单测。重点覆盖: 基础运算、除零、缺变量、None 传播、
内置函数, 以及 AST 安全性 (禁止任意函数/属性访问 → 防注入)。
"""
from decimal import Decimal

import pytest

from app.services.formula_engine_service import eval_safe, extract_field_names


def test_basic_arithmetic():
    assert eval_safe("1 + 2 * 3", {}) == Decimal("7")
    assert eval_safe("(标价 - 成本) / 2", {"标价": 100, "成本": 40}) == Decimal("30")


def test_division_by_zero_returns_none():
    # 除零返回 None (不抛, 不臆造 0), 由下游标记成本不完整
    assert eval_safe("a / b", {"a": 10, "b": 0}) is None


def test_missing_variable_raises():
    with pytest.raises(ValueError):
        eval_safe("缺失字段 + 1", {})


def test_none_input_propagates():
    assert eval_safe("a + b", {"a": None, "b": 5}) is None
    assert eval_safe("ABS(x)", {"x": None}) is None


def test_builtin_functions():
    assert eval_safe("SUM(1, 2, 3)", {}) == Decimal("6")
    assert eval_safe("MIN(3, 1, 2)", {}) == Decimal("1")
    assert eval_safe("MAX(3, 1, 2)", {}) == Decimal("3")
    assert eval_safe("ABS(-5)", {}) == Decimal("5")
    assert eval_safe("ROUND(3.14159, 2)", {}) == Decimal("3.14")
    assert eval_safe("IF(a > b, 1, 0)", {"a": 5, "b": 3}) == Decimal("1")
    assert eval_safe("IF(a > b, 1, 0)", {"a": 1, "b": 3}) == Decimal("0")


def test_syntax_error_raises():
    with pytest.raises(ValueError):
        eval_safe("1 +", {})


def test_disallowed_callables_blocked():
    # 安全: 只允许白名单函数, 防止 __import__/eval/exec 之类注入
    for expr in ("__import__('os')", "eval('1')", "exec('x=1')", "open('/etc/passwd')"):
        with pytest.raises(ValueError):
            eval_safe(expr, {})


def test_attribute_access_blocked():
    # 安全: 属性访问 (如 a.__class__) 不被支持 → 防沙箱逃逸
    with pytest.raises(ValueError):
        eval_safe("a.__class__", {"a": 1})


def test_extract_field_names():
    names = extract_field_names("标价 - 成本 + SUM(a, b)")
    assert "标价" in names and "成本" in names and "a" in names and "b" in names
    assert "SUM" not in names  # 函数名不算字段
