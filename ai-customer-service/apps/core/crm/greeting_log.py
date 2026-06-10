"""
v1.6.26 持久化「同日已问候」去重。

背景：旧逻辑用每次启动随机生成的 session_id（workbench_session.py
`"sess-"+uuid4()`）去 messages 表查"今天是否已对话"，重启托管后 session_id 变化
→ 查不到历史 → 对同一客户重复发"您好，在的呢～" → 触发千牛"服务态度提醒/重复消息"风控。

本模块用**稳定的客户标识（买家昵称）+ 店铺**落地到 JSON，跨重启仍能判定
今天是否已问候过该客户。买家昵称取不到（OCR 失败）时不参与去重（优雅降级）。

存储：data_dir/greeting_log.json，结构 { "<shop_id>|<昵称>": "YYYY-MM-DD" }。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_LOCK = threading.Lock()
_MAX_ENTRIES = 4000


def _path() -> Path:
    try:
        from apps.core.runtime_paths import data_dir

        return Path(data_dir()) / "greeting_log.json"
    except Exception:
        return Path.cwd() / "data" / "greeting_log.json"


def _load() -> dict:
    try:
        p = _path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _save(d: dict) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _norm_customer(customer_key: str) -> str:
    """v1.6.28：归一化客户标识——去掉所有空白 + 小写。
    OCR 常把同一昵称读成"kid_betsy"/"kid _betsy"（空格差异），归一化后可跨重启稳定匹配。"""
    return re.sub(r"\s+", "", (customer_key or "")).lower()


def _key(shop_id: str, customer_key: str) -> str:
    return f"{(shop_id or '').strip()}|{_norm_customer(customer_key)}"


def has_greeted_today(shop_id: str, customer_key: str, today: str) -> bool:
    """该 (店铺, 客户) 今天是否已问候过。客户标识为空时返回 False（不去重）。"""
    if not (customer_key or "").strip():
        return False
    with _LOCK:
        return _load().get(_key(shop_id, customer_key)) == today


def mark_greeted_today(shop_id: str, customer_key: str, today: str) -> None:
    """记录该 (店铺, 客户) 今天已问候。客户标识为空时不记录。"""
    if not (customer_key or "").strip():
        return
    with _LOCK:
        d = _load()
        d[_key(shop_id, customer_key)] = today
        # 体积兜底：超上限时只保留今天的条目
        if len(d) > _MAX_ENTRIES:
            d = {k: v for k, v in d.items() if v == today}
        _save(d)
