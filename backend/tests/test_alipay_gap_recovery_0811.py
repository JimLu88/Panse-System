# -*- coding: utf-8 -*-
"""支付宝日账单缺口自愈：多 CSV ZIP 选明细、失败 hash 重试、按日覆盖补拉。"""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date, timedelta

from app.models.finance import AlipayFlow
from app.models.import_file import ImportedFile
from app.services import agent_ingest_service as ingest


def _signcustomer_zip(order_no: str = "5120803044263167809") -> bytes:
    summary = (
        "#支付宝账务明细查询\n"
        "账号:[example@example.com]\n"
        "业务类型,收入合计,支出合计\n"
        "交易付款,3162.56,0\n"
    ).encode("gbk")
    detail = (
        "#支付宝账务明细查询\n"
        "账号:[example@example.com]\n"
        "起始日期:[2026-08-02 00:00:00] 终止日期:[2026-08-02 23:59:59]\n"
        "------------------------------------------------------------\n"
        "账务流水号,业务流水号,商户订单号,商品名称,发生时间,对方账号,"
        "收入金额（+元）,支出金额（-元）,账户余额（元）,交易渠道,业务类型,备注\n"
        f"L001,B202608020001,T200P{order_no},订单货款,2026-08-02 14:49:39,buyer@example.com,"
        "3162.56,0,10000.00,淘宝,交易付款,\n"
    ).encode("gbk")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # 真实 ZIP 就是汇总在前；旧逻辑固定取第一个成员，必然解析失败。
        zf.writestr("2088_交易明细(汇总).csv", summary)
        zf.writestr("2088_交易明细.csv", detail)
    return buf.getvalue()


def test_signcustomer_zip_selects_detail_member(db_session, tmp_path):
    raw = _signcustomer_zip()
    path = tmp_path / "alipay_api" / "账单_signcustomer_2026-08-02.zip"
    path.parent.mkdir()
    path.write_bytes(raw)

    kind, status, summary = ingest._import_one(db_session, "alipay", path, raw)

    assert kind == "alipay"
    assert status == "imported"
    assert summary["inserted"] == 1
    flow = db_session.query(AlipayFlow).one()
    assert flow.related_order_no == "T200P5120803044263167809"


def test_run_ingest_retries_known_failed_alipay_hash(db_session, monkeypatch, tmp_path):
    raw = _signcustomer_zip("5121274323155006832")
    path = tmp_path / "alipay_api" / "账单_signcustomer_2026-08-02.zip"
    path.parent.mkdir()
    path.write_bytes(raw)
    db_session.add(ImportedFile(
        kind="alipay",
        original_filename=path.name,
        stored_path=str(path),
        file_hash=hashlib.sha256(raw).hexdigest(),
        source="api",
        row_summary={"agent_status": "error", "errors": ["CSV 缺少『交易流水号』列"]},
    ))
    db_session.commit()
    monkeypatch.setattr(ingest, "OUTPUT_DIR", tmp_path)

    result = ingest.run_ingest(db_session, only_paths=[str(path)])

    assert result["retried_errors"] == 1
    assert result["imported"] == 1
    assert db_session.query(AlipayFlow).filter(
        AlipayFlow.related_order_no == "T200P5121274323155006832"
    ).count() == 1


def test_refresh_alipay_daily_pulls_only_failed_or_missing_days(
    db_session, monkeypatch,
):
    today = date.today()
    covered_days = [today - timedelta(days=3), today - timedelta(days=1)]
    failed_day = today - timedelta(days=2)
    for bill_day in covered_days:
        db_session.add(ImportedFile(
            kind="alipay",
            original_filename=f"账单_signcustomer_{bill_day.isoformat()}.zip",
            stored_path=f"/tmp/{bill_day}.zip",
            file_hash=f"ok-{bill_day}",
            source="api",
            row_summary={"agent_status": "imported"},
        ))
    db_session.add(ImportedFile(
        kind="alipay",
        original_filename=f"账单_signcustomer_{failed_day.isoformat()}.zip",
        stored_path=f"/tmp/{failed_day}.zip",
        file_hash=f"error-{failed_day}",
        source="api",
        row_summary={"agent_status": "error"},
    ))
    db_session.commit()

    pulled: list[str] = []
    monkeypatch.setattr(
        ingest.web_agent_service,
        "alipay_accounts",
        lambda db: [{"id": "enterprise", "name": "企业号"}],
    )
    monkeypatch.setattr(
        ingest.web_agent_service,
        "alipay_bill",
        lambda db, aid, bill_type, bill_date: pulled.append(bill_date) or {"ok": True},
    )

    result = ingest.refresh_alipay_daily(db_session, max_days=3)

    assert pulled == [failed_day.isoformat()]
    assert result["pulled"] == 1
    assert result["skipped_covered"] == 2
