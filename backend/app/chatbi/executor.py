# -*- coding: utf-8 -*-
"""ChatBI 只读执行器 (Plan4 v2 §4.6 六道闸的 DB 层: 只读连接/超时/EXPLAIN干跑/行数截断)。

执行前提: SQL 必须已过 sql_gate.validate_readonly_select (结构+白名单+LIMIT)。这里再叠 DB 层:
  - 只读: 优先用独立只读角色 DSN(chatbi_ro_dsn); 否则主连接强制 SET TRANSACTION READ ONLY。
  - 超时: SET LOCAL statement_timeout (防笛卡尔积拖死 NAS 的 PG)。
  - EXPLAIN 干跑 (抄 WrenAI dry-plan): 先 EXPLAIN 拦幻觉列/边缘方言, 再真跑。
  - 行数: fetchmany 截断兜底。
全程事务 rollback (只读, 无需提交)。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_log = logging.getLogger("panse.chatbi.executor")

STATEMENT_TIMEOUT_MS = 10000
FETCH_CAP = 1000

_ro_engine_cache: dict[str, Engine] = {}


class ExecutorError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _ro_engine(ro_dsn: str | None) -> Engine | None:
    if not ro_dsn:
        return None
    eng = _ro_engine_cache.get(ro_dsn)
    if eng is None:
        eng = create_engine(ro_dsn, future=True, pool_pre_ping=True,
                            pool_size=2, max_overflow=2, pool_recycle=1800)
        _ro_engine_cache[ro_dsn] = eng
    return eng


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def infer_column_kinds(columns: list[str], rows: list[list]) -> list[dict]:
    """按值推断列类型 (给 AI 直出结果做图表选择)。time/number/category。"""
    out = []
    for i, name in enumerate(columns):
        vals = [r[i] for r in rows if r[i] is not None][:50]
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            kind = "number"
        elif vals and all(isinstance(v, (date, datetime)) for v in vals):
            kind = "time"
        else:
            kind = "category"
        out.append({"name": name, "kind": kind})
    return out


def run_readonly(safe_sql: str, *, db: Session, ro_dsn: str | None = None,
                 timeout_ms: int = STATEMENT_TIMEOUT_MS,
                 fetch_cap: int = FETCH_CAP) -> tuple[list[str], list[list]]:
    """在只读事务里执行已过闸门的 SQL。返回 (列名, 行[list])。失败抛 ExecutorError。"""
    engine = _ro_engine(ro_dsn) or db.get_bind()
    is_pg = engine.dialect.name == "postgresql"
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                if is_pg:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                    conn.execute(text("EXPLAIN " + safe_sql))   # 干跑; 幻觉列/方言错在此拦
                result = conn.execute(text(safe_sql))
                columns = list(result.keys())
                raw = result.fetchmany(fetch_cap)
                rows = [[_jsonable(v) for v in row] for row in raw]
                return columns, rows
            finally:
                trans.rollback()   # 只读, 永远回滚
    except ExecutorError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(getattr(e, "orig", e))
        _log.warning("chatbi 执行失败: %s | SQL=%s", msg, safe_sql[:200])
        raise ExecutorError(f"查询执行失败: {msg[:200]}") from e
