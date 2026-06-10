"""
真实打字模拟（逐字 + 偶尔退格 + 偶尔错别字 + 字间延迟）。

为什么需要：
  - 现在 send_text 是 paste+Enter → 大段文字瞬间出现，是机器特征
  - 真人是 SendInput 逐字 → 字间有随机延迟
  - 真人偶尔会按错（退格重打）/ 打出错别字（不回退也常见）
  - 千牛风控可统计输入事件流（打字间隔分布、是否有 backspace）

方案：
  - type_text_realistic(text)：把整段文字拆字符，逐个 SendInput WM_CHAR / VK
  - 每 N 字符以 typo_rate 概率插入 "打错→退格→改对" 序列
  - 字间延迟 0.05-0.25s 正态分布

为什么不用 keystroke_delay 当唯一抖动：
  - 那是固定 0.1-0.3 均匀分布，与真人分布不一样
  - 真人是右偏分布（多数字 50ms，少数字停顿 500ms 想措辞）

限制：
  - 中文字符不能直接 SendInput VK，必须走 Unicode 字符（WM_CHAR / VK_PACKET）
  - 当前实现用 KEYEVENTF_UNICODE，对中英文都兼容

不引入新依赖：纯 ctypes + Win32 SendInput。
"""

from __future__ import annotations

import ctypes
import random
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

#
# ⚠ 重要修复（v1.5.2 → v1.5.3）：
# 原先这里重复声明了 _INPUT / _KEYBDINPUT / _SendInput.argtypes，
# 与 win_input.py 的同名结构体在 ctypes 内是**不同的类**（虽然字段一样）。
# user32.SendInput 是同一个全局函数指针，谁后 import 谁的 argtypes 覆盖谁的，
# 导致另一个文件调用时报：
#   ArgumentError: expected LP__INPUT instance instead of pointer to _INPUT
# 修复：复用 win_input 的 _INPUT / _KEYBDINPUT / _SendInput / ULONG_PTR，
# 不再独立声明，从根本上消除类型冲突。
#
from apps.core.automation.win_input import (
    ULONG_PTR,
    _INPUT,
    _KEYBDINPUT,
    _SendInput,
    _send_input_vk,
)

LogFn = Callable[[str], None]

# KEYBDINPUT.dwFlags 标志位
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# VK 常用键
VK_BACK = 0x08  # Backspace


@dataclass(frozen=True, slots=True)
class TypingSettings:
    """打字模拟参数。"""
    enabled: bool = False
    #: 字间延迟下限（秒）
    inter_char_min_s: float = 0.04
    #: 字间延迟上限（秒）
    inter_char_max_s: float = 0.22
    #: 每字符以该概率触发"打错→退格→改对"
    typo_rate: float = 0.025
    #: 退格延迟下限（秒）— 真人意识到打错后停一下
    backspace_pause_min_s: float = 0.25
    backspace_pause_max_s: float = 0.7


def _send_unicode_char(ch: str) -> None:
    """通过 KEYEVENTF_UNICODE 把任意 Unicode 字符注入到焦点窗口。

    一个字符要发 down + up 两个 INPUT。
    BMP 之外的字符（如 emoji 4 字节）需要走 UTF-16 代理对，这里只处理 BMP。
    """
    if not ch:
        return
    code = ord(ch)
    if code > 0xFFFF:
        # 非 BMP 字符，先拆代理对再发
        hi = 0xD800 + ((code - 0x10000) >> 10)
        lo = 0xDC00 + ((code - 0x10000) & 0x3FF)
        _send_unicode_code(hi)
        _send_unicode_code(lo)
        return
    _send_unicode_code(code)


