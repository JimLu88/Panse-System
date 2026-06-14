"""测试夹具：独立 SQLite 测试库 + 种子账号。"""
import os
import pathlib

TEST_DB = "test_marketing.db"
os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB}"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["API_TOKEN"] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    p = pathlib.Path(TEST_DB)
    if p.exists():
        p.unlink()
    from app.seed import run
    run()
    yield
    if p.exists():
        p.unlink()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def db():
    from app.database import SessionLocal
    s = SessionLocal()
    yield s
    s.close()
