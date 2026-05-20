from app.models.exception import DataException
from app.models.material import Material
from app.services import material_service


def test_ensure_existing_returns_same_record(db_session):
    db_session.add(Material(code="AC-0042", name="电力轨道-黑色-1.0", unit="条"))
    db_session.flush()
    res = material_service.ensure_by_name(db_session, "电力轨道-黑色-1.0")
    assert res.created is False
    assert res.material.code == "AC-0042"
    assert db_session.query(DataException).count() == 0


def test_ensure_missing_creates_custom_with_dingzhi_prefix(db_session):
    res = material_service.ensure_by_name(db_session, "电力轨道-黑色-1.358")
    assert res.created is True
    assert res.material.code == "AC-1000"
    assert res.material.name == "定制电力轨道-黑色-1.358"
    assert res.material.is_custom is True
    # 价格/单位/尺寸类型按确认策略：全部留空
    assert res.material.price is None
    assert res.material.unit is None
    assert res.material.size_type is None
    # 应写入异常表
    excs = db_session.query(DataException).all()
    assert len(excs) == 1
    assert excs[0].source_table == "materials"
    assert excs[0].source_pk == "AC-1000"
    assert excs[0].exception_type == "missing_material_autocreated"


def test_ensure_missing_preserves_dingzhi_prefix_if_already_present(db_session):
    res = material_service.ensure_by_name(db_session, "定制电力轨道-X")
    assert res.created is True
    assert res.material.name == "定制电力轨道-X"  # 不重复加前缀
    assert res.material.code == "AC-1000"


def test_ensure_increments_across_calls(db_session):
    r1 = material_service.ensure_by_name(db_session, "定制电力轨道-1.358")
    r2 = material_service.ensure_by_name(db_session, "定制电力轨道-1.658")
    r3 = material_service.ensure_by_name(db_session, "定制电力轨道-1.958")
    assert [r1.material.code, r2.material.code, r3.material.code] == ["AC-1000", "AC-1001", "AC-1002"]


def test_ensure_with_existing_custom_picks_next(db_session):
    db_session.add(Material(code="AC-1005", name="定制电力轨道-2.0", is_custom=True))
    db_session.flush()
    res = material_service.ensure_by_name(db_session, "定制电力轨道-2.5")
    assert res.material.code == "AC-1006"


def test_ensure_empty_name_raises(db_session):
    import pytest
    with pytest.raises(ValueError):
        material_service.ensure_by_name(db_session, "")
    with pytest.raises(ValueError):
        material_service.ensure_by_name(db_session, "   ")
