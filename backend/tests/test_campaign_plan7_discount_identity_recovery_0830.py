import base64
from datetime import datetime
import hashlib
import io
import json

import openpyxl

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.taobao_listing import TaobaoListing
from app.services import campaign_discount_identity_recovery_service as svc
from app.services import settings_service


def _official_export(*, bad_spec=False) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["注意"])
    ws.append(["商品信息"])
    ws.append([
        "商品Id", "类目id", "类目名称", "宝贝标题", "一口价",
        "导购标题", "商家编码", "发货时间", "最长发货时间",
        "销售属性", "属性对", "skuId", "价格(元)", "库存(件)",
        "发货时间", "商家编码",
    ])
    common = [svc.EXPECTED_ITEM_ID, "cat", "实木餐桌", "蜂蜜餐桌",
              "1333.33", None, "PFG25210021222", "30", "50"]
    for idx, row in enumerate(svc.EXPECTED_ROWS, 1):
        ws.append(common + [f"颜色分类:蜂蜜餐桌-1.{idx};", None,
                            row["old_sku_id"], "3770", 100, None, None])
    for idx, row in enumerate(svc.EXPECTED_ROWS):
        spec = "颜色分类:错误规格;" if bad_spec and idx == 0 else row["spec"]
        ws.append(common + [spec, None, row["sku_id"], "4520", 100,
                            None, row["sku_code"]])
    ws.append(common + ["颜色分类:尺寸定制;", None, "6076826145980",
                        "1333.33", 100, None, "PFG2521002122299"])
    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def _seed(db, monkeypatch, *, current_old=True):
    db.add(CampaignPlan(
        id=7, workflow_key=svc.WORKFLOW_KEY, name="9月超级立减",
        campaign_type="super_reduce", tier="mid",
        start_at=datetime(2026, 9, 1), end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减", status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="long_running_update",
    ))
    snapshot_raw = b"old-audit"
    snapshot_sha = hashlib.sha256(snapshot_raw).hexdigest()
    monkeypatch.setattr(svc, "EXPECTED_ORIGINAL_SNAPSHOT_SHA256", snapshot_sha)
    db.add(CampaignEvidenceSnapshot(
        id=1, plan_id=7, workflow_key=svc.WORKFLOW_KEY,
        evidence_type="single_item_discount_readback",
        request_id="old", scope_sha256=svc.EXPECTED_ORIGINAL_SCOPE_SHA256,
        result_status="differences", rows=[], failure_rows=[],
        execution_boundary={"platform_write": False},
        artifact_sha256=snapshot_sha, artifact_size=len(snapshot_raw),
        artifact_blob=snapshot_raw,
    ))
    for row in svc.EXPECTED_ROWS:
        db.add(PricingSku(
            product_code="PFG25210021222", product_name="蜂蜜餐桌",
            sku=row["spec"], sku_code=row["sku_code"],
            daily_price=row["daily"], small_promo=row["final"],
        ))
        db.add(PricingSkuPromo(
            sku_code=row["sku_code"], taobao_item_id=svc.EXPECTED_ITEM_ID,
            taobao_sku_id=(row["old_sku_id"] if current_old else "wrong"),
            shop_internal_promo=row["deduct"],
            shop_internal_final=row["final"],
        ))
    settings_service.set_value(
        db, svc.OLD_ATTEMPT_KEY,
        json.dumps({
            "status": "failed_terminal_no_retry",
            "attempt_id": svc.EXPECTED_OLD_ATTEMPT_ID,
            "submitted": False, "terminal_job_id": "job2",
            "terminal_evidence_request_id":
                "single-discount-terminal-2ff91afda28d24a1",
            "missing_scope_sha256":
                "2ef18e9537abae8af10ec1a0580336e2377b1ca3a7da38d4247a9bc7bf4a9739",
        }),
    )
    db.commit()
    monkeypatch.setattr(
        svc, "_now_shanghai", lambda: datetime(2026, 8, 30, 12, 0, 0))


