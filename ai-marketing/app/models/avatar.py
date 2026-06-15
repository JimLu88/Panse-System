"""数字人域：数字人档案 + 视频渲染任务。对标 InfiniteTalk/Duix-Avatar/HeyGen。

数字分身 = 绑定真人(老板/设计师)的授权头像，语音克隆+对口型，把口播脚本→成片，
还能 24/7 出个性化短视频/私信视频。渲染走可插拔 provider(mock/真实数字人服务)。
⚠️ 合规：必须真人书面授权(authorized) 才能用其声音/肖像。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AvatarProfile(Base):
    """数字人/数字分身档案。"""

    __tablename__ = "avatar_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))               # 数字人名(如"畔色老板IP")
    real_person: Mapped[str] = mapped_column(String(40), default="")  # 绑定真人
    bound_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 绑定的小红书号
    voice_sample_ref: Mapped[str] = mapped_column(String(300), default="")  # 声音样本(克隆源)
    face_ref: Mapped[str] = mapped_column(String(300), default="")          # 肖像样本
    authorized: Mapped[bool] = mapped_column(default=False)     # 真人是否书面授权(合规闸)
    persona: Mapped[dict] = mapped_column(JSON, default=dict)   # 口吻/语速/风格
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft/ready
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class VideoJob(Base):
    """口播脚本 → 数字人成片 的渲染任务。"""

    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar_profiles.id"), index=True)
    content_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 来源口播脚本草稿
    job_type: Mapped[str] = mapped_column(String(16), default="note")  # note(笔记视频)/dm(私信视频)
    script: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[str] = mapped_column(String(80), default="")  # 私信视频的对象(个性化)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending/rendering/done/failed
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    output_url: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
