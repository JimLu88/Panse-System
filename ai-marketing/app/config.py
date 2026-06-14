"""配置中心（简化版）：环境变量 + 家具品牌种子规则。

对应设计稿 02-cross-cutting/config-rule-center.md。
易变项（敏感词分级 / 平台折叠线 / 产品关键词）集中在这里，便于后续做热更新。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./marketing.db"

    llm_provider: str = "mock"  # mock / anthropic / openai
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-fable-5"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"

    erp_base_url: str = ""
    erp_token: str = ""

    # 简单鉴权：配了 API_TOKEN 则 /api/* 需 Bearer token（/api/health 除外）
    api_token: str = ""

    # 看门狗（60s体检，连续3次失败 SIGTERM 自救，配合 Docker unless-stopped）
    watchdog_enabled: bool = True

    # 飞书机器人 webhook（群自定义机器人）：看门狗告警/超期线索/到点发布 推送
    feishu_webhook_url: str = ""

    # 真实数据源（AI 数据爬虫，挂载项A）：配了则选题热榜/上升笔记走爬虫，否则用内置演示数据
    crawler_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---- 家具品牌敏感词分级（config-rule-center: S 熔断 / A 强制人工 / B 预警）----
# 家具文案高频踩的雷词，与美妆不同。
BANNED_WORDS = {
    "S": ["甲醛超标", "致癌", "国家免检"],  # 直接拒绝
    "A": ["最", "第一", "全网最低", "纯实木", "零甲醛", "100%", "绝对", "顶级"],  # 强制人工改
    "B": ["超好用", "强烈推荐", "yyds", "巨划算"],  # 仅预警
}

# ---- 平台折叠线（platforms.md：小红书 标题20 + 正文前50）----
PLATFORM_FOLD = {
    "xhs": {"title_chars": 20, "body_first_chars": 50, "tag_count": [5, 10], "cover_ratio": "3:4"},
    "zhihu": {"title_chars": 30, "body_first_chars": 100, "tag_count": [3, 6], "cover_ratio": "16:9"},
}

# ---- 产品线关键词（实际应来自 ERP products 表；MVP 用种子）----
PRODUCT_KEYWORDS = {
    "餐桌": ["实木餐桌", "榉木餐桌", "岩板餐桌", "小户型餐桌", "餐桌椅组合"],
    "餐椅": ["实木餐椅", "靠背椅", "餐椅推荐"],
    "茶几": ["实木茶几", "岩板茶几", "小户型茶几"],
    "柜类": ["电视柜", "餐边柜", "玄关柜", "储物柜"],
    "床": ["实木床", "原木床", "主卧大床"],
}

# ---- 私信话术库（线索客服页，一键复制；含老客返图邀约）----
FAQ_SCRIPTS = [
    {"key": "尺寸", "title": "问尺寸", "text": "您好～方便告诉我您家餐厅的长宽吗？一般预留每边走动空间 60cm 比较舒服，我帮您算下合适的桌子尺寸（也可以发户型图给我）。"},
    {"key": "材质", "title": "问材质", "text": "我们主打榉木/白蜡木全实木框架，每件出厂前都做含水率处理。您比较关注耐用性还是颜色质感？我可以分别给您推荐～"},
    {"key": "工期", "title": "问工期", "text": "现货款 3 天内发出；定制尺寸一般 15-20 天出厂，物流到您那边大约再 5-7 天。着急用的话告诉我，我看看现货仓有没有合适的。"},
    {"key": "价格", "title": "问价格", "text": "这款日常价是 X 元，最近店铺有活动可以再聊～您可以淘宝搜「畔色」找到我们店铺看详情，下单备注小红书有惊喜。"},
    {"key": "售后", "title": "问售后", "text": "全实木家具支持 X 年质保，开裂/结构问题免费处理。日常每月用木蜡油擦一次就很耐用，下单会随箱送保养说明。"},
    {"key": "返图", "title": "老客返图邀约", "text": "家具用得还满意吗？如果方便拍 2-3 张实景图发给我，给您返现 XX 元/送一瓶保养木蜡油～真实买家秀对我们帮助特别大，谢谢支持！"},
]

# ---- 大促节点日历（内容要提前 45 天种草）----
PROMO_CALENDAR = [
    {"name": "年货节", "month": 1, "day": 5},
    {"name": "38焕新周", "month": 3, "day": 1},
    {"name": "618大促", "month": 6, "day": 1},
    {"name": "818家装节", "month": 8, "day": 8},
    {"name": "双11", "month": 11, "day": 1},
    {"name": "双12", "month": 12, "day": 1},
]
PROMO_LEAD_DAYS = 45  # 家具决策周期长，提前 45 天开始种草

# ---- 知乎长答案占坑题库（高搜索问题，一次投入吃两年流量）----
ZHIHU_SEED_QUESTIONS = [
    "一万元预算能买到什么样的实木餐桌？",
    "实木餐桌怎么选不踩坑？",
    "榉木、橡木、白蜡木做家具哪个好？",
    "岩板餐桌和实木餐桌怎么选？",
    "小户型餐桌怎么选尺寸？",
    "网购大件家具靠谱吗？要注意什么？",
    "实木家具开裂正常吗？怎么保养？",
    "全屋定制和成品家具怎么选？",
    "白蜡木和橡胶木的区别大吗？",
    "餐边柜有必要买吗？怎么选？",
    "实木床怎么挑？哪些坑要避开？",
    "贴皮家具和实木家具怎么分辨？",
    "家具的甲醛主要来自哪里？怎么避免？",
    "茶几选岩板还是实木？",
    "电视柜怎么选尺寸和高度？",
    "餐椅选实木还是软包？",
    "新中式家具适合什么装修风格？",
    "原木风装修怎么选家具？",
    "家具网购和线下买价格差多少？",
    "实木家具值不值得买贵的？",
]

# ---- 品牌专业号官方功能开通清单（合法引流通道，能用尽用）----
OFFICIAL_SETUP_ITEMS = ["企业认证", "开通店铺组件", "开通群聊(私域)", "完善合集/瞬间"]

# ---- 小红书 SEO 搜索词库（#8，选题/标签都吃；家具是搜索驱动）----
SEO_KEYWORDS = {
    "餐桌": ["小户型餐桌", "1米4餐桌", "岩板餐桌优缺点", "实木餐桌推荐", "餐桌尺寸怎么选",
            "折叠餐桌", "餐桌椅搭配", "新中式餐桌", "原木餐桌"],
    "餐椅": ["实木餐椅", "餐椅推荐", "靠背餐椅舒服吗", "餐椅高度多少合适"],
    "茶几": ["小户型茶几", "岩板茶几", "茶几尺寸", "实木茶几推荐"],
    "柜类": ["餐边柜推荐", "电视柜尺寸", "玄关柜", "餐边柜有必要吗"],
    "床": ["实木床推荐", "原木床", "床的尺寸怎么选", "床头软包好吗"],
}

# ---- 标题钩子库（#9，套用到生成；A/B 不同钩子）----
TITLE_HOOKS = [
    "避雷！{kw}千万别这样选",
    "{kw}怎么选？看完这篇不踩坑",
    "搬家才后悔，{kw}早知道就好了",
    "花了冤枉钱才懂，{kw}这样挑最香",
    "{kw}保姆级攻略｜新手必看",
    "真实测评｜{kw}用半年后说点实话",
]

# ---- 封面模板库（#9，给拍图/排版参考）----
COVER_TEMPLATES = [
    {"name": "大字报对比", "desc": "左右对比图+大号标题字，适合避雷/测评", "ratio": "3:4"},
    {"name": "实景氛围", "desc": "家居实拍场景+手写体小标题，适合种草", "ratio": "3:4"},
    {"name": "尺寸标注", "desc": "产品图+尺寸数字标注，适合选购攻略", "ratio": "3:4"},
    {"name": "清单九宫格", "desc": "多产品九宫格+清单标题，适合合集", "ratio": "1:1"},
]

# ---- 团队角色与权限（#14，轻量协作分工）----
TEAM_ROLES = {
    "writer": {"label": "内容", "can": ["topic", "draft"]},
    "reviewer": {"label": "审核", "can": ["review"]},
    "publisher": {"label": "发布", "can": ["dispatch", "comment"]},
    "admin": {"label": "管理员", "can": ["*"]},
}
