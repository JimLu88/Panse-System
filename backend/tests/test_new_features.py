"""新功能单测: 定制编码识别 / 导入冲突检测 / 飞书同步决策 / 导入后核查 / 用户改密."""
from __future__ import annotations

import io
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.models.exception import DataException
from app.models.feishu_sync import FeishuSyncMap, FeishuTableBinding
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import (
    auth_service,
    excel_importer,
    feishu_sync_service,
    post_import_ai_service,
    sku_utils,
)


def _xlsx(sheet_name, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------- 定制编码识别 ---------------------- #


@pytest.mark.parametrize("sku,prod,expected", [
    ("ABC11", "ABC", False),
    ("ABC12", "ABC", False),
    ("ABC99", "ABC", True),
    ("ABC98", "ABC", True),
    ("ABC97", "ABC", True),
    ("10020111", None, False),   # 尾 11
    ("10020199", None, True),    # 尾 99
])
def test_is_custom_sku_code(sku, prod, expected):
    assert sku_utils.is_custom_sku_code(sku, prod) is expected


def test_pricing_import_flags_custom(db_session):
    data = _xlsx("定价总表", ["产品编码", "SKU编码", "标价"], [
        ["P1", "P111", 100],
        ["P1", "P199", 200],   # 定制
    ])
    excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="定价总表",
        entity_type="pricing_sku",
        mapping={"product_code": "产品编码", "sku_code": "SKU编码", "list_price": "标价"},
    )
    db_session.commit()
    flagged = db_session.query(DataException).filter(
        DataException.exception_type == "custom_sku_detected").all()
    assert len(flagged) == 1
    assert flagged[0].source_pk == "P199"


# ----------------------------- 导入冲突检测 ---------------------- #


def _import_product(db, name, on_conflict="overwrite"):
    data = _xlsx("产品", ["编码", "名称"], [["P1", name]])
    return excel_importer.commit_sheet(
        db, file_bytes=data, sheet_name="产品", entity_type="product",
        mapping={"code": "编码", "name": "名称"}, on_conflict=on_conflict,
    )


def test_reimport_conflict_ask_does_not_overwrite(db_session):
    _import_product(db_session, "椅子A")
    db_session.commit()
    report = _import_product(db_session, "椅子B", on_conflict="ask")
    db_session.commit()
    assert len(report.conflicts) == 1
    diff = report.conflicts[0]["diffs"][0]
    assert diff["field"] == "name"
    assert diff["old"] == "椅子A"
    assert diff["new"] == "椅子B"
    # 未覆盖
    p = db_session.query(Product).filter_by(code="P1").one()
    assert p.name == "椅子A"


def test_reimport_conflict_overwrite_applies(db_session):
    _import_product(db_session, "椅子A")
    db_session.commit()
    _import_product(db_session, "椅子B", on_conflict="overwrite")
    db_session.commit()
    p = db_session.query(Product).filter_by(code="P1").one()
    assert p.name == "椅子B"


def test_reimport_identical_no_conflict(db_session):
    _import_product(db_session, "椅子A")
    db_session.commit()
    report = _import_product(db_session, "椅子A", on_conflict="ask")
    assert report.conflicts == []


# ----------------------------- 支付宝 sheet_account 注入 --------- #


def test_alipay_sheet_account_injection(db_session):
    from app.models.finance import AlipayFlow
    data = _xlsx("企业号流水", ["流水号", "金额"], [["TX1", 100], ["TX2", -50]])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="企业号流水",
        entity_type="alipay_flow",
        mapping={"transaction_no": "流水号", "amount": "金额"},
        sheet_account="企业号",
    )
    db_session.commit()
    assert report.inserted_parents == 2
    flows = db_session.query(AlipayFlow).all()
    assert all(f.account == "企业号" for f in flows)


# ----------------------------- 导入后逻辑核查 (确定性) ---------- #


