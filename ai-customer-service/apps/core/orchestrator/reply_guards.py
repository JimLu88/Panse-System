"""
接待轮次防误触：视觉哨兵重复触发、寒暄 OCR _echo、无新买家消息仍自动回复。
"""

from __future__ import annotations

import re
import time

# v1.5.7 修复：原来把买家开场寒暄("在吗""在么")和卖家回声/口头禅("在的呢""嗯嗯")
# 混在同一个 _ECHO_GREETING 里，audio_peak（叮咚=真买家新消息）触发"在么"时
# 被错判为 noise→静默跳过→不回复。现拆成两类：
#  - _BUYER_OPENING_GREETING : 买家开场寒暄（audio_peak 时是真实留言，不应过滤）
#  - _SELLER_ECHO_OR_FILLER  : 卖家回声/语气词（任何 trigger 都视为噪声）
_BUYER_OPENING_GREETING = re.compile(
    r"^("
    r"您好[，,]?在吗[~～]?|您好[，,]?在么[~～]?|"
    r"你好[，,]?在吗[~～]?|你好[，,]?在么[~～]?|"
    r"在吗|在么|在嘛|"
    r"您好[，,]?在不在|你好[，,]?在不在|在不在|"
    r"您好|你好|hi|hello"
    r")[.．。!！?？~～]*$",
    re.I,
)
_SELLER_ECHO_OR_FILLER = re.compile(
    r"^("
    r"您好[，,]?在的呢[~～]?|你好[，,]?在的呢[~～]?|"
    r"在的呢|在的|嗯嗯|嗯|好的呢|好的|收到|稍等"
    r")[.．。!！?？~～]*$",
    re.I,
)

# 过短且像商品标签碎片，常来自右侧足迹误入聊天 ROI
_PRODUCT_FRAGMENT = re.compile(
    r"^(金属|实木|岩板|樱桃|胡桃|配件).{0,20}$",
)

# 聊天区 OCR 误读时间戳/系统行（非买家发言）
_TIMESTAMP_LIKE = re.compile(
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[\sT]\d{1,2}:\d{2}|"
    r"^\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}",
)

# 千牛聊天区系统提示行（v1.3.93）——切到无最近活动的会话时，千牛会在
# 聊天区中央显示"当前用户来自X""对方正在输入""[系统消息]"等灰色提示，
# 它们会被 OCR 当作 buyer_text，导致话术库查不到 → 错误转人工 → 批量安抚刷屏 → 风控
_QIANNIU_SYSTEM_LINE = re.compile(
    r"当前用户来自|"          # 当前用户来自商品详情页/首页/购物车/店铺主页/搜索...
    r"对方正在输入|"          # 对方正在输入...
    r"^\[?系统消息\]?|"       # [系统消息]
    r"^\[?自动回复\]?|"       # [自动回复]
    r"^\[?系统提示\]?|"       # [系统提示]
    r"已撤回(?:一条)?消息|"   # XX 撤回了一条消息
    r"对方已读|"              # 对方已读
    r"^已读$|^未读$|"         # 已读 / 未读
    r"网络.{0,4}(?:异常|断开|连接)|"  # 网络断开/连接异常
    r"对方暂时.{0,6}回复",    # 对方暂时无法回复
    re.IGNORECASE,
)


def normalize_buyer_digest(text: str) -> str:
    """复合去重键：长度 + 规范化文本，防止"你好"/"你好？"被合并、不同买家同短语被误吞。"""
    normalized = re.sub(r"\s+", "", (text or "").strip())[:240]
    return f"{len(normalized)}|{normalized}"


def is_buyer_opening_greeting(text: str) -> bool:
    """买家开场寒暄（"在吗""在么""您好"等）。audio_peak 触发时这就是真买家。"""
    t = (text or "").strip()
    return bool(t and _BUYER_OPENING_GREETING.match(t))


# 千牛 OCR 常见噪声：店铺名/买家 ID/打招呼填充词。v1.5.8 引入 strip_qianniu_ocr_noise
# 用于"先清洗再判断"：清洗后若剩下只剩开场寒暄，说明买家其实就是说"在么"之类，
# 不应当再调 LLM 生成第二句回复（welcome 已经够了）。
_TB_BUYER_ID = re.compile(r"\btb\d{6,}\b", re.IGNORECASE)
_FILLER_TOKENS = ("hi", "hello", "你好", "您好")


def strip_qianniu_ocr_noise(text: str, *, seller_display_name: str = "") -> str:
    """
    把千牛 OCR 把店铺名+买家 ID+寒暄填充词都读进 buyer_text 的噪声清掉。

    例：
        '孚格家居 hi tb697331180593 hi 孚格家居 在么 tb697331180593 你好'
        + seller_display_name='孚格家居'
        => '在么'
    """
    t = (text or "").strip()
    if not t:
        return ""
    # 1) 移除买家 ID 串（tbXXXXXXXX）
    t = _TB_BUYER_ID.sub(" ", t)
    # 2) 移除店铺名（若 yaml 配置了 shop_display_name）
    if seller_display_name:
        t = t.replace(seller_display_name, " ")
    # 3) 拆 token，过滤掉 hi/hello 这类填充词（不剔除"您好"，它是真寒暄）
    tokens = [tok for tok in re.split(r"\s+", t) if tok]
    tokens = [tok for tok in tokens if tok.lower() not in ("hi", "hello")]
    return " ".join(tokens).strip()


