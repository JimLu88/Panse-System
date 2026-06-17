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


def _wb_with_remark():
    """带「常用备注」列(=淘宝订单号, 用户的合并单号匹配)的最小 wb。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["订单信息"])
    ws.append(["订单编号", "服务类目/类型", "订单状态", "商品类别", "客户姓名",
               "客户手机号", "客户地址", "", "", "", "订单服务费", "下单时间",
               "物流公司", "物流单号", "常用备注"])
    ws.append(["", "", "", "", "", "", "省", "市", "区", "详细地址", "", "", "", "", ""])
    ws.append(["P200", "家具|安装", "交易成功", "桌类-餐台/餐桌", "王五测",
               "13700000003", "广东", "深圳", "南山区", "某路3号",
               "68", "2026-05-12 10:00:00", "", "", "3016793965259618396"])
    ws.append(["P201", "家具|安装", "交易成功", "柜类-餐边柜", "赵六测",
               "13600000004", "北京", "北京", "海淀区", "某街4号",
               "88", "2026-05-13 11:00:00", "", "", "9999999999999999999"])  # 备注单号不在订单库
    return wb


def test_remark_taobao_no_is_authoritative_match(db_session):
    """常用备注里的淘宝订单号 = 用户"合并单号匹配", 命中订单库即权威配对(method=remark)。"""
    # 订单库里有 P200 备注指向的淘宝单, 没有 P201 的
    db_session.add(Order(platform="淘宝", order_no="3016793965259618396", qty=1,
                         customer_name="某客户", order_date=date(2026, 5, 1)))
    db_session.flush()

    wsf.import_workbook(db_session, _wb_with_remark())

    w200 = db_session.query(WanshifuOrder).filter_by(wsf_order_no="P200").one()
    assert w200.matched_order_no == "3016793965259618396"
    assert w200.match_method == "remark"
    assert "淘宝单号:3016793965259618396" in (w200.remark or "")

    # P201 备注单号不在订单库 → 不强配, 但留备注提示
    w201 = db_session.query(WanshifuOrder).filter_by(wsf_order_no="P201").one()
    assert w201.matched_order_no is None
    assert "未在订单库" in (w201.match_note or "")


def test_remark_match_overrides_heuristic_on_reimport(db_session):
    """备注单号权威: 即便之前被启发式配过, 重导也以备注为准(非人工)。"""
    db_session.add(Order(platform="淘宝", order_no="3016793965259618396", qty=1,
                         customer_name="某客户", order_date=date(2026, 5, 1)))
    db_session.flush()
    wsf.import_workbook(db_session, _wb_with_remark())
    w = db_session.query(WanshifuOrder).filter_by(wsf_order_no="P200").one()
    w.matched_order_no = "WRONG-001"
    w.match_method = "name_unique"
    db_session.flush()
    # 重导 → 备注覆盖
    wsf.import_workbook(db_session, _wb_with_remark())
    w = db_session.query(WanshifuOrder).filter_by(wsf_order_no="P200").one()
    assert w.matched_order_no == "3016793965259618396"
    assert w.match_method == "remark"
