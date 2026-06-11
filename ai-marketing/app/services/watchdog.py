"""看门狗：自检 + 自救。对标 Panse-System ERP 的 system_monitor 模式。

每 60s 体检四项：DB 连通 / 磁盘剩余 / 内存占用 / 调度器心跳。
体检写 system_health_logs；连续 3 次失败 → SIGTERM 自己 →
Docker `restart: unless-stopped` 自动拉起（10 分钟冷却防重启风暴）。
状态供 /api/watchdog 查询，工作台顶栏显示 🐶。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import shutil
import signal
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import HealthLog
from . import scheduler

log = logging.getLogger("marketing.watchdog")

CHECK_INTERVAL = 60               # 秒
FAILURES_TO_RESTART = 3           # 连续失败次数 → 自救
RESTART_COOLDOWN_MIN = 10         # 重启冷却（防风暴）
DISK_MIN_FREE_PCT = 5.0           # 磁盘剩余低于 5% 判异常
MEM_MAX_USED_PCT = 95.0           # 内存占用高于 95% 判异常
KEEP_LOGS = 1000                  # 体检日志保留条数

_COOLDOWN_FILE = Path("./watchdog.cooldown")

STATE: dict = {
    "consecutive_failures": 0,
    "last_check_at": None,
    "last_ok_at": None,
    "restarts_blocked_by_cooldown": 0,
}


# ---------------- 四项体检 ----------------

def _check_db() -> tuple[bool, str]:
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return True, "ok"
        finally:
            db.close()
    except Exception as e:  # DB 挂了也不能让看门狗崩
        return False, str(e)[:200]


def _check_disk() -> tuple[bool, str]:
    u = shutil.disk_usage(".")
    free_pct = u.free / u.total * 100
    return free_pct >= DISK_MIN_FREE_PCT, f"剩余 {free_pct:.1f}%"


def _check_memory() -> tuple[bool, str]:
    try:
        info: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, val = line.split(":", 1)
            info[key.strip()] = int(val.strip().split()[0])
        used_pct = (1 - info["MemAvailable"] / info["MemTotal"]) * 100
        return used_pct <= MEM_MAX_USED_PCT, f"占用 {used_pct:.1f}%"
    except Exception:
        return True, "n/a（非Linux环境跳过）"


def _check_scheduler() -> tuple[bool, str]:
    gen = scheduler.DIGEST.get("generated_at")
    if gen is None:
        return True, "尚未首跑（启动宽限）"
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(gen)).total_seconds()
    ok = age < scheduler.INTERVAL_SECONDS * 2 + 300
    return ok, f"上次心跳 {int(age)}s 前"


_CHECKS = {"db": _check_db, "disk": _check_disk, "memory": _check_memory,
           "scheduler": _check_scheduler}


def check_once() -> dict:
    """跑一轮体检，写日志，更新连续失败计数。"""
    detail = {}
    for name, fn in _CHECKS.items():
        try:
            ok, info = fn()
        except Exception as e:
            ok, info = False, f"检查器异常: {e}"
        detail[name] = {"ok": ok, "info": info}
    all_ok = all(v["ok"] for v in detail.values())

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    STATE["last_check_at"] = now
    if all_ok:
        STATE["consecutive_failures"] = 0
        STATE["last_ok_at"] = now
    else:
        STATE["consecutive_failures"] += 1

    # 写日志（best-effort：DB 挂了写不进就只留内存态）
    try:
        db = SessionLocal()
        try:
            db.add(HealthLog(ok=all_ok, details=detail))
            db.commit()
            _prune(db)
        finally:
            db.close()
    except Exception:
        pass

    return {"ok": all_ok, "checks": detail,
            "consecutive_failures": STATE["consecutive_failures"]}


def _prune(db: Session) -> None:
    """日志只留最近 KEEP_LOGS 条。"""
    max_id = db.scalar(select(HealthLog.id).order_by(HealthLog.id.desc()).limit(1))
    if max_id and max_id > KEEP_LOGS:
        db.execute(delete(HealthLog).where(HealthLog.id <= max_id - KEEP_LOGS))
        db.commit()


# ---------------- 自救 ----------------

def in_cooldown() -> bool:
    if not _COOLDOWN_FILE.exists():
        return False
    try:
        ts = dt.datetime.fromisoformat(_COOLDOWN_FILE.read_text().strip())
        return (dt.datetime.now(dt.timezone.utc) - ts) < dt.timedelta(minutes=RESTART_COOLDOWN_MIN)
    except Exception:
        return False


def self_restart(kill=None) -> bool:
    """SIGTERM 自己 → Docker unless-stopped 拉起。冷却期内只告警不重启。

    kill 参数仅供测试注入，生产走 os.kill(SIGTERM)。
    返回是否真的触发了重启。
    """
    if in_cooldown():
        STATE["restarts_blocked_by_cooldown"] += 1
        log.critical("看门狗：连续体检失败但在 %s 分钟冷却期内，跳过重启（防风暴）",
                     RESTART_COOLDOWN_MIN)
        return False
    _COOLDOWN_FILE.write_text(dt.datetime.now(dt.timezone.utc).isoformat())
    log.critical("看门狗：连续 %s 次体检失败，SIGTERM 自救重启", FAILURES_TO_RESTART)
    (kill or (lambda: os.kill(os.getpid(), signal.SIGTERM)))()
    return True


async def loop() -> None:
    while True:
        try:
            check_once()
            if STATE["consecutive_failures"] >= FAILURES_TO_RESTART:
                self_restart()
        except Exception:  # 看门狗本身永不退出
            log.exception("watchdog check failed")
        await asyncio.sleep(CHECK_INTERVAL)


def status(db: Session) -> dict:
    """看门狗状态 + 最近体检记录（工作台/排障用）。"""
    recent = db.scalars(
        select(HealthLog).order_by(HealthLog.id.desc()).limit(20)
    ).all()
    return {
        **STATE,
        "in_cooldown": in_cooldown(),
        "config": {
            "check_interval_s": CHECK_INTERVAL,
            "failures_to_restart": FAILURES_TO_RESTART,
            "cooldown_min": RESTART_COOLDOWN_MIN,
        },
        "recent": [{"ok": h.ok, "at": h.checked_at.isoformat(), "details": h.details}
                   for h in recent],
    }
