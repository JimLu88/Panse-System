"""Pydantic I/O schemas。"""
from __future__ import annotations

from pydantic import BaseModel


class TopicGenIn(BaseModel):
    category: str
    count: int = 3


class DraftGenIn(BaseModel):
    topic_id: int
    account_id: int | None = None


class ReviewActionIn(BaseModel):
    note: str = ""
    reason: str = ""


class ScheduleIn(BaseModel):
    content_id: int
    account_ids: list[int]


class HealthIn(BaseModel):
    post_alive_rate: float | None = None
    real_comment_rate: float | None = None


class MetricIn(BaseModel):
    content_id: int
    account_id: int
    views: int = 0
    likes: int = 0
    comments: int = 0
    collects: int = 0
    # 方式一：直接给比例（0-1）
    question_rate: float = 0.0
    interaction_rate: float = 0.0
    long_comment_ratio: float = 0.0
    # 方式二（普通人友好）：给条数，后端自动算比例
    question_comments: int | None = None   # 评论里"提问"的条数
    long_comments: int | None = None       # 超过15字的评论条数
    reply_comments: int | None = None      # 用户互相回复的条数


class LeadIn(BaseModel):
    source_type: str = "dm"
    contact: str = ""
    question: str = ""
    interest_category: str = ""
    attribution_code: str = ""
    source_account_id: int | None = None
    source_content_id: int | None = None


class LeadStatusIn(BaseModel):
    status: str


class LeadWonIn(BaseModel):
    erp_order_no: str


class ZhihuUpdateIn(BaseModel):
    status: str | None = None
    answer_url: str | None = None
    note: str | None = None


class MeetingIn(BaseModel):
    hot_case: str = ""
    flop_case: str = ""
    conclusion: str = ""


class ComplianceCheckIn(BaseModel):
    text: str


class AccountProfileIn(BaseModel):
    real_person: str | None = None
    device_note: str | None = None
    sim_note: str | None = None
    official_setup: dict | None = None
