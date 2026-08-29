# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
from datetime import date

from app.api import web_agent as api
from app.models.import_file import ImportedFile
from app.models.finance import AccountBalance
from app.services import agent_ingest_service as ingest
from app.services import notify_service, settings_service


def test_qr_goes_to_wechat_image_and_never_feishu(db_session, monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    settings_service.set_value(db_session, "notify_provider", "wechat_work")
    settings_service.set_value(db_session, "notify_webhook", "https://qyapi.example/robot")
    calls = []
    monkeypatch.setattr(
        notify_service, "notify",
        lambda db, text, **kwargs: calls.append(("text", text, kwargs)) or (True, "ok"),
    )
    monkeypatch.setattr(
        notify_service, "notify_image",
        lambda db, body: calls.append(("image", body)) or (True, "ok"),
    )
    monkeypatch.setattr(
        "app.services.feishu_client.send_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得写飞书订单群")),
    )

    result = api.agent_notify(api.AgentNotify(
        kind="qr", text="支付宝主力号请扫码",
        image_b64=base64.b64encode(b"PNG-QR").decode("ascii"),
    ), db_session)

    assert result["feishu"] == "disabled"
    assert result["wechat"] == "二维码已发企业微信"
    assert calls[0][0] == "text"
    assert calls[0][2]["wechat_allowed"] is True
    assert calls[1] == ("image", b"PNG-QR")


def test_wechat_image_payload_uses_required_base64_and_md5(db_session, monkeypatch):
    settings_service.set_value(db_session, "notify_provider", "wechat_work")
    settings_service.set_value(db_session, "notify_webhook", "https://qyapi.example/robot")
    sent = []
    monkeypatch.setattr(
        notify_service, "_post_json",
        lambda url, body, **kwargs: sent.append((url, body)) or (True, '{"errcode":0}'),
    )

    assert notify_service.notify_image(db_session, b"QR") == (True, '{"errcode":0}')
    payload = sent[0][1]
    assert payload["msgtype"] == "image"
    assert payload["image"]["base64"] == base64.b64encode(b"QR").decode("ascii")
    assert payload["image"]["md5"] == hashlib.md5(b"QR").hexdigest()  # noqa: S324


def test_balance_duplicate_scope_is_hash_plus_filename(db_session):
    db_session.add_all([
        ImportedFile(kind="account_balance", original_filename="淘宝聚合_2026-08-23.png",
                     stored_path="a.png", file_hash="same", source="api"),
        ImportedFile(kind="account_balance", original_filename="推广_2026-08-23.png",
                     stored_path="b.png", file_hash="same", source="api"),
    ])
    db_session.commit()

    assert ingest._hash_name_exists(db_session, "same", "淘宝聚合_2026-08-23.png") is not None
    assert ingest._hash_name_exists(db_session, "same", "淘宝聚合_2026-08-24.png") is None


def test_balance_ocr_uses_evidence_date_not_ingest_date(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.vision_ocr_service.parse_balance_screenshot",
        lambda db, raw, account_hint: {
            "available": "123.45", "confidence": "high", "label_found": "可用余额",
        },
    )
    ingest._ocr_balance_to_db(
        db_session,
        ingest.Path("淘宝聚合_2026-08-21.png"),
        b"screenshot",
    )
    row = db_session.query(AccountBalance).filter_by(account_name="淘宝聚合账户").one()
    assert row.as_of_date == date(2026, 8, 21)


def test_shipping_password_endpoint_does_not_echo_secret(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.feishu_bot_service.apply_shipping_password",
        lambda db, password: {"tried": 1, "imported": 1, "failed": 0, "updated": 2},
    )
    result = api.submit_shipping_password(
        api.ShippingPassword(password="SECRET-123"), db_session, _=object(),
    )
    assert result == {
        "accepted": True, "tried": 1, "imported": 1, "failed": 0,
        "updated": 2, "failure_reason": None,
    }
    assert "SECRET-123" not in repr(result)
