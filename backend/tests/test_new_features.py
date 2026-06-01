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


def test_bill_tables_sync_key_autogenerated(db_session):
    """3 张账单表插入时自动生成 sync_key, 且飞书同步实体主键已切到 sync_key。"""
    from datetime import date
    from app.models.finance import WanshifuBill, LogisticsBill, RefillRecord
    from app.services import feishu_sync_service as S

    w = WanshifuBill(order_no="O1", bill_date=date(2026, 6, 1),
                     service_type="安装", amount=Decimal("30"))
    l = LogisticsBill(order_no="O2", tracking_no="SF99", freight_amount=Decimal("12"))
    r = RefillRecord(order_no="O3", refill_date=date(2026, 6, 1), sku="S1", qty=2)
    db_session.add_all([w, l, r])
    db_session.commit()

    assert w.sync_key == "wsf:O1:2026-06-01:安装:30"
    assert l.sync_key == "log:SF99"          # 有运单号优先用运单号
    assert r.sync_key == "refill:O3:2026-06-01:S1:2"

    ents = S._entities()
    for t in ("wanshifu_bills", "logistics_bills", "refill_records"):
        assert ents[t].pk_attr == "sync_key"


def test_all_preset_mappings_include_pk():
    """所有预设的 field_mapping 都必须包含其同步主键, 且映射字段在模型里都存在。"""
    from app.services.feishu_preset import PRESETS
    from app.services import feishu_sync_service as S
    ents = S._entities()
    for system_table, _tid, _dir, _label, fm in PRESETS:
        ent = ents.get(system_table)
        assert ent is not None, f"{system_table} 未在 _entities 注册"
        assert ent.pk_attr in fm, f"{system_table} 映射缺主键 {ent.pk_attr}"
        for f in fm:
            assert ent.model.__table__.columns.get(f) is not None, \
                f"{system_table}.{f} 模型无此列"


def test_primary_field_value_coerced_to_text():
    """落到飞书主字段的值强制转文本 (主字段永远是文本类型), 非主字段保持原类型。"""
    from datetime import date
    from app.services.feishu_sync_service import _to_feishu_fields

    class _Row:
        pass
    r = _Row()
    r.code = "ABC"
    r.bill_date = date(2026, 6, 1)
    r.amount = 50
    fm = {"code": "编码", "bill_date": "日期", "amount": "金额"}

    # 主字段恰好是日期列 → 该列被转成字符串, 其余列类型不变
    out = _to_feishu_fields(r, fm, primary_fe="日期")
    assert isinstance(out["日期"], str)
    assert isinstance(out["金额"], int)

    # 常规: 主字段是文本编码列 → 完全无影响 (no-op)
    out2 = _to_feishu_fields(r, fm, primary_fe="编码")
    assert out2["编码"] == "ABC"
    assert isinstance(out2["日期"], int)


def test_factory_order_no_sequential(db_session):
    """工厂订单号自动生成 畔色0001 序列, 递增不重复。"""
    from app.services import factory_order_service as fos
    assert fos.next_factory_order_no(db_session) == "畔色0001"
    from app.models.order import FactoryOrder
    db_session.add(FactoryOrder(factory_order_no="畔色0001", qty=1)); db_session.flush()
    assert fos.next_factory_order_no(db_session) == "畔色0002"
    # 旧式 F<订单号> 不参与计数
    db_session.add(FactoryOrder(factory_order_no="F12345", qty=1)); db_session.flush()
    assert fos.next_factory_order_no(db_session) == "畔色0002"


def test_expected_amount_from_pricing(db_session):
    """产品预期金额 = 定价表总出厂成本 × 数量; 无定价/无出厂成本 → None (待补)。"""
    from app.services import factory_order_service as fos
    db_session.add(PricingSku(product_code="P9", sku="窄柜", sku_code="P9-01",
                              factory_cost=Decimal("830")))
    db_session.flush()
    assert fos.expected_amount_for(db_session, "P9", "P9-01", 2) == Decimal("1660")
    assert fos.expected_amount_for(db_session, "P9", None, 1) == Decimal("830")
    assert fos.expected_amount_for(db_session, "NOPE", "NOPE-01", 1) is None


def test_factory_reconciliation_rebuild(db_session):
    """按月把工厂下单表汇总成对账记录: 本期下单金额/账单金额/差异。"""
    from datetime import date
    from app.models.order import FactoryOrder
    from app.models.finance import FactoryReconciliation
    from app.services import factory_reconciliation_service as frs
    db_session.add_all([
        FactoryOrder(factory_order_no="畔色1001", factory_name="玉山县博冠家具有限公司",
                     order_date=date(2026, 3, 5), qty=1,
                     expected_amount=Decimal("800"), factory_bill_amount=Decimal("820")),
        FactoryOrder(factory_order_no="畔色1002", factory_name="玉山县博冠家具有限公司",
                     order_date=date(2026, 3, 20), qty=1,
                     expected_amount=Decimal("1000"), factory_bill_amount=Decimal("980")),
    ])
    db_session.flush()
    res = frs.rebuild_all_periods(db_session, factory_name="玉山县博冠家具有限公司")
    assert res.periods == 1
    rec = db_session.query(FactoryReconciliation).one()
    assert rec.order_amount == Decimal("1800")
    assert rec.bill_amount == Decimal("1800")
    assert rec.period_start == date(2026, 3, 1)
    assert rec.period_end == date(2026, 3, 31)


