import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DISABLE_WATCHDOG", "1")   # 测试不跑后台 60s tick
os.environ.setdefault("DISABLE_SCHEDULER", "1")  # 测试不跑 APScheduler
os.environ.setdefault("DISABLE_RATE_LIMIT", "1") # 测试不限速
os.environ.setdefault("PANSE_DISABLE_NOTIFY", "1")  # 测试绝不往真实飞书群推消息 (2026-06-11)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models import Base  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
