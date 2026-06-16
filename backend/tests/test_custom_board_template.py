"""品类参数化板单模板测试 (custom_board_template)。

锁住: 柜/桌/床三族生成的板单部位/数量, + 模板→引擎报价能跑通。
sqlite 内存, 合成数据。
"""
from app.services import custom_board_template as tpl


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def _parts(boards):
    return {b["part"] for b in boards}


def test_cabinet_basic():
    b = tpl.generate_boards("餐厅-餐边柜", 150, depth_cm=40, height_cm=80)
    p = _parts(b)
    assert {"顶板", "底板", "左右侧板", "背板", "门板"} <= p
    top = next(x for x in b if x["part"] == "顶板")
    assert top["length_cm"] == 150 and top["width_cm"] == 40   # 顶板=L×D
    sides = next(x for x in b if x["part"] == "左右侧板")
    assert sides["qty"] == 2 and sides["length_cm"] == 40 and sides["width_cm"] == 80   # 侧板=D×H


def test_drawer_chest_counts():
    b = tpl.generate_boards("斗柜", 80, drawers=5, cols=1)
    face = next(x for x in b if x["part"] == "抽屉面板")
    assert face["qty"] == 5
    side = next(x for x in b if x["part"] == "抽屉侧板")
    assert side["qty"] == 10                                   # 每抽 2 侧


def test_table_family():
    b = tpl.generate_boards("餐厅-餐桌", 140, depth_cm=80, height_cm=75)
    p = _parts(b)
    assert "桌面" in p and "桌腿" in p
    legs = next(x for x in b if x["part"] == "桌腿")
    assert legs["qty"] == 4


def test_bed_family():
    b = tpl.generate_boards("卧室-床", 150)
    p = _parts(b)
    assert "床头板" in p and "铺板条" in p


def test_quote_from_template_runs():
    db = _db()
    r = tpl.quote_from_template(db, "餐边柜", 150, depth_cm=40, height_cm=80)
    assert r["final_price"] > 0
    assert r["factory_quote_compare"] > 0
    assert len(r["generated_boards"]) >= 5
    # 餐边柜 1.5m → 中型人工(配置兜底 1070)
    assert r["labor_fee"] == 1070.0


def test_unknown_category_falls_back():
    b = tpl.generate_boards("未知品类", 100)
    assert len(b) >= 4                                          # 回落餐边柜模板
