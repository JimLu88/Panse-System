"""主力号=个人支付宝: parse_balance_screenshot 必须走专用简单提示词(_BALANCE_MAIN_SYSTEM),
不是多板块 _BALANCE_SYSTEM —— 后者会因"找不到叫主力号的板块"把个人交易记录页误判成 null
(2026-07-10 主力号余额抓图从 b.alipay 企业平台改回个人网址后配套)。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import vision_ocr_service
from app.services.ai_provider import AiResponse


def _fake(resp_text: str) -> MagicMock:
    fake = MagicMock()
    fake.chat_with_image.return_value = AiResponse(text=resp_text, model="qwen2.5vl:7b")
    return fake


def test_main_account_uses_dedicated_prompt(db_session):
    fake = _fake('{"available":10250.49,"label_found":"可用余额 10250.49 元","confidence":"high","note":""}')
    with patch.object(vision_ocr_service, "build_provider", return_value=fake):
        r = vision_ocr_service.parse_balance_screenshot(db_session, b"img", account_hint="主力号")
    assert r["available"] == 10250.49 and r["confidence"] == "high"
    system = fake.chat_with_image.call_args.kwargs["system"]
    assert system == vision_ocr_service._BALANCE_MAIN_SYSTEM
    assert "个人支付宝" in system            # 专用提示词特征


def test_nonmain_account_uses_multipanel_prompt(db_session):
    fake = _fake('{"available":60489.45,"label_found":"聚合结算账户","confidence":"high","note":""}')
    with patch.object(vision_ocr_service, "build_provider", return_value=fake):
        vision_ocr_service.parse_balance_screenshot(db_session, b"img", account_hint="淘宝聚合账户")
    system = fake.chat_with_image.call_args.kwargs["system"]
    assert system == vision_ocr_service._BALANCE_SYSTEM   # 聚合仍走多板块


def test_multipanel_prompt_no_longer_carries_stale_enterprise_main_clause():
    # 主力号企业逃生条款已从多板块提示词移除(改走专用分支), 防再次误判个人页
    assert "只有企业账户X和账单期初Y" not in vision_ocr_service._BALANCE_SYSTEM
