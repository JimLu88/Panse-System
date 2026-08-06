# -*- coding: utf-8 -*-
"""关键任务错过触发补跑 (用户 2026-07-13 "现在补上")。

部署/重启撞上触发点 → 进程内调度器该班直接丢(实锤: 07-11/07-13 两次 18:00 取数被打断,
发货报表断供)。启动 60s 后按名单查"该班已过+宽限内+无运行记录"→ 依序补跑。
"""
from datetime import datetime, timedelta, timezone

from app.models.scheduled_job import ScheduledJobRun
from app.services import scheduler as sched


def _now_at(hour: int, minute: int = 0) -> datetime:
    """今天某时刻(带本地时区)。"""
    return datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)


def _run_row(db, job_id: str, when: datetime):
    db.add(ScheduledJobRun(job_id=job_id, job_label=job_id, status="ok",
                           started_at=when.astimezone(timezone.utc)))
    db.flush()


def setup_module(_m):
    sched._register_default_jobs()


def test_missed_jobs_detected_in_order(db_session):
    """19:00 时: 18:00取数/18:30日报 缺班 → 按名单顺序补; 22:50/06:50 超宽限不追。"""
    missed = sched.missed_catchup_jobs(db_session, now=_now_at(19, 0), overrides={})
    assert missed == ["daily_0630_web_agent", "daily_1810_order_sheets"]


def test_run_row_suppresses_catchup(db_session):
    """该班已有运行记录(如 18:37 手动补跑过) → 不再补; 日报仍缺 → 只补日报。"""
    _run_row(db_session, "daily_0630_web_agent", _now_at(18, 37))
    db_session.commit()
    missed = sched.missed_catchup_jobs(db_session, now=_now_at(19, 0), overrides={})
    assert missed == ["daily_1810_order_sheets"]


def test_failed_run_is_eligible_for_one_startup_catchup(db_session):
    """A failed critical run is not mistaken for completion after restart."""
    fire = _now_at(22, 50)
    db_session.add(ScheduledJobRun(job_id="daily_0230_orders_maintain", job_label="x",
                                   status="fail", started_at=fire.astimezone(timezone.utc)))
    db_session.commit()
    missed = sched.missed_catchup_jobs(db_session, now=fire + timedelta(minutes=40), overrides={})
    assert "daily_0230_orders_maintain" in missed


def test_beyond_grace_not_chased(db_session):
    """超宽限不追: 第二天中午(18:00 班 18 小时前) → 取数不补(等当天正常班)。"""
    missed = sched.missed_catchup_jobs(
        db_session, now=_now_at(12, 0), overrides={})
    assert "daily_0630_web_agent" not in missed
    assert "daily_1810_order_sheets" not in missed


def test_disabled_job_not_chased(db_session):
    """用户停用的任务不补跑。"""
    missed = sched.missed_catchup_jobs(
        db_session, now=_now_at(19, 0),
        overrides={"daily_0630_web_agent": {"enabled": False}})
    assert "daily_0630_web_agent" not in missed
    assert "daily_1810_order_sheets" in missed
