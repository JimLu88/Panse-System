"""订单「远期(挂起)/激活」判定 —— 看板 + 工厂推送共用一套口径 (2026-07-08)。

业务: 远期单(未激活)不推工厂、不占单号(挂起); 备注改「开始制作」等激活词 → 才推工厂、拿新单号。
判词从 api/orders.py 抽出集中, 两处引用同一份, 避免口径漂移。
"""
from __future__ import annotations

from typing import Optional

# 远期关键字 (备注/生产备注/买家留言/商家备注 任一含即远期)
REMOTE_KW = (
    "远期", "走远期",
    "等通知", "等客户通知", "通知后", "通知再发", "通知再做", "客户通知", "待通知", "等客户",
    "收到通知", "随时通知", "等确认", "确认后再", "确认再发",
    "暂不发", "暂不生产", "暂不制作", "先不发", "先不做", "先别发", "先别做", "先放", "先压着",
    "押后", "暂缓", "缓发", "缓一缓", "延后发", "延期发", "延迟发", "推迟发", "晚点发", "迟点发",
    "装修好", "装修完", "房子好", "房子装好", "新房", "入住前", "还没装修", "房子还没",
    "别提前", "不要提前", "别太早", "不要太早",
)
# 激活关键字 (客户已通知/该做了 → 解除远期): 只留无歧义激活词
ACTIVATE_KW = (
    "开始制作", "现在制作", "立即制作", "马上制作", "可以制作", "安排制作",
    "开始生产", "可以生产", "安排生产", "投产", "上生产", "排产",
    "现在做", "可以做了", "开始做",
    "现在发货", "可以发货", "已通知",
)
# 激活词前若紧跟 等/待/延/迟/缓/不/别 等前缀 → 远期/否定语境, 不算激活
_NOT_ACT_PRE = ("等", "待", "收到", "随时", "暂", "先", "不", "别", "勿", "没", "未", "无",
                "延", "迟", "缓", "晚", "推", "停", "慢")


def order_text(o) -> str:
    """订单四个备注字段拼一起 (备注/生产备注/买家留言/商家备注)。"""
    return " ".join(t for t in (
        getattr(o, "remark", None), getattr(o, "production_note", None),
        getattr(o, "buyer_message", None), getattr(o, "seller_memo", None),
    ) if t)


def is_activated_text(text: Optional[str]) -> bool:
    """文本是否含【无否定前缀】的激活词。"""
    if not text:
        return False
    for k in ACTIVATE_KW:
        i = text.find(k)
        while i != -1:
            if not any(w in text[max(0, i - 2):i] for w in _NOT_ACT_PRE):
                return True
            i = text.find(k, i + 1)
    return False


def is_activated(o) -> bool:
    """订单是否已激活 (备注含开始制作等且非否定语境) → 该推工厂。"""
    return is_activated_text(order_text(o))


def has_remote_keyword(o) -> bool:
    """订单备注是否含远期关键字 (装修好/等通知/暂不发…)。"""
    return any(k in order_text(o) for k in REMOTE_KW)


def is_remote(o) -> bool:
    """是否远期挂起单 = (手动远期 或 备注远期词) 且【未被激活】。激活优先级最高。
    远期挂起单: 不生成/不推工厂下单图、不占工厂单号 —— 等激活后再以新号推 (用户 2026-07-08)。"""
    if is_activated(o):
        return False
    return bool(getattr(o, "is_remote_ship", False)) or has_remote_keyword(o)
