"""Post-collection, read-only main Alipay reconciliation and quiet alerts.

Only audit receipts/notification intents are written. Never import, repair money,
retry collection, or treat an empty transaction day as a missing day.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from app.models.finance import AlipayFlow
from app.models.import_file import ImportedFile
from app.services import settings_service, tabular

ACCOUNT = "主力号"
START = date(2026, 6, 30)
LATEST_KEY = "alipay_main_completeness_latest_v1"
log = logging.getLogger(__name__)


def parse_bill(body: str):
    lines = body.splitlines()
    index = next((i for i, line in enumerate(lines)
                  if line.lstrip().startswith("交易号") and "收/支" in line), None)
    if index is None:
        raise ValueError("缺少官方个人账单列头")
    dates = re.findall(r"20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "\n".join(lines[:index]))
    if len(dates) != 2:
        raise ValueError("无法唯一确认官方账单起止时间")
    begin, end = map(datetime.fromisoformat, dates)
    if end < begin:
        raise ValueError("账单时间倒置")
    reader = csv.reader(io.StringIO("\n".join(lines[index:])))
    header = [h.strip() for h in next(reader)]
    cols = [next(i for i, h in enumerate(header) if label in h)
            for label in ("交易号", "交易创建时间", "类型", "金额", "收/支")]
    rows = {}
    for cells in reader:
        if not cells or not cells[0].strip()[:1].isdigit():
            continue
        if len(cells) <= max(cols):
            raise ValueError("交易记录列数不足")
        no, stamp, kind, amount, direction = (cells[i].strip() for i in cols)
        when = datetime.fromisoformat(stamp)
        if not begin <= when <= end:
            raise ValueError("交易日期超出账单范围")
        amount = Decimal(amount)
        if direction == "支出":
            amount = -amount
        elif direction not in ("收入", "不计收支"):
            raise ValueError("未知收支方向")
        kind = "分账" if kind == "交易分账" else kind
        rows[(no, kind, amount)] = when.date()
    return begin, end, rows


def reconcile(bills, database_rows, *, start=START, end=None):
    """Pure comparison; header intervals prove coverage, not transaction dates."""
    end = end or (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1))
    issues = []
    covered = set()
    source = {}
    for identity, body in bills:
        try:
            begin, finish, rows = parse_bill(body)
            # A partially exported final day does not prove that entire day.
            last = finish.date() if finish.time().isoformat() >= "23:59:59" else finish.date()-timedelta(days=1)
            first = begin.date() if begin.time().isoformat() == "00:00:00" else begin.date()+timedelta(days=1)
            day = max(start, first)
            while day <= min(end, last):
                covered.add(day)
                day += timedelta(days=1)
            for key, day in rows.items():
                if start <= day <= end:
                    if key in source and source[key] != day:
                        issues.append(("source_date_conflict:"+hashlib.sha256(repr(key).encode()).hexdigest(), "重叠账单交易日期冲突"))
                    source[key] = day
        except Exception as exc:
            issues.append((f"unreadable:{identity}", f"原始账单{identity}无法核验：{type(exc).__name__}"))
    dbrows = {(r[0].strip(), "分账" if r[1] == "交易分账" else (r[1] or "").strip(), Decimal(r[2])): r[3] for r in database_rows}
    missing = [k for k, d in source.items() if k not in dbrows or dbrows[k] != d]
    if missing:
        digest = hashlib.sha256(repr(sorted(missing)).encode()).hexdigest()
        issues.append(("missing_rows:"+digest, f"账单中有{len(missing)}条记录未正确入账（含金额或日期不一致）"))
    gaps = []
    day = start
    while day <= end:
        if day not in covered:
            gaps.append(day.isoformat())
        day += timedelta(days=1)
    if gaps:
        # Stable issue per uncovered day; a growing gap does not resend old days.
        issues.extend(("coverage:"+d, f"{d}缺少完整原始账单覆盖，需补拉核实") for d in gaps)
    return {"account": ACCOUNT, "start": start.isoformat(), "end": end.isoformat(),
            "source_rows": len(source), "missing_rows": len(missing), "uncovered_days": gaps,
            "issues": [{"key": k, "message": m} for k, m in sorted(set(issues))], "ok": not issues}


def audit(db, *, end=None, collection_failed=False):
    files = db.scalars(select(ImportedFile).where(ImportedFile.kind == "alipay")).all()
    bills, errors = [], []
    for f in files:
        summary = f.row_summary or {}
        if summary.get("account") != ACCOUNT and not (f.original_filename or "").startswith("alipay_main_"):
            continue
        try:
            bills.append((str(f.id), tabular.to_csv_text(Path(f.stored_path).read_bytes(), f.original_filename or "")))
        except Exception:
            errors.append({"key": f"archive:{f.id}", "message": f"原始账单{f.id}无法读取，完整性尚未确认"})
    rows = db.execute(select(AlipayFlow.transaction_no, AlipayFlow.transaction_type,
                             AlipayFlow.amount, AlipayFlow.transaction_time).where(AlipayFlow.account == ACCOUNT)).all()
    result = reconcile(bills, [(a,b,c,d.date() if d else None) for a,b,c,d in rows], end=end)
    result["issues"].extend(errors)
    if collection_failed:
        result["issues"].append({"key":"collection_or_ingest_failed", "message":"本次主力号流水采集或入账失败，请检查本次任务回执"})
    result["ok"] = not result["issues"]
    return result


def record_and_notify(db, result, *, send=None):
    """Persist per-issue send intent before network I/O; unknown is not retried."""
    from app.services import feishu_client, notify_service
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(20261029, 1)"))
    fresh = []
    for issue in result["issues"]:
        key = "alipay_gap_alert_"+hashlib.sha256(issue["key"].encode()).hexdigest()
        if not settings_service.get(db, key, env_fallback=False):
            fresh.append((key, issue))
    result["new_issues"] = len(fresh)
    result["checked_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    result["notification"] = "quiet"
    if fresh and not os.environ.get("PANSE_DISABLE_NOTIFY"):
        chat = notify_service.get_alert_chat_id(db)
        if chat:
            for key, _ in fresh:
                settings_service.set_value(db, key, json.dumps({"state":"intent", "at":result["checked_at"]}))
            db.commit()  # crash/timeout after this point must never duplicate delivery
            try:
                message = "⚠️ 畔色ERP｜主力号流水核对异常\n" + "\n".join(i["message"] for _,i in fresh[:20])
                if len(fresh)>20:
                    message += f"\n另有{len(fresh)-20}项，详见ERP核对记录。"
                receipt = (send or feishu_client.send_text)(db, chat, message)
                mid = receipt.get("message_id")
                state = {"state":"sent" if mid else "unknown", "message_id":mid}
            except Exception:
                state = {"state":"unknown"}  # no alternate route or blind retry
            for key, _ in fresh:
                settings_service.set_value(db, key, json.dumps(state))
            result["notification"] = state
        else:
            result["notification"] = "missing_alert_chat"
    settings_service.set_value(db, LATEST_KEY, json.dumps(result,ensure_ascii=False))
    db.commit()
    return result


def after_collection(*, collection_failed=False):
    """Separate session: audit failures cannot roll back successful imports."""
    from app.database import SessionLocal
    with SessionLocal() as db:
        try:
            result = audit(db, collection_failed=collection_failed)
        except Exception as exc:
            db.rollback()
            result = {"ok":False,"issues":[{"key":"audit_unavailable", "message":"主力号流水核对程序未完成，不能确认完整性"}], "error_type":type(exc).__name__}
        try:
            return record_and_notify(db, result)
        except Exception:
            db.rollback()
            log.exception("Alipay completeness receipt/notification failed")
            return {"ok":False, "notification":"unavailable"}
