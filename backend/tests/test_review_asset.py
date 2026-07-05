"""评价资产台账 service 测试 (Plan1 v2, 只加不改现有)。

覆盖: fold 计算/settings 重算、from-order 幂等+source、导入行级幂等+invalid、
run_daily_scan 分级(30·14·7)+状态流转+info 防刷屏+终态不动、待评价超时、coverage。
"""
from datetime import date, timedelta

import openpyxl

from app.models.order import Order
from app.services import review_asset_service as svc


def _mk_order(db, order_no="TB1", is_refill=True, product_code="M8812",
              sku_code="洞石/1.2m", shop="旗舰店", ship_date=None):
    o = Order(order_no=order_no, platform="淘宝", is_refill=is_refill,
              product_code=product_code, sku_code=sku_code, shop=shop,
              ship_date=ship_date, status="signed", qty=1)
    db.add(o)
    db.flush()
    return o


def test_compute_fold_due():
    assert svc.compute_fold_due(date(2026, 1, 1), 180) == date(2026, 1, 1) + timedelta(days=180)
    assert svc.compute_fold_due(None, 180) is None


def test_level_for_days():
    assert svc._level_for_days(-3) == "error"
    assert svc._level_for_days(5) == "error"
    assert svc._level_for_days(12) == "warn"
    assert svc._level_for_days(25) == "info"
    assert svc._level_for_days(40) is None


def test_from_order_source_refill_idempotent(db_session):
    o = _mk_order(db_session, is_refill=True)
    ra, created = svc.from_order(db_session, o.id)
    assert created is True
    assert ra.source == "refill"
    assert ra.status == "pending_review"
    ra2, created2 = svc.from_order(db_session, o.id)
    assert created2 is False
    assert ra2.id == ra.id


def test_from_order_source_natural(db_session):
    o = _mk_order(db_session, order_no="TB2", is_refill=False)
    ra, _ = svc.from_order(db_session, o.id)
    assert ra.source == "natural"


def test_create_manual_with_review_date_sets_fold(db_session):
    _mk_order(db_session, order_no="TB3")
    ra = svc.create_manual(db_session, order_no="TB3", review_date=date(2026, 5, 12),
                           image_count=3, rating=5)
    assert ra.status == "reviewed"
    assert ra.fold_due_date == date(2026, 5, 12) + timedelta(days=180)
    assert ra.source == "refill"  # 关联订单 is_refill=True


def test_create_manual_pending_when_no_review_date(db_session):
    ra = svc.create_manual(db_session, order_no="TBX")  # 无订单也能建
    assert ra.status == "pending_review"
    assert ra.fold_due_date is None
    assert ra.source == "refill"  # 无订单默认 refill


def test_import_rows_idempotent_and_unlinked(db_session):
    _mk_order(db_session, order_no="TB10")

    def _wb():
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(svc.TEMPLATE_HEADERS)
        ws.append(["TB10", "2026-05-01", 2, 5, "好评", "", "", "备注"])
        ws.append(["TB11", "2026-05-02", 1, 5, "不错", "M9901", "色/1m", ""])  # 订单不在库
        return wb

    rep = svc.import_rows(db_session, _wb())
    assert rep.inserted == 2
    assert rep.unlinked == 1  # TB11 无订单
    # 再导入同表 → 行级幂等 (order_no+review_date) 全跳过
    rep2 = svc.import_rows(db_session, _wb())
    assert rep2.inserted == 0
    assert rep2.skipped_duplicate == 2


def test_import_skips_invalid_and_blank(db_session):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(svc.TEMPLATE_HEADERS)
    ws.append(["", "2026-05-01", 2, 5, "缺订单号", "", "", ""])  # invalid
    ws.append([None, None, None, None, None, None, None, None])  # 空行, 不计
    ws.append(["TB99", "2026-05-03", 1, 5, "ok", "", "", ""])
    rep = svc.import_rows(db_session, wb)
    assert rep.inserted == 1
    assert rep.skipped_invalid == 1