def test_post_import_deterministic_pricing_below_cost(db_session):
    db_session.add(PricingSku(product_code="P1", sku_code="P111",
                              daily_price=Decimal("50"), accounting_cost=Decimal("80")))
    db_session.commit()
    # AI 未配置 → 走确定性写入
    result = post_import_ai_service.run_after_import(db_session, summary={})
    db_session.commit()
    assert result["logic_issues"] >= 1
    exc = db_session.query(DataException).filter(
        DataException.exception_type == "pricing_below_cost").all()
    assert len(exc) == 1


# ----------------------------- 用户改密 / 编辑 ------------------- #


def test_update_user_and_password(db_session):
    u = auth_service.create_user(db_session, username="bob", password="oldpass1", role="viewer")
    db_session.commit()
    auth_service.update_user(db_session, u, display_name="Bob B", role="operator")
    auth_service.set_password(db_session, u, "newpass1")
    db_session.commit()
    assert u.display_name == "Bob B"
    assert u.role == "operator"
    assert auth_service.authenticate(db_session, "bob", "newpass1") is not None
    assert auth_service.authenticate(db_session, "bob", "oldpass1") is None


# ----------------------------- 飞书同步决策逻辑 ----------------- #


class _FakeFeishu:
    """替身: 内存模拟一张飞书表."""
    def __init__(self):
        self.records = {}   # record_id -> fields
        self._seq = 0
        self.fields = {}    # field_name -> {"field_id", "is_primary"}
        self._fseq = 0
        self.FeishuError = feishu_sync_service.feishu_client.FeishuError

    def list_records(self, db, app_token, table_id, page_size=500):
        return [{"record_id": rid, "fields": dict(f), "last_modified_time": 1}
                for rid, f in self.records.items()]

    def list_table_fields(self, db, app_token, table_id):
        return [{"field_name": n, "field_id": v["field_id"],
                 "is_primary": v["is_primary"], "type": 1}
                for n, v in self.fields.items()]

    def create_field(self, db, app_token, table_id, field_name, field_type=1):
        self._fseq += 1
        fid = f"fld{self._fseq}"
        self.fields[field_name] = {"field_id": fid, "is_primary": False}
        return fid

    def delete_field(self, db, app_token, table_id, field_id):
        for n, v in list(self.fields.items()):
            if v["field_id"] == field_id:
                del self.fields[n]

    def create_record(self, db, app_token, table_id, fields):
        self._seq += 1
        rid = f"rec{self._seq}"
        self.records[rid] = dict(fields)
        return rid

    def update_record(self, db, app_token, table_id, record_id, fields):
        self.records[record_id] = dict(fields)

    def delete_record(self, db, app_token, table_id, record_id):
        self.records.pop(record_id, None)


@pytest.fixture()
def fake_feishu(monkeypatch):
    fake = _FakeFeishu()
    monkeypatch.setattr(feishu_sync_service.feishu_client, "list_records", fake.list_records)
    monkeypatch.setattr(feishu_sync_service.feishu_client, "list_table_fields", fake.list_table_fields)
    monkeypatch.setattr(feishu_sync_service.feishu_client, "create_field", fake.create_field)
    monkeypatch.setattr(feishu_sync_service.feishu_client, "delete_field", fake.delete_field)
    monkeypatch.setattr(feishu_sync_service.feishu_client, "create_record", fake.create_record)
    monkeypatch.setattr(feishu_sync_service.feishu_client, "update_record", fake.update_record)
    monkeypatch.setattr(feishu_sync_service.feishu_client, "delete_record", fake.delete_record)
    return fake


def _binding(db):
    b = FeishuTableBinding(
        system_table="products", feishu_app_token="appT", feishu_table_id="tbl1",
        direction="bidirectional", enabled=True,
        field_mapping='{"code": "编码", "name": "名称"}',
    )
    db.add(b)
    db.flush()
    return b


