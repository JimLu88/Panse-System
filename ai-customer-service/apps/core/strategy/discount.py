"""询价递进提示（与 Pipeline 轮次计数配合）。"""


def discount_round_hint(round_index: int) -> str:
    n = max(1, int(round_index))
    if n <= 1:
        return "【询价策略·第1轮】可先给小额优惠或包邮试探，不要一次报底价。"
    if n == 2:
        return "【询价策略·第2轮】可提及大件/套装组合优惠或赠品。"
    return "【询价策略·第3轮及以后】提示需向主管申请或线下确认方案，避免擅自承诺。"
