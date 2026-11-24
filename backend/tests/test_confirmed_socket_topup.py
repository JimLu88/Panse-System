from decimal import Decimal
from types import SimpleNamespace

from app.services.order_financials import physical_cost_breakdown


def test_confirmed_mixed_topup_keeps_three_sockets():
    order = SimpleNamespace(
        is_refill=False,
        parts_override={"confirmed_socket_qty": 3, "confirmed_socket_unit_cost": 55,
                        "confirmed_socket_total": 165, "confirmed_socket_freight": 0,
                        "primary_order_no": "3305889121934002974"},
    )
    result = physical_cost_breakdown(order)
    assert result["final"] == Decimal("165")
    assert result["factory_wood"] == 0
    assert result["logistics_component"] == 0


def test_confirmed_mixed_topup_can_keep_explicit_freight():
    order = SimpleNamespace(
        is_refill=False,
        parts_override={"confirmed_socket_qty": 2, "confirmed_socket_unit_cost": 55,
                        "confirmed_socket_total": 110, "confirmed_socket_freight": 8,
                        "primary_order_no": "test-primary"},
    )
    result = physical_cost_breakdown(order)
    assert result["final"] == Decimal("118")
    assert result["logistics_component"] == Decimal("8")