def test_feishu_push_new_record(db_session, fake_feishu):
    db_session.add(Product(code="P1", name="椅子"))
    b = _binding(db_session)
    db_session.commit()
    res = feishu_sync_service.sync_binding(db_session, b)
    db_session.commit()
    assert res.created_feishu == 1
    assert any(f.get("编码") == "P1" for f in fake_feishu.records.values())
    assert db_session.query(FeishuSyncMap).count() == 1


def test_feishu_pull_new_record(db_session, fake_feishu):
    b = _binding(db_session)
    fake_feishu.records["rec1"] = {"编码": "P9", "名称": "飞书来的"}
    db_session.commit()
    res = feishu_sync_service.sync_binding(db_session, b)
    db_session.commit()
    assert res.created_system == 1
    p = db_session.query(Product).filter_by(code="P9").one()
    assert p.name == "飞书来的"


def test_feishu_conflict_both_changed(db_session, fake_feishu):
    # 先建立同步映射 (一致状态)
    db_session.add(Product(code="P1", name="同名"))
    b = _binding(db_session)
    fake_feishu.records["rec1"] = {"编码": "P1", "名称": "同名"}
    db_session.commit()
    feishu_sync_service.sync_binding(db_session, b)
    db_session.commit()
    # 两侧各改一处
    p = db_session.query(Product).filter_by(code="P1").one()
    p.name = "系统改的"
    fake_feishu.records["rec1"]["名称"] = "飞书改的"
    db_session.commit()
    res = feishu_sync_service.sync_binding(db_session, b)
    db_session.commit()
    assert res.conflicts == 1
    conflict = db_session.query(DataException).filter(
        DataException.exception_type == "feishu_conflict").one()
    assert conflict.context["system_pk"] == "P1"
    # 解决: 保留飞书
    feishu_sync_service.resolve_conflict(db_session, conflict.id, "feishu")
    db_session.commit()
    p = db_session.query(Product).filter_by(code="P1").one()
    assert p.name == "飞书改的"


def test_feishu_conflict_field_level_merge(db_session, fake_feishu):
    db_session.add(Product(code="P1", name="同名"))
    b = _binding(db_session)
    fake_feishu.records["rec1"] = {"编码": "P1", "名称": "同名"}
    db_session.commit()
    feishu_sync_service.sync_binding(db_session, b); db_session.commit()
    p = db_session.query(Product).filter_by(code="P1").one()
    p.name = "系统改的"
    fake_feishu.records["rec1"]["名称"] = "飞书改的"
    db_session.commit()
    feishu_sync_service.sync_binding(db_session, b); db_session.commit()
    conflict = db_session.query(DataException).filter(
        DataException.exception_type == "feishu_conflict").one()
    # 字段级合并: name 字段选飞书 → 系统+飞书两侧都变飞书值, 异常解除
    feishu_sync_service.resolve_conflict_merged(db_session, conflict.id, {"name": "feishu"})
    db_session.commit()
    assert db_session.query(Product).filter_by(code="P1").one().name == "飞书改的"
    assert fake_feishu.records["rec1"]["名称"] == "飞书改的"
    assert db_session.query(DataException).filter_by(status="open").count() == 0


def test_feishu_first_sync_system_authoritative(db_session, fake_feishu):
    # 首次同步: 两侧都有 P1 但值不同 → 以系统为准覆盖飞书, 不报冲突
    db_session.add(Product(code="P1", name="系统名"))
    b = _binding(db_session)
    fake_feishu.records["rec1"] = {"编码": "P1", "名称": "飞书名"}
    fake_feishu.fields = {"编码": {"field_id": "f1", "is_primary": True},
                          "名称": {"field_id": "f2", "is_primary": False}}
    db_session.commit()
    res = feishu_sync_service.sync_binding(db_session, b)
    db_session.commit()
    assert res.conflicts == 0
    assert res.pushed == 1
    assert fake_feishu.records["rec1"]["名称"] == "系统名"


