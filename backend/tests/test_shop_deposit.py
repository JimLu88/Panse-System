"""保证金可加项: 多店铺条目求和并入可用资金; 无条目时回退旧单常量。"""
from decimal import Decimal

from app.models.shop_deposit import ShopDeposit
from app.services import cash_flow_service, settings_service


def _deposit_row(db):
    cs = cash_flow_service.compute_summary(db)
    return next(a for a in cs["additions"] if a["key"] == "shop_deposit")


def test_falls_back_to_legacy_setting_when_no_entries(db_session):
    db = db_session
    settings_service.set_value(db, cash_flow_service.SETTING_SHOP_DEPOSIT, "8000")
    db.flush()
    row = _deposit_row(db)
    assert row["amount"] == Decimal("8000")
    assert "单值" in row["source"]


def test_sums_multiple_shop_deposit_entries(db_session):
    db = db_session
    # 即使旧常量存在, 有条目时以条目合计为准
    settings_service.set_value(db, cash_flow_service.SETTING_SHOP_DEPOSIT, "8000")
    db.add_all([
        ShopDeposit(platform="淘宝", shop_name="A店", amount=Decimal("3000")),
        ShopDeposit(platform="抖音", shop_name="B店", amount=Decimal("5000")),
        ShopDeposit(platform="拼多多", shop_name="C店", amount=Decimal("2000")),
    ])
    db.flush()
    row = _deposit_row(db)
    assert row["amount"] == Decimal("10000")
    assert "3 店铺" in row["source"]
