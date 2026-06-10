"""买家文本意图：询价 / 实拍 / 拍下 / 愤怒 / 补单 / 广告噪声（关键词 + 正则）。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntentSignals:
    price_quote: bool
    real_photo: bool
    order_placed: bool
    anger: bool
    replenish: bool
    ad_noise: bool
    likely_price_discussion: bool


_RE_PRICE = re.compile(
    r"(多少钱|怎么卖|报价|价格|优惠|折扣|包邮吗|能便宜|最低价|批发价|一件什么价|怎么拍)",
    re.I,
)
_RE_PHOTO = re.compile(r"(实拍|看图|照片|发图|细节图|上传.*图|有没有图)", re.I)
_RE_ORDER = re.compile(r"(拍下了|已拍|下单了|待付款|改价|订单|付款链接|拍了)", re.I)
_RE_ANGER = re.compile(
    r"(垃圾|太差|投诉|退款|骗子|受不了|气死|糊弄|骗人|差评|黑名单|举报)",
    re.I,
)
_RE_REPLEN = re.compile(r"(补单|回购|再来一单|同款再来|复购|追加)", re.I)
_RE_AD = re.compile(
    r"(限时活动|官方活动|点击查看|店铺红包|秒杀预告|系统消息|参与.*?活动)",
    re.I,
)


def classify_buyer_text(text: str) -> IntentSignals:
    t = (text or "").strip()
    if not t:
        return IntentSignals(False, False, False, False, False, False, False)

    ad_noise = bool(len(t) < 36 and _RE_AD.search(t))
    return IntentSignals(
        price_quote=bool(_RE_PRICE.search(t)),
        real_photo=bool(_RE_PHOTO.search(t)),
        order_placed=bool(_RE_ORDER.search(t)),
        anger=bool(_RE_ANGER.search(t)),
        replenish=bool(_RE_REPLEN.search(t)),
        ad_noise=ad_noise,
        likely_price_discussion=bool(_RE_PRICE.search(t) or "价" in t),
    )