def _raw_rows():
    return [{
        "taobao_item_id": row["item_id"], "taobao_sku_id": row["sku_id"],
        "sku_code": row["sku_code"], "deduct": float(row["deduct"]),
        "official": float(row["official"]),
        "target_price": float(row["final"]),
        "calculation_base": float(row["daily"]), "kind": "nosales",
        "concession": 0.0,
    } for row in svc.EXPECTED_ROWS]


def _canonical():
    return sorted(({
        "item_id": row["item_id"], "sku_id": row["sku_id"],
        "sku_code": row["sku_code"], "daily": row["daily"],
        "deduct": row["deduct"], "official": row["official"],
        "final": row["final"], "kind": "nosales", "concession": "0.00",
    } for row in svc.EXPECTED_ROWS), key=lambda row: row["sku_id"])


def _rows(classification):
    return [{
        "item_id": row["item_id"], "sku_id": row["sku_id"],
        "expected_deduct": row["deduct"],
        "actual_deduct": row["deduct"] if classification != "missing" else None,
        "classification": classification,
        "status": "未开始" if classification != "missing" else None,
    } for row in svc.EXPECTED_ROWS]


def _artifact():
    raw = b"new-readback"
    return {
        "kind": "canonical_visible_readback_json", "filename": "readback.json",
        "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "content_b64": base64.b64encode(raw).decode(),
    }


def _call(db, raw):
    sha = hashlib.sha256(raw).hexdigest()
    return svc.recover_plan7_single_discount_identity(
        db, workflow_key=svc.WORKFLOW_KEY, expected_plan_id=7,
        expected_old_attempt_id=svc.EXPECTED_OLD_ATTEMPT_ID,
        official_product_export_sha256=sha,
        official_product_export_b64=base64.b64encode(raw).decode(),
        expected_new_scope_sha256=svc.EXPECTED_NEW_MISSING_SCOPE_SHA256,
    )


def _install_export_hash(monkeypatch, raw):
    monkeypatch.setattr(
        svc, "EXPECTED_OFFICIAL_EXPORT_SHA256", hashlib.sha256(raw).hexdigest())


def _seed_submitted_readback(db, monkeypatch):
    _seed(db, monkeypatch)
    for promo in db.query(PricingSkuPromo).all():
        expected = next(
            row for row in svc.EXPECTED_ROWS
            if row["sku_code"] == promo.sku_code)
        promo.taobao_sku_id = expected["sku_id"]
    settings_service.set_value(
        db, svc.IDENTITY_RECEIPT_KEY,
        json.dumps({
            "status": "completed",
            "official_export_sha256": svc.EXPECTED_OFFICIAL_EXPORT_SHA256,
        }),
    )
    settings_service.set_value(
        db, svc.RECOVERY_ATTEMPT_KEY,
        json.dumps({
            "status": "failed_post_submit_readback",
            "attempt_id": svc.EXPECTED_RECOVERY_ATTEMPT_ID,
            "workflow_key": svc.WORKFLOW_KEY,
            "plan_id": 7,
            "submitted": True,
            "terminal_job_id": "job2",
            "terminal_evidence_request_id": (
                svc.EXPECTED_TERMINAL_EVIDENCE_REQUEST_ID),
            "official_export_sha256": svc.EXPECTED_OFFICIAL_EXPORT_SHA256,
            "new_scope_sha256": svc.EXPECTED_NEW_MISSING_SCOPE_SHA256,
            "post_submit_snapshot_id": None,
        }),
    )
    db.commit()


def _verify_readback(db):
    return svc.verify_plan7_identity_recovery_readback(
        db, workflow_key=svc.WORKFLOW_KEY, expected_plan_id=7,
        expected_attempt_id=svc.EXPECTED_RECOVERY_ATTEMPT_ID,
        expected_terminal_evidence_request_id=(
            svc.EXPECTED_TERMINAL_EVIDENCE_REQUEST_ID),
        expected_scope_sha256=svc.EXPECTED_NEW_MISSING_SCOPE_SHA256,
    )


