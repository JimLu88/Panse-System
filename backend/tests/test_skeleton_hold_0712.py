"""骨架单(SKU未回填)暂缓推工厂 (用户 2026-07-12: 畔色301单付款赶在报表导出后, 无图无尺寸推给了工厂)。

规则: 订单 sku_code 与 sku 都空 → 图/尺寸都解析不出 → 先不推、不占「畔色X单」号、【留在待推队列】;
取数回填后下一轮 push 自动带图带尺寸补推(自愈); 手动单单重推同样拦住并说明原因。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.import_file import ImportedFile  # noqa: F401 (确保建表)
from app.models.order import Order
from app.services import order_sheet_archive_service as osa
from app.services import settings_service


@pytest.fixture
def _stub(monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    monkeypatch.setattr(osa, "render_png", lambda sheet: f"PNG-{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, png: "k")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, cid, text: {"ok": True})
    monkeypatch.setattr("app.services.feishu_client.send_image", lambda db, cid, key: {"ok": True})
    notes: list[str] = []
    monkeypatch.setattr("app.services.notify_service.notify",
                        lambda db, text, **kw: notes.append(text))
    return notes


def _skeleton(db, no):
    o = Order(platform="淘宝", order_no=no, qty=1,
              product_name="畔色全实木榉木床头柜", sku=None, sku_code=None, product_code=None,
              order_date=date(2026, 7, 11), status="paid", paid_amount=Decimal("1080.88"))
    db.add(o)
    db.flush()
    return o


def test_skeleton_held_then_pushed_after_backfill(db_session, _stub):
    db = db_session
    settings_service.set_value(db, "feishu_push_chat_id", "oc_factory")
    o = _skeleton(db, "SK1")
    osa.generate_pending(db)

    res = osa.push_pending_images(db, include_baseline=True)
    assert "SK1" not in res["order_nos"]            # 不推
    assert res["held_no_sku"] == ["SK1"]            # 记入暂缓名单(内部提醒)
    assert _stub and "SK1" in _stub[0]               # 发了内部提醒(非工厂群)
    db.refresh(o)
    assert o.factory_no is None                      # 不占号

    # 取数回填 SKU → 下一轮自动补推(自愈)
    o.sku, o.sku_code, o.product_code = "榉木床头柜-标准", "PPS2638004022511", "PPS26380040225"
    db.commit()
    res2 = osa.push_pending_images(db, include_baseline=True)
    assert "SK1" in res2["order_nos"]                # 回填后推出去了
    db.refresh(o)
    assert o.factory_no is not None                  # 此时才顺排号


def test_repush_rejects_skeleton(db_session, _stub):
    db = db_session
    settings_service.set_value(db, "feishu_push_chat_id", "oc_factory")
    _skeleton(db, "SK2")
    db.commit()
    r = osa.repush_to_factory(db, "SK2")
    assert r["ok"] is False and "回填" in r["error"]
