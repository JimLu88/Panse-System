"""B: 看门狗重启目标 —— 容器内必须打 PID 1(否则 --reload/--workers 下父进程会重启子进程、
容器不退出, Docker 重启策略不触发); 非容器退回打自己, 安全。"""
from __future__ import annotations

from unittest.mock import patch

from app.services import system_monitor


def test_target_pid_in_container_is_pid1():
    with patch.object(system_monitor, "_in_container", return_value=True), \
         patch("app.services.system_monitor.os.getpid", return_value=42):
        assert system_monitor._restart_target_pid() == 1


def test_target_pid_when_self_is_pid1():
    with patch.object(system_monitor, "_in_container", return_value=True), \
         patch("app.services.system_monitor.os.getpid", return_value=1):
        assert system_monitor._restart_target_pid() == 1


def test_target_pid_non_container_is_self():
    with patch.object(system_monitor, "_in_container", return_value=False), \
         patch("app.services.system_monitor.os.getpid", return_value=42):
        assert system_monitor._restart_target_pid() == 42
