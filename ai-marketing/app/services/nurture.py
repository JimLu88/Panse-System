"""⑨ 养号 SOP 引擎。对应 09-account-nurturing.md。

系统排程人工任务清单 + 打卡 + 三阶段成长门槛。不做自动脚本养号。
复用 Panse-System ops_checklist 的 period_key 模式（日任务=YYYY-MM-DD，周任务=YYYY-Www）。
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, NurtureTask

# 每日养号任务模板（任务量给浮动范围，避免机械动作）
_DAILY_TASKS = [
    ("browse", "浏览同城/家居笔记 {n} 分钟", (8, 15)),
    ("like", "点赞优质笔记 {n} 条", (6, 12)),
    ("collect", "收藏家居灵感 {n} 条", (2, 5)),
    ("follow", "关注同好/家居博主 {n} 个", (1, 3)),
]
_WEEKLY_TASKS = [
    ("profile", "完善/微调资料（每周1次）"),
]

# 三阶段晋级门槛
STAGE_GATES = {
    "nurturing": {"next": "trial", "min_days": 14, "min_checkin_days": 10,
                  "desc": "养号期：只浏览/点赞/收藏，不发原创"},
    "trial": {"next": "active", "min_days": 15, "min_alive": 0.8,
              "desc": "试发期：隔天1篇，看存活率"},
    "active": {"next": None, "desc": "正式期：进发布队列 + 可承接评论任务"},
}


def _week_key(day: dt.date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def today_tasks(db: Session, account_id: int) -> dict:
    """生成/读取某号当日养号清单（日任务 + 本周周任务 + 矩阵互动）。"""
    account = db.get(Account, account_id)
    if account is None:
        raise ValueError("账号不存在")
    today = dt.date.today()
    day_key = today.isoformat()
    week_key = _week_key(today)

    def _load(period: str) -> list[NurtureTask]:
        return list(db.scalars(
            select(NurtureTask).where(NurtureTask.account_id == account_id,
                                      NurtureTask.period_key == period)
        ))

    daily = _load(day_key)
    if not daily:
        # #2 差异化养号：任务量按账号+日期播种，每号节奏稳定但各不相同（非机械统一）
        rnd = random.Random(f"{account_id}-{day_key}")
        for key, tmpl, rng in _DAILY_TASKS:
            db.add(NurtureTask(account_id=account_id, period_key=day_key,
                               task_key=key, target=tmpl.format(n=rnd.randint(*rng))))
        # #1 矩阵互动换量（抱团）：给一个不同的矩阵号互动，控比例防被识别为养号团
        peer = db.scalar(
            select(Account).where(Account.id != account_id,
                                  Account.stage.in_(["active", "trial"]))
            .order_by(Account.id)
        )
        if peer:
            db.add(NurtureTask(account_id=account_id, period_key=day_key, task_key="matrix",
                               target=f"给矩阵号「{peer.nickname}」真实互动1-2条(点赞/评论，别天天同一个号)"))
        db.commit()
        daily = _load(day_key)

    weekly = _load(week_key)
    if not weekly:
        for key, target in _WEEKLY_TASKS:
            db.add(NurtureTask(account_id=account_id, period_key=week_key,
                               task_key=key, target=target))
        db.commit()
        weekly = _load(week_key)

    tasks = daily + weekly
    return {
        "account_id": account_id,
        "stage": account.stage,
        "stage_desc": STAGE_GATES[account.stage]["desc"],
        "period": day_key,
        "tasks": [{"id": t.id, "key": t.task_key, "target": t.target, "done": t.done,
                   "scope": "week" if t.period_key == week_key else "day"}
                  for t in tasks],
        "done_count": sum(1 for t in tasks if t.done),
        "total": len(tasks),
        "checkin_days": checkin_days(db, account_id),
    }


def check_task(db: Session, task_id: int) -> NurtureTask:
    t = db.get(NurtureTask, task_id)
    if t is None:
        raise ValueError("任务不存在")
    t.done = True
    t.done_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return t


def checkin_days(db: Session, account_id: int) -> int:
    """累计日活打卡天数：有任意已完成日任务的不同日期数（周任务不算天）。"""
    return db.scalar(
        select(func.count(func.distinct(NurtureTask.period_key))).where(
            NurtureTask.account_id == account_id,
            NurtureTask.done.is_(True),
            func.length(NurtureTask.period_key) == 10,  # YYYY-MM-DD
        )
    ) or 0


def try_promote(db: Session, account_id: int) -> dict:
    """检查是否满足晋级门槛（养号期→试发期→正式期）。"""
    a = db.get(Account, account_id)
    if a is None:
        raise ValueError("账号不存在")
    gate = STAGE_GATES[a.stage]
    if gate["next"] is None:
        return {"promoted": False, "stage": a.stage, "reason": "已是正式期"}

    days = (dt.date.today() - a.stage_since).days
    if days < gate["min_days"]:
        return {"promoted": False, "stage": a.stage,
                "reason": f"在本阶段 {days} 天，需满 {gate['min_days']} 天"}
    if "min_checkin_days" in gate:
        done_days = checkin_days(db, account_id)
        if done_days < gate["min_checkin_days"]:
            return {"promoted": False, "stage": a.stage,
                    "reason": f"日活打卡 {done_days} 天，需满 {gate['min_checkin_days']} 天"}
    if "min_alive" in gate and a.post_alive_rate < gate["min_alive"]:
        return {"promoted": False, "stage": a.stage,
                "reason": f"笔记存活率 {a.post_alive_rate} < {gate['min_alive']}"}
    if a.health_flag == "red":
        return {"promoted": False, "stage": a.stage, "reason": "健康红牌，不能晋级"}

    a.stage = gate["next"]
    a.stage_since = dt.date.today()
    db.commit()
    return {"promoted": True, "stage": a.stage}
