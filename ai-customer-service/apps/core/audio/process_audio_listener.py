"""
千牛新消息：以「系统混音」音量峰值为触发器，再进入视觉流程。

监听策略：轮询 **所有 WASAPI 会话** 的音量表计，取全局最大峰值（不绑定某一进程 PID），
因此系统里任意应用/通知音（含千牛叮咚走 PID=0 的系统声道）都会反映在该峰值上。

触发后由编排层执行：千牛置前 → 左侧会话列表 ROI 像素差分找闪烁/未读行并点击 →
再截图聊天区 OCR（需在店铺 YAML 配置 session_list_rect、unread_session_switch 等）。

可选闸门：仅当配置进程名（默认 AliWorkbench.exe）在运行时才真正触发接待，避免未开千牛时
听音乐等误触发；关闭闸门则「只要有系统声音」即跑一轮视觉+接待（慎用）。
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

try:
    from apps.core.audio._wasapi_ctypes import get_global_peak as _wasapi_global_peak
    from apps.core.audio._wasapi_ctypes import get_all_session_peaks as _wasapi_session_peaks
    _WASAPI_OK = True
except Exception:  # pragma: no cover
    _WASAPI_OK = False
    def _wasapi_global_peak(): return None  # type: ignore[misc]
    def _wasapi_session_peaks(pid_name_map=None): return []  # type: ignore[misc]

# Legacy pycaw kept for reference but no longer used
AudioUtilities = None


@dataclass(slots=True)
class AudioPeakListenerConfig:
    target_exe_name: str = "AliWorkbench.exe"
    peak_threshold: float = 0.02
    #: 低于此峰值视为环境底噪，不触发（与 peak_threshold 取较大者）
    min_peak_threshold: float = 0.02
    poll_interval_s: float = 0.08
    cooldown_s: float = 0.45
    #: True：仅当 target_exe_name 对应进程在跑时才 on_trigger（峰值仍始终读全系统）
    gate_fire_only_when_target_exe_running: bool = True


OnTrigger = Callable[[], None]
OnDiag = Callable[[str], None]


def _tasklist_pids(name_fragment: str) -> list[int]:
    """用 Windows tasklist 命令枚举进程（绕过 AlibabaProtect 等对 psutil API 的封锁）。
    只在 psutil 方式返回空时作为 fallback 调用，开销约 50~100ms/次，5s 才一轮可接受。
    """
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            timeout=3,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stderr=subprocess.DEVNULL,
        ).decode("gbk", errors="replace")
    except Exception:
        return []
    needle = name_fragment.lower()
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip().strip('"')
        if not line:
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        nm = parts[0].lower()
        if needle in nm:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return sorted(set(pids))


def _find_pids_by_exe(name: str) -> list[int]:
    """先用 psutil 查，若全部被 AlibabaProtect 封锁则 fallback 到 tasklist。"""
    needle = name.lower()
    out: list[int] = []
    if psutil is not None:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pn = (p.info.get("name") or "").lower()
                if needle in pn:
                    out.append(int(p.info["pid"]))
            except (psutil.Error, TypeError, ValueError):
                continue
    if not out:
        # psutil 找不到（可能被进程保护拦截），用 tasklist 兜底
        out = _tasklist_pids(needle)
    return sorted(set(out))


def search_audio_candidate_processes(
    keywords: tuple[str, ...] = ("workbench", "qianniu", "千牛", "alimm")
) -> list[tuple[int, str]]:
    """供「搜索千牛进程名」按钮：列出名字包含任一关键词的进程（PID, 名字）。
    先用 psutil，被封锁时 fallback 到 tasklist（可绕过 AlibabaProtect）。
    """
    out: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    # psutil 路径
    if psutil is not None:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                nm = str(p.info.get("name") or "")
                if not nm:
                    continue
                low = nm.lower()
                if any(kw.lower() in low for kw in keywords):
                    key = (int(p.info["pid"]), nm)
                    if key not in seen:
                        seen.add(key)
                        out.append(key)
            except (psutil.Error, TypeError, ValueError):
                continue

    # tasklist fallback（psutil 被封锁时补充）
    try:
        raw = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            timeout=3,
            creationflags=0x08000000,
            stderr=subprocess.DEVNULL,
        ).decode("gbk", errors="replace")
        for line in raw.splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) < 2:
                continue
            nm = parts[0]
            low = nm.lower()
            if any(kw.lower() in low for kw in keywords):
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                key = (pid, nm)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    except Exception:
        pass

    out.sort(key=lambda x: (x[1].lower(), x[0]))
    return out


def _pid_to_name_map() -> dict[int, str]:
    """用 tasklist 构建 PID→exe_name 映射，补全 psutil/pycaw 因 AlibabaProtect 拿不到的名字。"""
    m: dict[int, str] = {}
    try:
        raw = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            timeout=3,
            creationflags=0x08000000,
            stderr=subprocess.DEVNULL,
        ).decode("gbk", errors="replace")
        for line in raw.splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) < 2:
                continue
            try:
                m[int(parts[1])] = parts[0]
            except ValueError:
                pass
    except Exception:
        pass
    return m


def enumerate_sessions_now() -> list[tuple[int, str, float, bool]] | str:
    """供「实时峰值监视」按钮使用：返回当前所有 WASAPI 会话的 (pid, exe_name, peak, has_meter)。

    用纯 ctypes WASAPI 实现，无需 pycaw/comtypes；打包后也可用。
    """
    if not _WASAPI_OK:
        return "WASAPI ctypes 模块加载失败（Windows 非预期平台？）"
    pid_map = _pid_to_name_map()
    try:
        raw = _wasapi_session_peaks(pid_map)
    except Exception as e:
        return f"_wasapi_session_peaks 异常：{e!r}"
    if not raw:
        gp = _wasapi_global_peak()
        if gp is None:
            try:
                from apps.core.audio._wasapi_ctypes import diagnose
                return "WASAPI 初始化失败，详细诊断：\n" + diagnose()
            except Exception as e:
                return f"WASAPI 不可用，诊断也失败：{e!r}"
        return []  # WASAPI 可用，但当前无活跃会话
    out = [(pid, nm, peak, True) for pid, nm, peak in raw]
    out.sort(key=lambda x: (-x[2], x[1].lower(), x[0]))
    return out


def _find_pid_by_exe(name: str) -> int | None:
    pids = _find_pids_by_exe(name)
    return pids[0] if pids else None


def _all_sessions_peak() -> float | None:
    """返回所有 WASAPI 会话的最大峰值；完全无法枚举时返回 None。"""
    if not _WASAPI_OK:
        return None
    # 优先用全局设备峰值（更快、更可靠）
    gp = _wasapi_global_peak()
    return gp  # None = WASAPI 不可用；0.0~1.0 = 正常


def _session_peak_for_pid(pid: int) -> float | None:
    """返回指定 PID 的峰值（仅用于诊断）；该 PID 在 WASAPI 中无会话时返回 None。"""
    if not _WASAPI_OK:
        return None
    sessions = _wasapi_session_peaks()
    for spid, _nm, peak in sessions:
        if spid == pid:
            return peak
    return None


class ProcessAudioPeakListener:
    """
    后台线程轮询峰值；超过阈值触发 on_trigger（应尽快返回，由编排层异步跑 OCR/LLM）。
    """

    def __init__(
        self,
        cfg: AudioPeakListenerConfig,
        *,
        on_trigger: OnTrigger,
        on_diag: OnDiag | None = None,
    ) -> None:
        self._cfg = cfg
        self._on_trigger = on_trigger
        self._on_diag = on_diag
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fire = 0.0
        self._warned_no_psutil = False
        self._warned_no_pycaw = False
        self._warned_no_process = False
        self._warned_no_meter = False
        self._diag_stall_ticks = 0
        self._peak_max_since_bind: float = 0.0
        self._peak_report_deadline: float = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="AudioPeakListener", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=float(join_timeout_s))

    def _diag(self, msg: str) -> None:
        if self._on_diag is not None:
            try:
                self._on_diag(msg)
            except Exception:
                pass

    def _run(self) -> None:
        com_inited = False
        try:
            try:
                import pythoncom

                pythoncom.CoInitialize()
                com_inited = True
            except Exception:
                pass

            _warned_enum_ok = False
            last_qn_check = 0.0
            qn_running = True
            self._peak_report_deadline = time.monotonic() + 15.0

            while not self._stop.is_set():
                if psutil is None and not self._warned_no_psutil:
                    self._warned_no_psutil = True
                    self._diag(
                        "听觉诊断：未安装 psutil，已改用 tasklist 检查千牛进程；"
                        "建议 pip install psutil 以获得更稳定的进程守卫。"
                    )

                if not _WASAPI_OK and not self._warned_no_pycaw:
                    self._warned_no_pycaw = True
                    self._diag("听觉诊断：WASAPI ctypes 模块不可用，音量监听已跳过。")
                    time.sleep(2.0)
                    continue

                now = time.monotonic()
                if self._cfg.gate_fire_only_when_target_exe_running and (
                    now - last_qn_check >= 5.0 or last_qn_check == 0.0
                ):
                    last_qn_check = now
                    qn_running = bool(_find_pids_by_exe(self._cfg.target_exe_name))
                    if not qn_running and not self._warned_no_process:
                        self._warned_no_process = True
                        self._diag(
                            f"听觉诊断：未找到进程「{self._cfg.target_exe_name}」。"
                            f"在千牛启动前不会触发接待。"
                        )
                    if qn_running:
                        self._warned_no_process = False

                peak = _all_sessions_peak()
                if peak is None:
                    self._diag_stall_ticks += 1
                    if self._diag_stall_ticks == 25 and not self._warned_no_meter:
                        self._warned_no_meter = True
                        self._diag(
                            "听觉诊断：无法枚举系统音量会话（可能缺少权限或声卡驱动不兼容）。"
                            "请以管理员身份运行，或改用「人工测试」。"
                        )
                    time.sleep(self._cfg.poll_interval_s)
                    continue

                if not _warned_enum_ok:
                    _warned_enum_ok = True
                    self._diag(
                        "听觉诊断：正在监听「全系统混音」峰值；有新声音后将先置前千牛，"
                        "再在会话列表里找闪烁/未读行并点击，然后截图聊天区。"
                    )

                self._diag_stall_ticks = 0

                if peak > self._peak_max_since_bind:
                    self._peak_max_since_bind = peak

                if self._peak_report_deadline > 0 and now >= self._peak_report_deadline:
                    self._peak_report_deadline = 0.0
                    mx = self._peak_max_since_bind
                    if mx >= self._cfg.peak_threshold:
                        pass
                    elif mx > 0.001:
                        self._diag(
                            f"听觉诊断：检测到音量信号（最大峰值 {mx:.3f}），"
                            f"但低于触发阈值 {self._cfg.peak_threshold:.3f}。"
                            f"可在「系统设置」调低叮咚触发灵敏度。"
                        )
                    else:
                        self._diag(
                            f"听觉诊断：15 秒内全系统峰值约 {mx:.4f}，未听到明显声音。"
                            f"请确认系统未静音；或改用「人工测试」。"
                        )

                try:
                    from apps.core.ai.input_quality_gate import load_min_audio_peak

                    floor = max(
                        float(self._cfg.min_peak_threshold),
                        load_min_audio_peak(),
                    )
                except Exception:
                    floor = float(self._cfg.min_peak_threshold)
                effective_thr = max(float(self._cfg.peak_threshold), floor)

                gated = (
                    self._cfg.gate_fire_only_when_target_exe_running and not qn_running
                )
                if (
                    peak is not None
                    and peak >= effective_thr
                    and peak > 0.001
                    and (now - self._last_fire) >= self._cfg.cooldown_s
                    and not gated
                ):
                    self._last_fire = now
                    try:
                        self._on_trigger()
                    except Exception as e:
                        self._diag(f"听觉诊断：音量触发后回调异常：{e!r}")
                time.sleep(self._cfg.poll_interval_s)
        finally:
            if com_inited:
                try:
                    import pythoncom

                    pythoncom.CoUninitialize()
                except Exception:
                    pass


class SweepFallbackTimer:
    """10 分钟兜底 OCR：线程 Timer 循环触发。"""

    def __init__(self, interval_s: float, *, on_sweep: OnTrigger) -> None:
        self._interval = float(interval_s)
        self._on_sweep = on_sweep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="SweepFallback", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=float(join_timeout_s))

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(timeout=self._interval):
                break
            try:
                self._on_sweep()
            except Exception:
                pass
