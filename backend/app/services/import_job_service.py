"""异步 Excel 导入作业 (业务需求 6 + 扩展).

POST /api/importer/commit-async →
    1) 创建 ImportJob row (status=pending)
    2) Excel 文件落盘到 IMPORTER_TMP_DIR/<job_id>.xlsx (避免常驻内存 100MB+)
    3) submit 到 ThreadPoolExecutor (默认 N=2 worker, 可经 IMPORTER_WORKERS 调)
    4) 立刻返回 job_id

Worker:
    - 改 status=running, started_at
    - 跑 excel_importer.commit_sheet(progress_callback=..., cancel_callback=...)
    - 进度每 50 行向 ImportJob.processed_rows 写一次 (单独 session)
    - 每次 tick 检查 ImportJob.cancel_requested, True → 抛 CancelledImport
    - 完成: status=done, report=asdict(ImportReport), completed_at
    - 失败: status=failed, error=traceback, completed_at
    - 取消: status=cancelled, error="用户取消", completed_at
    - 无论成败, 删除磁盘临时文件

GET /api/importer/jobs/{id} → 前端每 2s 轮询
POST /api/importer/jobs/{id}/cancel → 设 cancel_requested=True; worker 自检后退出
"""
from __future__ import annotations

import logging
import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_job import ImportJob
from app.services import excel_importer

_logger = logging.getLogger("panse.import_job")


# ----------------------------- 执行器 ---------------------------- #


def _max_workers() -> int:
    """默认 2: 一次同时跑 2 个 100MB 导入, 内存峰值 ~600MB (Excel parse 翻倍).
    部署到云服务器 4GB 内存机器仍安全。生产可经 IMPORTER_WORKERS 环境变量调。"""
    try:
        n = int(os.environ.get("IMPORTER_WORKERS", "2"))
        return max(1, min(n, 8))   # 上限 8 防误调爆内存
    except ValueError:
        return 2


def _tmp_dir() -> str:
    d = os.environ.get("IMPORTER_TMP_DIR", "/tmp/panse_import")
    os.makedirs(d, exist_ok=True)
    return d


_EXECUTOR: Optional[ThreadPoolExecutor] = None
_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=_max_workers(),
                                           thread_name_prefix="import-job")
        return _EXECUTOR


def shutdown_executor() -> None:
    """shutdown hook 调; 等当前作业完成后停掉."""
    global _EXECUTOR
    with _LOCK:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _EXECUTOR = None


# ----------------------------- 文件落盘 ---------------------------- #


def _persist_file(file_bytes: bytes, job_id: int) -> str:
    """写到 /tmp/panse_import/<job_id>_<uuid>.xlsx; 返回绝对路径."""
    fname = f"{job_id}_{uuid.uuid4().hex[:8]}.xlsx"
    path = os.path.join(_tmp_dir(), fname)
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path


def _cleanup_file(path: Optional[str]) -> None:
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as e:  # pragma: no cover
        _logger.warning("无法删除临时文件 %s: %s", path, e)


# ----------------------------- submit ---------------------------- #


