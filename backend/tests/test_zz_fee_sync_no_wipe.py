# -*- coding: utf-8 -*-
"""sync_fee_components 只设不清 (2026-07-11 修"清空雷"):
全量: 账单配到才写, 没配到保留现值(手工合并/历史实报不再被抹); 安装恒只填空;
定点(人工改配单/删账单): 保留对齐语义可清; 覆盖/清空前旧值备份进 settings。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
import json
from decimal import Decimal as D

from app.models.finance import LogisticsBill, PackingBill
from app.models.order import Order
from app.models.settings import SystemSetting
from app.services import order_fee_actual_service as svc


def _order(db, no, **kw):
    base = dict(platform="淘宝", order_no=no, qty=1, status="signed", paid_amount=D("3000"))
    base.update(kw)
    o = Order(**base)
    db.add(o)
    db.flush()
    return o


def test_global_sync_keeps_manual_actuals_when_no_bill(db_session):
    """全量: 手工合并的实际费用(无对应账单) → 保留, 不再清 None。"""
    _order(db_session, "M1", actual_packing=D("290"), actual_logistics=D("457"),
           actual_install=D("112"))
    r = svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="M1").one()
    assert (o.actual_packing, o.actual_logistics, o.actual_install) == (D("290"), D("457"), D("112"))
    assert r["kept_manual"] >= 2   # 打包+物流各保留一次


def test_global_sync_sets_bill_values(db_session):
    """全量: 配到账单的单正常写入(多票求和)。"""
    _order(db_session, "B1")
    db_session.add(LogisticsBill(row_type="line", order_no="B1", freight_amount=D("100")))
    db_session.add(LogisticsBill(row_type="line", order_no="B1", freight_amount=D("26")))
    db_session.add(PackingBill(matched_order_no="B1", packing_fee=D("80")))
    db_session.flush()
    svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="B1").one()
    assert o.actual_logistics == D("126")
    assert o.actual_packing == D("80")


def test_global_sync_overwrite_backs_up_old_value(db_session):
    """全量: 账单值覆盖手工旧值(账单=事实源), 但旧值先备份进 fee_sync_prev_values。"""
    _order(db_session, "OV1", actual_logistics=D("100"))
    db_session.add(LogisticsBill(row_type="line", order_no="OV1", freight_amount=D("126")))
    db_session.flush()
    svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="OV1").one()
    assert o.actual_logistics == D("126")
    row = db_session.query(SystemSetting).filter_by(key="fee_sync_prev_values").one()
    assert json.loads(row.value_plain)["OV1"]["actual_logistics"] == "100.00"


def test_scoped_sync_clears_on_unmatch(db_session):
    """定点(人工取消配单/删账单行): 保留对齐语义 → 无账单则清空回退, 且旧值有备份。"""
    _order(db_session, "SC1", actual_logistics=D("126"))
    svc.sync_fee_components(db_session, order_nos=["SC1"])   # 无账单 + 定点 → 清
    o = db_session.query(Order).filter_by(order_no="SC1").one()
    assert o.actual_logistics is None
    row = db_session.query(SystemSetting).filter_by(key="fee_sync_prev_values").one()
    assert json.loads(row.value_plain)["SC1"]["actual_logistics"] == "126.00"


def test_install_fill_only_never_overwrites(db_session):
    """安装恒只填空: 手工合并的 actual_install 不被 install_fee+upstairs 覆盖; 空才填。"""
    _order(db_session, "I1", actual_install=D("239"), install_fee=D("100"))
    _order(db_session, "I2", install_fee=D("130"), upstairs_fee=D("20"))
    svc.sync_fee_components(db_session)
    assert db_session.query(Order).filter_by(order_no="I1").one().actual_install == D("239")
    assert db_session.query(Order).filter_by(order_no="I2").one().actual_install == D("150")


def test_install_falls_back_to_wanshifu_first_install(db_session):
    """万师傅首装兜底 (2026-07-12): 订单自身安装字段全空 → 用已配对+交易成功+非维修的最早
    万师傅单净额; 维修单不算; 手工值仍优先不覆盖。"""
    from datetime import datetime
    from app.models.finance import WanshifuOrder
    _order(db_session, "W1")                                    # 全空 → 兜底
    _order(db_session, "W2", actual_install=D("88"))            # 手工值在 → 不动
    db_session.add(WanshifuOrder(wsf_order_no="WS1", matched_order_no="W1",
                                 status="交易成功", service_type="家具|安装",
                                 net_amount=D("116"), created_time=datetime(2026, 4, 12)))
    db_session.add(WanshifuOrder(wsf_order_no="WS2", matched_order_no="W1",
                                 status="交易成功", service_type="家具|维修",
                                 net_amount=D("50"), created_time=datetime(2026, 4, 1)))   # 维修更早也不取
    db_session.add(WanshifuOrder(wsf_order_no="WS3", matched_order_no="W2",
                                 status="交易成功", service_type="家具|安装",
                                 net_amount=D("300"), created_time=datetime(2026, 4, 2)))
    db_session.flush()
    svc.sync_fee_components(db_session)
    assert db_session.query(Order).filter_by(order_no="W1").one().actual_install == D("116")
    assert db_session.query(Order).filter_by(order_no="W2").one().actual_install == D("88")


def test_scoped_sync_skips_est_refresh(db_session):
    """定点模式不重刷 est_*(与配单无关; 子集中位兜底会把无定价单的 est 误清)。"""
    _order(db_session, "E1", est_packing=D("170"), est_logistics=D("300"),
           actual_logistics=D("50"))
    db_session.add(LogisticsBill(row_type="line", order_no="E1", freight_amount=D("60")))
    db_session.flush()
    r = svc.sync_fee_components(db_session, order_nos=["E1"])
    o = db_session.query(Order).filter_by(order_no="E1").one()
    assert o.est_packing == D("170")        # est 原样(定点不刷)
    assert o.est_logistics == D("300")
    assert o.actual_logistics == D("60")    # 实际照常按账单更新
    assert r["est_set"] == 0


def test_global_sync_idempotent_no_backup_growth(db_session):
    """幂等: 第二次全量跑不再新增写入/备份(值相同直接跳过)。"""
    _order(db_session, "ID1", actual_logistics=D("88"))
    db_session.add(LogisticsBill(row_type="line", order_no="ID1", freight_amount=D("88")))
    db_session.flush()
    r = svc.sync_fee_components(db_session)
    assert r["actual_logistics_set"] == 0   # 已相等, 不写
    assert db_session.query(SystemSetting).filter_by(key="fee_sync_prev_values").count() == 0