import logging
import os
import time
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# SQLite 不支持连接池调参; Postgres 显式配置, 避免重导入+定时扫描并发耗尽连接.
_pool_kwargs = (
    {}
    if _is_sqlite
    else {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
        "pool_pre_ping": True,
    }
)

engine = create_engine(
    settings.database_url,
    future=True,
    connect_args=_connect_args,
    **_pool_kwargs,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


# ----------------------------- 慢查询日志 (优化 #5) ----------------------------- #
# 把 >SLOW_QUERY_MS 的 SQL 记成 warning, 把"哪里慢/N+1"从猜变成数据。
# SLOW_QUERY_MS=0 关闭。纯读侧插桩, 不改任何行为。
_slow_logger = logging.getLogger("panse.slowquery")
try:
    _SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_MS", "200"))
except ValueError:
    _SLOW_QUERY_MS = 200

if _SLOW_QUERY_MS > 0:
    @event.listens_for(engine, "before_cursor_execute")
    def _record_query_start(conn, cursor, statement, parameters, context, executemany):
        conn.info["_query_start"] = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def _log_slow_query(conn, cursor, statement, parameters, context, executemany):
        start = conn.info.pop("_query_start", None)
        if start is None:
            return
        dur_ms = (time.monotonic() - start) * 1000
        if dur_ms >= _SLOW_QUERY_MS:
            stmt = " ".join(statement.split())[:300]
            _slow_logger.warning("慢查询 %.0fms%s: %s",
                                 dur_ms, " (batch)" if executemany else "", stmt)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