def submit_import(
    db: Session,
    *,
    file_bytes: bytes,
    sheet_name: str,
    entity_type: str,
    mapping: dict[str, str],
    user_id: Optional[int] = None,
    auto_create_suppliers: bool = True,
    auto_match_orders: bool = True,
    inline: bool = False,
) -> ImportJob:
    """创建一个 ImportJob 并 submit 给后台执行器。

    inline=True: 同步跑 (测试用), 不落盘, 不进 executor
    """
    options = {
        "auto_create_suppliers": auto_create_suppliers,
        "auto_match_orders": auto_match_orders,
    }
    job = ImportJob(
        user_id=user_id, entity_type=entity_type, sheet_name=sheet_name,
        mapping=mapping, options_json=options,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if inline:
        # 测试用: 在调用方的 session 里同步跑 (不开新 SessionLocal, 不落盘)
        _run_job_inline(db, job, file_bytes, sheet_name, entity_type, mapping, options)
        return job

    # 业务需求: 落盘到 /tmp 后再 submit, 避免 N=2 worker × 100MB 同时占内存
    try:
        file_path = _persist_file(file_bytes, job.id)
    except OSError as e:
        job.status = "failed"
        job.error = f"落盘失败: {e}"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return job
    job.file_path = file_path
    db.commit()

    _executor().submit(
        _run_job_safe, job.id, file_path, sheet_name, entity_type, mapping, options,
    )
    return job


def _run_job_inline(
    db: Session, job: ImportJob, file_bytes: bytes,
    sheet_name: str, entity_type: str, mapping: dict[str, str], options: dict,
) -> None:
    """同步跑, 复用调用方 session (测试用)."""
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()
    try:
        report = excel_importer.commit_sheet(
            db, file_bytes=file_bytes,
            sheet_name=sheet_name, entity_type=entity_type, mapping=mapping,
            auto_create_suppliers=options.get("auto_create_suppliers", True),
            auto_match_orders=options.get("auto_match_orders", True),
        )
        db.commit()
        job.status = "done"
        job.total_rows = report.total_rows
        job.processed_rows = report.total_rows
        job.report = asdict(report)
    except excel_importer.CancelledImport as e:
        db.rollback()
        job.status = "cancelled"
        job.error = str(e)
    except excel_importer.ImporterError as e:
        db.rollback()
        job.status = "failed"
        job.error = str(e)
    except Exception as e:
        db.rollback()
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
    finally:
        job.completed_at = datetime.now(timezone.utc)
        db.commit()


def _run_job_safe(*args, **kwargs) -> None:
    """ThreadPoolExecutor 不上报异常; 这里兜底 log."""
    try:
        _run_job(*args, **kwargs)
    except Exception:  # pragma: no cover — 内层已处理
        _logger.exception("ImportJob 未捕获异常")


def _run_job(
    job_id: int,
    file_path: str,
    sheet_name: str,
    entity_type: str,
    mapping: dict[str, str],
    options: dict,
) -> None:
    from app.database import SessionLocal

    # 1) 标 running
    with SessionLocal() as s:
        job = s.get(ImportJob, job_id)
        if job is None:
            _cleanup_file(file_path)
            return
        # 提交期间用户可能已点取消
        if job.cancel_requested:
            job.status = "cancelled"
            job.error = "提交后立即取消"
            job.completed_at = datetime.now(timezone.utc)
            s.commit()
            _cleanup_file(file_path)
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        s.commit()

    # 2) 跑导入 (独立 session)
    import_db = SessionLocal()
    last_progress = [0, 0]   # 记上次写库的值, 避免每 50 行都写

    def progress(done: int, total: int) -> None:
        if done == last_progress[0] and total == last_progress[1]:
            return
        last_progress[0], last_progress[1] = done, total
        with SessionLocal() as s:
            j = s.get(ImportJob, job_id)
            if j is None:
                return
            j.processed_rows = done
            j.total_rows = total
            s.commit()

    def check_cancel() -> bool:
        """worker 每 50 行调一次, 读 cancel_requested 旗标."""
        with SessionLocal() as s:
            j = s.get(ImportJob, job_id)
            return bool(j and j.cancel_requested)

    try:
        report = excel_importer.commit_sheet(
            import_db, file_path=file_path,
            sheet_name=sheet_name, entity_type=entity_type, mapping=mapping,
            auto_create_suppliers=options.get("auto_create_suppliers", True),
            auto_match_orders=options.get("auto_match_orders", True),
            progress_callback=progress,
            cancel_callback=check_cancel,
        )
        import_db.commit()
    except excel_importer.CancelledImport as e:
        import_db.rollback()
        with SessionLocal() as s:
            j = s.get(ImportJob, job_id)
            if j is not None:
                j.status = "cancelled"
                j.error = str(e)
                j.completed_at = datetime.now(timezone.utc)
                s.commit()
        _cleanup_file(file_path)
        return
    except excel_importer.ImporterError as e:
        import_db.rollback()
        with SessionLocal() as s:
            j = s.get(ImportJob, job_id)
            if j is not None:
                j.status = "failed"
                j.error = str(e)
                j.completed_at = datetime.now(timezone.utc)
                s.commit()
        _cleanup_file(file_path)
        return
    except Exception as e:  # pragma: no cover
        import_db.rollback()
        _logger.exception("ImportJob %s 失败", job_id)
        with SessionLocal() as s:
            j = s.get(ImportJob, job_id)
            if j is not None:
                j.status = "failed"
                j.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[:2000]}"
                j.completed_at = datetime.now(timezone.utc)
                s.commit()
        _cleanup_file(file_path)
        return
    finally:
        import_db.close()

    # 3) 标 done + 写报告
    with SessionLocal() as s:
        j = s.get(ImportJob, job_id)
        if j is None:
            _cleanup_file(file_path)
            return
        j.status = "done"
        j.completed_at = datetime.now(timezone.utc)
        j.total_rows = report.total_rows
        j.processed_rows = report.total_rows
        j.report = asdict(report)
        s.commit()
    _cleanup_file(file_path)


# ----------------------------- 取消 ------------------------------- #


def request_cancel(db: Session, job_id: int) -> Optional[ImportJob]:
    """用户点 "取消" 按钮: 设 cancel_requested=True. worker 在下一次 tick 退出.

    返回 None 表示作业不存在. 已 done/failed/cancelled 的作业返回时不再 mutate。
    """
    job = db.get(ImportJob, job_id)
    if job is None:
        return None
    if job.status in ("done", "failed", "cancelled"):
        return job
    job.cancel_requested = True
    if job.status == "pending":
        # 还没进 worker, 直接标 cancelled (worker 上手就检测到不会跑)
        job.status = "cancelled"
        job.error = "用户在排队期取消"
        job.completed_at = datetime.now(timezone.utc)
    db.commit()
    return job


# ----------------------------- 读取 ------------------------------- #


def get_job(db: Session, job_id: int) -> Optional[ImportJob]:
    return db.get(ImportJob, job_id)


def list_jobs(db: Session, *, user_id: Optional[int] = None,
              limit: int = 50) -> list[ImportJob]:
    q = select(ImportJob).order_by(ImportJob.id.desc()).limit(limit)
    if user_id is not None:
        q = select(ImportJob).where(ImportJob.user_id == user_id) \
            .order_by(ImportJob.id.desc()).limit(limit)
    return list(db.execute(q).scalars())
