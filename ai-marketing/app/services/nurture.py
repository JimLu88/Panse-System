"""⑨ 养号 SOP 引擎。对应 09-account-nurturing.md。

系统排程人工任务清单 + 打卡 + 三阶段成长门槛。不做自动脚本养号。
复用 Panse-System ops_checklist 的 period_key 模式。
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, NurtureTask

# 每日养号任务模板（任务量给浮动范围，避免机械动作）
_DAILY_TASKS = [
    ("browse", "浏览同城/家居笔记 {n} 分钟", (8, 15)),
    ("like", "点赞优质笔记 {n} 条", (6, 12)),
    ("collect", "收藏家居灵感 {n} 条", (2, 5)),
    ("follow", "关注同好/家居博主 {n} 个", (1, 3)),
    ("profile", "完善/微调资料(每周1次)", None),
]

# 三阶段晋级门槛
STAGE_GATES = {
    "nurturing": {"next": "trial", "min_days": 14, "desc": "养号期：只浏览/点赞/收藏，不发原创"},
    "trial": {"next": "active", "min_days": 15, "min_alive": 0.8, "desc": "试发期：隔天1篇，看存活率"},
    "active": {"next": None, "desc": "正式期：进发布队列 + 可承接评论任务"},
}


def today_tasks(db: Session, account_id: int) -> dict:
    """生成/读取某号当日养号清单。"""
    account = db.get(Account, account_id)
    if account is None:
        raise ValueError("账号不存在")
    period = dt.date.today().isoformat()

    existing = list(db.scalars(
        select(NurtureTask).where(NurtureTask.account_id == account_id,
                                  NurtureTask.period_key == period)
    ))
    if not existing:
        for key, tmpl, rng in _DAILY_TASKS:
            target = tmpl.format(n=random.randint(*rng)) if rng else tmpl
            db.add(NurtureTask(account_id=account_id, period_key=period,
                               task_key=key, target=target))
        db.commit()
        existing = list(db.scalars(
            select(NurtureTask).where(NurtureTask.account_id == account_id,
                                      NurtureTask.period_key == period)
        ))

    return {
        "account_id": account_id,
        "stage": account.stage,
        "stage_desc": STAGE_GATES[account.stage]["desc"],
        "period": period,
        "tasks": [{"id": t.id, "key": t.task_key, "target": t.target, "done": t.done}
                  for t in existing],
        "done_count": sum(1 for t in existing if t.done),
        "total": len(existing),
    }


def check_task(db: Session, task_id: int) -> NurtureTask:
    t = db.get(NurtureTask, task_id)
    if t is None:
        raise ValueError("任务不存在")
    t.done = True
    t.done_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return t


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
    if "min_alive" in gate and a.post_alive_rate < gate["min_alive"]:
        return {"promoted": False, "stage": a.stage,
                "reason": f"笔记存活率 {a.post_alive_rate} < {gate['min_alive']}"}
    if a.health_flag == "red":
        return {"promoted": False, "stage": a.stage, "reason": "健康红牌，不能晋级"}

    a.stage = gate["next"]
    a.stage_since = dt.date.today()
    db.commit()
    return {"promoted": True, "stage": a.stage}
