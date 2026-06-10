"""退出观测模式时：用深度模型复盘行为序列，合并安全规则到 evolution_rules.json。"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from apps.core.ai.llm_client import deep_analysis_api_configured, deep_analysis_completion
from apps.core.configs.base_settings import BaseSettings
from apps.core.runtime_paths import default_evolution_rules_path
from apps.core.shadow.rules_prompt import clear_shadow_evolution_prompt_cache
from apps.core.shadow.safety_guard import merge_evolution_rules_file, rule_passes_safety_filter


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        return m.group(1).strip()
    return s


class EvolveEngine:
    """仅文件与 LLM，无任何 UI 自动化。"""

    def analyze_and_merge_rules(
        self,
        *,
        settings: BaseSettings,
        action_events: list[dict[str, Any]],
        customer_scene_excerpt: str,
        log: Callable[[str], None],
    ) -> int:
        if not action_events:
            log("Shadow 演化：无行为记录，跳过。")
            return 0
        if not deep_analysis_api_configured(settings):
            log("Shadow 演化：未配置深度模型或密钥，跳过。")
            return 0
        ev_path = default_evolution_rules_path()
        blob = json.dumps(action_events[:200], ensure_ascii=False)[:24000]
        system = (
            "你是客服流程复盘助手。输入为「客户场景摘要」与「人工操作时间线 JSON」。"
            "请归纳可复用的非价格类策略（如：某类材质质疑时先发对比图、引用某文件夹话术等）。\n"
            "硬性禁止：输出任何涉及改价、订单金额修改、收款码、代付、小额收款、退款操作、"
            "或要求用 UIAutomation/脚本点击后台订单属性的内容；一律不写。\n"
            "另请关注「递进话术」：若时间线体现多轮追问/分段确认节奏，"
            "在 strategy 或 few_shot_example 中给出可复制的递进句式建议（短句、分条），"
            "不得涉及改价或订单金额。\n"
            "只输出一个 JSON 对象："
            '{"rules":[{"trigger":"触发条件简述","strategy":"可执行策略简述",'
            '"few_shot_example":"示例短句","follow_up_hint":"递进追问一句（可空）"}]} ，无则 rules 为空数组。'
        )
        user = (
            "【客户场景摘要】\n"
            + (customer_scene_excerpt or "").strip()[:4000]
            + "\n\n【人工行为序列】\n"
            + blob
        )
        raw = deep_analysis_completion(
            settings=settings,
            system=system,
            user=user,
            max_tokens=4096,
            temperature=0.2,
        )
        try:
            obj = json.loads(_strip_json_fence(raw))
        except json.JSONDecodeError:
            log("Shadow 演化：LLM 输出非 JSON，跳过合并。")
            return 0
        rules = obj.get("rules") if isinstance(obj, dict) else None
        if not isinstance(rules, list):
            return 0
        clean: list[dict[str, Any]] = []
        for r in rules:
            if isinstance(r, dict) and rule_passes_safety_filter(r):
                clean.append(r)
        n = merge_evolution_rules_file(path=ev_path, new_rules=clean)
        clear_shadow_evolution_prompt_cache()
        log(f"Shadow 演化：已合并 {n} 条规则到 {ev_path.name}")
        return n
