"""工厂账单只含木作 → 补回非木作成本估算 (order_financials)。

用户拍板 2026-06-20: 工厂对账单 actual_cost 只含木作, 漏算打包/配件/物流/安装/税/平台。
物理成本 = actual_cost + max(0, theoretical_cost − wood_cost_est)。
wood_cost_est 缺 → 退回旧行为(actual_cost 直接当物理成本)。物流安装绝不双算。
全部 SimpleNamespace 合成对象, 无DB。
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.order_financials import cost_breakdown, physical_cost


def _coef():
    return {"handling_rate": Decimal("0.006"), "activity_rate": Decimal("0.02"),
            "activity_since": date(2026, 5, 1), "tax_rate": Decimal("0.02")}


def _o(actual=None, theo=None, wood=None, paid="0", freight="0", install="0", upstairs="0"):
    return SimpleNamespace(
        actual_cost=Decimal(actual) if actual else None,
        theoretical_cost=Decimal(theo) if theo else None,
        wood_cost_est=Decimal(wood) if wood else None,
        paid_amount=Decimal(paid), actual_freight=Decimal(freight),
        install_fee=Decimal(install), upstairs_fee=Decimal(upstairs),
        tax=None, shop_received_amount=Decimal("0"), order_date=date(2026, 5, 1))


def test_physical_adds_non_wood():
    # 工厂账单木作¥1300 + 非木作(theoretical2000 − 木作估1500 = 500) → 1800
    assert physical_cost(_o(actual="1300", theo="2000", wood="1500", paid="3000")) == Decimal("1800")


def test_physical_no_wood_est_legacy():
    # wood_cost_est 缺 → 退回旧行为, actual 直接当物理成本
    assert physical_cost(_o(actual="1300", theo="2000", paid="3000")) == Decimal("1300")


def test_fragment_cap_still_applies():
    # 片段封顶仍生效: actual8200, 无木作估, 实付2335(<50%) → 2335×0.85
    assert physical_cost(_o(actual="8200", paid="2335")) == Decimal("1984.75")


def test_breakdown_no_double_count_when_wood_est():
    # actual非空 + wood_cost_est非空 → 非木作(含物流安装)已并入 physical, 不再单独加
    bd = cost_breakdown(_o(actual="1300", theo="2000", wood="1500", paid="3000",
                           freight="200", install="100", upstairs="50"),
                        _coef(), aftersales=Decimal("0"))
    assert bd["freight"] == Decimal("0")
    assert bd["install_upstairs"] == Decimal("0")


def test_breakdown_adds_freight_when_no_wood_est():
    # actual非空 + wood_cost_est=None(未补非木作) → 仍单独加实际运费/安装(向后兼容)
    bd = cost_breakdown(_o(actual="2000", paid="3000", freight="200", install="100", upstairs="50"),
                        _coef(), aftersales=Decimal("0"))
    assert bd["freight"] == Decimal("200")
    assert bd["install_upstairs"] == Decimal("150")


def test_recompute_clears_stale_wood_est():
    # 重算改判: 先有 wood_cost_est, 重算落到 zero_cost(补单)分支 → 应被清成 None(防残留污染 physical_cost)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.models.order import Order
    from app.services.order_cost_service import recompute_and_save
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, autoflush=False, future=True)()
    o = Order(platform="淘宝", order_no="O1", is_refill=True,
              wood_cost_est=Decimal("1500"), paid_amount=Decimal("100"))
    db.add(o)
    db.commit()
    recompute_and_save(db, o)
    assert o.wood_cost_est is None
