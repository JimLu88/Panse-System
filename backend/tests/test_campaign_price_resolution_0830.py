from app.services import campaign_price_resolution_service as service


def test_standard_list_line_gap_up_to_two_is_adjusted():
    result = service.resolve(
        daily_price=100, target_final_price=80, official_rate=.18,
        minimum_list_price=99, minimum_coupon_after_price=80)
    assert result["ok"] is True
    assert result["signup_price"] == 99.0
    assert result["final_price"] == 80.0


def test_standard_coupon_gap_up_to_two_uses_single_discount():
    result = service.resolve(
        daily_price=100, target_final_price=80, official_rate=.18,
        minimum_list_price=100, minimum_coupon_after_price=80)
    assert result["ok"] is True
    assert result["single_discount"] == 2.0


def test_combined_adjustment_cannot_hide_more_than_two_final_concession():
    result = service.resolve(
        daily_price=100, target_final_price=82, official_rate=.18,
        minimum_list_price=98.5, minimum_coupon_after_price=79)
    assert result["ok"] is False
    assert result["action"] == "use_clean_sku_slot"


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
