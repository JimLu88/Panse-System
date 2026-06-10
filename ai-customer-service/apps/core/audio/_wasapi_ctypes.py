"""Pure-ctypes WASAPI audio peak reader.

No pycaw / comtypes needed — uses only ctypes + windll.ole32 (Python stdlib).
Works in both dev mode and PyInstaller frozen exe.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as _W
from ctypes import POINTER, c_float, c_int, c_uint32, c_void_p, HRESULT

# ── GUID ─────────────────────────────────────────────────────────────────────

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, s: str) -> "GUID":
        s = s.strip("{}")
        parts = s.split("-")
        g = cls()
        g.Data1 = int(parts[0], 16)
        g.Data2 = int(parts[1], 16)
        g.Data3 = int(parts[2], 16)
        raw = bytes.fromhex(parts[3] + parts[4])
        for i, v in enumerate(raw):
            g.Data4[i] = v
        return g


# ── COM GUIDs ─────────────────────────────────────────────────────────────────

_CLSID_MMDeviceEnumerator   = GUID.from_str("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMMDeviceEnumerator    = GUID.from_str("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_IAudioMeterInformation = GUID.from_str("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
_IID_IAudioSessionManager2  = GUID.from_str("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
_IID_IAudioSessionEnumerator = GUID.from_str("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
_IID_IAudioSessionControl2  = GUID.from_str("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")

_CLSCTX_ALL  = 0x17
_eRender     = 0
_eMultimedia = 1
_COINIT_MULTITHREADED = 0

# ── ole32 bindings ────────────────────────────────────────────────────────────

_ole32 = ctypes.windll.ole32
_ole32.CoInitializeEx.argtypes  = [c_void_p, c_uint32]
_ole32.CoInitializeEx.restype   = HRESULT
_ole32.CoCreateInstance.argtypes = [
    POINTER(GUID), c_void_p, c_uint32, POINTER(GUID), POINTER(c_void_p)
]
_ole32.CoCreateInstance.restype = HRESULT
_ole32.CoUninitialize.argtypes  = []
_ole32.CoUninitialize.restype   = None

# ── vtable helper ─────────────────────────────────────────────────────────────

def _vfn(iface: c_void_p, idx: int, restype, *argtypes):
    """Return callable for the idx-th vtable slot of a COM interface pointer."""
    vtbl = ctypes.cast(iface, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[idx])


# ── QueryInterface helper ─────────────────────────────────────────────────────

def _qi(iface: c_void_p, iid: GUID) -> c_void_p | None:
    out = c_void_p()
    hr = _vfn(iface, 0, HRESULT, POINTER(GUID), POINTER(c_void_p))(
        iface, ctypes.byref(iid), ctypes.byref(out)
    )
    return out if hr == 0 else None


# ── IMMDeviceEnumerator ───────────────────────────────────────────────────────
# vtable: 0=QI 1=AddRef 2=Release 3=EnumAudioEndpoints 4=GetDefaultAudioEndpoint

def _enum_get_default(enum_ptr: c_void_p) -> c_void_p:
    dev = c_void_p()
    hr = _vfn(enum_ptr, 4, HRESULT, c_uint32, c_uint32, POINTER(c_void_p))(
        enum_ptr, _eRender, _eMultimedia, ctypes.byref(dev)
    )
    if hr < 0:
        raise OSError(f"GetDefaultAudioEndpoint failed: {hr:#010x}")
    return dev


# ── IMMDevice ─────────────────────────────────────────────────────────────────
# vtable: 0=QI 1=AddRef 2=Release 3=Activate 4=OpenPropertyStore 5=GetId 6=GetState

def _device_activate(dev_ptr: c_void_p, iid: GUID) -> c_void_p:
    obj = c_void_p()
    hr = _vfn(dev_ptr, 3, HRESULT, POINTER(GUID), c_uint32, c_void_p, POINTER(c_void_p))(
        dev_ptr, ctypes.byref(iid), _CLSCTX_ALL, None, ctypes.byref(obj)
    )
    if hr < 0:
        raise OSError(f"IMMDevice.Activate failed: {hr:#010x}")
    return obj


# ── IAudioMeterInformation ────────────────────────────────────────────────────
# vtable: 0=QI 1=AddRef 2=Release 3=GetPeakValue ...

def _meter_peak(meter: c_void_p) -> float:
    peak = c_float(0.0)
    hr = _vfn(meter, 3, HRESULT, POINTER(c_float))(meter, ctypes.byref(peak))
    if hr < 0:
        raise OSError(f"GetPeakValue failed: {hr:#010x}")
    return float(peak.value)


# ── IAudioSessionManager2 ─────────────────────────────────────────────────────
# vtable (from IMMDeviceActivationInterface base):
#   0=QI 1=AddRef 2=Release
#   (IAudioSessionManager) 3=GetAudioSessionControl 4=GetSimpleAudioVolume
#   (IAudioSessionManager2) 5=GetSessionEnumerator

def _mgr_get_enumerator(mgr: c_void_p) -> c_void_p:
    out = c_void_p()
    hr = _vfn(mgr, 5, HRESULT, POINTER(c_void_p))(mgr, ctypes.byref(out))
    if hr < 0:
        raise OSError(f"GetSessionEnumerator failed: {hr:#010x}")
    return out


# ── IAudioSessionEnumerator ───────────────────────────────────────────────────
# vtable: 0=QI 1=AddRef 2=Release 3=GetCount 4=GetSession

def _sess_count(se: c_void_p) -> int:
    n = c_int(0)
    _vfn(se, 3, HRESULT, POINTER(c_int))(se, ctypes.byref(n))
    return n.value


def _sess_get(se: c_void_p, idx: int) -> c_void_p | None:
    ctrl = c_void_p()
    hr = _vfn(se, 4, HRESULT, c_int, POINTER(c_void_p))(se, idx, ctypes.byref(ctrl))
    return ctrl if hr == 0 else None


# ── IAudioSessionControl2 ─────────────────────────────────────────────────────
# vtable (IAudioSessionControl base: 0-11, IAudioSessionControl2 adds):
#   12=GetSessionIdentifier 13=GetSessionInstanceIdentifier 14=GetProcessId
#   15=IsSystemSoundsSession 16=SetDuckingPreference

def _ctrl2_pid(ctrl2: c_void_p) -> int:
    pid = c_uint32(0)
    _vfn(ctrl2, 14, HRESULT, POINTER(c_uint32))(ctrl2, ctypes.byref(pid))
    return int(pid.value)


# ── Public helpers ────────────────────────────────────────────────────────────

_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106 — COM already init with different model (STA/MTA), safe to ignore


def _co_init() -> None:
    try:
        _ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    except OSError as e:
        # RPC_E_CHANGED_MODE: Qt 已在此线程用 STA 初始化 COM，WASAPI 在 STA 下同样可用
        if len(e.args) >= 4 and e.args[3] == _RPC_E_CHANGED_MODE:
            return
        raise


def _make_enumerator() -> c_void_p:
    enum_ptr = c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(_CLSID_MMDeviceEnumerator),
        None,
        _CLSCTX_ALL,
        ctypes.byref(_IID_IMMDeviceEnumerator),
        ctypes.byref(enum_ptr),
    )
    if hr < 0:
        raise OSError(f"CoCreateInstance(MMDeviceEnumerator) failed: {hr:#010x}")
    return enum_ptr


# ── Public API ────────────────────────────────────────────────────────────────

def get_global_peak() -> float | None:
    """Return peak (0–1) for the default render device; None on any error."""
    try:
        _co_init()
        enum_ptr = _make_enumerator()
        dev_ptr  = _enum_get_default(enum_ptr)
        meter    = _device_activate(dev_ptr, _IID_IAudioMeterInformation)
        return _meter_peak(meter)
    except Exception:
        return None


def diagnose() -> str:
    """Return a step-by-step diagnostic string for the UI."""
    lines: list[str] = []
    import sys
    lines.append(f"Python {sys.version}  frozen={getattr(sys,'frozen',False)}")
    # Step 1: CoInit
    try:
        _ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
        lines.append("Step1 CoInitializeEx: OK (MTA)")
    except OSError as e:
        if len(e.args) >= 4 and e.args[3] == _RPC_E_CHANGED_MODE:
            lines.append("Step1 CoInitializeEx: OK (STA already — Qt thread, WASAPI still works)")
        else:
            lines.append(f"Step1 CoInitializeEx: FATAL {e!r}")
            return "\n".join(lines)
    except Exception as e:
        lines.append(f"Step1 CoInitializeEx: EXCEPTION {e!r}")
        return "\n".join(lines)
    # Step 2: CoCreateInstance
    try:
        enum_ptr = c_void_p()
        hr = _ole32.CoCreateInstance(
            ctypes.byref(_CLSID_MMDeviceEnumerator),
            None, _CLSCTX_ALL,
            ctypes.byref(_IID_IMMDeviceEnumerator),
            ctypes.byref(enum_ptr),
        )
        lines.append(f"Step2 CoCreateInstance: hr={hr:#010x}  ptr={enum_ptr.value}")
        if hr < 0 or not enum_ptr.value:
            return "\n".join(lines)
    except Exception as e:
        lines.append(f"Step2 CoCreateInstance: EXCEPTION {e!r}")
        return "\n".join(lines)
    # Step 3: GetDefaultAudioEndpoint
    try:
        dev_ptr = _enum_get_default(enum_ptr)
        lines.append(f"Step3 GetDefaultAudioEndpoint: ptr={dev_ptr.value}")
    except Exception as e:
        lines.append(f"Step3 GetDefaultAudioEndpoint: EXCEPTION {e!r}")
        return "\n".join(lines)
    # Step 4: Activate IAudioMeterInformation
    try:
        meter = _device_activate(dev_ptr, _IID_IAudioMeterInformation)
        lines.append(f"Step4 Activate(MeterInfo): ptr={meter.value}")
    except Exception as e:
        lines.append(f"Step4 Activate(MeterInfo): EXCEPTION {e!r}")
        return "\n".join(lines)
    # Step 5: GetPeakValue
    try:
        peak = _meter_peak(meter)
        lines.append(f"Step5 GetPeakValue: {peak:.6f}")
    except Exception as e:
        lines.append(f"Step5 GetPeakValue: EXCEPTION {e!r}")
    return "\n".join(lines)


def get_all_session_peaks(
    pid_name_map: dict[int, str] | None = None,
) -> list[tuple[int, str, float]]:
    """Return [(pid, exe_name, peak), ...] for every WASAPI audio session.

    ``pid_name_map`` is an optional {pid: exe_name} dict (e.g. from tasklist).
    """
    result: list[tuple[int, str, float]] = []
    try:
        _co_init()
        enum_ptr = _make_enumerator()
        dev_ptr  = _enum_get_default(enum_ptr)
        mgr      = _device_activate(dev_ptr, _IID_IAudioSessionManager2)
        sess_enum = _mgr_get_enumerator(mgr)
        count = _sess_count(sess_enum)
        for i in range(count):
            try:
                ctrl = _sess_get(sess_enum, i)
                if not ctrl:
                    continue
                # PID via IAudioSessionControl2
                ctrl2 = _qi(ctrl, _IID_IAudioSessionControl2)
                pid = _ctrl2_pid(ctrl2) if ctrl2 else 0
                # Peak via IAudioMeterInformation
                meter = _qi(ctrl, _IID_IAudioMeterInformation)
                peak = _meter_peak(meter) if meter else 0.0
                name = (pid_name_map or {}).get(
                    pid, "SystemSounds" if pid == 0 else f"pid_{pid}"
                )
                result.append((pid, name, peak))
            except Exception:
                continue
    except Exception:
        pass
    return result
