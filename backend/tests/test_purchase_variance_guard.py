# -*- coding: utf-8 -*-
"""采购价差体检·名字一致性护栏 (2026-07-10): 流水自动建单被 difflib 猜错编码
("卓晔五金定金"→AC-1006 床头柜侧板) → 价差比对无意义, 当匹配不上, 不报假偏离; 真价差照报。"""
from datetime import date
from decimal import Decimal as D

from app.models.material import Material
from app.models.order import PartPurchase
from app.services import scanner_service as sc


def _mat(db, code, name, price):
    db.add(Material(code=code, name=name, price=D(str(price)), unit="套"))


def _pp(db, pno, mcode, mname, price):
    db.add(PartPurchase(purchase_no=pno, purchase_date=date.today(),
                        material_code=mcode, material_name=mname,
                        qty=D("1"), unit_price=D(str(price)), amount=D(str(price))))


def test_miscoded_purchase_not_flagged(db_session):
    """名字与编码物料毫不相干(猜错的) → 不比价, 不报假偏离。"""
    _mat(db_session, "AC-1006", "榉木床头柜-金属侧板-窄款尺寸", 220)
    _pp(db_session, "P001", "AC-1006", "卓晔五金6-8定金 卓晔五金6-8定金", 4375)
    db_session.commit()
    finds = sc.scan_purchase_price_outliers(db_session)
    assert not [f for f in finds if f.source_pk == "P001"]


def test_correct_coded_purchase_still_flagged(db_session):
    """名字对得上(真是这个料)且价差超阈值 → 照报, 护栏不误伤。"""
    _mat(db_session, "AC-1006", "榉木床头柜-金属侧板-窄款尺寸", 220)
    _pp(db_session, "P002", "AC-1006", "榉木床头柜-金属侧板-窄款尺寸", 300)  # +36%
    db_session.commit()
    finds = sc.scan_purchase_price_outliers(db_session)
    assert [f for f in finds if f.source_pk == "P002"]


def test_checker_resolves_miscoded(db_session):
    """复核器: 错挂编码的旧异常 → 匹配不上 → 可销账(自愈)。"""
    from app.models.exception import DataException
    from app.services import exception_recheck_service as er
    _mat(db_session, "AC-1007", "xpower电力轨道插座-单独", 75)
    _pp(db_session, "P003", "AC-1007", "孙豪电力轨道 孙豪电力轨道", 367)
    exc = DataException(source_table="part_purchases", source_pk="P003",
                        exception_type="purchase_price_variance", severity="warning",
                        description="t", status="open", context={"purchase_no": "P003"})
    db_session.add(exc); db_session.commit()
    assert er.recheck(db_session, exc) is None   # 匹配不上 → 销账
