"""飞书同步增量 hash：不同 JSON 标量表示的同值不能触发全表回写。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.models.order import PartPurchase
from app.services import feishu_sync_service as sync


def _fixture_values():
    row = PartPurchase(
        purchase_no="PO-322",
        purchase_date=date(2026, 7, 22),
        qty=Decimal("1.0000"),
        total_amount=Decimal("322.00"),
    )
    fields = ["purchase_no", "purchase_date", "qty", "total_amount"]
    mapping = {
        "purchase_no": "采购单号",
        "purchase_date": "采购日期",
        "qty": "数量",
        "total_amount": "总金额",
    }
    timestamp_ms = int(datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp() * 1000)
    remote = {
        "采购单号": "PO-322",
        "采购日期": timestamp_ms,
        "数量": "1",
        "总金额": 322,
    }
    return row, fields, mapping, remote


def test_feishu_scalar_variants_hash_as_system_values():
    row, fields, mapping, remote = _fixture_values()
    remote_values = sync._canonical_feishu_values(
        PartPurchase, sync._feishu_values(remote, mapping))
    assert sync._hash(remote_values) == sync._hash(sync._system_values(row, fields))


def test_unchanged_out_binding_does_not_queue_update():
    row, fields, mapping, remote = _fixture_values()
    stable_hash = sync._hash(sync._system_values(row, fields))
    entity = sync.SyncEntity(model=PartPurchase, pk_attr="purchase_no")
    record_map = SimpleNamespace(
        system_hash=stable_hash,
        feishu_hash=stable_hash,
        feishu_record_id="rec-322",
    )
    batch = {"create": [], "update": []}

    sync._sync_one(
        db=None,
        binding=SimpleNamespace(system_table="part_purchases"),
        ent=entity,
        fm=mapping,
        fields=fields,
        pk="PO-322",
        sys_row=row,
        fe_rec={"record_id": "rec-322", "fields": remote},
        m=record_map,
        can_push=True,
        can_pull=False,
        first_sync=False,
        res=sync.SyncResult(system_table="part_purchases"),
        batch=batch,
    )

    assert batch == {"create": [], "update": []}
