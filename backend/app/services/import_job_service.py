"""异步 Excel 导入作业 (业务需求 6).

POST /api/importer/commit-async →
    1) 创建 ImportJob row (status=pending)
    2) submit 到 ThreadPoolExecutor
    3) 立刻返回 job_id

Worker:
    - 改 status=running, started_at
    - 跑 excel_importer.commit_sheet(progress_callback=...)
    - 进度每 50 行向 ImportJob.processed_rows 写一次 (单独 session)
    - 完成: status=done, report=asdict(ImportReport), completed_at
    - 异常: status=failed, error=traceback, completed_at

GET /api/importer/jobs/{id} → 前端每 2s 轮询
"""
from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_job import ImportJob
from app.services import excel_importer

_logger = logging.getLogger("panse.import_job")

# 单 worker 即可: 一次只跑一个导入, 避免多 100MB 同时挤爆内存
_EXECUTOR: Optional[ThreadPoolExecutor] = None
_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="import-job")
        return _EXECUTOR


def shutdown_executor() -> None:
    """shutdown hook 调; 等当前作业完成后停掉."""
    global _EXECUTOR
    with _LOCK:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _EXECUTOR = None


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

    inline=True: 同步跑 (测试用)
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
        # 测试用: 在调用方的 session 里同步跑 (不开新 SessionLocal)
        _run_job_inline(db, job, file_bytes, sheet_name, entity_type, mapping, options)
    else:
        _executor().submit(
            _run_job_safe, job.id, file_bytes, sheet_name, entity_type, mapping, options,
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
    file_bytes: bytes,
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
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        s.commit()

    # 2) 跑导入 (独立 session)
    import_db = SessionLocal()
    last_progress = [0, 0]   # 记上次写库的值, 避免每 50 行都写

    def progress(done: int, total: int) -> None:
        # 进度更新: 只在变化≥50 或 total/done 首次报告时写
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

    try:
        report = excel_importer.commit_sheet(
            import_db, file_bytes=file_bytes,
            sheet_name=sheet_name, entity_type=entity_type, mapping=mapping,
            auto_create_suppliers=options.get("auto_create_suppliers", True),
            auto_match_orders=options.get("auto_match_orders", True),
            progress_callback=progress,
        )
        import_db.commit()
    except excel_importer.ImporterError as e:
        import_db.rollback()
        with SessionLocal() as s:
            j = s.get(ImportJob, job_id)
            if j is not None:
                j.status = "failed"
                j.error = str(e)
                j.completed_at = datetime.now(timezone.utc)
                s.commit()
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
        return
    finally:
        import_db.close()

    # 3) 标 done + 写报告
    with SessionLocal() as s:
        j = s.get(ImportJob, job_id)
        if j is None:
            return
        j.status = "done"
        j.completed_at = datetime.now(timezone.utc)
        j.total_rows = report.total_rows
        j.processed_rows = report.total_rows
        # ImportReport → dict (Decimal 没在里面, 都是 int / list[str])
        j.report = asdict(report)
        s.commit()


def get_job(db: Session, job_id: int) -> Optional[ImportJob]:
    return db.get(ImportJob, job_id)


def list_jobs(db: Session, *, user_id: Optional[int] = None,
              limit: int = 50) -> list[ImportJob]:
    q = select(ImportJob).order_by(ImportJob.id.desc()).limit(limit)
    if user_id is not None:
        q = select(ImportJob).where(ImportJob.user_id == user_id) \
            .order_by(ImportJob.id.desc()).limit(limit)
    return list(db.execute(q).scalars())
