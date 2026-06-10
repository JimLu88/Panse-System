from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_DELETE = 0x2E


def _vk_from_char(ch: str) -> int:
    o = ord(ch.upper())
    if 0x41 <= o <= 0x5A:
        return o
    raise ValueError(f"unsupported char for vk: {ch!r}")


ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_SendInput.restype = wintypes.UINT


def _send_input_vk(vk: int, key_down: bool) -> None:
    KEYEVENTF_KEYUP = 0x0002
    flags = 0 if key_down else KEYEVENTF_KEYUP
    inp = _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=ULONG_PTR(0)))
    sent = _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    if sent != 1:
        err = ctypes.windll.kernel32.GetLastError()
        raise RuntimeError(f"SendInput failed (sent={sent}, vk={vk}, down={key_down}, err={err})")


def press_vk(vk: int) -> None:
    _send_input_vk(vk, True)
    time.sleep(0.01)
    _send_input_vk(vk, False)


def press_ctrl_combo(ch: str) -> None:
    vk = _vk_from_char(ch)
    _send_input_vk(VK_CONTROL, True)
    time.sleep(0.01)
    press_vk(vk)
    time.sleep(0.01)
    _send_input_vk(VK_CONTROL, False)

