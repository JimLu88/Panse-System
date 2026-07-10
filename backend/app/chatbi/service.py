# -*- coding: utf-8 -*-
"""ChatBI 编排 (Plan4 v2 §4.4) —— 四级回答链 + 审计。

route 命中模板 → ✅ 口径已审 (service 直算 / sql 走指标字典);
未命中 → 🟡 半生成 (LLM 选指标) → ⚠ AI 直出 (LLM 写 SQL) → ⛔ 拒答。
PC 离线 → 模板仍可用, 兜底路径降级为"AI 引擎离线"。全路径落 chatbi_queries 审计。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.chatbi import assembler, charts, executor, llm_client
from app.chatbi import router as chatbi_router
from app.chatbi import templates as T
from app.chatbi.assembler import AssemblerError
from app.chatbi.catalog import ALLOWED_VIEWS
from app.chatbi.executor import ExecutorError, infer_column_kinds
from app.chatbi.sql_gate import SqlGateError, validate_readonly_select
from app.chatbi.time_parser import TimeRange
from app.models.chatbi_query import ChatbiQuery
from app.services import settings_service

_log = logging.getLogger("panse.chatbi.service")

BADGE_VERIFIED = "verified"    # ✅ 模板·口径已审
BADGE_SEMI = "semi"            # 🟡 半生成·口径字典拼装
BADGE_GENERATED = "generated"  # ⚠ AI 直出·口径未审
BADGE_REFUSED = "refused"      # ⛔ 拒答
BADGE_POINTER = "pointer"      # ℹ️ 命中模板但指向报表页


def _fingerprint(sql: str | None) -> str | None:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16] if sql else None


def _ro_dsn(db: Session) -> str | None:
    return settings_service.get(db, "chatbi_ro_dsn")


def _promo_windows(db: Session) -> dict | None:
    raw = settings_service.get(db, "chatbi_promo_windows")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        out = {}
        for k, v in data.items():
            out[k] = {"start": date.fromisoformat(v["start"]), "end": date.fromisoformat(v["end"]),
                      "label": v.get("label", k)}
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _sql_template_range(t: "T.Template", route_tr, today: date) -> TimeRange:
    if route_tr is not None:
        return route_tr
    days = t.default_days
    gran = "month" if days >= 60 else "day"
    return TimeRange(today - timedelta(days=days - 1), today, gran, f"近{days}天")


def _data_as_of(db: Session) -> str | None:
    try:
        row = db.execute(text("SELECT max(updated_at) FROM orders")).scalar()
        return row.isoformat() if row else None
    except Exception:  # noqa: BLE001
        return None


def _refuse(message: str, *, badge: str = BADGE_REFUSED) -> dict:
    return {"route": "refused", "badge": badge, "template_key": None,
            "columns": [], "rows": [], "chart": {"type": "table"},
            "sql": None, "caliber_notes": [message], "message": message}


# ------------------------------- 模板路径 ------------------------------- #

def _answer_template(db: Session, r: "chatbi_router.Route", question: str, today: date) -> dict:
    t = r.template
    if t.kind == "pointer":
        return {"route": "refused", "badge": BADGE_POINTER, "template_key": t.key,
                "columns": [], "rows": [], "chart": {"type": "table"}, "sql": None,
                "caliber_notes": [t.pointer], "message": t.pointer}
    if t.kind == "service":
        res = t.handler(db, r.time_range)
        return {"route": "template", "badge": BADGE_VERIFIED, "template_key": t.key,
                "columns": res.columns, "rows": res.rows, "chart": res.chart,
                "sql": res.sql, "caliber_notes": list(res.caliber_notes), "message": ""}
    # sql 模板
    tr = _sql_template_range(t, r.time_range, today)
    aq = assembler.assemble(t.spec, time_range=tr)
    columns, rows = executor.run_readonly(aq.sql, db=db, ro_dsn=_ro_dsn(db))
    chart = charts.pick_chart(aq.columns, row_count=len(rows), question=question)
    return {"route": "template", "badge": BADGE_VERIFIED, "template_key": t.key,
            "columns": aq.columns, "rows": rows, "chart": chart, "sql": aq.sql,
            "caliber_notes": list(aq.caliber_notes) + list(t.caliber_notes), "message": ""}


# ------------------------------- 兜底路径 ------------------------------- #

def _answer_fallback(db: Session, question: str, today: date, r: "chatbi_router.Route") -> dict:
    if not llm_client.is_available(db):
        return _refuse("AI 引擎离线, 请换用模板问法 (如: 本月净利润 / 产品毛利率排行 / 退款率趋势)",
                       badge=BADGE_REFUSED)

    # 🟡 半生成: LLM 只选指标, 代码确定性拼 SQL
    spec = llm_client.gen_semi_spec(db, question)
    if spec:
        try:
            aq = assembler.assemble(spec, time_range=r.time_range)
            columns, rows = executor.run_readonly(aq.sql, db=db, ro_dsn=_ro_dsn(db))
            chart = charts.pick_chart(aq.columns, row_count=len(rows), question=question)
            return {"route": "semi", "badge": BADGE_SEMI, "template_key": None,
                    "columns": aq.columns, "rows": rows, "chart": chart, "sql": aq.sql,
                    "caliber_notes": list(aq.caliber_notes) + ["🟡 AI 选取指标, 口径由字典保证; 请核对所选口径"],
                    "message": ""}
        except (AssemblerError, ExecutorError, SqlGateError) as e:
            _log.info("半生成降级到直出: %s", getattr(e, "reason", e))

    # ⚠ AI 直出: LLM 写 SQL
    sql = llm_client.gen_direct_sql(db, question)
    if sql:
        try:
            gate = validate_readonly_select(sql, ALLOWED_VIEWS)
            columns, rows = executor.run_readonly(gate.safe_sql, db=db, ro_dsn=_ro_dsn(db))
            cols_meta = infer_column_kinds(columns, rows)
            chart = charts.pick_chart(cols_meta, row_count=len(rows), question=question)
            return {"route": "generated", "badge": BADGE_GENERATED, "template_key": None,
                    "columns": cols_meta, "rows": rows, "chart": chart, "sql": gate.safe_sql,
                    "caliber_notes": ["⚠ AI 生成, 口径未审, 重要决策请核对", f"引用: {', '.join(gate.tables)}"],
                    "message": ""}
        except (SqlGateError, ExecutorError) as e:
            _log.info("直出被拒/失败: %s", getattr(e, "reason", e))

    return _refuse("没能理解这个问题, 换个问法或用联想里的模板问法试试")


# ------------------------------- 入口 + 审计 ------------------------------- #

def ask(db: Session, question: str, username: str | None = None, *, today: date | None = None) -> dict:
    today = today or date.today()
    t0 = time.monotonic()
    try:
        r = chatbi_router.route(question, today=today, promo_windows=_promo_windows(db))
        resp = _answer_template(db, r, question, today) if r.kind == "template" \
            else _answer_fallback(db, question, today, r)
        status = "ok" if resp["route"] not in ("refused",) else "refused"
        error = None
    except Exception as e:  # noqa: BLE001
        _log.exception("chatbi ask 失败")
        resp = _refuse(f"处理出错: {str(e)[:120]}")
        status, error = "error", str(e)[:500]

    duration = int((time.monotonic() - t0) * 1000)
    llm_model = None
    if resp["route"] in ("semi", "generated"):
        llm_model = llm_client.active_model(db)
    qid = _write_audit(db, username, question, resp, duration, status, error, llm_model)
    resp.update({"query_id": qid, "duration_ms": duration,
                 "data_as_of": _data_as_of(db), "suggestions": T.suggestions()})
    return resp


def _write_audit(db: Session, username, question, resp, duration, status, error, llm_model) -> int | None:
    try:
        row = ChatbiQuery(
            username=username, question=question[:4000], route=resp["route"],
            template_key=resp.get("template_key"), sql_text=resp.get("sql"),
            sql_fingerprint=_fingerprint(resp.get("sql")),
            row_count=len(resp.get("rows") or []), duration_ms=duration,
            status=status, error=error, llm_model=llm_model,
        )
        db.add(row)
        db.commit()
        return row.id
    except Exception:  # noqa: BLE001
        db.rollback()
        _log.warning("chatbi 审计写入失败", exc_info=True)
        return None


def set_feedback(db: Session, query_id: int, feedback: str, note: str | None = None) -> bool:
    if feedback not in ("up", "down"):
        return False
    row = db.get(ChatbiQuery, query_id)
    if row is None:
        return False
    row.feedback = feedback
    row.feedback_note = (note or "")[:2000] or None
    db.commit()
    return True
