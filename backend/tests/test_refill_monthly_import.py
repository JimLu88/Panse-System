# -*- coding: utf-8 -*-
"""月度补单汇总表导入 (用户 2026-06 起统一格式: 分月核算/补单流水, 无订单号)。"""
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import select

from app.models.finance import RefillRecord
from app.models.order import Order
from app.services import bill_import_service as bis


def _monthly_wb():
    """构造一份「补单记录」月度汇总表 (含合计/空月份/边界行, 模拟真实文件)。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "补单记录"
    ws.append(["合计项目"])  # 标题行
    ws.append(["分月核算", "补单流水", "补单佣金", "补单快递费",
               "平台服务费", "88vip消费券技术服务费"])  # 真实表头(第2行)
    ws.append(["2025年1月", 66890.65, 901.62, 300, 401.34, 0])
    ws.append(["2025年6月", 67082.87, 1530, 505, 402.81, 460.77])
    ws.append(["2026年6月", 0, 0, 0, 0, 0])          # 空月份 → 跳过
    ws.append(["<2025/1/1", None, 0, 0, 0, None])     # 边界行 → 跳过
    ws.append(["Total", 790022, 22927.62, 7565, 4648.98, 598.97])  # 合计 → 跳过
    return wb


def test_detects_monthly_refill_format():
    assert bis.is_monthly_refill_xlsx(_monthly_wb()) is True


def test_import_monthly_refill_creates_one_row_per_month(db_session):
    db = db_session
    rep = bis.import_refill_monthly_xlsx(db, _monthly_wb())
    assert rep.inserted == 2  # 只有两个有流水的月份, 空月/合计/边界都跳过

    rows = {r.order_no: r for r in db.execute(select(RefillRecord)).scalars().all()}
    assert set(rows) == {"补单月度-2025-01", "补单月度-2025-06"}

    jan = rows["补单月度-2025-01"]
    assert jan.refill_date == date(2025, 1, 1)
    assert jan.order_amount == Decimal("66890.65")
    assert jan.commission == Decimal("901.62")
    assert jan.refill_freight == Decimal("300")
    assert jan.platform_fee == Decimal("401.34")
    assert jan.remark and "月度补单汇总" in jan.remark

    jun = rows["补单月度-2025-06"]
    assert jun.fee_remark and "88vip技术服务费" in jun.fee_remark  # 技术服务费记备注


def test_reimport_is_idempotent_upsert(db_session):
    """重导整表不应重复堆积 — 同月覆盖。"""
    db = db_session
    bis.import_refill_monthly_xlsx(db, _monthly_wb())
    bis.import_refill_monthly_xlsx(db, _monthly_wb())
    n = db.execute(select(RefillRecord)).scalars().all()
    assert len([r for r in n if r.order_no.startswith("补单月度-")]) == 2


def test_simple_xlsx_entry_delegates_to_monthly(db_session):
    """旧的逐单入口对月度表应自动转月度路径, 不再报「缺订单号」。"""
    db = db_session
    rep = bis.import_refill_simple_xlsx(db, _monthly_wb(), refill_date=date(2026, 6, 1))
    assert rep.inserted == 2
    assert not rep.errors


# ---------------- 补单流水明细 (逐单, 真实文件主数据) ---------------- #
def _detail_wb():
    """构造「补单记录.xlsx」结构: 上半月度汇总 + 下半逐单明细。"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "补单记录"
    ws.append(["合计项目"])
    ws.append(["分月核算", "补单流水", "补单佣金", "补单快递费", "平台服务费", "88vip消费券技术服务费"])
    ws.append(["2025年1月", 66890.65, 901.62, 300, 401.34, 0])
    for _ in range(5):
        ws.append([])
    ws.append(["补单流水明细", None, "*标红色订单被查"])
    ws.append(["支付时间", "补单团队", "订单号", "买家昵称", "打款日期", "打款金额",
               "是否回款", "回款金额", "check", "补单佣金", "补单快递费",
               "平台服务费", "88vip消费券技术服务费", "备注"])
    ws.append(["2025-01-01", "水冰月", "4187154529820670323", "太阳花3512", "2025-01-01",
               943.93, "是", 943.93, 0, 15, 5, 5.66, 0, None])
    ws.append(["2025-01-02", "水冰月", "2427781476059576866", "林彩霞", "2025-01-02",
               2771.33, "是", 2771.33, 0, 16.62, 5, 16.62, 0, None])
    ws.append(["合计", None, None, None, None, 3715, None, None, None, 31, 10])  # 非订单行→跳过
    return wb


def test_detects_detail_format_preferred_over_monthly():
    wb = _detail_wb()
    assert bis.is_refill_detail_xlsx(wb) is True


def test_import_detail_creates_one_row_per_order_and_flags_orders(db_session):
    db = db_session
    db.add(Order(platform="淘宝", order_no="4187154529820670323", status="paid",
                 paid_amount=Decimal("900")))
    db.flush()

    rep = bis.import_refill_simple_xlsx(db, _detail_wb(), refill_date=date(2026, 6, 1))
    assert rep.inserted == 2  # 两个真实订单, 合计行被跳过
    assert not rep.errors

    rows = {r.order_no: r for r in db.execute(select(RefillRecord)).scalars().all()}
    assert set(rows) == {"4187154529820670323", "2427781476059576866"}
    r0 = rows["4187154529820670323"]
    assert r0.buyer_nick == "太阳花3512"
    assert r0.refill_date == date(2025, 1, 1)
    assert r0.order_amount == Decimal("943.93")
    assert r0.commission == Decimal("15")
    assert r0.refill_freight == Decimal("5")
    assert r0.fee_remark and "团队:水冰月" in r0.fee_remark

    # 命中订单被标 is_refill (从而不当真实销售/缺成本异常)
    o = db.execute(select(Order).where(Order.order_no == "4187154529820670323")).scalar_one()
    assert o.is_refill is True


def test_detail_reimport_is_idempotent(db_session):
    db = db_session
    bis.import_refill_simple_xlsx(db, _detail_wb(), refill_date=date(2026, 6, 1))
    bis.import_refill_simple_xlsx(db, _detail_wb(), refill_date=date(2026, 6, 1))
    n = db.execute(select(RefillRecord)).scalars().all()
    assert len(n) == 2  # 重导覆盖, 不堆积
