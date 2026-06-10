from __future__ import annotations

from apps.core.ai.answer_splitter import _is_eligible_base, _verify_split, should_attempt_split


def test_should_attempt_split_ratio_is_deterministic() -> None:
    ratio = 0.35
    k1 = "a||b"
    k2 = "a||c"
    r1 = should_attempt_split(key=k1, ratio=ratio)
    r1_again = should_attempt_split(key=k1, ratio=ratio)
    r2 = should_attempt_split(key=k2, ratio=ratio)
    assert isinstance(r1, bool)
    assert r1 == r1_again
    assert isinstance(r2, bool)


def test_verify_split_requires_exact_reconstruction() -> None:
    original = "你好世界"
    assert not _verify_split(original, "你好", "界", join="。")  # missing 字符
    assert _verify_split(original, "你", "好世界", join="，")


def test_no_split_greeting_like_tokens() -> None:
    assert not _is_eligible_base("您好，在的呢")
    assert not _is_eligible_base("嗯嗯")
    # Normal-ish longer text (no punctuation) should be eligible.
    assert _is_eligible_base("我明白了请您再确认一下订单")  # length >= 10 and not in denylist