def is_only_opening_after_strip(
    text: str, *, seller_display_name: str = ""
) -> bool:
    """
    清洗千牛 OCR 噪声后，剩下的字符串如果**全部**由开场寒暄词构成，
    则视为「买家只是开场打招呼」——welcome 一句就够，不应再调 LLM。

    例：
      "孚格家居 hi tb... 在么 在的呢 你好" → 清洗后 "在么 在的呢 你好"
        → 拆 token 后每个都被 _BUYER_OPENING_GREETING / _SELLER_ECHO_OR_FILLER 命中
        → True
    """
    cleaned = strip_qianniu_ocr_noise(text, seller_display_name=seller_display_name)
    if not cleaned:
        return True
    # 整段就是开场寒暄
    if _BUYER_OPENING_GREETING.match(cleaned):
        return True
    # 拆 token 后每个都是寒暄/回声/语气词
    tokens = [tok for tok in re.split(r"[\s,，.。!！?？~～]+", cleaned) if tok]
    if not tokens:
        return True
    for tok in tokens:
        if not (_BUYER_OPENING_GREETING.match(tok) or _SELLER_ECHO_OR_FILLER.match(tok)):
            return False
    return True


def is_echo_or_noise_buyer_text(
    text: str, *, trigger: str = "", seller_display_name: str = ""
) -> bool:
    """
    判断 OCR 出来的 buyer_text 是否应当过滤掉。

    Args:
        text:    OCR 出来的买家发言
        trigger: 本轮事件来源（"audio_peak" / "visual_scan" / "chat_rescan" / ...）。
                 默认空字符串保持旧调用方兼容（等同 visual_scan 的保守语义）。

    规则：
      - 卖家回声/语气词（"在的呢""嗯嗯""收到"）：任何 trigger 都过滤
      - 买家开场寒暄（"在吗""在么""您好"）：
          * audio_peak：不过滤（叮咚=真新消息，"在么"就是该回复的开场）
          * 其它 trigger：过滤（视觉哨兵反复扫到同一历史气泡时防自循环）
      - 时间戳 / 商品碎片 / 千牛系统行：任何 trigger 都过滤
    """
    t = (text or "").strip()
    if not t:
        return True
    # v1.6.10：买家气泡不会以卖家店名开头；以店名开头说明 OCR 抓到的是
    # 卖家自己刚发的气泡（如"孚格家居 嗯嗯好的~"），按噪声过滤，防"自己回自己"死循环。
    if seller_display_name and t.startswith(seller_display_name):
        return True
    if _SELLER_ECHO_OR_FILLER.match(t):
        return True
    if _BUYER_OPENING_GREETING.match(t):
        # audio_peak（叮咚=真新消息）时，"在么" 就是该回复的开场，不过滤；
        # visual_scan / chat_rescan 时仍过滤，防止反复扫历史气泡自循环
        if trigger == "audio_peak":
            return False
        return True
    if len(t) <= 8 and _PRODUCT_FRAGMENT.match(t):
        return True
    if _TIMESTAMP_LIKE.search(t):
        return True
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", t):
        return True
    if _QIANNIU_SYSTEM_LINE.search(t):
        return True
    return False


# 托管启动后 visual_scan 仅建基线，不弹窗、不接待（真实新消息靠 audio_peak）
HOSTING_VISUAL_GRACE_S = 55.0


def hosting_visual_grace_active(*, trigger: str, hosting_started_at: float) -> bool:
    if trigger not in ("visual_scan", "chat_area_diff"):
        return False
    if hosting_started_at <= 0:
        return False
    return (time.monotonic() - hosting_started_at) < HOSTING_VISUAL_GRACE_S


_GENERIC_ATTRIBUTE_RE = re.compile(
    r"材质|材料|什么木|实木|多大|多高|多长|多宽|多厚|多重|重量|尺寸|规格|大小|"
    r"承重|克重|面料|工艺|什么颜色|颜色|有几种|几个颜色|有没有颜色|配色"
)


def is_generic_attribute_question(text: str) -> bool:
    """
    v1.6.27：是否为「泛问属性」——材质/尺寸/颜色/规格等。

    用途：知识库未命中时，若是泛问属性且没指定哪款产品，应反问澄清「您说的哪一款呢」，
    而不是直接转人工。价格类（多少钱/什么价）由 jim_price 分支单独处理，不在此列。
    """
    t = (text or "").strip()
    if not t:
        return False
    return bool(_GENERIC_ATTRIBUTE_RE.search(t))


def should_send_welcome(
    *,
    trigger: str,
    welcome_last_at: float,
    cooldown_minutes: float | None = None,
) -> bool:
    """首句问候仅 audio_peak；同会话冷却内不重复发送。"""
    if trigger != "audio_peak":
        return False
    if welcome_last_at <= 0:
        return True
    try:
        from apps.core.ai.input_quality_gate import load_greeting_cooldown_minutes

        cd_min = (
            float(cooldown_minutes)
            if cooldown_minutes is not None
            else load_greeting_cooldown_minutes()
        )
    except Exception:
        cd_min = 5.0
    return (time.monotonic() - welcome_last_at) >= cd_min * 60.0


def should_skip_duplicate_buyer(
    *,
    buyer_text: str,
    last_digest: str,
    last_handled_at: float,
    cooldown_s: float = 90.0,
) -> bool:
    d = normalize_buyer_digest(buyer_text)
    if not d or not last_digest:
        return False
    if d != last_digest:
        return False
    return (time.monotonic() - last_handled_at) < cooldown_s


def visual_scan_in_quiet_period(
    *,
    trigger: str,
    quiet_until: float,
) -> bool:
    return trigger in ("visual_scan", "chat_rescan", "chat_area_diff") and time.monotonic() < quiet_until
