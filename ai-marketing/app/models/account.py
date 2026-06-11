"""账号域：账号档案 + 养号任务。

对应 05-account-manager.md（档案/操作两层分离）与 09-account-nurturing.md。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Account(Base):
    """⑤ 账号档案（纯数据层，零风险）。"""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nickname: Mapped[str] = mapped_column(String(80))
    platform: Mapped[str] = mapped_column(String(10), default="xhs")
    role: Mapped[str] = mapped_column(String(20), default="persona")  # brand/persona/review/spare/zhihu
    # 性格档案（注入生成 prompt，矩阵号同质化的根本解）
    voice_persona: Mapped[dict] = mapped_column(JSON, default=dict)
    negative_words: Mapped[list] = mapped_column(JSON, default=list)
    topic_affinity: Mapped[dict] = mapped_column(JSON, default=dict)

    follower_count: Mapped[int] = mapped_column(Integer, default=0)

    # 运营管理字段（评审建议2/11/13）：
    real_person: Mapped[str] = mapped_column(String(40), default="")   # 人设号绑定的真人（同事）
    device_note: Mapped[str] = mapped_column(String(80), default="")   # 绑定手机/设备
    sim_note: Mapped[str] = mapped_column(String(40), default="")      # 手机卡
    official_setup: Mapped[dict] = mapped_column(JSON, default=dict)   # 专业号官方功能开通清单

    # 养号阶段：nurturing(养号期)/trial(试发期)/active(正式期)
    stage: Mapped[str] = mapped_column(String(12), default="nurturing")
    stage_since: Mapped[dt.date] = mapped_column(Date, default=lambda: dt.date.today())

    # 健康心跳
    post_alive_rate: Mapped[float] = mapped_column(Float, default=1.0)
    real_comment_rate: Mapped[float] = mapped_column(Float, default=0.0)
    health_score: Mapped[int] = mapped_column(Integer, default=100)
    health_flag: Mapped[str] = mapped_column(String(10), default="green")  # green/yellow/red
    driver_mode: Mapped[str] = mapped_column(String(10), default="assist")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class NurtureTask(Base):
    """⑨ 养号任务打卡记录（system_settings-JSON 模式的关系表落地）。"""

    __tablename__ = "nurture_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    period_key: Mapped[str] = mapped_column(String(20), index=True)  # YYYY-MM-DD
    task_key: Mapped[str] = mapped_column(String(40))  # browse/like/collect/follow/profile
    target: Mapped[str] = mapped_column(String(40), default="")
    done: Mapped[bool] = mapped_column(default=False)
    done_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
