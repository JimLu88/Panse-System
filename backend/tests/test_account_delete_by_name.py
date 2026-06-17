"""整账删除 (DELETE /api/finance/accounts/by-name/all): 清理重复/废弃账户的全部余额。"""
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.finance import AccountBalance


def _make_client():
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, TestingSession, lambda: app.dependency_overrides.clear()


def _seed(session_factory):
    db = session_factory()
    # 旧手填『企业号』两个月 + 自动抓取『支付宝-企业账号』一个月
    db.add(AccountBalance(account_name="企业号", period_year=2026, period_month=4,
                          closing_balance=Decimal("464444.93")))
    db.add(AccountBalance(account_name="企业号", period_year=2026, period_month=5,
                          closing_balance=Decimal("464444.93")))
    db.add(AccountBalance(account_name="支付宝-企业账号", period_year=2026, period_month=6,
                          closing_balance=Decimal("386654.04")))
    db.commit()
    db.close()


def test_delete_whole_account_removes_all_rows():
    client, sf, cleanup = _make_client()
    try:
        _seed(sf)
        r = client.delete("/api/finance/accounts/by-name/all",
                          params={"account_name": "企业号"})
        assert r.status_code == 200, r.text
        assert r.json()["deleted_rows"] == 2

        db = sf()
        names = db.execute(select(AccountBalance.account_name)).scalars().all()
        assert "企业号" not in names           # 旧重复账户已整账删除
        assert "支付宝-企业账号" in names        # 自动抓取的保留
        db.close()
    finally:
        cleanup()


def test_delete_unknown_account_404():
    client, sf, cleanup = _make_client()
    try:
        _seed(sf)
        r = client.delete("/api/finance/accounts/by-name/all",
                          params={"account_name": "不存在的账户"})
        assert r.status_code == 404
    finally:
        cleanup()
