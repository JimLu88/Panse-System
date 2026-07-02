"""系统监控 + 看门狗 (业务需求).

功能:
    - get_status(db)             — 状态快照 (CPU/内存/磁盘/DB/迁移/版本)
    - run_checks(db)             — 跑全部检查, 写一条 SystemHealthLog
    - request_restart(actor)     — 发 SIGTERM 重启; 写 restart_requested 事件
    - claim_pid_file()           — 启动时检测孤立进程, SIGKILL 后写自己 PID
    - log_process_started()      — 启动时记录事件, 让 UI 能 diff 重启前后
    - start_background()         — 60s 轮询; 连续 3 次 fail 自动触发自救重启

事件 (SystemEvent.kind):
    restart_requested  — admin 点按钮 / 看门狗自救
    process_started    — 新进程启动后立刻写一条 (含 snapshot)
    watchdog_triggered — 看门狗判定要重启 (precedes restart_requested)
    orphan_killed      — 启动时检测到上次进程残留, kill 后启动
    restart_failed     — request_restart 自身出错
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
        # 按"剩余空间"判定而非纯百分比: 群晖等共享大卷长期接近满(整卷被媒体占),
        # 纯 used% 会对 ERP 误报; 真实风险是"写不进去"→ 看绝对可用空间。
        # 阈值可用 DISK_FREE_FAIL_GB / DISK_FREE_WARN_GB 环境变量覆盖。
        fail_gb = float(os.environ.get("DISK_FREE_FAIL_GB", "10"))
        warn_gb = float(os.environ.get("DISK_FREE_WARN_GB", "30"))
        if free_gb < fail_gb:
            status = "fail"
        elif free_gb < warn_gb:
            status = "warn"
        else:
            status = "ok"
        return HealthCheck("disk", status,
                           f"free={free_gb:.1f}GB used={used_pct:.1f}%",
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


def _in_container() -> bool:
    """是否在 Docker 容器内 (决定重启时是否要杀 PID 1)。"""
    return os.path.exists("/.dockerenv")


def _restart_target_pid() -> int:
    """要重启整个容器, 必须杀容器主进程 PID 1 (uvicorn 主进程/reloader/worker master)。

    在 --reload / --workers 下, 杀自己这个子进程只会被父进程重新拉起、容器不退出,
    Docker 的 restart 策略就永远不触发 —— 这正是看门狗"拉起失败"的根因。
    非容器(本地 / 测试)退回杀自己, 安全。
    """
    me = os.getpid()
    if me != 1 and _in_container():
        return 1
    return me


def request_restart(db: Optional[Session] = None, *,
                    actor: Optional[str] = None,
                    detail: Optional[str] = None) -> None:
    """触发 graceful 重启: 给容器主进程发 SIGTERM, uvicorn shutdown 后由 docker
    restart 策略 (unless-stopped) 自动再拉起。

    关键: 容器内杀 PID 1 而非自己这个子进程 —— 否则 --reload/--workers 下父进程会把
    子进程重新拉起、容器不退出, Docker 重启策略不触发 → 拉起失败。

    db 不为 None 时, 会写一条 restart_requested 事件; 否则只 schedule SIGTERM。
    """
    pid = _restart_target_pid()
    _logger.warning("收到重启请求 actor=%s, 给容器主进程 pid=%s 发 SIGTERM (self=%s)",
                    actor, pid, os.getpid())

    if db is not None:
        try:
            log_event(db, "restart_requested",
                      actor=actor or "system", detail=detail,
                      snapshot=_snapshot_for_event(db))
            db.commit()
        except Exception as e:  # pragma: no cover
            _logger.warning("无法记录 restart_requested 事件: %s", e)

    try:
        loop = asyncio.get_event_loop()
        loop.call_later(0.5, lambda: os.kill(pid, signal.SIGTERM))
    except RuntimeError:  # pragma: no cover — 没有 loop, 同步杀
        os.kill(pid, signal.SIGTERM)


import threading as _threading

_BG_THREAD = None   # type: ignore[var-annotated]
_BG_STOP = None     # type: ignore[var-annotated]


def _quiet_hours_window():
    """夜间安静时段 [start, end) 小时 (用户拍板 2026-06-23 夜间模式: 让 NAS 盘连续休眠几小时)。
    env PANSE_QUIET_HOURS="23-7"(默认); "off"/"none"/"0" 禁用。返回 (start, end) 或 None。"""
    raw = (os.environ.get("PANSE_QUIET_HOURS", "23-7") or "").strip().lower()
    if raw in ("", "off", "none", "disabled", "0"):
        return None
    try:
        s, e = raw.split("-")
        return (int(s) % 24, int(e) % 24)
    except Exception:
        return (23, 7)


def _in_quiet_hours(now_hour: int | None = None) -> bool:
    win = _quiet_hours_window()
    if win is None:
        return False
    if now_hour is None:
        from datetime import datetime
        now_hour = datetime.now().hour
    s, e = win
    return (now_hour >= s or now_hour < e) if s > e else (s <= now_hour < e)


def _quiet_fail_alert(db, results) -> None:
    """夜间检测到异常: 落库 + critical 告警(推送), 但不自动重启(用户拍板: 夜里崩溃留早上人工)。"""
    from app.services import alert_service
    bad = [c for c in results if c.status == "fail"]
    body = "; ".join(f"{c.name}: {c.detail}" for c in bad) or "未知"
    alert_service.upsert(
        db, kind="watchdog", severity="critical",
        title="夜间模式: 系统异常(已暂停自动重启)",
        body=f"安静时段 {_quiet_hours_window()} 检测到异常, 按夜间模式不自动重启, 请人工介入:\n{body}",
        dedupe_key="watchdog_quiet_fail",
        related_url="/admin", auto_resolve_after_minutes=60 * 12,
    )
    db.commit()


def _resolve_quiet_alert(db) -> None:
    """自愈: 体检恢复正常时, 关掉之前的「夜间模式: 系统异常」告警。

    例: 部署时先把新迁移文件放进容器、几秒后才 alembic upgrade, 夜间看门狗在这窗口内
    体检会误报 migrations current<latest; upgrade 跑完后条件已恢复, 下一轮体检据此自动销警,
    不必等 12h 自动过期、也不用人工点「已知晓」。"""
    from app.services import alert_service
    try:
        if alert_service.resolve_by_dedupe(db, "watchdog_quiet_fail"):
            db.commit()
    except Exception as e:  # pragma: no cover - 防御性
        _logger.warning("夜间告警自愈失败: %s", e)


def _background_loop_sync(interval_sec: int, stop_event) -> None:
    """看门狗循环 — 跑在独立 OS 线程 (评审#6): 业务 event loop 被慢 SQL / 外部 HTTP
    阻塞时, 看门狗仍能照常体检 + 自救, 不会跟着冻住。"""
    from app.database import SessionLocal
    while not stop_event.wait(interval_sec):
        try:
            db = SessionLocal()
            try:
                if _in_quiet_hours():
                    # 夜间模式 (用户拍板 2026-06-23): 健康则 persist=False 不写盘 → NAS 盘能连续休眠;
                    # 仅在检测到 fail 时破例落库 + critical 告警(保留崩溃告警), 但不自动重启(留早上人工)。
                    results = run_checks(db, persist=False)
                    if any(c.status == "fail" for c in results):
                        run_checks(db, persist=True)
                        db.commit()
                        _quiet_fail_alert(db, results)
                    else:
                        _resolve_quiet_alert(db)   # 自愈: 夜间异常已恢复 → 关掉夜间告警
                else:
                    results = run_checks(db, persist=True)
                    db.commit()
                    if not any(c.status == "fail" for c in results):
                        _resolve_quiet_alert(db)   # 夜里报的异常, 白天体检恢复也关掉
                    # 业务需求 6: 看门狗自救
                    triggered = maybe_auto_restart(db)
                    if triggered:
                        _logger.warning("看门狗自救已触发: %s", triggered)
            finally:
                db.close()
        except Exception as e:  # pragma: no cover
            _logger.warning("健康检查失败: %s", e)


def start_background(interval_sec: int = 60) -> None:
    """FastAPI startup 时调一次. 重入安全. 看门狗跑在独立 OS 线程, 不受业务 loop 阻塞影响。"""
    global _BG_THREAD, _BG_STOP
    if _BG_THREAD is not None and _BG_THREAD.is_alive():
        return
    _BG_STOP = _threading.Event()
    _BG_THREAD = _threading.Thread(
        target=_background_loop_sync, args=(interval_sec, _BG_STOP),
        daemon=True, name="panse-watchdog")
    _BG_THREAD.start()
    _logger.info("看门狗后台线程已启动 (interval=%ss, 独立OS线程)", interval_sec)


def stop_background() -> None:
    global _BG_THREAD, _BG_STOP
    if _BG_STOP is not None:
        _BG_STOP.set()
    _BG_THREAD = None


# ----------------------------- 事件 / Diff (业务需求 5) ------------- #


def _snapshot_for_event(db: Session) -> dict:
    """重启前后状态精简版, 用于 UI diff."""
    try:
        s = get_status(db)
    except Exception:  # pragma: no cover
        return {}
    return {
        "uptime_sec": s.uptime_sec,
        "version_sha": s.version_sha,
        "db_ok": s.db_ok,
        "db_latency_ms": s.db_latency_ms,
        "mem_used_pct": round(s.mem_used_pct, 1),
        "disk_used_pct": round(s.disk_used_pct, 1),
        "storage_used_mb": s.storage_used_mb,
        "fail_count": sum(1 for c in s.recent_checks if c.status == "fail"),
        "warn_count": sum(1 for c in s.recent_checks if c.status == "warn"),
    }


def log_event(db: Session, kind: str, *, actor: Optional[str] = None,
              detail: Optional[str] = None,
              snapshot: Optional[dict] = None) -> None:
    from app.models.system_event import SystemEvent
    db.add(SystemEvent(
        kind=kind, actor=actor, detail=detail, snapshot_json=snapshot,
    ))
    db.flush()


def recent_events(db: Session, *, limit: int = 50) -> list:
    from app.models.system_event import SystemEvent
    return list(db.execute(
        select(SystemEvent).order_by(SystemEvent.id.desc()).limit(limit)
    ).scalars())


def log_process_started(db: Session) -> None:
    """startup hook 调; 写一条 process_started 事件 (snapshot 之后)."""
    log_event(db, "process_started", actor="system",
              detail=f"pid={os.getpid()} version={_version_sha()}",
              snapshot=_snapshot_for_event(db))


# ----------------------------- PID 文件 (业务需求 7) --------------- #


PID_FILE = os.environ.get("PANSE_PID_FILE", "/tmp/panse_api.pid")


def _is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但不归我们 (在容器里不太可能), 视为活的
        return True


def claim_pid_file(db: Optional[Session] = None) -> Optional[int]:
    """启动时调用. 如果 PID 文件已有别人 → SIGTERM → 1s 后还活着就 SIGKILL → 写自己 PID.

    返回被杀的旧 pid (None 表示没有孤立进程)。
    """
    killed: Optional[int] = None
    try:
        if os.path.isfile(PID_FILE):
            try:
                prev = int(open(PID_FILE, encoding="utf-8").read().strip())
            except (ValueError, OSError):
                prev = -1
            if prev > 0 and prev != os.getpid() and _is_alive(prev):
                _logger.warning("发现孤立进程 pid=%s, 准备杀掉", prev)
                try:
                    os.kill(prev, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                # 等最多 3 秒 graceful 退出
                for _ in range(15):
                    if not _is_alive(prev):
                        break
                    time.sleep(0.2)
                if _is_alive(prev):
                    _logger.warning("孤立进程 pid=%s 不肯走, SIGKILL", prev)
                    try:
                        os.kill(prev, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                killed = prev
        # 写自己 PID
        os.makedirs(os.path.dirname(PID_FILE) or ".", exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as e:  # pragma: no cover
        _logger.warning("无法读写 PID 文件 %s: %s", PID_FILE, e)

    if killed is not None and db is not None:
        try:
            log_event(db, "orphan_killed", actor="system",
                      detail=f"上次的 pid={killed} 残留, 已 kill 重启",
                      snapshot=_snapshot_for_event(db))
        except Exception:  # pragma: no cover
            pass

    return killed


def release_pid_file() -> None:
    """shutdown hook 调; 只删除属于自己的 PID 文件 (避免覆盖新进程)."""
    try:
        if os.path.isfile(PID_FILE):
            current = open(PID_FILE, encoding="utf-8").read().strip()
            if current == str(os.getpid()):
                os.remove(PID_FILE)
    except OSError:  # pragma: no cover
        pass


# ----------------------------- 自救逻辑 (业务需求 6) --------------- #


# 同一进程下不重复触发: 重启请求 cooldown
_LAST_AUTO_RESTART_TS = 0.0
AUTO_RESTART_COOLDOWN_SEC = 600   # 10 分钟
AUTO_RESTART_FAIL_THRESHOLD = 3   # 连续 3 次 fail


def _should_auto_restart(db: Session) -> tuple[bool, str]:
    """检查关键 check 是否连续 fail 3 次. 返回 (是否要重启, 原因)."""
    from app.models.system_health import SystemHealthLog
    critical = ("db_ping", "memory")
    for name in critical:
        rows = db.execute(
            select(SystemHealthLog).where(SystemHealthLog.check_name == name)
            .order_by(SystemHealthLog.id.desc())
            .limit(AUTO_RESTART_FAIL_THRESHOLD)
        ).scalars().all()
        if len(rows) >= AUTO_RESTART_FAIL_THRESHOLD and \
           all(r.status == "fail" for r in rows):
            return True, f"{name} 连续 {AUTO_RESTART_FAIL_THRESHOLD} 次 fail"
    return False, ""


def _recent_restart_count(db: Session, within_sec: int) -> int:
    """最近 within_sec 秒内 watchdog 自救重启次数 (跨进程重启持久, 读 system_events)。"""
    from datetime import datetime, timezone, timedelta
    from app.models.system_event import SystemEvent
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_sec)
    rows = db.execute(
        select(SystemEvent.created_at)
        .where(SystemEvent.kind == "watchdog_triggered")
        .order_by(SystemEvent.id.desc()).limit(10)
    ).scalars().all()
    cnt = 0
    for ts in rows:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            cnt += 1
    return cnt


def maybe_auto_restart(db: Session) -> Optional[str]:
    """看门狗 tick 后调用。返回触发原因 (重启) 或 None."""
    global _LAST_AUTO_RESTART_TS
    if time.time() - _LAST_AUTO_RESTART_TS < AUTO_RESTART_COOLDOWN_SEC:
        return None
    # 持久化冷却: 进程内变量重启后会清零, 故跨重启读 DB 里的 watchdog_triggered 事件。
    # 冷却窗口内已重启过 → 跳过, 避免病因未消时每 3 分钟(=3 tick)无限重启。
    if _recent_restart_count(db, AUTO_RESTART_COOLDOWN_SEC) >= 1:
        return None
    # 熔断: 1 小时内已自救 >=3 次仍未好转 → 不再自动重启, 留给人工 (自动重启解决不了的问题)。
    if _recent_restart_count(db, 3600) >= AUTO_RESTART_FAIL_THRESHOLD:
        _logger.error("看门狗熔断: 1 小时内已自救重启 >=%d 次仍未恢复, 停止自动重启, 请人工介入",
                      AUTO_RESTART_FAIL_THRESHOLD)
        return None
    should, reason = _should_auto_restart(db)
    if not should:
        return None
    _LAST_AUTO_RESTART_TS = time.time()
    snap = _snapshot_for_event(db)
    log_event(db, "watchdog_triggered", actor="watchdog",
              detail=reason, snapshot=snap)
    db.commit()

    # 通知 (Slack/微信/钉钉/飞书) — 不抛, 失败只 log
    try:
        from app.services import notify_service
        notify_service.notify(
            db,
            f"看门狗触发自救重启\n原因: {reason}\n"
            f"内存: {snap.get('mem_used_pct')}%  磁盘: {snap.get('disk_used_pct')}%  "
            f"DB: {'OK' if snap.get('db_ok') else 'FAIL'}",
            level="error", title="畔色 ERP 看门狗告警",
        )
    except Exception as e:  # pragma: no cover
        _logger.warning("通知发送异常 (不影响重启): %s", e)

    # 真的发 SIGTERM
    log_event(db, "restart_requested", actor="watchdog",
              detail=f"自救重启: {reason}", snapshot=snap)
    db.commit()
    request_restart()
    return reason
