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
