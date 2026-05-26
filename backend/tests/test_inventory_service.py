import pytest

from app.models.exception import DataException
from app.models.inventory import PartInventory
from app.models.material import Material
from app.services import inventory_service


def test_add_row_existing_material_no_autocreate(db_session):
    db_session.add(Material(code="AC-0042", name="电力轨道-黑色-1.0", unit="条"))
    db_session.flush()
    result = inventory_service.add_part_row(
        db_session,
        warehouse="江西仓库",
        material_name="电力轨道-黑色-1.0",
        physical_qty=3,
    )
    assert result.material_created is False
    assert result.inventory.material_code == "AC-0042"
    assert result.inventory.physical_qty == 3
    assert result.inventory.unit == "条"  # 继承自物料
    assert db_session.query(DataException).count() == 0


def test_add_row_missing_material_autocreates_and_records_exception(db_session):
    result = inventory_service.add_part_row(
        db_session,
        warehouse="江西仓库",
        material_name="电力轨道-Xpower-T25-黑色-1.358-2插座",
        physical_qty=1,
    )
    assert result.material_created is True
    assert result.material.code == "AC-1000"
    assert result.material.name.startswith("定制")
    assert result.inventory.material_code == "AC-1000"
    assert result.inventory.physical_qty == 1

    excs = db_session.query(DataException).filter_by(source_pk="AC-1000").all()
    assert len(excs) == 1
    assert excs[0].exception_type == "missing_material_autocreated"
    assert excs[0].status == "open"


def test_add_row_reuses_custom_material_on_second_inbound(db_session):
    name = "电力轨道-Xpower-T25-黑色-1.358-2插座"
    r1 = inventory_service.add_part_row(db_session, warehouse="江西仓库", material_name=name, physical_qty=1)
    r2 = inventory_service.add_part_row(db_session, warehouse="江西仓库", material_name=name, physical_qty=2)
    assert r1.material_created is True
    assert r2.material_created is False
    assert r1.material.code == r2.material.code == "AC-1000"
    assert db_session.query(PartInventory).count() == 2
    assert db_session.query(DataException).count() == 1  # 只在首次自动创建时记一条


def test_add_row_requires_warehouse(db_session):
    with pytest.raises(ValueError):
        inventory_service.add_part_row(db_session, warehouse="", material_name="x")


def test_add_row_requires_material_identifier(db_session):
    with pytest.raises(ValueError):
        inventory_service.add_part_row(db_session, warehouse="江西仓库")


def test_add_row_with_existing_code_does_not_autocreate(db_session):
    db_session.add(Material(code="AC-0042", name="电力轨道-黑色-1.0", unit="条"))
    db_session.flush()
    result = inventory_service.add_part_row(
        db_session,
        warehouse="江西仓库",
        material_code="AC-0042",
        physical_qty=5,
    )
    assert result.material_created is False
    assert result.inventory.material_code == "AC-0042"
