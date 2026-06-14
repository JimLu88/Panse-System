"""运行时配置解析：DB settings 优先，回退 .env。

非技术用户在「系统设置」页填采集器/飞书地址即生效，无需重启、无需改 .env。
"""
from __future__ import annotations

from ..config import get_settings
from ..database import SessionLocal
from ..models import Setting

# 可在界面配置的项 → (env 字段名, 展示名, 说明)
EDITABLE = {
    "crawler_base_url": ("crawler_base_url", "采集器地址",
                         "MediaCrawler/Spider_XHS 类只读采集服务的 URL，填了爆文/评论/舆情/数据回采走真实数据"),
    "feishu_webhook_url": ("feishu_webhook_url", "飞书群机器人 Webhook",
                           "看门狗告警/超期线索/到点发布 推送到飞书群"),
    "erp_base_url": ("erp_base_url", "ERP 地址", "线索成交回写 Panse-System 订单归因"),
}


def get(key: str) -> str:
    """取配置：DB 优先，回退 env。"""
    env_attr = EDITABLE.get(key, (key,))[0]
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        if row and row.value:
            return row.value
    finally:
        db.close()
    return getattr(get_settings(), env_attr, "") or ""


def set_value(key: str, value: str) -> None:
    if key not in EDITABLE:
        raise ValueError(f"不可配置项: {key}")
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        db.commit()
    finally:
        db.close()


def all_settings() -> list[dict]:
    out = []
    for key, (_env, label, hint) in EDITABLE.items():
        out.append({"key": key, "label": label, "hint": hint,
                    "value": get(key), "configured": bool(get(key))})
    return out
