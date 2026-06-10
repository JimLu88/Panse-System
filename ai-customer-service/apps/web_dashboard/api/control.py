"""
apps/web_dashboard/api/control.py
===================================
POST 控制接口：紧急暂停 / 恢复全部接待。
写入 control_signal.json，主程序每 1s 轮询并执行。
"""
from __future__ import annotations

from fastapi import APIRouter

from apps.web_dashboard.ipc.state_reader import write_control_signal

router = APIRouter(prefix="/api", tags=["control"])


@router.post("/pause_all")
def pause_all() -> dict:
    """紧急暂停所有设备的接待（主程序 1s 内响应）。"""
    write_control_signal("pause_all")
    return {"ok": True, "message": "已发送暂停指令，主程序将在 1 秒内响应"}


@router.post("/resume_all")
def resume_all() -> dict:
    """恢复所有已暂停设备的接待。"""
    write_control_signal("resume_all")
    return {"ok": True, "message": "已发送恢复指令，主程序将在 1 秒内响应"}
