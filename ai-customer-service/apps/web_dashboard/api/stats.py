"""
apps/web_dashboard/api/stats.py
================================
GET 只读接口：概览 / 设备列表 / 最近消息。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from apps.web_dashboard.ipc.state_reader import (
    read_devices,
    read_overview,
    read_recent_msgs,
)

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/overview")
def get_overview() -> dict:
    """总览：今日接待量、在线设备数、异常设备数、最后更新时间。"""
    return read_overview()


@router.get("/devices")
def get_devices() -> list[dict]:
    """所有设备的当前状态列表。"""
    return read_devices()


@router.get("/recent_msgs")
def get_recent_msgs(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """最近接待记录（买家昵称已脱敏）。"""
    return read_recent_msgs(limit=limit)
