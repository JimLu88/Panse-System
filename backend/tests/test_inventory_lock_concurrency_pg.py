# -*- coding: utf-8 -*-
"""Plan C6: 库存锁定并发测试 (PostgreSQL only)。

验证 _get_or_create_inventory 的 SELECT ... FOR UPDATE 行锁:
两线程同物料并发 lock_for_factory_order, locked_qty 不丢失更新, ledger 各记一条。
sqlite 内存库无真并发语义 → skip (CI 的 backend-postgres job 自动覆盖)。
"""
import threading
from decimal import Decimal

import pytest

from app.config import get_settings

_DB_URL = get_settings().database_url

pytestmark = pytest.mark.skipif(
    not _DB_URL.startswith("postgresql"),
    reason="并发行锁只在 PostgreSQL 生效, sqlite 跳过",
)

_CODE = "AC-C6TEST"
_PRODUCT = "P-C6TEST"


def _cleanup(s):
    from app.models.alert import Alert
    from app.models.bom import BomLine
    from app.models.inventory import PartInventory
    from app.models.inventory_lock import InventoryLockLedger
    from app.models.material import Material
    from app.models.order import FactoryOrder
    # 缺料告警也清掉 — 否则测试每跑一次, 生产页面就冒一条 AC-C6TEST 缺货横幅
    s.query(Alert).filter(
        Alert.dedupe_key.like(f"low_stock_part:{_CODE}%")).delete(synchronize_session=False)
    s.query(InventoryLockLedger).filter(
        InventoryLockLedger.material_code == _CODE).delete(synchronize_session=False)
    s.query(BomLine).filter(BomLine.product_code == _PRODUCT).delete(synchronize_session=False)
    s.query(PartInventory).filter(PartInventory.material_code == _CODE).delete(synchronize_session=False)
    s.query(FactoryOrder).filter(FactoryOrder.factory_order_no.like("C6T%")).delete(synchronize_session=False)
    s.query(Material).filter(Material.code == _CODE).delete(synchronize_session=False)


@pytest.fixture()
def pg_sessionmaker():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(_DB_URL, future=True, pool_size=5)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    yield Session
    s = Session()
    try:
        _cleanup(s)
        s.commit()
    finally:
        s.close()
    engine.dispose()


def _seed(Session, *, with_inventory: bool) -> list:
    """造数: 物料 + BOM + 两张工厂单 (可选预置库存行)。返回工厂单 id。"""
    from app.models.bom import BomLine
    from app.models.inventory import PartInventory
    from app.models.material import Material
    from app.models.order import FactoryOrder
    from app.services.inventory_lock_service import DEFAULT_WAREHOUSE
    setup = Session()
    try:
        _cleanup(setup)
        setup.add(Material(code=_CODE, name="并发测试件C6", price=Decimal("1")))
        setup.flush()   # 先落 Material, 满足 bom_lines.material_code 外键
        if with_inventory:
            setup.add(PartInventory(material_code=_CODE, warehouse=DEFAULT_WAREHOUSE,
                                    physical_qty=Decimal("100"), locked_qty=Decimal("0")))
        setup.add(BomLine(product_code=_PRODUCT, sku_code="SKU-C6TEST",
                          material_code=_CODE, qty_per_product=Decimal("3")))
        fo_ids = []
        for i in range(2):
            fo = FactoryOrder(factory_order_no=f"C6T{i}", product_code=_PRODUCT, qty=2)
            setup.add(fo)
            setup.flush()
            fo_ids.append(fo.id)
        setup.commit()
        return fo_ids
    finally:
        setup.close()


def _run_concurrent_locks(Session, fo_ids: list) -> list:
    from app.services import inventory_lock_service
    errors: list = []

    def _lock(fo_id: int):
        s = Session()
        try:
            inventory_lock_service.lock_for_factory_order(s, fo_id, actor=f"c6-{fo_id}")
            s.commit()
        except Exception as e:  # noqa: BLE001 - 收集线程内异常供主线程断言
            s.rollback()
            errors.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=_lock, args=(fid,)) for fid in fo_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return errors


def _assert_single_row_locked_12(Session):
    from app.models.inventory import PartInventory
    from app.models.inventory_lock import InventoryLockLedger
    verify = Session()
    try:
        rows = verify.query(PartInventory).filter_by(material_code=_CODE).all()
        assert len(rows) == 1, f"应只有一条库存行, 实际 {len(rows)} 条 (并发建行竞态)"
        # 2 张工厂单 × qty 2 × 单耗 3 = 12; 行锁保证两次 += 6 都生效
        assert Decimal(rows[0].locked_qty) == Decimal("12"), \
            f"locked_qty 丢失更新: {rows[0].locked_qty} != 12"
        ledger_rows = verify.query(InventoryLockLedger).filter_by(
            material_code=_CODE, kind="lock").all()
        assert len(ledger_rows) == 2
        assert sum(Decimal(r.qty) for r in ledger_rows) == Decimal("12")
    finally:
        verify.close()


def test_concurrent_lock_no_lost_update(pg_sessionmaker):
    """已有库存行: SELECT FOR UPDATE 行锁防丢失更新。"""
    fo_ids = _seed(pg_sessionmaker, with_inventory=True)
    errors = _run_concurrent_locks(pg_sessionmaker, fo_ids)
    assert not errors, f"并发锁定线程报错: {errors}"
    _assert_single_row_locked_12(pg_sessionmaker)


def test_concurrent_first_lock_single_row(pg_sessionmaker):
    """无库存行 (首锁): get-or-create 竞态被唯一键+SAVEPOINT 兜住, 只产生一行。"""
    fo_ids = _seed(pg_sessionmaker, with_inventory=False)
    errors = _run_concurrent_locks(pg_sessionmaker, fo_ids)
    assert not errors, f"并发首锁线程报错: {errors}"
    _assert_single_row_locked_12(pg_sessionmaker)
