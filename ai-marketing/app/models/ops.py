"""运营域：运营台账（全局周期任务）/ 知乎占坑 / 复盘记录。

运营台账 = 把"实拍素材日/复盘会/数据录入/投放记账/设备盘点"等制度
做成系统排程的打卡任务（ops_checklist 模式：period_key 跨周期自动重置）。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class OpsTask(Base):
    """运营台账任务：日/周/月周期打卡。"""

    __tablename__ = "ops_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_key: Mapped[str] = mapped_column(String(20), index=True)  # 日YYYY-MM-DD/周YYYY-Www/月YYYY-MM
    scope: Mapped[str] = mapped_column(String(8), default="day")  # day/week/month
    task_key: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    done: Mapped[bool] = mapped_column(default=False)
    done_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class ZhihuQuestion(Base):
    """知乎长答案占坑：20 个高搜索问题的写作进度。"""

    __tablename__ = "zhihu_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="todo")  # todo/writing/posted
    answer_url: Mapped[str] = mapped_column(String(300), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ReviewMeeting(Base):
    """每周复盘会记录：爆款/扑款各拆一篇 + 结论（爆款指纹的人工版）。"""

    __tablename__ = "review_meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_key: Mapped[str] = mapped_column(String(12), index=True)  # YYYY-Www
    hot_case: Mapped[str] = mapped_column(Text, default="")   # 爆款拆解
    flop_case: Mapped[str] = mapped_column(Text, default="")  # 扑款拆解
    conclusion: Mapped[str] = mapped_column(Text, default="")  # 下周行动结论
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