def test_feishu_first_sync_deletes_extra_column(db_session, fake_feishu):
    db_session.add(Product(code="P1", name="椅子"))
    b = _binding(db_session)
    # 飞书表多了一列「多余列」, 首次同步应删掉
    fake_feishu.fields = {
        "编码": {"field_id": "f1", "is_primary": True},
        "名称": {"field_id": "f2", "is_primary": False},
        "多余列": {"field_id": "f3", "is_primary": False},
    }
    db_session.commit()
    feishu_sync_service.sync_binding(db_session, b)
    db_session.commit()
    assert "多余列" not in fake_feishu.fields


def test_feishu_extra_column_after_first_sync_records_conflict(db_session, fake_feishu):
    db_session.add(Product(code="P1", name="椅子"))
    b = _binding(db_session)
    fake_feishu.fields = {"编码": {"field_id": "f1", "is_primary": True},
                          "名称": {"field_id": "f2", "is_primary": False}}
    db_session.commit()
    feishu_sync_service.sync_binding(db_session, b); db_session.commit()  # 首次
    # 之后飞书新增一列 → 第二次同步记冲突, 不删
    fake_feishu.fields["新加列"] = {"field_id": "f9", "is_primary": False}
    feishu_sync_service.sync_binding(db_session, b); db_session.commit()
    assert "新加列" in fake_feishu.fields
    exc = db_session.query(DataException).filter_by(
        exception_type="feishu_extra_field", status="open").one()
    assert "新加列" in exc.context["extra_fields"]
    # 裁决: 删除
    feishu_sync_service.resolve_extra_fields(db_session, exc.id, "delete")
    db_session.commit()
    assert "新加列" not in fake_feishu.fields


def test_webhook_url_verification(db_session):
    from app.services import feishu_webhook_service as wh
    assert wh.handle(db_session, {"type": "url_verification", "challenge": "abc"}) == {"challenge": "abc"}


def test_webhook_token_mismatch_rejected(db_session):
    from app.services import feishu_webhook_service as wh, settings_service
    settings_service.set_value(db_session, "feishu_verification_token", "GOOD"); db_session.commit()
    with pytest.raises(PermissionError):
        wh.handle(db_session, {"type": "url_verification", "challenge": "x", "token": "BAD"})


def _aes_backend_works() -> bool:
    """该沙箱的 cryptography 缺 _cffi_backend, 实际运算会 panic; 生产 docker 正常。"""
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        enc = Cipher(algorithms.AES(b"0" * 32), modes.CBC(b"0" * 16),
                     backend=default_backend()).encryptor()
        enc.update(b"0" * 16) + enc.finalize()
        return True
    except BaseException:
        return False


def test_webhook_decrypt_roundtrip():
    if not _aes_backend_works():
        pytest.skip("cryptography AES 后端在本沙箱不可用 (生产 docker 正常)")
    import base64, hashlib, json
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from app.services import feishu_webhook_service as wh
    key = "mykey"
    plain = json.dumps({"type": "url_verification", "challenge": "ok"}).encode()
    pad = 16 - (len(plain) % 16)
    padded = plain + bytes([pad]) * pad
    iv = b"\x00" * 16
    enc = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(iv),
                 backend=default_backend()).encryptor()
    blob = base64.b64encode(iv + enc.update(padded) + enc.finalize()).decode()
    assert json.loads(wh.decrypt(blob, key))["challenge"] == "ok"


def test_webhook_event_triggers_sync(db_session, fake_feishu):
    db_session.add(Product(code="P1", name="椅子"))
    _binding(db_session)
    db_session.commit()
    from app.services import feishu_webhook_service as wh
    resp = wh.handle(db_session, {
        "header": {"event_type": "drive.file.bitable_record_changed_v1"},
        "event": {"table_id": "tbl1"},
    })
    assert resp == {}
    assert any(f.get("编码") == "P1" for f in fake_feishu.records.values())
