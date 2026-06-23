"""cost_exceeds_paid 自动关闭根治 (2026-06-23 用户拍板「异常处理根治」)。

治本「关了又冒 / 僵尸告警」: 订单成本已修正/已取消/实付已补齐 → 旧 open 告警自动销账;
被人工标 ignored 的订单不再重报; 仍真错配的继续报(不能误关真问题)。
全 sqlite 内存 + 合成数据, 不碰生产。
"""
from decimal import Decimal as D

from app.services import data_quality_service as dq
from app.models.exception import DataException
from app.models.order import Order


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def _order(db, oid, order_no, paid, theoretical=None, actual=None,
           status="signed", name="樱桃木咖啡柜", refill=False):
    o = Order(id=oid, platform="taobao", order_no=order_no, status=status, is_refill=refill,
              paid_amount=D(str(paid)),
              theoretical_cost=(D(str(theoretical)) if theoretical is not None else None),
              actual_cost=(D(str(actual)) if actual is not None else None),
              product_name=name)
    db.add(o)
    db.commit()
    return o


def _opens(db):
    return db.query(DataException).filter(
        DataException.exception_type == "cost_exceeds_paid",
        DataException.status == "open").all()


def test_mismatch_creates_exception():
    db = _db()
    _order(db, 1, "O1", paid=100, theoretical=4631.83)
    n = dq.scan_cost_exceeds_paid(db); db.commit()
    assert n == 1
    assert len(_opens(db)) == 1


def test_fixed_cost_autocloses():
    # 报了之后把成本归零 → 再扫描自动关闭, 且不再新建
    db = _db()
    o = _order(db, 1, "O1", paid=100, theoretical=4631.83)
    dq.scan_cost_exceeds_paid(db); db.commit()
    assert len(_opens(db)) == 1
    o.actual_cost = D("0"); o.theoretical_cost = D("0"); db.commit()
    n = dq.scan_cost_exceeds_paid(db); db.commit()
    assert n == 0
    assert len(_opens(db)) == 0
    ex = db.query(DataException).filter_by(exception_type="cost_exceeds_paid").first()
    assert ex.status == "resolved" and ex.resolved_by == "auto"


def test_cancelled_autocloses():
    db = _db()
    o = _order(db, 1, "O1", paid=100, theoretical=4631.83)
    dq.scan_cost_exceeds_paid(db); db.commit()
    o.status = "cancelled"; db.commit()
    dq.scan_cost_exceeds_paid(db); db.commit()
    assert len(_opens(db)) == 0


def test_paid_caught_up_autocloses():
    # 实付后来涨过成本 → 自动关
    db = _db()
    o = _order(db, 1, "O1", paid=100, actual=5000)
    dq.scan_cost_exceeds_paid(db); db.commit()
    assert len(_opens(db)) == 1
    o.paid_amount = D("7748"); db.commit()
    dq.scan_cost_exceeds_paid(db); db.commit()
    assert len(_opens(db)) == 0


def test_ignored_not_recreated():
    # 人工标 ignored → 不再重报, ignored 记录保留
    db = _db()
    _order(db, 1, "O1", paid=100, theoretical=4631.83)
    dq.scan_cost_exceeds_paid(db); db.commit()
    ex = db.query(DataException).filter_by(exception_type="cost_exceeds_paid").first()
    ex.status = "ignored"; db.commit()
    n = dq.scan_cost_exceeds_paid(db); db.commit()
    assert n == 0
    assert len(_opens(db)) == 0
    assert db.query(DataException).filter_by(status="ignored").count() == 1


def test_still_mismatched_keeps_firing():
    # 没修的真错配 → 仍然报, 不误关 (幂等: 重扫仍只1条 open)
    db = _db()
    _order(db, 1, "O1", paid=100, theoretical=4631.83)
    dq.scan_cost_exceeds_paid(db); db.commit()
    dq.scan_cost_exceeds_paid(db); db.commit()
    assert len(_opens(db)) == 1


def test_non_product_excluded():
    # 差价/专链单(关键词命中) → 不报
    db = _db()
    _order(db, 1, "O1", paid=100, theoretical=4631.83, name="补差价专链")
    n = dq.scan_cost_exceeds_paid(db); db.commit()
    assert n == 0
    assert len(_opens(db)) == 0


# --- recheck 检查器回归 (2026-06-23: 修 _check_cost_exceeds_paid 漏 import Decimal) ---

def _exc(db, oid):
    ex = DataException(source_table="orders", source_pk=str(oid),
                       exception_type="cost_exceeds_paid", severity="warning",
                       status="open", description="x")
    db.add(ex); db.commit()
    return ex


def test_recheck_flags_real_mismatch():
    # 真错配 → recheck 返回原因。旧版漏 import Decimal 时会 NameError 被吞成 None → 此断言失败
    from app.services import exception_recheck_service as rk
    db = _db()
    _order(db, 1, "O1", paid=100, theoretical=4631.83)
    reason = rk.recheck(db, _exc(db, 1))
    assert reason and "错配未解决" in reason


def test_recheck_clears_when_fixed():
    # 成本归零 → recheck 返回 None; bulk_close_resolved 据此自动销账
    from app.services import exception_recheck_service as rk
    db = _db()
    _order(db, 1, "O1", paid=100, actual=0, theoretical=0)
    ex = _exc(db, 1)
    assert rk.recheck(db, ex) is None
    closed = rk.bulk_close_resolved(db, types=["cost_exceeds_paid"])
    assert closed.get("cost_exceeds_paid") == 1
    assert ex.status == "resolved"


def test_recheck_clears_when_cancelled():
    # 已取消 → 与 scanner 同判据 → recheck 返回 None (口径对齐: 旧版不查状态会误判"仍错配")
    from app.services import exception_recheck_service as rk
    db = _db()
    _order(db, 1, "O1", paid=100, theoretical=4631.83, status="cancelled")
    assert rk.recheck(db, _exc(db, 1)) is None
