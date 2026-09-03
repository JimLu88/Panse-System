from app.services import campaign_price_resolution_service as service


def test_real_sku_list_line_conflict_holds_item_without_repricing():
    result = service.resolve(
        daily_price=100, target_final_price=80, official_rate=.18,
        minimum_list_price=99, minimum_coupon_after_price=80)
    assert result["ok"] is False
    assert result["action"] == "hold_whole_item"
    assert result["reason"] == "real_sku_signup_price_would_differ_from_erp_daily"
    assert result["signup_price"] == 100.0


def test_real_sku_coupon_line_conflict_holds_item_without_auto_concession():
    result = service.resolve(
        daily_price=100, target_final_price=80, official_rate=.18,
        minimum_list_price=100, minimum_coupon_after_price=79)
    assert result["ok"] is False
    assert result["action"] == "hold_whole_item"
    assert result["reason"] == "erp_target_above_platform_coupon_floor"
    assert result["signup_price"] == 100.0


def test_combined_real_sku_conflict_never_rotates_sku():
    result = service.resolve(
        daily_price=100, target_final_price=82, official_rate=.18,
        minimum_list_price=98.5, minimum_coupon_after_price=79)
    assert result["ok"] is False
    assert result["action"] == "hold_whole_item"


def test_custom_final_price_may_fall_to_exactly_twenty_percent_baseline():
    result = service.resolve(
        daily_price=100, target_final_price=30, official_rate=.10,
        minimum_list_price=100,
        minimum_coupon_after_price=20, is_custom=True,
        immutable_baseline_daily_price=100)
    assert result["ok"] is True
    assert result["final_price"] == 20.0


def test_custom_final_below_twenty_percent_baseline_blocks():
    result = service.resolve(
        daily_price=100, target_final_price=19, official_rate=.10,
        is_custom=True,
        immutable_baseline_daily_price=100)
    assert result["ok"] is False
    assert result["reason"] == "custom_final_below_20_percent_baseline"
