"""reconciliation_diff 自动关闭 + 复核器 (2026-06-23 根治).

治本「关了又冒 / 僵尸告警」: 对账差异修好后, 每日全量重算自动销账旧告警; 且 reconciliation_diff
加复核器 (resolve 拦未对平的 + /recheck-all 关已修好的)。全 sqlite 内存 + 合成数据, 不碰生产。
用非支付宝账户名 → run_ledger_check 只跑①账面自洽 (不做②流水勾稽), 测试更纯。
"""
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.finance import AccountBalance
from app.services import reconciliation_service as rs


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def _ab(db, name, y, m, op, cl, inc=0, exp=0):
    db.add(AccountBalance(account_name=name, period_year=y, period_month=m,
                          opening_balance=D(str(op)), closing_balance=D(str(cl)),
                          income=D(str(inc)), expense=D(str(exp))))
    db.commit()


def _ledger_opens(db):
    return db.query(DataException).filter(
        DataException.exception_type == "reconciliation_diff",
        DataException.status == "open",
        DataException.source_pk.like("ledger_check:%")).all()


def test_book_mismatch_creates_exception():
    db = _db()
    _ab(db, "测试卡", 2026, 6, op=10000, cl=500)  # 账面不平: 期初1万→期末500, 收支0
    rs.run_ledger_check(db, record_exceptions=True); db.commit()
    assert len(_ledger_opens(db)) == 1


def test_autoclose_when_fixed():
    # 修好(补支出9500) → 重算 + 自动关闭, 不再 open
    db = _db()
    _ab(db, "测试卡", 2026, 6, op=10000, cl=500)
    rs.run_ledger_check(db, record_exceptions=True); db.commit()
    assert len(_ledger_opens(db)) == 1
    ab = db.query(AccountBalance).first(); ab.expense = D("9500"); db.commit()  # 10000-9500=500=期末
    res2 = rs.run_ledger_check(db, record_exceptions=False)
    rs._autoclose_resolved_diffs(db, {"ledger_check": res2}); db.commit()
    assert len(_ledger_opens(db)) == 0
    ex = db.query(DataException).filter_by(exception_type="reconciliation_diff").first()
    assert ex.status == "resolved" and ex.resolved_by == "auto"


def test_autoclose_keeps_still_off():
    # 没修的真差异 → 不误关
    db = _db()
    _ab(db, "测试卡", 2026, 6, op=10000, cl=500)
    rs.run_ledger_check(db, record_exceptions=True); db.commit()
    res2 = rs.run_ledger_check(db, record_exceptions=False)
    rs._autoclose_resolved_diffs(db, {"ledger_check": res2}); db.commit()
    assert len(_ledger_opens(db)) == 1


def test_recheck_reconciliation_diff():
    # 复核器: 仍有差→返回原因(resolve 会被拦); 修好→None(可销账)
    from app.services import exception_recheck_service as rk
    db = _db()
    _ab(db, "测试卡", 2026, 6, op=10000, cl=500)
    rs.run_ledger_check(db, record_exceptions=True); db.commit()
    ex = db.query(DataException).filter_by(exception_type="reconciliation_diff").first()
    assert rk.recheck(db, ex) is not None
    ab = db.query(AccountBalance).first(); ab.expense = D("9500"); db.commit()
    assert rk.recheck(db, ex) is None