def test_exact_identity_repair_and_recovery_writes_four_rows_once(
        db_session, monkeypatch):
    raw = _official_export()
    _install_export_hash(monkeypatch, raw)
    _seed(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_current_rows", lambda *_: (_raw_rows(), _canonical()))
    reads = iter([
        {"ok": True, "web_agent_job_id": "read-before", "rows": _rows("missing")},
        {"ok": True, "web_agent_job_id": "read-after",
         "rows": _rows("present_not_effective"),
         "platform_summary": {"present_not_effective": 4},
         "artifact": _artifact()},
    ])
    monkeypatch.setattr(svc, "_platform_read", lambda *_: next(reads))
    writes = []

    def upload(_db, channel, phase, xlsx, *_args, **kwargs):
        writes.append((channel, phase, xlsx, kwargs))
        return {"ok": True, "submitted": True, "job": "write-once",
                "evidence_request_id": "terminal-new-identities"}

    monkeypatch.setattr(svc.campaign_service, "_upload_and_wait", upload)
    result = _call(db_session, raw)

    assert result["ok"] is True
    assert result["attempt"]["status"] == "completed"
    assert len(writes) == 1
    assert writes[0][0:2] == ("single_item_discount", "commit")
    assert writes[0][3]["expected_rows"] == 4
    assert svc.campaign_discount_audit_service.xlsx_scope_sha256(
        writes[0][2]) == svc.EXPECTED_NEW_MISSING_SCOPE_SHA256
    promos = db_session.query(PricingSkuPromo).all()
    assert {p.sku_code: p.taobao_sku_id for p in promos} == {
        row["sku_code"]: row["sku_id"] for row in svc.EXPECTED_ROWS}
    listings = db_session.query(TaobaoListing).filter(
        TaobaoListing.sku_code.is_not(None)).all()
    actual_listings = {row.sku_code: row.taobao_sku_id for row in listings}
    expected_listings = {
        item["sku_code"]: item["sku_id"] for item in svc.EXPECTED_ROWS}
    assert expected_listings.items() <= actual_listings.items()
    snapshots = db_session.query(CampaignEvidenceSnapshot).order_by(
        CampaignEvidenceSnapshot.id).all()
    assert [row.evidence_type for row in snapshots] == [
        "single_item_discount_readback", "taobao_sku_identity_correction",
        "single_item_discount_identity_recovery_readback"]


def test_mapping_cas_mismatch_stops_before_platform(db_session, monkeypatch):
    raw = _official_export()
    _install_export_hash(monkeypatch, raw)
    _seed(db_session, monkeypatch, current_old=False)
    reads = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: reads.append(True))

    result = _call(db_session, raw)

    assert result["error"] == "sku_identity_old_value_cas_mismatch"
    assert reads == []


def test_official_spec_mismatch_stops_before_identity_write(
        db_session, monkeypatch):
    raw = _official_export(bad_spec=True)
    _install_export_hash(monkeypatch, raw)
    _seed(db_session, monkeypatch)

    result = _call(db_session, raw)

    assert result["error"] == "official_product_export_current_identity_mismatch"
    assert {p.taobao_sku_id for p in db_session.query(PricingSkuPromo).all()} == {
        row["old_sku_id"] for row in svc.EXPECTED_ROWS}


def test_prior_failed_attempt_identity_is_mandatory(db_session, monkeypatch):
    raw = _official_export()
    _install_export_hash(monkeypatch, raw)
    _seed(db_session, monkeypatch)
    settings_service.set_value(db_session, svc.OLD_ATTEMPT_KEY, "{}")
    db_session.commit()

    result = _call(db_session, raw)

    assert result["error"] == "sku_identity_recovery_prior_evidence_mismatch"


