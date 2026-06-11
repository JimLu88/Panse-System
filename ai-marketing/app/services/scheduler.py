"""轻量调度器：替代"全靠人打开页面才知道"。

每小时跑一轮：
1. 为所有账号生成当日养号清单（运营忘了打开也有任务在等）
2. 扫超 48h 未跟进线索
3. 扫已到发布时间但还没人点发的 ASSIST 事件
结果存内存摘要，工作台 /api/digest 拉取展示。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select

from ..database import SessionLocal
from ..models import Account, PublishEvent
from . import lead_inbox, nurture

log = logging.getLogger("marketing.scheduler")

DIGEST: dict = {"generated_at": None, "overdue_leads": 0, "due_publishes": 0,
                "nurture_accounts": 0}

INTERVAL_SECONDS = 3600


def run_once() -> dict:
    db = SessionLocal()
    try:
        # 1. 全账号生成今日养号清单
        accounts = list(db.scalars(select(Account)))
        for a in accounts:
            nurture.today_tasks(db, a.id)

        # 2. 超期线索
        overdue = sum(1 for l in lead_inbox.list_leads(db) if l["overdue_48h"])

        # 3. 到点未发的 ASSIST 事件
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        due = db.scalars(
            select(PublishEvent).where(PublishEvent.result == "pending",
                                       PublishEvent.scheduled_at <= now)
        ).all()

        DIGEST.update({
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "overdue_leads": overdue,
            "due_publishes": len(due),
            "nurture_accounts": len(accounts),
        })
        return dict(DIGEST)
    finally:
        db.close()


async def loop() -> None:
    while True:
        try:
            run_once()
            log.info("scheduler digest: %s", DIGEST)
        except Exception:  # 单轮失败不杀循环
            log.exception("scheduler run failed")
        await asyncio.sleep(INTERVAL_SECONDS)
