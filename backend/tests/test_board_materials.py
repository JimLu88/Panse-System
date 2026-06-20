"""材质下拉新增「多层板/海洋板」+ 板材取价(优先 2.2cm > 1.8cm/18mm > 其他)测试。

用户拍板 2026-06-20: 报价材质加多层板(取1.8cm¥150不取0.9cm¥100)+ 海洋板(18mm¥100)。
全部 sqlite 内存 + 合成数据。
"""
from decimal import Decimal as D

from app.services import custom_quote_v2_service as v2


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def test_board_materials_in_dropdown():
    db = _db()
    woods = v2.part_options(db)["woods"]
    assert "多层板" in woods and "海洋板" in woods


def test_plywood_price_prefers_18mm_over_9mm():
    from app.models.material import Material
    db = _db()
    db.add(Material(code="MW-A", name="实木多层板0.9cm厚度", price=D("100"), unit="每平米"))
    db.add(Material(code="MW-B", name="实木多层板1.8cm厚度（樱桃木皮）", price=D("150"), unit="每平米"))
    db.commit()
    # 多层板 应取 1.8cm ¥150, 避开 0.9cm 薄背板 ¥100
    assert v2._wood_unit_price(db, "多层板") == 150.0


def test_marine_board_price():
    from app.models.material import Material
    db = _db()
    db.add(Material(code="MW-C", name="海洋板1.8cm", price=D("100"), unit="每平米"))
    db.commit()
    assert v2._wood_unit_price(db, "海洋板") == 100.0


def test_wood_still_prefers_22cm():
    from app.models.material import Material
    db = _db()
    db.add(Material(code="MW-D", name="樱桃木-2.8cm厚度", price=D("520"), unit="每平米"))
    db.add(Material(code="MW-E", name="樱桃木-2.2cm厚度", price=D("300"), unit="每平米"))
    db.commit()
    assert v2._wood_unit_price(db, "樱桃木") == 300.0  # 实木主材 2.2cm 仍优先, 无回归
