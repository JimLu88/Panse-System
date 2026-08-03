"""订单「远期(挂起)/激活」判定 —— 看板 + 工厂推送共用一套口径 (2026-07-08)。

业务: 远期单(未激活)不推工厂、不占单号(挂起); 备注改「开始制作」等激活词 → 才推工厂、拿新单号。
判词从 api/orders.py 抽出集中, 两处引用同一份, 避免口径漂移。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

DEFAULT_SHIP_DAYS = 30   # 工厂默认工期(天); 备注预定发货日距今 > 此 = 太早别做 = 远期

URGENCY_LABELS = {
    "overdue": "已超期",
    "critical": "非常紧急",
    "urgent": "紧急",
    "normal": "正常安排",
    "remote": "远期单",
}

# 远期关键字 (备注/生产备注/买家留言/商家备注 任一含即远期)
REMOTE_KW = (
    "远期", "远期单", "走远期", "改远期", "转远期", "改为远期", "设为远期",
    "等通知", "等客户通知", "待客户通知", "通知后", "通知再发", "通知再做", "客户通知", "待通知", "等客户",
    "收到通知", "随时通知", "等确认", "等客户确认", "待客户确认", "等客户确定", "待客户确定", "确认后再", "确认再发", "等消息", "待消息",
    "暂不发", "暂不生产", "暂不制作", "先不发", "先不做", "先别发", "先别做", "先放", "先压着",
    "押后", "暂缓", "缓发", "缓一缓", "延后发", "延期发", "延迟发", "推迟发", "晚点发", "迟点发",
    "延后发货", "延期发货", "延迟发货", "推迟发货", "晚点发货", "迟点发货", "稍后发货", "晚些发货",
    "装修好", "装修完", "房子好", "房子装好", "新房", "入住前", "还没装修", "房子还没",
    "别提前", "不要提前", "别太早", "不要太早",
)
# 普通远期单的激活关键字 (客户已通知/该做了 → 解除远期)。
# 客户延期单使用更严格的“开始制作”铁证，见 is_customer_delay_activated()。
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
    if bool(getattr(o, "is_customer_delayed", False)):
        return is_customer_delay_activated(o)
    return is_activated_text(order_text(o))


def is_customer_delay_activated(o) -> bool:
    """客户延期单只有备注明确写“开始制作”才解除挂起。

    “可以制作”“预计发货日”“制作好后通风”等都不能替代客户已通知开工的
    明确信号，避免尚未要求发货的订单因日期临近被算成非常紧急。
    """
    text = order_text(o)
    i = text.find("开始制作")
    while i != -1:
        if not any(w in text[max(0, i - 2):i] for w in _NOT_ACT_PRE):
            return True
        i = text.find("开始制作", i + 1)
    return False


def has_remote_keyword(o) -> bool:
    """订单备注是否含远期关键字 (装修好/等通知/暂不发…)。"""
    return any(k in order_text(o) for k in REMOTE_KW)


def is_remote(o) -> bool:
    """是否远期挂起单 = (手动远期 或 备注远期词) 且【未被激活】。激活优先级最高。
    远期挂起单: 不生成/不推工厂下单图、不占工厂单号 —— 等激活后再以新号推 (用户 2026-07-08)。"""
    if is_activated(o):
        return False
    if bool(getattr(o, "is_remote_ship", False)):
        return True
    # 客户延期默认挂起；只有明确“开始制作”才会被上面的 is_activated 解除。
    if bool(getattr(o, "is_customer_delayed", False)):
        return True
    return has_remote_keyword(o)


def factory_label(o) -> str:
    """"工厂下单号"列的统一显示 (用户 2026-07-09): 正式单=`畔色N单`; 远期单=`远期单N`(内部序号);
    两者都没有=空。远期单不占工厂号, 只发 remote_seq；当前远期身份优先于待清退的旧 factory_no。"""
    # 状态纠偏与旧工厂号清退之间可能有短暂窗口；远期身份优先，避免飞书继续
    # 把尚未开工的客户延期单显示成正式工厂单。
    if is_remote(o):
        rseq = getattr(o, "remote_seq", None)
        return f"远期单{rseq}" if rseq else "远期单"
    fno = getattr(o, "factory_no", None)
    if fno:
        return f"畔色{fno}单"
    rseq = getattr(o, "remote_seq", None)
    if rseq:
        return f"远期单{rseq}"
    return ""


def parse_resume_date(text: Optional[str], order_date, today) -> Optional[date]:
    """备注解析预定发货日 (『X月X日(以后/再)发』/『X日发』/『N天后发』) → date; 取不到 None。
    与 api/orders.py factory_production 同一套正则集中于此, 避免口径漂移 (用户 2026-07-09 统一)。"""
    if not text:
        return None
    by, bm = (order_date.year, order_date.month) if order_date else (today.year, today.month)
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*(?:以?后|再|左右)?\s*发", text)
    if m:
        try:
            return date(by, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s*[日号]\s*(?:以?后|之?后|左右|再)?\s*发", text)
    if m:
        try:
            d = date(by, bm, int(m.group(1)))
            if order_date and d < order_date:   # 该日早于下单 → 顺延下月
                d = date(by + (1 if bm == 12 else 0), 1 if bm == 12 else bm + 1, int(m.group(1)))
            return d
        except ValueError:
            return None
    m = re.search(r"(\d{1,3})\s*天\s*[后之]?\s*(?:再)?\s*发", text)
    if m and order_date:
        try:
            return order_date + timedelta(days=int(m.group(1)))
        except (ValueError, OverflowError):
            return None
    return None


def is_factory_remote(o, today=None, ship_days: int = DEFAULT_SHIP_DAYS) -> bool:
    """工厂看板口径的『远期挂起』(比 is_remote 多了【日期式延期】, 如"8月1日发货"):
    未激活 且 (手动远期 / (无人工截止时)备注预定发货日距今 > 工期 / 关键词远期)。
    与 api/orders.py factory_production 的 st=='remote' 级联完全一致 (用户 2026-07-09 统一)。"""
    if is_activated(o):
        return False
    if getattr(o, "is_remote_ship", False):
        return True
    if getattr(o, "is_customer_delayed", False):
        return True    # 未写“开始制作”的客户延期单默认挂起
    if getattr(o, "ship_deadline", None):
        return False   # 人工设了发货截止 → 倒计时, 不算远期
    today = today or date.today()
    rdate = parse_resume_date(order_text(o), getattr(o, "order_date", None), today)
    if rdate is not None:
        return (rdate - today).days > ship_days   # 发货日太远 = 太早别做 = 远期
    return has_remote_keyword(o)


def urgency_by_days(days: Optional[int]) -> str:
    """剩余交期天数 → 工厂制作单紧急度。"""
    if days is None:
        return "normal"
    if days < 0:
        return "overdue"
    if days <= 5:
        return "critical"
    if days <= 11:
        return "urgent"
    return "normal"


def factory_schedule(o, *, today=None, ship_days: int = DEFAULT_SHIP_DAYS) -> dict:
    """计算工厂制作单的唯一交期口径，供 ERP 看板、飞书和导出共同调用。

    返回 original_deadline / effective_deadline / days_left / urgency /
    urgency_label / remote_resume_date。该函数只读，不修改订单。
    """
    today = today or date.today()
    base = getattr(o, "order_date", None)
    manual_deadline = getattr(o, "ship_deadline", None)
    original_deadline = manual_deadline or (
        base + timedelta(days=ship_days) if base else None
    )
    text = order_text(o)
    resume_date = parse_resume_date(text, base, today)

    if bool(getattr(o, "is_customer_delayed", False)) and not is_customer_delay_activated(o):
        effective, days_left, urgency = None, None, "remote"
    elif bool(getattr(o, "is_customer_delayed", False)):
        effective = getattr(o, "customer_delay_deadline", None)
        days_left = (effective - today).days if effective else None
        urgency = urgency_by_days(days_left)
    elif is_activated_text(text):
        effective = resume_date or (
            base + timedelta(days=ship_days) if base else None
        )
        days_left = (effective - today).days if effective else None
        urgency = urgency_by_days(days_left)
    elif bool(getattr(o, "is_remote_ship", False)):
        effective, days_left, urgency = None, None, "remote"
    elif manual_deadline:
        effective = manual_deadline
        days_left = (effective - today).days
        urgency = urgency_by_days(days_left)
    elif resume_date is not None:
        effective = resume_date
        days_left = (effective - today).days
        urgency = "remote" if days_left > ship_days else urgency_by_days(days_left)
    elif has_remote_keyword(o):
        effective, days_left, urgency = None, None, "remote"
    else:
        effective = base + timedelta(days=ship_days) if base else None
        days_left = (effective - today).days if effective else None
        urgency = urgency_by_days(days_left)

    return {
        "original_deadline": original_deadline,
        "effective_deadline": effective,
        "days_left": days_left,
        "urgency": urgency,
        "urgency_label": URGENCY_LABELS[urgency],
        "remote_resume_date": resume_date,
    }
