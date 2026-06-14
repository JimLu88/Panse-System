"""⑤ 账号与凭证管理 + 健康心跳。对应 05-account-manager.md。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account


def list_accounts(db: Session) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.id)))


def health_dashboard(db: Session) -> list[dict]:
    """账号健康一屏聚合（评审补充的健康仪表盘）。"""
    out = []
    for a in list_accounts(db):
        out.append({
            "id": a.id,
            "nickname": a.nickname,
            "role": a.role,
            "platform": a.platform,
            "stage": a.stage,
            "follower_count": a.follower_count,
            "health_score": a.health_score,
            "health_flag": a.health_flag,
            "post_alive_rate": a.post_alive_rate,
            "driver_mode": a.driver_mode,
            "real_person": a.real_person,
            "device_note": a.device_note,
            "sim_note": a.sim_note,
            "official_setup": a.official_setup or {},
        })
    return out


def device_conflicts(db: Session) -> list[dict]:
    """#4 一机多号告警：同一设备/手机卡绑定多个号 → 连坐封号首因。"""
    accts = list_accounts(db)
    by_device: dict[str, list[str]] = {}
    for a in accts:
        if a.device_note:
            by_device.setdefault(a.device_note, []).append(a.nickname)
    return [{"device": dev, "accounts": names}
            for dev, names in by_device.items() if len(names) > 1]


def ingest_risk_signal(db: Session, account_id: int, signal: str) -> Account:
    """#3 风控信号→熔断：采集到限流/滑块/笔记被隐藏 → 降健康分并熔断。

    signal: captcha(滑块) / throttled(限流) / note_hidden(笔记被隐藏) / normal
    """
    a = db.get(Account, account_id)
    if a is None:
        raise ValueError("账号不存在")
    penalty = {"captcha": 40, "throttled": 35, "note_hidden": 25, "normal": 0}.get(signal, 0)
    a.health_score = max(0, a.health_score - penalty)
    if a.health_score < 50:
        a.health_flag = "red"
        a.driver_mode = "assist"  # 熔断：强制人工
    elif a.health_score < 80:
        a.health_flag = "yellow"
    db.commit()
    return a


def update_health(db: Session, account_id: int, *, post_alive_rate: float | None = None,
                  real_comment_rate: float | None = None) -> Account:
    """更新健康指标并重算红/黄/绿牌（动态基线的简化：硬阈值）。"""
    a = db.get(Account, account_id)
    if a is None:
        raise ValueError("账号不存在")
    if post_alive_rate is not None:
        a.post_alive_rate = post_alive_rate
    if real_comment_rate is not None:
        a.real_comment_rate = real_comment_rate

    score = 100
    flag = "green"
    if a.post_alive_rate < 0.8:
        score -= 30
        flag = "yellow"
    if a.post_alive_rate < 0.5:
        score -= 30
        flag = "red"
    a.health_score = max(0, score)
    a.health_flag = flag
    # 熔断：红牌强制 ASSIST
    if flag == "red":
        a.driver_mode = "assist"
    db.commit()
    return a
