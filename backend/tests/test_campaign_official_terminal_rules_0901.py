"""Official-export terminal state and custom-SKU price gates."""
from datetime import datetime
from decimal import Decimal
import io

import openpyxl

from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_recon_service as recon
from app.services import campaign_service as campaign


def _official_export(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["超级立减已报商品列表"])
    ws.append([
        "商品ID", "商品名称", "营销ID", "商品状态", "SKUID", "SKU名称",
        "一口价", "最低标价", "最低普惠券后价要求", "超级立减建议金额",
        "活动普惠券后价", "空1", "空2", "空3", "空4", "活动价",
    ])
    ws.append([])
    for row in rows:
        ws.append(row)
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def _row(item_id, marketing_id, status, sku_id, *, activity=284,
         coupon_after=255, coupon_line=255):
    return [
        item_id, "测试商品", marketing_id, status, sku_id, "测试SKU",
        5000, 5000, coupon_line, 29, coupon_after, None, None, None, None,
        activity,
    ]


def test_latest_official_export_selects_new_active_record_and_reports_old_withdrawn():
    content = _official_export([
        _row("1044450741007", "10029885751755", "撤销报名", "old-1"),
        [None, None, None, None, "old-2", "旧SKU", 5000, 5000, 255, 29,
         255, None, None, None, None, 284],
        _row("1044450741007", "10030597697995", "活动中", "new-1"),
        [None, None, None, None, "new-2", "新SKU", 5000, 5000, 255, 29,
         255, None, None, None, None, 284],
    ])

    parsed = recon.parse_activity_floor_evidence_export(content)
    result = recon.resolve_current_activity_records(parsed)

    assert result["ok"] is True
    assert {row["sku_id"] for row in result["rows"]} == {"new-1", "new-2"}
    by_id = {row["marketing_id"]: row for row in result["marketing_records"]}
    assert by_id["10029885751755"]["terminal_state"] == "withdrawn"
    assert by_id["10029885751755"]["selected"] is False
    assert by_id["10030597697995"]["proves_active"] is True
    assert by_id["10030597697995"]["proves_scheduled"] is False


def test_withdrawn_or_failed_record_never_proves_enrollment():
    records = [
        {"item_id": "1", "marketing_id": "old", "status": "撤销报名", "sku_id": "11"},
        {"item_id": "2", "marketing_id": "bad", "status": "异常", "sku_id": "22"},
    ]

    result = recon.resolve_current_activity_records(records, include_paused=True)

    assert result["ok"] is True
    assert result["rows"] == []
    assert all(not row["proves_enrollment"] for row in result["marketing_records"])


def test_two_effective_marketing_ids_are_ambiguous_and_select_nothing():
    records = [
        {"item_id": "1", "marketing_id": "a", "status": "活动中", "sku_id": "11"},
        {"item_id": "1", "marketing_id": "b", "status": "已发布设定", "sku_id": "11"},
    ]

    result = recon.resolve_current_activity_records(records)

    assert result["ok"] is False
    assert result["rows"] == []
    assert result["ambiguities"] == [{
        "item_id": "1",
        "error": "multiple_effective_marketing_records",
        "marketing_ids": ["a", "b"],
    }]


def test_custom_sku_284_255_255_passes_and_all_rows_are_counted():
    expected = [
        {"taobao_item_id": "1044450741007", "taobao_sku_id": "normal",
         "price": 5662.5, "is_placeholder": False},
        {"taobao_item_id": "1044450741007", "taobao_sku_id": "6066017208184",
         "price": 284, "is_placeholder": True},
    ]
    live = [
        {"item_id": "1044450741007", "sku_id": "normal", "status": "活动中",
         "activity_price": 5662.5, "coupon_after": 5000, "min_coupon_line": 5000},
        {"item_id": "1044450741007", "sku_id": "6066017208184",
         "status": "活动中", "activity_price": 284,
         "coupon_after": 255, "min_coupon_line": 255},
    ]

    result = campaign._verify_signup_rows(expected, live, require_active=True)

    assert result["ok"] is True
    assert result["checked_real_skus"] == 1
    assert result["checked_custom_skus"] == 1
    assert result["checked_total_skus"] == 2


def test_custom_sku_coupon_after_above_official_line_fails():
    result = campaign._verify_signup_rows(
        [{"taobao_item_id": "1", "taobao_sku_id": "11", "price": 284,
          "is_placeholder": True}],
        [{"item_id": "1", "sku_id": "11", "status": "活动中",
          "activity_price": 284, "coupon_after": 256, "min_coupon_line": 255}],
    )

    assert result["ok"] is False
    assert result["failed_custom_skus"] == 1
    assert result["failures"][0]["error"] == "定制SKU活动普惠券后价缺失或高于官方要求"


def test_current_window_requires_active_not_only_scheduled():
    expected = [{"taobao_item_id": "1", "taobao_sku_id": "11", "price": 284,
                 "is_placeholder": True}]
    live = [{"item_id": "1", "sku_id": "11", "status": "已发布设定",
             "activity_price": 284, "coupon_after": 255, "min_coupon_line": 255}]

    scheduled_ok = campaign._verify_signup_rows(expected, live)
    active_required = campaign._verify_signup_rows(
        expected, live, require_active=True)

    assert scheduled_ok["ok"] is True
    assert active_required["ok"] is False
    assert "尚未实际活动中" in active_required["failures"][0]["error"]


def test_builder_allows_custom_price_below_old_twenty_percent_gate(db_session):
    plan = CampaignPlan(
        name="custom-official-line",
        campaign_type="super_reduce",
        tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 30, 23, 59, 59),
        status="draft",
        remark=("custom_placeholder_sku_allowlist=6066017208184; "
                "placeholder_live_prices=6066017208184:284"),
    )
    db_session.add(plan)
    db_session.add(PricingSku(
        product_code="CUSTOM", sku_code="CUSTOM-01", sku="尺寸定制",
        product_name="测试商品", daily_price=Decimal("5000"),
        is_custom_placeholder=True,
    ))
    db_session.add(PricingSkuPromo(
        sku_code="CUSTOM-01", taobao_item_id="1044450741007",
        taobao_sku_id="6066017208184", big_buyer_price=Decimal("4000"),
        coupon_floor_price=Decimal("255"),
        enrolled_floor_price=Decimal("284"),
    ))
    db_session.commit()

    rows, stats = campaign.build_signup_rows(db_session, plan)

    assert rows == [{
        "taobao_item_id": "1044450741007",
        "taobao_sku_id": "6066017208184",
        "sku_code": "CUSTOM-01",
        "price": 284.0,
        "is_placeholder": True,
        "remark": "平台当前定制活动价经官方券后线核验可保留",
    }]
    assert stats["custom_floor_guard_items"] == []
    assert stats["placeholder_price_preserved_by_official_floor"] == [{
        "taobao_item_id": "1044450741007",
        "taobao_sku_id": "6066017208184",
        "sku_code": "CUSTOM-01",
        "activity_price": 284.0,
        "official_coupon_after": 255.0,
        "official_coupon_floor_price": 255.0,
    }]
