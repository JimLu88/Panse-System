"""运营台账：把运营制度做成日/周/月周期打卡任务。

覆盖评审建议：实拍素材日(月) / 复盘会(周) / 数据录入(日) / 私信响应(日) /
发布打卡(日) / 评论打卡(日) / 投放记账(月,记入ERP) / 设备盘点(月) / 敏感词增补(周)。
period_key 跨周期自动重置（同 Panse-System ops_checklist 模式）。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OpsTask

# (scope, task_key, 标题——引导语直接写在任务里，普通人照做即可)
TEMPLATES = [
    ("day", "dm_reply", "回复私信与评论：上午/下午各查一次，家具客户在比价期，响应快=成单"),
    ("day", "data_entry", "数据录入(5分钟)：把昨天发布笔记的曝光/赞藏录进「发布队列→录数据」"),
    ("day", "publish", "发布打卡：到「发布队列」把今天到点的笔记人工发出"),
    ("day", "comment", "评论引流打卡：到「评论引流」处理今日机会（每号≤5条）"),
    ("week", "review_meeting", "复盘会30分钟：到「数据复盘」拆1篇爆款+1篇扑款，写下结论"),
    ("week", "banned_words", "敏感词增补：把本周文案踩到的雷词告诉管理员加进违禁词库"),
    ("week", "health_check", "账号健康巡检：到「账号管理」更新各号笔记存活率"),
    ("month", "shoot_day", "实拍素材日：集中1-2天拍产品场景图/短视频入素材库——AI管文案，实拍管生死"),
    ("month", "paid_budget", "付费投放记账：本月蒲公英/薯条支出记入 ERP 营销页（建议每月固定小预算测爆款放大）"),
    ("month", "device_audit", "设备台账盘点：到「账号管理」核对每号绑定手机/手机卡有无变更——一机多号是连坐封号首因"),
]


def _period_key(scope: str, day: dt.date) -> str:
    if scope == "day":
        return day.isoformat()
    if scope == "week":
        iso = day.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return day.strftime("%Y-%m")


def today(db: Session) -> dict:
    """读取/生成当期运营台账（按日/周/月各自的 period 自动重置）。"""
    day = dt.date.today()
    for scope, key, title in TEMPLATES:
        period = _period_key(scope, day)
        task = db.scalar(select(OpsTask).where(OpsTask.task_key == key,
                                               OpsTask.period_key == period))
        if task is None:
            db.add(OpsTask(period_key=period, scope=scope, task_key=key, title=title))
            db.flush()
    db.commit()
    rows = []
    for scope, key, _ in TEMPLATES:
        period = _period_key(scope, day)
        t = db.scalar(select(OpsTask).where(OpsTask.task_key == key,
                                            OpsTask.period_key == period))
        rows.append({"id": t.id, "scope": scope, "title": t.title, "done": t.done})
    done = sum(1 for r in rows if r["done"])
    return {"tasks": rows, "done": done, "total": len(rows)}


def toggle(db: Session, task_id: int) -> OpsTask:
    t = db.get(OpsTask, task_id)
    if t is None:
        raise ValueError("任务不存在")
    t.done = not t.done
    t.done_at = dt.datetime.now(dt.timezone.utc) if t.done else None
    db.commit()
    return t
