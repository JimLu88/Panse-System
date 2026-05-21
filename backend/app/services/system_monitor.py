"""系统监控 + 看门狗 (业务需求).

提供:
    - get_status(db)       — 一次性状态快照 (CPU/内存/磁盘/DB/迁移/版本)
    - run_checks(db)       — 跑全部检查, 入库一条 SystemHealthLog
    - request_restart()    — 触发 graceful SIGTERM, 配合 docker restart 策略自启
    - start_background()   — fastapi startup 起一个 60s 轮询循环

实现注意:
    - 不依赖 psutil (镜像无装), 用 /proc + shutil 取 mem/disk
    - 重启用 os.kill(getpid(), SIGTERM), uvicorn 收到信号会 graceful shutdown
      Docker compose `restart: unless-stopped` 会立刻再起一个
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.system_health import SystemHealthLog

_logger = logging.getLogger("panse.system_monitor")

_PROCESS_START_TS = time.time()


@dataclass
class HealthCheck:
    name: str
    status: str          # ok / warn / fail
    detail: str
    duration_ms: int


@dataclass
class SystemStatus:
    uptime_sec: int
    process_started_at: str
    version_sha: str
    python_version: str
    db_ok: bool
    db_latency_ms: Optional[int]
    pending_migrations: int
    disk_total_gb: float
    disk_free_gb: float
    disk_used_pct: float
    mem_total_mb: int
    mem_available_mb: int
    mem_used_pct: float
    storage_used_mb: int          # ./storage 目录大小
    recent_checks: list[HealthCheck]


# ----------------------------- helpers --------------------------- #


def _version_sha() -> str:
    sha = os.environ.get("PANSE_VERSION_SHA") or os.environ.get("GIT_COMMIT")
    if sha:
        return sha[:12]
    # 兜底: 读 .git/HEAD (开发模式)
    for p in (".git/HEAD", "/app/.git/HEAD"):
        if os.path.isfile(p):
            try:
                head = open(p, encoding="utf-8").read().strip()
                if head.startswith("ref:"):
                    ref_path = head.split(" ", 1)[1].strip()
                    full = os.path.join(os.path.dirname(p), ref_path)
                    if os.path.isfile(full):
                        return open(full, encoding="utf-8").read().strip()[:12]
                return head[:12]
            except OSError:
                pass
    return "unknown"


def _read_meminfo() -> tuple[int, int]:
    """Return (total_kb, available_kb) from /proc/meminfo; (-1, -1) if 不可用."""
    try:
        total = avail = -1
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
                if total > 0 and avail > 0:
                    break
        return total, avail
    except (OSError, ValueError):
        return -1, -1


def _dir_size_mb(path: str) -> int:
    try:
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total // (1024 * 1024)
    except OSError:
        return 0


def _storage_root() -> str:
    return os.environ.get("DELIVERY_STORAGE_ROOT") or "./storage"


# ----------------------------- checks ---------------------------- #


def _check_db(db: Session) -> HealthCheck:
    t0 = time.time()
    try:
        db.execute(text("SELECT 1"))
        dt = int((time.time() - t0) * 1000)
        status = "ok" if dt < 200 else "warn"
        return HealthCheck("db_ping", status, f"{dt}ms", dt)
    except Exception as e:
        return HealthCheck("db_ping", "fail", f"{type(e).__name__}: {e}",
                            int((time.time() - t0) * 1000))


def _check_disk() -> HealthCheck:
    t0 = time.time()
    try:
        usage = shutil.disk_usage("/")
        used_pct = usage.used / usage.total * 100
        free_gb = usage.free / (1024 ** 3)
        if used_pct >= 95:
            status = "fail"
        elif used_pct >= 85:
            status = "warn"
        else:
            status = "ok"
        return HealthCheck("disk", status,
                           f"used={used_pct:.1f}% free={free_gb:.1f}GB",
                           int((time.time() - t0) * 1000))
    except OSError as e:
        return HealthCheck("disk", "fail", str(e),
                           int((time.time() - t0) * 1000))


def _check_memory() -> HealthCheck:
    t0 = time.time()
    total_kb, avail_kb = _read_meminfo()
    if total_kb <= 0:
        return HealthCheck("memory", "warn", "无法读取 /proc/meminfo (非 Linux?)", 0)
    used_pct = (total_kb - avail_kb) / total_kb * 100
    if used_pct >= 95:
        status = "fail"
    elif used_pct >= 85:
        status = "warn"
    else:
        status = "ok"
    return HealthCheck("memory", status,
                       f"used={used_pct:.1f}% avail={avail_kb // 1024}MB",
                       int((time.time() - t0) * 1000))


def _check_migrations(db: Session) -> HealthCheck:
    """检查 alembic_version 是否 = 最新 revision."""
    t0 = time.time()
    try:
        try:
            current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            current = None
        # 找最新的 revision 文件
        import os as _os
        versions_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                                     "..", "alembic", "versions")
        if not _os.path.isdir(versions_dir):
            versions_dir = "alembic/versions"
        latest = None
        if _os.path.isdir(versions_dir):
            files = sorted(_os.listdir(versions_dir))
            for f in reversed(files):
                if f.endswith(".py") and not f.startswith("__"):
                    # revision id = 文件名前缀直到 _
                    latest = f.split("_", 1)[0]
                    break
        if latest is None:
            return HealthCheck("migrations", "warn", "找不到迁移文件夹",
                               int((time.time() - t0) * 1000))
        status = "ok" if current == latest else "fail"
        detail = f"current={current} latest={latest}"
        return HealthCheck("migrations", status, detail,
                           int((time.time() - t0) * 1000))
    except Exception as e:
        return HealthCheck("migrations", "warn", str(e),
                           int((time.time() - t0) * 1000))


def _check_ai_config(db: Session) -> HealthCheck:
    """看是否配了 AI key (不实际调 API, 仅核对设置存在)."""
    t0 = time.time()
    from app.services import settings_service
    diag = settings_service.get(db, "ai_diagnose_api_key", env_fallback=True)
    ocr = settings_service.get(db, "ai_ocr_api_key", env_fallback=True)
    parts = []
    if diag:
        parts.append("诊断 ✓")
    else:
        parts.append("诊断 ✗")
    if ocr:
        parts.append("OCR ✓")
    else:
        parts.append("OCR ✗")
    # 全没配 → warn (不阻塞); 配了至少一个 → ok
    status = "ok" if (diag or ocr) else "warn"
    return HealthCheck("ai_config", status, " / ".join(parts),
                       int((time.time() - t0) * 1000))


_CHECKS = [
    ("db_ping", _check_db, True),
    ("disk", _check_disk, False),
    ("memory", _check_memory, False),
    ("migrations", _check_migrations, True),
    ("ai_config", _check_ai_config, True),
]


def run_checks(db: Session, *, persist: bool = True) -> list[HealthCheck]:
    """跑所有 health check, 可选写入 system_health_logs."""
    results: list[HealthCheck] = []
    for _, fn, needs_db in _CHECKS:
        try:
            r = fn(db) if needs_db else fn()
        except Exception as e:  # pragma: no cover
            r = HealthCheck(getattr(fn, "__name__", "?"), "fail",
                            f"{type(e).__name__}: {e}", 0)
        results.append(r)
        if persist:
            db.add(SystemHealthLog(
                check_name=r.name, status=r.status,
                detail=r.detail, duration_ms=r.duration_ms,
            ))
    if persist:
        db.flush()
    return results


# ----------------------------- 状态汇总 -------------------------- #


def get_status(db: Session) -> SystemStatus:
    import sys
    checks = run_checks(db, persist=False)
    db_check = next((c for c in checks if c.name == "db_ping"), None)
    mig_check = next((c for c in checks if c.name == "migrations"), None)

    pending = 0
    if mig_check and mig_check.status == "fail":
        pending = 1

    disk = shutil.disk_usage("/")
    total_kb, avail_kb = _read_meminfo()

    return SystemStatus(
        uptime_sec=int(time.time() - _PROCESS_START_TS),
        process_started_at=datetime.fromtimestamp(
            _PROCESS_START_TS, tz=timezone.utc).isoformat(),
        version_sha=_version_sha(),
        python_version=sys.version.split()[0],
        db_ok=bool(db_check and db_check.status == "ok"),
        db_latency_ms=db_check.duration_ms if db_check else None,
        pending_migrations=pending,
        disk_total_gb=disk.total / (1024 ** 3),
        disk_free_gb=disk.free / (1024 ** 3),
        disk_used_pct=disk.used / disk.total * 100,
        mem_total_mb=max(total_kb, 0) // 1024,
        mem_available_mb=max(avail_kb, 0) // 1024,
        mem_used_pct=(total_kb - avail_kb) / total_kb * 100 if total_kb > 0 else 0,
        storage_used_mb=_dir_size_mb(_storage_root()),
        recent_checks=checks,
    )


def recent_logs(db: Session, *, limit: int = 100,
                check_name: Optional[str] = None) -> list[SystemHealthLog]:
    q = select(SystemHealthLog).order_by(SystemHealthLog.id.desc()).limit(limit)
    if check_name:
        q = select(SystemHealthLog).where(
            SystemHealthLog.check_name == check_name
        ).order_by(SystemHealthLog.id.desc()).limit(limit)
    return list(db.execute(q).scalars())


# ----------------------------- 重启 / 看门狗 --------------------- #


def request_restart() -> None:
    """触发 graceful 重启. uvicorn 收到 SIGTERM 后会 shutdown, 由 docker
    restart 策略 (unless-stopped) 自动再拉起."""
    pid = os.getpid()
    _logger.warning("收到重启请求, 给 pid=%s 发 SIGTERM", pid)
    # 异步 schedule, 让当前 HTTP 响应能返回完成
    loop = asyncio.get_event_loop()
    loop.call_later(0.5, lambda: os.kill(pid, signal.SIGTERM))


async def _background_loop(interval_sec: int) -> None:
    from app.database import SessionLocal
    while True:
        try:
            await asyncio.sleep(interval_sec)
            db = SessionLocal()
            try:
                run_checks(db, persist=True)
                db.commit()
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover
            _logger.warning("健康检查失败: %s", e)


_BG_TASK: Optional[asyncio.Task] = None


def start_background(interval_sec: int = 60) -> None:
    """FastAPI startup 时调一次. 重复调安全."""
    global _BG_TASK
    if _BG_TASK is not None and not _BG_TASK.done():
        return
    try:
        loop = asyncio.get_event_loop()
        _BG_TASK = loop.create_task(_background_loop(interval_sec))
        _logger.info("看门狗后台任务已启动 (interval=%ss)", interval_sec)
    except RuntimeError:  # pragma: no cover — 没有 loop
        pass


def stop_background() -> None:
    global _BG_TASK
    if _BG_TASK is not None and not _BG_TASK.done():
        _BG_TASK.cancel()
        _BG_TASK = None
