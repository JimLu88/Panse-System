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
    question_rate: float = 0.0
    interaction_rate: float = 0.0
    long_comment_ratio: float = 0.0


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
