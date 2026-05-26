from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import ai_assistant

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AiStatus(BaseModel):
    configured: bool
    model: str


@router.get("/status", response_model=AiStatus)
def status():
    from app.config import get_settings
    return AiStatus(configured=ai_assistant.is_configured(), model=get_settings().ai_model)


class DiagnoseOut(BaseModel):
    log_id: int
    exception_id: int
    text: Optional[str]
    model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_read_tokens: Optional[int]
    error: Optional[str]


@router.post("/diagnose/{exception_id}", response_model=DiagnoseOut)
def diagnose(exception_id: int, db: Session = Depends(get_db)):
    try:
        log, ai = ai_assistant.diagnose_exception(db, exception_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    db.commit()
    return DiagnoseOut(
        log_id=log.id,
        exception_id=exception_id,
        text=ai.text if ai else None,
        model=ai.model if ai else None,
        input_tokens=ai.input_tokens if ai else None,
        output_tokens=ai.output_tokens if ai else None,
        cache_read_tokens=ai.cache_read_tokens if ai else None,
        error=log.error,
    )


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatOut(BaseModel):
    log_id: int
    text: Optional[str]
    model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_read_tokens: Optional[int]
    error: Optional[str]


@router.post("/chat", response_model=ChatOut)
def chat(payload: ChatIn, db: Session = Depends(get_db)):
    log, ai = ai_assistant.chat(
        db, user_message=payload.message, session_id=payload.session_id,
    )
    db.commit()
    return ChatOut(
        log_id=log.id,
        text=ai.text if ai else None,
        model=ai.model if ai else None,
        input_tokens=ai.input_tokens if ai else None,
        output_tokens=ai.output_tokens if ai else None,
        cache_read_tokens=ai.cache_read_tokens if ai else None,
        error=log.error,
    )


class AiLogOut(BaseModel):
    id: int
    action_type: str
    session_id: Optional[str]
    related_exception_id: Optional[int]
    model: Optional[str]
    user_message: Optional[str]
    ai_response: Optional[str]
    error: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_read_tokens: Optional[int]
    created_at: str


class ReconcileWalkthroughOut(BaseModel):
    issues: list[dict]
    ai_used: bool
    total: int


@router.post("/reconcile-walkthrough", response_model=ReconcileWalkthroughOut)
def reconcile_walkthrough(db: Session = Depends(get_db)):
    """对账 AI 走查: 先跑对账规则汇总差异(聚合), 再叠加 open 异常; AI 配置时逐条诊断."""
    from app.models.exception import DataException
    from sqlalchemy import select

    issues: list[dict] = []

    # 1. 实时对账差异 (聚合, 确定性) — 即使没记成异常也能看到
    for f in ai_assistant.collect_reconcile_findings(db):
        issues.append({
            "type": f.rule,
            "description": f"{f.problem}（原因：{f.cause}）",
            "suggestion": f.suggestion,
            "source": f"reconciliation/{f.rule} · 样例 {', '.join(f.sample_keys) or '—'}",
        })

    # 2. open 异常池
    exceptions = db.execute(
        select(DataException).where(DataException.status == "open").limit(50)
    ).scalars().all()

    ai_used = False
    configured = ai_assistant.is_configured(db)
    for exc in exceptions:
        item = {
            "id": exc.id,
            "type": exc.exception_type,
            "description": exc.description,
            "suggestion": exc.suggestion_action,
            "source": f"{exc.source_table}/{exc.source_pk}",
        }
        if configured:
            try:
                _log, ai = ai_assistant.diagnose_exception(db, exc.id)
                db.commit()
                if ai:
                    item["ai_analysis"] = ai.text
                    ai_used = True
            except Exception:
                pass
        issues.append(item)

    return ReconcileWalkthroughOut(issues=issues, ai_used=ai_used, total=len(issues))


@router.get("/logs", response_model=list[AiLogOut])
def list_logs(limit: int = 50, db: Session = Depends(get_db)):
    from sqlalchemy import select
    from app.models.ai import AiChatLog
    rows = db.execute(
        select(AiChatLog).order_by(AiChatLog.id.desc()).limit(limit)
    ).scalars().all()
    return [
        AiLogOut(
            id=r.id, action_type=r.action_type, session_id=r.session_id,
            related_exception_id=r.related_exception_id, model=r.model,
            user_message=r.user_message, ai_response=r.ai_response, error=r.error,
            input_tokens=r.input_tokens, output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens, created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
