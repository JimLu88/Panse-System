"""
独立诊断：买家文本 + trigger 经过 noise filter 后会被怎么处理？

不需要 GUI、不需要千牛、不需要打开 EXE。
直接命令行：
    py D:\\AI\\AI 客服系统\\scripts\\diag_buyer_noise_filter.py

会逐条打印每个测试用例：buyer_text + trigger → 最终被吞 / 落入欢迎语 / 落入 quick_reply / 进 LLM。

v1.5.7 验收要点：
    "在么" + audio_peak  应当：欢迎语 → quick_reply（不再被吞）
    "在么" + visual_scan 应当：仍然被吞（防视觉哨兵自循环）
    "在的呢" + 任何 trigger 应当：仍然被吞（卖家回声）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让脚本能从 dist 里和源码里两种环境直接跑
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.core.orchestrator.reply_guards import (  # noqa: E402
    is_echo_or_noise_buyer_text,
    is_buyer_opening_greeting,
)
from apps.core.ai.input_quality_gate import check_buyer_input  # noqa: E402


CASES = [
    # (buyer_text, trigger, 期望_是否被_noise_过滤, 备注)
    ("在么",     "audio_peak",  False, "v1.5.7 修复核心：叮咚 + 在么 应当回复"),
    ("在吗",     "audio_peak",  False, "v1.5.7 修复：叮咚 + 在吗 应当回复"),
    ("您好",     "audio_peak",  False, "买家纯问候 + 叮咚 应当回复"),
    ("你好在吗", "audio_peak",  False, "买家完整问候 + 叮咚 应当回复"),

    ("在么",     "visual_scan", True,  "视觉哨兵扫到 在么 仍过滤（防自循环）"),
    ("在吗",     "chat_rescan", True,  "聊天重扫 在吗 仍过滤"),

    ("在的呢",   "audio_peak",  True,  "卖家回声：任何 trigger 都过滤"),
    ("嗯嗯",     "audio_peak",  True,  "语气词：任何 trigger 都过滤"),
    ("收到",     "audio_peak",  True,  "客服口头禅：任何 trigger 都过滤"),

    ("有货吗？", "audio_peak",  False, "真问题：放行进 LLM"),
    ("这个多少钱", "audio_peak", False, "真问题：放行进 LLM"),

    ("",         "audio_peak",  True,  "空字符串：过滤"),
    ("2026-05-29 16:42:39", "audio_peak", True, "时间戳：过滤"),
]


def main() -> int:
    print("=" * 72)
    print("买家文本 noise filter 诊断   v1.5.7 修复验证")
    print("=" * 72)
    fail = 0
    for buyer, trigger, expect_filtered, note in CASES:
        actual_filtered = is_echo_or_noise_buyer_text(buyer, trigger=trigger)
        ok = (actual_filtered == expect_filtered)
        flag = "OK " if ok else "FAIL"
        if not ok:
            fail += 1

        is_opening = is_buyer_opening_greeting(buyer)
        gate = check_buyer_input(buyer)

        # 模拟管线最终结果
        if actual_filtered:
            outcome = "noise=true 跳过本轮（不回复）"
        elif gate.action == "discard_log":
            outcome = f"OCR 门控丢弃 ({gate.rule_name})"
        elif gate.action == "quick_reply":
            outcome = f"快速引导回复（{gate.rule_name}）-> '{gate.reply[:24]}...'"
        else:
            outcome = "进 LLM 正常回复"

        prefix = "[开场]" if is_opening else "     "
        print(
            f"[{flag}] {prefix} buyer={buyer!r:14} trigger={trigger:12} "
            f"-> {outcome}"
        )
        print(f"        说明：{note}")
        if not ok:
            print(
                f"        WARN 期望 filtered={expect_filtered}, "
                f"实际 filtered={actual_filtered}"
            )
        print()

    if fail == 0:
        print(f"[PASS] 全部 {len(CASES)} 条用例通过")
        return 0
    print(f"[FAIL] {fail} / {len(CASES)} 条失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
