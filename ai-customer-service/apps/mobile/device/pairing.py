"""
apps/mobile/device/pairing.py
==============================
APK 配对令牌的本地持久化。

工作流：
  1. 用户在 APK MainActivity 看到二维码（含 {ip, port, token} JSON）
  2. PC 端"添加设备"对话框扫描或粘贴此 JSON
  3. parse_pairing_qr() → PairingRecord
  4. save_pairing() 写入 ~/.aiworkbench/mobile_pairings.json
  5. 之后 DeviceManager 启动时 load_all_pairings() 恢复

数据文件位置：
  Windows: C:\\Users\\<user>\\.aiworkbench\\mobile_pairings.json
  Linux/Mac: ~/.aiworkbench/mobile_pairings.json

JSON 结构（示例，token 已脱敏）：
{
  "pairings": [
    {
      "device_id": "192.168.1.42:8765",
      "ip": "192.168.1.42",
      "port": 8765,
      "token": "REDACTED-43CHAR-BASE64",
      "label": "卖家手机A",
      "paired_at": "2026-05-27T12:34:56"
    }
  ]
}
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("apps.mobile.device.pairing")


DEFAULT_PAIRING_DIR = Path.home() / ".aiworkbench"
DEFAULT_PAIRING_FILE = DEFAULT_PAIRING_DIR / "mobile_pairings.json"

_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class PairingRecord:
    device_id: str       # "ip:port"
    ip: str
    port: int
    token: str
    label: str = ""
    paired_at: str = ""  # ISO-8601


# ── 解析二维码内容 ─────────────────────────────────────────────

def parse_pairing_qr(raw: str, label: str = "") -> PairingRecord | None:
    """
    解析 APK 二维码扫描得到的字符串。

    期望格式：
      {"ip":"192.168.1.42","port":8765,"token":"xxx"}

    @return None 表示格式不合法
    """
    if not raw or not raw.strip():
        return None
    try:
        obj = json.loads(raw.strip())
    except Exception as e:
        log.warning("parse_pairing_qr 解析失败: %r raw=%r", e, raw[:200])
        return None
    if not isinstance(obj, dict):
        return None
    ip = str(obj.get("ip") or "").strip()
    port_raw = obj.get("port")
    token = str(obj.get("token") or "").strip()
    if not ip or not token:
        log.warning("parse_pairing_qr 缺字段: ip=%r token_len=%d", ip, len(token))
        return None
    try:
        port = int(port_raw)
    except Exception:
        port = 8765  # APK 默认
    return PairingRecord(
        device_id=f"{ip}:{port}",
        ip=ip,
        port=port,
        token=token,
        label=label.strip(),
        paired_at=datetime.now().isoformat(timespec="seconds"),
    )


# ── 文件读写 ─────────────────────────────────────────────────

def _ensure_dir() -> None:
    try:
        DEFAULT_PAIRING_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("_ensure_dir failed: %r", e)


def load_all_pairings(path: Path | None = None) -> list[PairingRecord]:
    """从磁盘加载所有已配对设备。文件不存在或损坏返回 []。"""
    p = path or DEFAULT_PAIRING_FILE
    if not p.is_file():
        return []
    try:
        with _lock:
            raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw) if raw.strip() else {}
        items = obj.get("pairings") or []
        out: list[PairingRecord] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            ip = str(it.get("ip") or "").strip()
            tok = str(it.get("token") or "").strip()
            if not ip or not tok:
                continue
            out.append(
                PairingRecord(
                    device_id=str(it.get("device_id") or f"{ip}:{int(it.get('port') or 8765)}"),
                    ip=ip,
                    port=int(it.get("port") or 8765),
                    token=tok,
                    label=str(it.get("label") or ""),
                    paired_at=str(it.get("paired_at") or ""),
                )
            )
        return out
    except Exception as e:
        log.exception("load_all_pairings 失败: %r", e)
        return []


def save_pairing(rec: PairingRecord, path: Path | None = None) -> bool:
    """
    新增 / 覆盖一条配对（按 device_id 去重）。
    @return True 表示已写入；False 表示 IO 错误
    """
    if not rec.ip or not rec.token:
        return False
    _ensure_dir()
    p = path or DEFAULT_PAIRING_FILE
    with _lock:
        try:
            existing = load_all_pairings(p)
            new_list = [r for r in existing if r.device_id != rec.device_id]
            new_list.append(rec)
            data = {"pairings": [asdict(r) for r in new_list]}
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, p)
            log.info("save_pairing 成功 device_id=%s label=%s", rec.device_id, rec.label)
            return True
        except Exception as e:
            log.exception("save_pairing 失败: %r", e)
            return False


def remove_pairing(device_id: str, path: Path | None = None) -> bool:
    """按 device_id 移除一条配对。无该条目返回 False。"""
    if not device_id:
        return False
    p = path or DEFAULT_PAIRING_FILE
    _ensure_dir()
    with _lock:
        try:
            existing = load_all_pairings(p)
            new_list = [r for r in existing if r.device_id != device_id]
            if len(new_list) == len(existing):
                return False
            data = {"pairings": [asdict(r) for r in new_list]}
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, p)
            log.info("remove_pairing 成功 device_id=%s", device_id)
            return True
        except Exception as e:
            log.exception("remove_pairing 失败: %r", e)
            return False


def get_pairing(device_id: str, path: Path | None = None) -> PairingRecord | None:
    """按 device_id 查一条配对。"""
    for r in load_all_pairings(path):
        if r.device_id == device_id:
            return r
    return None