def test_zero_cost_for_refill_and_install(db_session):
    """补单 + 安装SKU 订单理论成本归0。"""
    from app.models.order import Order
    from app.services import order_cost_service as ocs
    o1 = Order(platform="淘宝", order_no="R1", is_refill=True, qty=1, status="signed")
    o2 = Order(platform="淘宝", order_no="I1", sku="商家安装服务", qty=1, status="signed")
    db_session.add_all([o1, o2]); db_session.flush()
    ocs.recompute_and_save(db_session, o1)
    ocs.recompute_and_save(db_session, o2)
    assert o1.theoretical_cost == Decimal("0")
    assert o2.theoretical_cost == Decimal("0")


def test_default_warehouse():
    """样块/补单→杭州, 其余→江西仓库。"""
    from app.services.order_cost_service import default_warehouse_for
    assert default_warehouse_for("樱桃木样块", None, False) == "杭州"
    assert default_warehouse_for("窄柜100", None, True) == "杭州"
    assert default_warehouse_for("窄柜100", "P-01", False) == "江西仓库"


def test_rederive_refill_flags(db_session):
    """以补单记录为准: 在记录里的标补单, 误标的取消。"""
    from datetime import date
    from app.models.order import Order
    from app.models.finance import RefillRecord
    from app.services import order_sync_service as oss
    db_session.add_all([
        RefillRecord(order_no="A1", refill_date=date(2026, 1, 1), qty=1),
        Order(platform="淘宝", order_no="A1", is_refill=False, qty=1, status="signed"),
        Order(platform="淘宝", order_no="B2", is_refill=True, qty=1, status="signed"),
    ])
    db_session.flush()
    r = oss.rederive_refill_flags(db_session, recompute_cost=False)
    assert "A1" in r.flagged_orders and "B2" in r.unflagged_orders
    a1 = db_session.query(Order).filter_by(order_no="A1").one()
    b2 = db_session.query(Order).filter_by(order_no="B2").one()
    assert a1.is_refill is True and b2.is_refill is False


def test_backfill_compensation_from_aftersales(db_session):
    """售后赔付按订单号聚合回写 Order.compensation_fee。"""
    from app.models.order import Order
    from app.models.marketing import AfterSales
    from app.services import order_sync_service as oss
    db_session.add_all([
        Order(platform="淘宝", order_no="C1", qty=1, status="signed"),
        AfterSales(platform_order_no="C1", compensation_fee=Decimal("120")),
        AfterSales(platform_order_no="C1", factory_compensation=Decimal("30"),
                   logistics_compensation=Decimal("20")),
    ])
    db_session.flush()
    r = oss.backfill_compensation_from_aftersales(db_session)
    assert r.orders_updated == 1
    c1 = db_session.query(Order).filter_by(order_no="C1").one()
    assert c1.compensation_fee == Decimal("170")  # 120 + (30+20)


def test_alipay_flow_routing(db_session):
    """支付宝流水自动归类: 推广补流水号 + 未分类支出建采购 + 工厂翻已付款。"""
    from datetime import date, datetime, timezone
    from app.models.finance import AlipayFlow
    from app.models.marketing import PromotionFlow
    from app.models.order import FactoryOrder, PartPurchase
    from app.services import alipay_flow_router_service as ar
    db_session.add_all([
        # 推广记录待配 (金额200, 日期接近)
        PromotionFlow(transaction_date=date(2026, 3, 10), amount=Decimal("200"), flow_type="充值"),
        AlipayFlow(account="企业号", transaction_no="FLOW200", amount=Decimal("-200"),
                   transaction_time=datetime(2026, 3, 11, tzinfo=timezone.utc),
                   reconciliation_type="promotion"),
        # 未分类支出 → 建采购
        AlipayFlow(account="企业号", transaction_no="FLOW88", amount=Decimal("-88"),
                   counterparty="某五金店", remark="买铰链",
                   transaction_time=datetime(2026, 3, 12, tzinfo=timezone.utc)),
        # 工厂订单有流水号 → 翻已付款
        FactoryOrder(factory_order_no="畔色2001", qty=1, alipay_flow_no="FLOWFAC",
                     payment_status="unpaid"),
        AlipayFlow(account="企业号", transaction_no="FLOWFAC", amount=Decimal("-5000"),
                   transaction_time=datetime(2026, 3, 13, tzinfo=timezone.utc),
                   reconciliation_type="factory_payment"),
    ])
    db_session.flush()
    r = ar.run_all(db_session)
    assert r.promotion_filled == 1
    assert r.purchases_created == 1
    assert r.factory_flipped == 1
    pf = db_session.query(PromotionFlow).one()
    assert pf.alipay_flow_no == "FLOW200"
    pp = db_session.query(PartPurchase).one()
    assert pp.purchase_no.startswith("2026") and len(pp.purchase_no) == 9
    assert pp.alipay_flow_no == "FLOW88"
    fo = db_session.query(FactoryOrder).one()
    assert fo.payment_status == "paid" and fo.payment_date == date(2026, 3, 13)
