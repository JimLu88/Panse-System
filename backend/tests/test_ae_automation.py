"""AE 批自动化测试: 下单图付款条件/作废图幂等 + 售后自动建条 + 补单表 xlsx。"""
from datetime import date, datetime
from decimal import Decimal

import openpyxl

from app.models.finance import WanshifuOrder
from app.models.import_file import ImportedFile
from app.models.marketing import AfterSales
from app.models.order import Order
from app.services import aftersales_auto_service, order_sheet_archive_service as osa
from app.services import bill_import_service as bis
import pytest


@pytest.fixture(autouse=True)
def _mock_render_png(monkeypatch):
    """本机无 wkhtmltoimage(渲染二进制), docker 生产镜像有。下单图/作废图渲染 mock 成字节,
    这些用例测的是流程(付款条件/作废幂等/删原图), 非像素。
    _html_to_png 是底层渲染(render_png 与作废图 generate_void_sheets 都走它), mock 它即全覆盖。"""
    monkeypatch.setattr(osa, "_html_to_png", lambda html, **kw: b"PNG", raising=False)
    monkeypatch.setattr(osa, "render_png", lambda sheet: b"PNG", raising=False)


# -------- 下单图: 只给已付款订单生成 --------

def test_unpaid_order_skipped(db_session):
    db_session.add(Order(platform="淘宝", order_no="AE-UNPAID", qty=1,
                         order_date=date(2026, 6, 8), status="pending_payment"))
    db_session.add(Order(platform="淘宝", order_no="AE-PAID", qty=1,
                         order_date=date(2026, 6, 8), status="paid",
                         paid_amount=Decimal("1000")))
    db_session.flush()

    r = osa.generate_pending(db_session)

    assert "AE-PAID" in r["order_nos"]
    assert "AE-UNPAID" not in r["order_nos"]


# -------- 作废图: 付款+有下单图+退款 → 只一次 + 删原图 --------

def test_void_sheet_once_and_deletes_original(db_session):
    o = Order(platform="淘宝", order_no="AE-VOID", qty=1,
              order_date=date(2026, 6, 8), status="paid", paid_amount=Decimal("800"))
    db_session.add(o)
    db_session.flush()
    osa.generate_pending(db_session)

    def _os_count(no):  # 按解析出的订单号数下单图 (2026-06-19 命名改 {日期}_{单号}.jpg)
        return sum(1 for r in db_session.query(ImportedFile).filter_by(kind="order_sheet").all()
                   if osa._order_no_from_name(r.original_filename) == no)

    assert _os_count("AE-VOID") == 1

    # 重导刷出退款
    o.refund_amount = Decimal("800")
    o.refund_status = "退款成功"
    db_session.flush()

    r1 = osa.generate_void_sheets(db_session)
    assert r1["voided"] == 1
    # 原下单图已删, 作废图已建
    assert _os_count("AE-VOID") == 0
    voids = db_session.query(ImportedFile).filter_by(kind="order_sheet_void").all()
    assert len(voids) == 1
    assert osa._void_order_no_from_name(voids[0].original_filename) == "AE-VOID"

    # 幂等: 第二次跑不再作废
    r2 = osa.generate_void_sheets(db_session)
    assert r2["voided"] == 0


# -------- 售后自动化 ①万师傅 ③退款 --------

def test_aftersales_from_wanshifu_split(db_session):
    """用户拍板 2026-06-12: 安装=固定成本→订单 install_fee; 维修=变动成本→售后条。"""
    db_session.add(Order(platform="淘宝", order_no="T-AE1", qty=1,
                         order_date=date(2026, 6, 1), status="paid",
                         paid_amount=Decimal("1000")))
    db_session.add(WanshifuOrder(
        wsf_order_no="P-AE1", status="交易成功", matched_order_no="T-AE1",
        service_type="家具|安装",
        service_fee=Decimal("78"), finished_time=datetime(2026, 6, 9, 15, 0),
    ))
    db_session.add(WanshifuOrder(
        wsf_order_no="P-AE2", status="交易成功", matched_order_no="T-AE1",
        service_type="家具|维修",
        service_fee=Decimal("50"), finished_time=datetime(2026, 6, 10, 15, 0),
    ))
    db_session.flush()

    assert aftersales_auto_service.create_from_wanshifu(db_session) == 2
    assert aftersales_auto_service.create_from_wanshifu(db_session) == 0  # 幂等

    # 安装 → 订单固定成本
    o = db_session.query(Order).filter_by(order_no="T-AE1").one()
    assert float(o.install_fee) == 78
    # 维修 → 售后条 (变动成本池)
    a = db_session.query(AfterSales).filter_by(platform_order_no="T-AE1").one()
    assert float(a.wanshifu_deduction) == 50
    assert a.reason == "万师傅维修"


def test_aftersales_from_refund(db_session):
    db_session.add(Order(platform="淘宝", order_no="AE-RF", qty=1,
                         order_date=date(2026, 3, 1), status="paid",
                         paid_amount=Decimal("500"), refund_amount=Decimal("500"),
                         refund_status="退款成功", refund_date=date(2026, 3, 5)))
    db_session.flush()

    assert aftersales_auto_service.create_from_refunds(db_session) == 1
    assert aftersales_auto_service.create_from_refunds(db_session) == 0  # 幂等
    a = db_session.query(AfterSales).filter_by(platform_order_no="AE-RF").one()
    assert a.reason == "平台退款"
    assert "500" in (a.remark or "")


# -------- 补单表 xlsx --------

def test_refill_simple_xlsx_import(db_session):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["订单号", "旺旺（淘宝账号非昵称）/JD填写账户", "金额（不要加佣金）", "", "店铺名字"])
    ws.append(["5118702697771020544", "测试旺旺01", 15, 15, "畔色木作"])
    ws.append(["3305859528920016984", "tb测试02", 20, 15, "畔色木作"])

    d = bis.refill_date_from_filename("5.31畔色.xlsx", today=date(2026, 6, 11))
    assert d == date(2026, 5, 31)

    rep = bis.import_refill_simple_xlsx(db_session, wb, refill_date=d,
                                        freight_default=Decimal("5"))
    assert rep.inserted == 2
    # 重复导入跳过
    rep2 = bis.import_refill_simple_xlsx(db_session, wb, refill_date=d,
                                         freight_default=Decimal("5"))
    assert rep2.skipped_duplicate == 2

    from app.models.finance import RefillRecord
    r = db_session.query(RefillRecord).filter_by(order_no="5118702697771020544").one()
    assert float(r.order_amount) == 15
    assert float(r.commission) == 15
    assert float(r.refill_freight) == 5    # 快递费缺省 ¥5 (用户拍板)
    assert r.buyer_nick == "测试旺旺01"
    assert "畔色木作" in (r.fee_remark or "")