def _send_unicode_code(code: int) -> None:
    down = _INPUT(type=1, ki=_KEYBDINPUT(
        wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=ULONG_PTR(0)))
    up = _INPUT(type=1, ki=_KEYBDINPUT(
        wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
        time=0, dwExtraInfo=ULONG_PTR(0)))
    arr = (_INPUT * 2)(down, up)
    sent = _SendInput(2, arr, ctypes.sizeof(_INPUT))
    if sent != 2:
        err = ctypes.windll.kernel32.GetLastError()
        raise RuntimeError(f"SendInput unicode failed (sent={sent}, code={code}, err={err})")


def _send_backspace() -> None:
    """按一次 Backspace。"""
    _send_input_vk(VK_BACK, True)
    time.sleep(0.01)
    _send_input_vk(VK_BACK, False)


def _random_inter_char_delay(cfg: TypingSettings) -> float:
    """字间延迟：用正态分布让"快字+偶尔慢字"更像真人。"""
    mean = (cfg.inter_char_min_s + cfg.inter_char_max_s) / 2.0
    sigma = (cfg.inter_char_max_s - cfg.inter_char_min_s) / 4.0
    delay = abs(random.gauss(mean, sigma))
    # 极少数情况下加一个 0.4-0.8s 的"想措辞停顿"
    if random.random() < 0.03:
        delay += random.uniform(0.4, 0.8)
    return min(max(delay, cfg.inter_char_min_s), cfg.inter_char_max_s + 0.8)


def _pick_typo_char(real_ch: str) -> str:
    """
    随机生成一个"看起来像打错"的字符（替代真实字符）。

    简化：
      - 英文/数字 → 邻近键盘字符（按 QWERTY 行）
      - 中文 → 随机常用汉字（千牛风控关心字数+频率分布，不关心是不是同音）
    """
    # 英文/数字：QWERTY 邻键映射
    neighbors = {
        "a": "sq", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg",
        "g": "fh", "h": "gj", "i": "uo", "j": "hk", "k": "jl", "l": "k",
        "m": "n", "n": "bm", "o": "ip", "p": "o", "q": "wa", "r": "et",
        "s": "ad", "t": "ry", "u": "yi", "v": "cb", "w": "qe", "x": "zc",
        "y": "tu", "z": "x",
        "0": "9", "1": "2", "2": "13", "3": "24", "4": "35", "5": "46",
        "6": "57", "7": "68", "8": "79", "9": "80",
    }
    low = real_ch.lower()
    if low in neighbors:
        ch = random.choice(neighbors[low])
        return ch.upper() if real_ch.isupper() else ch
    # 中文 / 其他：随机常用汉字
    common_pool = "的一是了我不在人有他这为之大来以个中上们到说国和地也"
    return random.choice(common_pool)


def type_text_realistic(
    text: str,
    cfg: TypingSettings,
    log: LogFn | None = None,
) -> None:
    """
    把 text 逐字符输入到焦点窗口，按 cfg 模拟打字节奏 + 错别字回退。

    使用前提：
      - 调用前请确保焦点已经在目标输入框（千牛 input）上
      - 上层负责按 Enter 发送（本函数只负责"打字"）
    """
    if not text:
        return
    if not cfg.enabled:
        # 关闭模式：退化为快速逐字（不带 typo）
        for ch in text:
            _send_unicode_char(ch)
        return

    typo_count = 0
    for ch in text:
        # 1. 偶尔打错并退格
        if random.random() < cfg.typo_rate:
            wrong = _pick_typo_char(ch)
            _send_unicode_char(wrong)
            time.sleep(_random_inter_char_delay(cfg))
            # "意识到打错"的停顿
            time.sleep(random.uniform(cfg.backspace_pause_min_s, cfg.backspace_pause_max_s))
            _send_backspace()
            typo_count += 1
            # 继续走正常打这个字
            time.sleep(_random_inter_char_delay(cfg))

        # 2. 打正确字
        _send_unicode_char(ch)
        time.sleep(_random_inter_char_delay(cfg))

    if log and typo_count:
        log(f"打字模拟：完成 {len(text)} 字（含 {typo_count} 次回退）")
