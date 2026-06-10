"""独立单元测试 SessionReplyBudget。命令行：py 该脚本。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.core.orchestrator.outbound_history import (  # noqa: E402
    SessionReplyBudget,
    jaccard_similarity,
)


def main() -> int:
    fail = 0

    print("--- jaccard ---")
    s1 = jaccard_similarity("您好在的呢", "您好在的呢")
    s2 = jaccard_similarity("您好在的呢", "您好我在的")
    s3 = jaccard_similarity("有货吗", "什么时候发货")
    print(f"同串 : {s1:.2f}")
    print(f"相似 : {s2:.2f}")
    print(f"不同 : {s3:.2f}")
    if s1 != 1.0:
        print("[FAIL] 同串应当 1.0")
        fail += 1
    if s2 < 0.5 or s2 > 0.9:
        print("[FAIL] 相似应当在 [0.5,0.9]")
        fail += 1
    if s3 > 0.3:
        print("[FAIL] 不同应当 < 0.3")
        fail += 1

    print("--- 3 次硬上限（同 buyer_digest 内） ---")
    b = SessionReplyBudget()
    d = "4|在么"
    expected = [True, True, True, False]
    for i, txt in enumerate([
        "您好欢迎光临",
        "请问您想了解什么",
        "可以稍等下吗",
        "第4条不应发",
    ]):
        ok, reason = b.can_send(txt, d)
        flag = "OK" if ok == expected[i] else "FAIL"
        print(f"  [{flag}] {i+1}. ok={ok} reason={reason!r} text={txt!r}")
        if ok != expected[i]:
            fail += 1
        if ok:
            b.record_sent(txt)

    print("--- 买家新消息后计数重置 ---")
    ok, reason = b.can_send("包邮的呢", "7|什么时候发货")
    flag = "OK" if ok else "FAIL"
    print(f"  [{flag}] 新买家消息: ok={ok} reason={reason!r}")
    if not ok:
        fail += 1

    print("--- 相似度拒发（默认路径，阈值 0.75） ---")
    b2 = SessionReplyBudget()
    # 先 can_send 后 record_sent 才是正确 API 顺序
    ok1, _ = b2.can_send("您好我们这边发圆通", "5|啥快递")
    if ok1:
        b2.record_sent("您好我们这边发圆通")
    # 再发一条极相似的（多一个字"哦"，jaccard ≈ 0.9）
    ok, reason = b2.can_send("您好我们这边发圆通哦", "5|啥快递")
    flag = "OK" if not ok and "similar_to_prev" in reason else "FAIL"
    print(f"  [{flag}] 极相似回复: ok={ok} reason={reason!r}")
    if not (not ok and "similar_to_prev" in reason):
        fail += 1

    print("--- bypass_dedup=True 绕过相似度但仍受 3 次上限 ---")
    b3 = SessionReplyBudget()
    # 1/3：第一次 can_send 会清零 reply_count（digest 从空变成 5|啥快递）
    ok, reason = b3.can_send(
        "您好我们这边发圆通", "5|啥快递", bypass_dedup=True
    )
    if ok:
        b3.record_sent("您好我们这边发圆通")
    flag = "OK" if ok else "FAIL"
    print(f"  [{flag}] 风控重发(1/3): ok={ok} reason={reason!r}")
    if not ok:
        fail += 1

    # 2/3：极相似但 bypass_dedup=True 跳过相似度
    ok, reason = b3.can_send(
        "您好我们这边发圆通哦", "5|啥快递", bypass_dedup=True
    )
    if ok:
        b3.record_sent("您好我们这边发圆通哦")
    flag = "OK" if ok else "FAIL"
    print(f"  [{flag}] 风控重发(2/3, bypass 极相似): ok={ok} reason={reason!r}")
    if not ok:
        fail += 1

    # 3/3
    ok, reason = b3.can_send(
        "您好已经发圆通了", "5|啥快递", bypass_dedup=True
    )
    if ok:
        b3.record_sent("您好已经发圆通了")
    flag = "OK" if ok else "FAIL"
    print(f"  [{flag}] 风控重发(3/3): ok={ok} reason={reason!r}")
    if not ok:
        fail += 1

    # 第 4 次 → 应被 hard_limit 拦
    ok, reason = b3.can_send("再发一句", "5|啥快递", bypass_dedup=True)
    flag = "OK" if not ok and "hard_limit" in reason else "FAIL"
    print(f"  [{flag}] 风控重发到 4 次（应被 hard_limit 拦）: ok={ok} reason={reason!r}")
    if not (not ok and "hard_limit" in reason):
        fail += 1

    print()
    if fail == 0:
        print(f"[PASS] 全部用例通过")
        return 0
    print(f"[FAIL] {fail} 条失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
