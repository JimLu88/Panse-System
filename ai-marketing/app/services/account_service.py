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
