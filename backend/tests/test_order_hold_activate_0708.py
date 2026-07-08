"""远期单挂起 + 激活才推(新单号) + 老单激活重推 (2026-07-08)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.services import import_storage
from app.services import order_flags
from app.services import order_sheet_archive_service as oss


def _order(**kw):
    base = dict(platform="淘宝", qty=1, order_date=date.today(),
                status="signed", paid_amount=Decimal("100"))
    base.update(kw)
    return Order(**base)


# ---- order_flags 判词 ----

def test_activated_true_on_kw():
    assert order_flags.is_activated(_order(order_no="A1", remark="客户说开始制作")) is True


def test_activated_false_when_negated():
    assert order_flags.is_activated(_order(order_no="A2", remark="先不做, 等通知")) is False


def test_remote_true_by_flag():
    assert order_flags.is_remote(_order(order_no="A3", is_remote_ship=True)) is True


def test_remote_true_by_keyword():
    assert order_flags.is_remote(_order(order_no="A4", remark="装修好了再发")) is True


def test_remote_false_when_activated_overrides():
    o = _order(order_no="A5", is_remote_ship=True, remark="现在可以开始制作了")
    assert order_flags.is_activated(o) is True
    assert order_flags.is_remote(o) is False


def test_remote_false_for_normal():
    assert order_flags.is_remote(_order(order_no="A6")) is False


# ---- generate_pending 跳过挂起远期单 ----

def test_generate_pending_skips_held_remote(db_session, monkeypatch):
    db_session.add(_order(order_no="HELD", is_remote_ship=True))                     # 远期挂起
    db_session.add(_order(order_no="NORMAL"))                                        # 普通
    db_session.add(_order(order_no="ACT", is_remote_ship=True, remark="开始制作"))    # 远期但已激活
    db_session.commit()
    seen: list = []
    monkeypatch.setattr(oss, "generate_for_order",
                        lambda db, o, **k: (seen.append(o.order_no),
                                            {"order_no": o.order_no, "duplicate": False})[1])
    oss.generate_pending(db_session)
    assert "HELD" not in seen        # 远期挂起 → 不生成
    assert "NORMAL" in seen          # 普通 → 生成
    assert "ACT" in seen             # 远期但已激活 → 生成(要推)


# ---- repush_activated: 远期老单激活 → 清号(以新号重推) ----

def test_repush_activated_resets_legacy(db_session, monkeypatch):
    monkeypatch.setattr(import_storage, "delete_record",
                        lambda db, fid: db.delete(db.get(ImportedFile, fid)))
    o = _order(order_no="LEG", is_remote_ship=True, remark="现在开始制作", factory_no=200)
    db_session.add(o)
    db_session.add(ImportedFile(
        kind="order_sheet", original_filename=f"{date.today().isoformat()}_LEG.jpg",
        stored_path="/x/LEG.jpg", row_summary={"pushed": True}))   # 老图: pushed 但无 activated 标记
    db_session.commit()
    res = oss.repush_activated(db_session)
    assert "LEG" in res["reset_for_new_no"]
    db_session.refresh(o)
    assert o.factory_no is None       # 清了工厂号 → 会拿新号重推
    left = db_session.query(ImportedFile).filter(
        ImportedFile.original_filename.like("%LEG%")).count()
    assert left == 0                  # 旧下单图归档已删


def test_repush_activated_skips_normal_and_already_activated(db_session, monkeypatch):
    monkeypatch.setattr(import_storage, "delete_record",
                        lambda db, fid: db.delete(db.get(ImportedFile, fid)))
    # 普通单(非远期)加了"开始制作" → 不该重编号
    db_session.add(_order(order_no="NORM2", remark="开始制作", factory_no=201))
    db_session.add(ImportedFile(kind="order_sheet", original_filename=f"{date.today().isoformat()}_NORM2.jpg",
                                stored_path="/x/NORM2.jpg", row_summary={"pushed": True}))
    # 已是激活态推过的远期单 → 不重复
    db_session.add(_order(order_no="AL2", is_remote_ship=True, remark="开始制作", factory_no=202))
    db_session.add(ImportedFile(kind="order_sheet", original_filename=f"{date.today().isoformat()}_AL2.jpg",
                                stored_path="/x/AL2.jpg", row_summary={"pushed": True, "activated": True}))
    db_session.commit()
    res = oss.repush_activated(db_session)
    assert "NORM2" not in res["reset_for_new_no"]   # 普通单不动
    assert "AL2" not in res["reset_for_new_no"]      # 已激活态推过, 不重复