def test_update_settings_recompute_fold(db_session):
    _mk_order(db_session, order_no="TB20")
    ra = svc.create_manual(db_session, order_no="TB20", review_date=date(2026, 5, 12))
    assert ra.fold_due_date == date(2026, 5, 12) + timedelta(days=180)
    svc.update_settings(db_session, fold_days=90)
    db_session.refresh(ra)
    assert ra.fold_due_date == date(2026, 5, 12) + timedelta(days=90)


def _mk_reviewed(db, order_no, days_left, today, image_count=1):
    """造一条 reviewed 行, 使 fold_due_date = today + days_left。"""
    review_date = today - timedelta(days=180) + timedelta(days=days_left)
    return svc.create_manual(db, order_no=order_no, review_date=review_date, image_count=image_count)


def test_run_daily_scan_levels_transitions_and_dedup(db_session):
    today = date(2026, 6, 1)
    _mk_reviewed(db_session, "TBE", 5, today)
    ra_warn = _mk_reviewed(db_session, "TBW", 12, today)
    _mk_reviewed(db_session, "TBI", 25, today)
    ra_over = _mk_reviewed(db_session, "TBO", -3, today)

    res = svc.run_daily_scan(db_session, today=today)
    assert len(res["fold_notify"]["error"]) == 2   # 剩5天 + 逾期3天
    assert len(res["fold_notify"]["warn"]) == 1
    assert len(res["fold_notify"]["info"]) == 1
    assert res["max_level"] == "error"

    db_session.refresh(ra_over)
    assert ra_over.status == "folded"
    db_session.refresh(ra_warn)
    assert ra_warn.status == "folding_soon"

    # info 防刷屏: 再扫 info 不再进清单; warn/error 每日仍推
    res2 = svc.run_daily_scan(db_session, today=today)
    assert len(res2["fold_notify"]["info"]) == 0
    assert len(res2["fold_notify"]["warn"]) == 1
    assert len(res2["fold_notify"]["error"]) == 2


def test_run_daily_scan_terminal_untouched(db_session):
    today = date(2026, 6, 1)
    ra = _mk_reviewed(db_session, "TBR", -5, today)  # 已逾期
    ra.status = "released"  # 终态
    db_session.flush()
    res = svc.run_daily_scan(db_session, today=today)
    db_session.refresh(ra)
    assert ra.status == "released"  # 终态不动
    assert all(x["id"] != ra.id for x in res["fold_notify"]["error"])


def test_pending_overdue(db_session):
    today = date.today()
    o = _mk_order(db_session, order_no="TBP", ship_date=today - timedelta(days=12))
    ra, _ = svc.from_order(db_session, o.id)  # pending_review
    rows = svc.pending_overdue_rows(db_session, 10)
    assert any(r[0].id == ra.id for r in rows)
    # 未超时的不算
    o2 = _mk_order(db_session, order_no="TBP2", ship_date=today - timedelta(days=3))
    ra2, _ = svc.from_order(db_session, o2.id)
    rows2 = svc.pending_overdue_rows(db_session, 10)
    assert all(r[0].id != ra2.id for r in rows2)


def test_coverage_below_min(db_session):
    _mk_order(db_session, order_no="TBC1")
    svc.create_manual(db_session, order_no="TBC1", review_date=date(2026, 5, 1),
                      image_count=2, product_code="M8812")
    cov = svc.coverage(db_session)
    m = [c for c in cov if c["product_code"] == "M8812"][0]
    assert m["active_image_reviews"] == 1
    assert m["below_min"] is True


def test_format_reminder_smoke(db_session):
    today = date(2026, 6, 1)
    _mk_reviewed(db_session, "TBF", 5, today)
    res = svc.run_daily_scan(db_session, today=today)
    msg = svc.format_reminder(res)
    assert "评价资产日报" in msg
    assert "折叠" in msg
