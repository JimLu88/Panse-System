"""兜底归档: 飞书原图收到即按类型落盘; 取图失败回退归档副本; 新增归档 KIND。"""
import json

from app.models.import_file import ImportedFile
from app.services import feishu_bot_service as fb, feishu_client, import_storage


def test_archive_kinds_present():
    for k in ("factory_recon", "purchase", "screenshot"):
        assert k in import_storage.KINDS


def test_feishu_image_archived_on_receipt(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(feishu_client, "download_message_resource", lambda *a, **k: b"IMG-bytes-123")
    monkeypatch.setattr(fb, "classify_image", lambda db, img, **k: ("order_table", 0.95))
    monkeypatch.setattr(feishu_client, "reply_card", lambda *a, **k: None)
    event = {"message": {"message_type": "image", "message_id": "m1",
                         "content": json.dumps({"image_key": "k1"})}}
    fb.on_message_event(db, event)
    db.flush()
    # 原图按 order_table→orders 归档, source=feishu
    files = db.query(ImportedFile).filter_by(kind="orders", source="feishu").all()
    assert len(files) == 1
    # 暂存里记下了归档路径(供取图回退)
    pending = fb._load_pending(db).get("m1")
    assert pending and pending.get("archived_path")


def test_unknown_image_still_archived_as_screenshot(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(feishu_client, "download_message_resource", lambda *a, **k: b"UNKNOWN-IMG")
    monkeypatch.setattr(fb, "classify_image", lambda db, img, **k: ("unknown", 0.1))
    monkeypatch.setattr(feishu_client, "reply_card", lambda *a, **k: None)
    event = {"message": {"message_type": "image", "message_id": "m2",
                         "content": json.dumps({"image_key": "k2"})}}
    fb.on_message_event(db, event)
    db.flush()
    # 不认识的图也兜底进 screenshot, 绝不丢
    assert db.query(ImportedFile).filter_by(kind="screenshot", source="feishu").count() == 1


def test_load_image_falls_back_to_archive_when_feishu_down(db_session, monkeypatch):
    db = db_session
    path = fb._archive_image(db, b"ARCHIVED-IMG", "alipay_flow")
    assert path
    def _boom(*a, **k):
        raise RuntimeError("feishu down")
    monkeypatch.setattr(feishu_client, "download_message_resource", _boom)
    img = fb._load_image(db, "mX", {"file_key": "k", "archived_path": path})
    assert img == b"ARCHIVED-IMG"


def test_load_image_raises_when_no_archive_and_feishu_down(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(feishu_client, "download_message_resource",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        fb._load_image(db, "mY", {"file_key": "k"})   # 无 archived_path
        assert False, "应抛出"
    except RuntimeError:
        pass