def test_new_terminal_failure_can_never_retry(db_session, monkeypatch):
    raw = _official_export()
    _install_export_hash(monkeypatch, raw)
    _seed(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_current_rows", lambda *_: (_raw_rows(), _canonical()))
    reads = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: (
        reads.append(True) or {"ok": True, "web_agent_job_id": "read",
                               "rows": _rows("missing")}))
    writes = []
    monkeypatch.setattr(svc.campaign_service, "_upload_and_wait", lambda *_a, **_k: (
        writes.append(True) or {"ok": False, "submitted": False,
                                "job": "failed", "error": "terminal"}))

    first = _call(db_session, raw)
    second = _call(db_session, raw)

    assert first["error"] == "sku_identity_recovery_terminal_failed_no_retry"
    assert second["error"] == "sku_identity_recovery_attempt_already_claimed_no_retry"
    assert len(reads) == 1
    assert len(writes) == 1


def test_current_four_rows_are_noop_after_identity_repair(
        db_session, monkeypatch):
    raw = _official_export()
    _install_export_hash(monkeypatch, raw)
    _seed(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_current_rows", lambda *_: (_raw_rows(), _canonical()))
    monkeypatch.setattr(svc, "_platform_read", lambda *_: {
        "ok": True, "web_agent_job_id": "read-only",
        "rows": _rows("present_not_effective")})
    writes = []
    monkeypatch.setattr(svc.campaign_service, "_upload_and_wait",
                        lambda *_a, **_k: writes.append(True))

    result = _call(db_session, raw)

    assert result["ok"] is True
    assert result["already_exact_no_write"] is True
    assert writes == []
    assert result["execution_boundary"]["platform_write"] is False


def test_submitted_attempt_closes_with_one_read_only_exact_readback(
        db_session, monkeypatch):
    _seed_submitted_readback(db_session, monkeypatch)
    monkeypatch.setattr(
        svc, "_current_rows", lambda *_: (_raw_rows(), _canonical()))
    reads = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: (
        reads.append(True) or {
            "ok": True, "web_agent_job_id": "readback-only",
            "rows": _rows("present_not_effective"),
            "platform_summary": {"present_not_effective": 4},
            "artifact": _artifact(),
        }))
    monkeypatch.setattr(
        svc.campaign_service, "_upload_and_wait",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("readback must never upload")))

    first = _verify_readback(db_session)
    second = _verify_readback(db_session)

    assert first["ok"] is True
    assert first["attempt"]["attempt_id"] == svc.EXPECTED_RECOVERY_ATTEMPT_ID
    assert first["attempt"]["status"] == "completed"
    assert first["execution_boundary"]["platform_write"] is False
    assert first["execution_boundary"]["account_action"] is False
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert reads == [True]


def test_readback_difference_is_terminal_and_never_uploads_or_rereads(
        db_session, monkeypatch):
    _seed_submitted_readback(db_session, monkeypatch)
    monkeypatch.setattr(
        svc, "_current_rows", lambda *_: (_raw_rows(), _canonical()))
    bad_rows = _rows("present_not_effective")
    bad_rows[0] = {
        **bad_rows[0], "classification": "amount_mismatch",
        "actual_deduct": "1.00",
    }
    reads = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: (
        reads.append(True) or {
            "ok": True, "web_agent_job_id": "difference",
            "rows": bad_rows, "artifact": _artifact(),
        }))
    monkeypatch.setattr(
        svc.campaign_service, "_upload_and_wait",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("readback must never upload")))

    first = _verify_readback(db_session)
    second = _verify_readback(db_session)

    assert first["error"] == "sku_identity_readback_not_exact_no_retry"
    assert first["execution_boundary"]["platform_write"] is False
    assert second["error"] == "sku_identity_readback_already_claimed_no_retry"
    assert reads == [True]


def test_readback_rejects_any_other_submitted_attempt(
        db_session, monkeypatch):
    _seed_submitted_readback(db_session, monkeypatch)
    attempt = json.loads(settings_service.get(
        db_session, svc.RECOVERY_ATTEMPT_KEY, env_fallback=False))
    attempt["terminal_evidence_request_id"] = "wrong-terminal"
    settings_service.set_value(
        db_session, svc.RECOVERY_ATTEMPT_KEY, json.dumps(attempt))
    db_session.commit()
    reads = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: reads.append(True))

    result = _verify_readback(db_session)

    assert result["error"] == "sku_identity_readback_attempt_receipt_mismatch"
    assert reads == []
