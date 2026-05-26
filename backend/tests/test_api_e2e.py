"""端到端：用 FastAPI TestClient 走一遍 inventory -> auto-create material -> exception。"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base


def _make_client():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    yield client
    app.dependency_overrides.clear()


def test_full_flow_autocreate_custom_material():
    gen = _make_client()
    client = next(gen)
    try:
        # 1. 库存里没物料的情况下，传名字直接录入
        r = client.post(
            "/api/inventory/parts",
            json={
                "warehouse": "江西仓库",
                "material_name": "电力轨道-Xpower-T25-黑色-1.358-2插座",
                "physical_qty": 1,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["material_created"] is True
        assert body["material_code"] == "AC-1000"
        assert body["material_name"].startswith("定制")

        # 2. 物料列表应该能查到这条新的定制物料
        r2 = client.get("/api/materials", params={"is_custom": True})
        assert r2.status_code == 200
        items = r2.json()
        assert any(m["code"] == "AC-1000" for m in items)

        # 3. 异常列表应该有一条 missing_material_autocreated
        r3 = client.get("/api/exceptions", params={"status": "open"})
        assert r3.status_code == 200
        excs = r3.json()
        assert any(e["exception_type"] == "missing_material_autocreated" and e["source_pk"] == "AC-1000" for e in excs)

        # 4. 再录入第二条同名物料：编码复用，不再开异常
        r4 = client.post(
            "/api/inventory/parts",
            json={
                "warehouse": "江西仓库",
                "material_name": "电力轨道-Xpower-T25-黑色-1.358-2插座",
                "physical_qty": 5,
            },
        )
        assert r4.status_code == 201
        assert r4.json()["material_created"] is False
        assert r4.json()["material_code"] == "AC-1000"

        r5 = client.get("/api/exceptions")
        assert len([e for e in r5.json() if e["source_pk"] == "AC-1000"]) == 1
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
