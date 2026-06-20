"""平台扣点 分期/部分到账 护栏测试 (order_financials.platform_deduction)。

用户拍板 2026-06-20: 分期购单 实付−实收 会把未到账款误当平台费, 超合理扣点(实付×8%)时改用率算法。
全部 SimpleNamespace 合成对象, 无DB。
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.order_financials import platform_deduction


def _coef():
    return {"handling_rate": Decimal("0.006"), "activity_rate": Decimal("0.02"),
            "activity_since": date(2026, 5, 1)}


def _o(paid, recv, d=date(2026, 5, 10)):
    return SimpleNamespace(paid_amount=Decimal(str(paid)), shop_received_amount=Decimal(str(recv)), order_date=d)


def test_normal_order_uses_real_diff():
    # 正常单: 实付-实收=2.6% (≤8%) → 用真实差额 ¥26
    assert platform_deduction(_o("1000", "974"), _coef()) == Decimal("26")


def test_installment_falls_to_rate():
    # 分期单 5117408713503179541: 实付6333.66 实收2635.81, 差58%(>8%) → 改率算法
    pd = platform_deduction(_o("6333.66", "2635.81", date(2026, 5, 24)), _coef())
    assert pd == Decimal("164.68")          # 6333.66×(0.006+0.02)
    assert pd < Decimal("200")              # 远小于 实付-实收 ¥3697.85


def test_no_recv_uses_rate():
    assert platform_deduction(_o("1000", "0"), _coef()) == Decimal("26.00")


def test_exactly_8pct_still_real():
    # 差额恰=8% → 仍视作真实扣点(边界)
    assert platform_deduction(_o("1000", "920"), _coef()) == Decimal("80")
