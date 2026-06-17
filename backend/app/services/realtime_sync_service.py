"""导入/改数据后的实时同步 (用户拍板 2026-06-17)。

痛点: 对账异常池只在每日 09:50 cron 才重写, 用户导入补单/流水、清理异常后, 待办里的
异常清单要等第二天才跟上 —— "感觉和清理后的数据不挂钩"。

本服务在关键写操作后**后台立即重算**: 成本兜底(缺成本异常) + 14 条对账规则(写异常池),
让待办/异常清单几秒内跟上最新数据, 而不是等到点。

设计:
- 防抖合并: 同一时刻只允许一个同步线程在跑; 期间来的新请求置 pending, 跑完再补一轮
  (把一批连续导入合并成一次重算, 避免每行都触发)。
- 独立 Session: 后台线程用自己的 SessionLocal, 不碰请求线程的 Session (SQLAlchemy 非线程安全)。
- 永不抛出: 同步失败只记日志, 不影响触发它的导入请求。

用法: 任何写完数据的地方调一句 `realtime_sync_service.trigger("import:refill")` 即可,
立即返回, 实际重算在后台。
"""
from __future__ import annotations

import logging
import threading

_logger = logging.getLogger("panse.realtime_sync")

_lock = threading.Lock()
_running = False
_pending = False
_last_result: dict = {}


def _do_resync() -> dict:
    """跑一轮全量同步 (成本兜底 + 对账写异常池)。用独立 Session。"""
    from app.database import SessionLocal
    from app.services import order_cost_service, reconciliation_service

    out: dict = {}
    db = SessionLocal()
    try:
        # 1) 成本兜底 + 缺成本异常 (含退款/关闭/旧单跳过, 见 #30); 内部自带 commit
        try:
            out["cost"] = order_cost_service.auto_cost_backfill(db)
        except Exception as e:  # noqa: BLE001
            _logger.exception("实时同步-成本兜底失败: %s", e)
            db.rollback()
            out["cost_error"] = f"{type(e).__name__}: {e}"
        # 2) 14 条对账规则 → 写异常池 (幂等: 同 rule:key 已 open 则跳过)
        try:
            reconciliation_service.run_all(db, record_exceptions=True)
            db.commit()
            out["reconcile"] = "ok"
        except Exception as e:  # noqa: BLE001
            _logger.exception("实时同步-对账失败: %s", e)
            db.rollback()
            out["reconcile_error"] = f"{type(e).__name__}: {e}"
        # 3) 批量销账: 有检查器的异常类型, 条件已不成立的(数据已修)自动关闭 → 待办即时清
        try:
            from app.services import exception_recheck_service
            out["closed"] = exception_recheck_service.bulk_close_resolved(db)
            db.commit()
        except Exception as e:  # noqa: BLE001
            _logger.exception("实时同步-批量销账失败: %s", e)
            db.rollback()
            out["close_error"] = f"{type(e).__name__}: {e}"
    finally:
        db.close()
    return out


def _worker() -> None:
    global _running, _pending, _last_result
    while True:
        _last_result = _do_resync()
        with _lock:
            if not _pending:
                _running = False
                return
            _pending = False  # 期间有新触发 → 再补一轮 (合并连续导入)


def trigger(reason: str = "") -> dict:
    """请求一次后台实时同步 (防抖合并)。立即返回, 不阻塞调用方。"""
    global _running, _pending
    _logger.info("实时同步触发: %s", reason or "(unspecified)")
    with _lock:
        if _running:
            _pending = True
            return {"scheduled": True, "merged": True, "reason": reason}
        _running = True
    threading.Thread(target=_worker, name="realtime-sync", daemon=True).start()
    return {"scheduled": True, "merged": False, "reason": reason}


def run_sync_blocking(reason: str = "") -> dict:
    """同步(阻塞)跑一轮 —— 供手动「立即同步」端点用, 跑完返回结果。"""
    _logger.info("实时同步(阻塞)触发: %s", reason or "(unspecified)")
    return _do_resync()


def status() -> dict:
    with _lock:
        return {"running": _running, "pending": _pending, "last_result": _last_result}
