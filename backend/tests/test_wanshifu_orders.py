"""万师傅安装订单档案: 解析 + 导入幂等 + 订单配对 测试。"""
from datetime import date

import openpyxl

from app.models.finance import WanshifuOrder
from app.models.order import Order
from app.services import wanshifu_order_service as wsf


def _make_wb():
    """合成 38 列格式最小版: r1 分组 / r2 字段名 / r3 地址子表头 / r4+ 数据。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["订单信息", "", "", "", "", "", "", "", "客户信息"])
    ws.append(["订单编号", "服务类目/类型", "订单状态", "商品类别", "客户姓名",
               "客户手机号", "客户地址", "", "", "", "订单服务费", "下单时间",
               "物流公司", "物流单号"])
    ws.append(["", "", "", "", "", "", "省", "市", "区", "详细地址", "", "", "", ""])
    ws.append(["P100", "家具|安装", "交易成功", "桌类-餐台/餐桌", "张三测",
               "13800000001-1234", "浙江", "杭州", "西湖区", "某街道1号",
               "78", "2026-05-10 12:00:00", "", ""])
    ws.append(["P101", "家具|安装", "交易关闭（自动关单）", "柜类-餐边柜", "李四测",
               "13900000002", "江苏", "苏州", "园区", "某路2号",
               "", "2025-12-01 09:00:00", "", ""])
    return wb


def test_parse_and_import_idempotent(db_session):
    rep = wsf.import_workbook(db_session, _make_wb())
    assert rep.parsed == 2 and rep.inserted == 2

    # 重导: 不重复插入
    rep2 = wsf.import_workbook(db_session, _make_wb())
    assert rep2.inserted == 0
    rows = db_session.query(WanshifuOrder).all()
    assert len(rows) == 2
    w = next(r for r in rows if r.wsf_order_no == "P100")
    assert w.city == "杭州" and w.customer_phone == "13800000001-1234"
    assert float(w.service_fee) == 78


def test_match_phone_and_unmatched_reasons(db_session):
    wsf.import_workbook(db_session, _make_wb())
    # 订单电话与 P100 全等 → phone_full 配对
    db_session.add(Order(platform="淘宝", order_no="T-001", qty=1,
                         customer_name="张三测", customer_phone="13800000001-1234",
                         order_date=date(2026, 5, 1)))
    db_session.flush()

    counts = wsf.match_orders(db_session)

    assert counts["matched"] == 1
    w100 = db_session.query(WanshifuOrder).filter_by(wsf_order_no="P100").one()
    assert w100.matched_order_no == "T-001"
    assert w100.match_method == "phone_full"
    # P101 是 2025 年单 → none + 人话原因
    w101 = db_session.query(WanshifuOrder).filter_by(wsf_order_no="P101").one()
    assert w101.match_method == "none"
    assert "2025" in (w101.match_note or "")
