"""多 LLM 路由（简化版）。对应 02-cross-cutting/llm-router.md。

业务代码只声明"任务类型"，由路由表决定调哪档模型。
默认 provider=mock：内置家具风格文案生成器，免 key 即可端到端跑通。
配 anthropic / openai 则走真实模型。
"""
from __future__ import annotations

import json
import random

import httpx

from ..config import get_settings

# 任务 → 档位（声明式路由，对应设计稿 routes 表）
ROUTES = {
    "generator.logic_restructure": "top",
    "generator.style_injection": "top",
    "generator.fact_check": "cheap",
    "generator.compliance_scan": "cheap",
    "comment.draft": "top",
    "zhihu.answer": "top",
    "generator.video_script": "top",
    "review.suggest": "cheap",
    "collector.style_extract": "cheap",
}


class LLMRouter:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = self.settings.llm_provider

    def complete(self, task: str, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        """按任务路由到模型并返回文本。mock 时走内置生成器。"""
        if self.provider == "mock":
            return _MockLLM.complete(task, prompt, json_mode=json_mode)
        if self.provider == "anthropic":
            return self._anthropic(prompt, system)
        if self.provider == "openai":
            return self._openai(prompt, system)
        raise ValueError(f"未知 LLM_PROVIDER: {self.provider}")

    def _anthropic(self, prompt: str, system: str) -> str:
        s = self.settings
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": s.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": s.anthropic_model,
                "max_tokens": 1500,
                "system": system or "你是家具品牌的小红书内容创作助手。",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    def _openai(self, prompt: str, system: str) -> str:
        s = self.settings
        base = s.openai_base_url or "https://api.openai.com/v1"
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {s.openai_api_key}"},
            json={
                "model": s.openai_model,
                "messages": [
                    {"role": "system", "content": system or "你是家具品牌的小红书内容创作助手。"},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ----------------- 内置 mock：家具风格文案生成器 -----------------
_HOOKS = [
    "搬进新家才发现，餐桌选错真的会天天后悔😭",
    "小户型的姐妹听我一句，餐桌别买大的！",
    "纠结了一个月的实木餐桌，终于到货啦～",
    "装修踩过的坑，今天全告诉你🙋",
]
_PAINS = [
    "之前那张贴皮的用了半年就开胶，边角还磕白了。",
    "客厅就那么大，桌子一摆转身都难。",
    "网上图片好看，到手色差大到想退货。",
]
_SOLUTIONS = [
    "后来换了榉木的，纹理是真的好看，手感也扎实。",
    "选了 1.2 米的折叠款，平时收起来一点不占地方。",
    "认准实木结构 + 环保板材，闻着没有刺鼻味道。",
]
_CASES = [
    "我家用了快一年，每天擦一擦还是很新。",
    "上次朋友来都问我在哪买的。",
]
_CTAS = [
    "有需要的可以扣 1，我整理了避坑清单～",
    "尺寸怎么选评论区告诉你家户型，帮你看看。",
]
_TAGS = ["实木家具", "小户型装修", "餐桌推荐", "家居好物", "装修避坑", "新家分享"]


class _MockLLM:
    @staticmethod
    def complete(task: str, prompt: str, *, json_mode: bool) -> str:
        if task == "generator.logic_restructure" or task == "generator.style_injection":
            data = {
                "title": random.choice(_HOOKS)[:20],
                "narrative_units": [
                    {"type": "hook", "content": random.choice(_HOOKS)},
                    {"type": "pain_point", "content": random.choice(_PAINS)},
                    {"type": "solution", "content": random.choice(_SOLUTIONS)},
                    {"type": "case", "content": random.choice(_CASES)},
                    {"type": "cta", "content": random.choice(_CTAS)},
                ],
                "tags": random.sample(_TAGS, k=5),
            }
            return json.dumps(data, ensure_ascii=False)
        if task == "comment.draft":
            seeds = [
                "我家也是榉木的，用了一年多还是很扎实，确实耐造👍",
                "小户型真的建议选窄一点的，我家 1.2 米刚刚好～",
                "纹理好看是真的，平时擦一擦保养下能用很久。",
            ]
            return random.choice(seeds)
        if task == "zhihu.answer":
            return _MockLLM._zhihu_answer(prompt)
        if task == "review.suggest":
            return ("从本周数据看，真实感高的品类值得加大投入、把发布时间前移到早高峰，"
                    "互动低的内容增加真实使用场景细节（如「用了三个月」这类锚点）。")
        if task == "generator.video_script":
            return _MockLLM._video_script(prompt)
        if task == "generator.fact_check":
            return json.dumps({"passed": [], "pending_human": []}, ensure_ascii=False)
        return "（mock 输出）"

    @staticmethod
    def _video_script(prompt: str) -> str:
        kw = prompt
        if "：" in prompt:
            kw = prompt.split("：")[-1]
        kw = kw.strip()[:20] or "实木餐桌"
        return f"""【口播脚本 · {kw}】

〔分镜1·0-3秒 抓人〕画面：手敲桌面特写
口播：买{kw}前，这3个坑我替你踩过了。

〔分镜2·3-10秒 痛点〕画面：贴皮开胶/色差对比
口播：第一，别只看"实木"两个字，框架实木和全实木差很多。

〔分镜3·10-20秒 干货〕画面：纹理特写+尺寸标注
口播：第二，尺寸按户型算，每边留60公分走动才不挤。

〔分镜4·20-28秒 信任〕画面：使用半年实拍
口播：我家这张用了快一年，每天擦一擦还跟新的一样。

〔分镜5·28-32秒 引导〕画面：店铺/主页
口播：想看尺寸清单的，评论区扣1，我整理好发你。

#实木家具 #{kw} #家居好物
（口播脚本初稿，配自家实拍视频后发布。）"""

    @staticmethod
    def _zhihu_answer(prompt: str) -> str:
        """生成一篇结构化的知乎长答案初稿（家具理性分析体）。

        从 prompt 里取出问题，套用「先给结论→拆维度→给避坑→收尾」骨架。
        真实模型会写得更细，mock 给出可直接润色的可用框架。
        """
        q = prompt
        for marker in ("问题：", "针对问题", "回答："):
            if marker in prompt:
                q = prompt.split(marker, 1)[1]
        q = q.strip().strip("「」\"' 。") or "实木家具怎么选"
        return f"""先说结论：{q.replace("？", "").replace("?", "")}——抓住「材质、结构、工艺、售后」四点就不容易踩坑，下面拆开讲。

## 一、先看材质，别被名字忽悠
市面上「实木」水分很大。真正耐用的是榉木、白蜡木这类硬木，纹理细、握感沉；橡胶木、松木偏软，价格低但容易磕碰。重点：问清楚是「全实木」还是「框架实木+板材」，看清主材而不是只看一个「实木」标签。

## 二、结构决定用几年
- 榫卯/五金加固的连接比纯胶粘的牢；
- 桌面厚度、横档数量直接影响承重和晃动；
- 含水率有没有做处理，南方潮湿地区尤其要问，否则容易变形开裂。

## 三、尺寸和场景匹配（最容易忽略）
小户型优先窄边或可折叠款，每边预留 60cm 走动空间才舒服。买之前量好你家的实际摆放尺寸，别只看图片。

## 四、避坑清单
1. 警惕「贴皮冒充实木」——看封边和木纹连续性；
2. 「零甲醛」是营销话术，关注的是是否达到 E0/E1 等环保等级；
3. 留意售后年限和开裂、结构问题是否包修。

## 五、总结
预算够上硬木全实木 + 正规品牌质保；预算有限就盯住主材和结构，别为花哨造型多花钱。家具是低频耐用品，宁可多花两天做功课，也别买回来天天后悔。

（以上为初稿框架，请补充自家产品的真实参数、实拍图和买家案例后再发布。）"""


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
