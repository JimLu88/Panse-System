"""AI 辅助系统 (plan §7 + 业务需求扩展).

封装 AI Provider 抽象 (anthropic / openai 兼容):
    - diagnose_exception(exception_id): 给一条 data_exceptions 写人话诊断 + 修复建议
    - chat(messages, system?): 通用对话

设计要点：
    - 系统提示走 prompt caching (anthropic 5 分钟 TTL)
    - provider/model 优先读 system_settings 表 (后台可改), 缺省 fallback 到 .env
    - 没 key 时返回 AiUnavailable，不抛
    - 每次调用写一条 ai_chat_logs 审计 (含 token usage)
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.ai import AiChatLog
from app.models.exception import DataException
from app.models.knowledge import AiKnowledge
from app.services import settings_service
from app.services.ai_provider import AiResponse as ProviderResponse
from app.services.ai_provider import AiUnavailable as ProviderUnavailable
from app.services.ai_provider import build_provider

settings = get_settings()


SYSTEM_PROMPT = """你是「畔色孚格 ERP」的内置 AI 故障分析助手。

你的职责（来自 plan §7.1）：
1. 看到一条「数据异常」时，用大白话告诉用户：
   - 这条异常说的是什么（business-level，不要复读字段名）
   - 为什么会发生
   - 怎么处理 — 给出 1~3 个具体操作步骤
2. 如果异常是「对账差异」，明确判断这条差异是否在「可安全抹平」范围内：
   - ±0.5% 内 或 ±5 元内 → 可抹平
   - 否则 → 必须人工核对，不要给出抹平建议
3. 永远不要瞎编数字。如果上下文里没有某个值，就说「需要确认 XX」。
4. 回答用简体中文，每段不超过 3 行。不要寒暄，直接进结论。

你不可以执行的事 (plan §7.2 安全边界)：
- 你不能直接改数据 — 你只给建议，由前端展示给用户确认
- 你不能修改代码 — 代码补丁必须经管理员审批
- 你看不到密码、API 密钥等敏感字段

输出格式（严格按这个结构）：
【发生了什么】<一句话总结>
【可能原因】<1~3 条短句>
【建议操作】<编号列表，每条以动词开头>
【是否可自动抹平】是 / 否 / 不适用（仅对账差异）
"""


# 兼容老接口: 旧测试仍在用 ai_assistant.AiResponse / AiUnavailable / _client
AiResponse = ProviderResponse
AiUnavailable = ProviderUnavailable


def _client():
    """老接口: 旧测试通过 patch('_client') 接管 anthropic 客户端调用。
    新代码请用 build_provider(); 这里只保留 fallback 实现以兼容老测试。
    """
    import anthropic
    if not settings.anthropic_api_key:
        raise AiUnavailable("ANTHROPIC_API_KEY 未配置；请在 .env 里设好后重启服务。")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _log(
    db: Session,
    *,
    action_type: str,
    user_message: Optional[str] = None,
    ai_response: Optional[str] = None,
    related_exception_id: Optional[int] = None,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    usage: Optional[dict[str, int]] = None,
    error: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> AiChatLog:
    log = AiChatLog(
        action_type=action_type,
        user_message=user_message,
        ai_response=ai_response,
        related_exception_id=related_exception_id,
        session_id=session_id,
        model=model,
        input_tokens=usage.get("input_tokens") if usage else None,
        output_tokens=usage.get("output_tokens") if usage else None,
        cache_read_tokens=usage.get("cache_read_input_tokens") if usage else None,
        cache_creation_tokens=usage.get("cache_creation_input_tokens") if usage else None,
        error=error,
        extra=extra,
    )
    db.add(log)
    db.flush()
    return log


def _call_claude(
    *,
    user_message: str,
    extra_system: str = "",
    max_tokens: int = 1024,
) -> AiResponse:
    """统一的 Claude 调用入口：缓存系统提示 + 返回结构化用量。"""
    client = _client()
    # 把上下文放在 system 第二块里, 第一块 = 固定 SYSTEM_PROMPT
    # 在最后一块加 cache_control 同时缓存 system_prompt + 上下文 — 上下文一般每次不同
    # 所以只缓存第一个固定块就够
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if extra_system:
        system.append({"type": "text", "text": extra_system})

    resp = client.messages.create(
        model=settings.ai_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    u = resp.usage
    return AiResponse(
        text=text,
        model=resp.model,
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _has_db_ai_config(db: Session, kind: str) -> bool:
    """db 里是否存了 ai_{kind}_* 任意键 (有 = 走 provider 路径, 无 = 走老 _client 路径)."""
    from app.models.settings import SystemSetting
    row = db.execute(
        select(SystemSetting.id).where(SystemSetting.key.like(f"ai_{kind}_%"))
    ).first()
    return row is not None


def _call_ai(
    db: Session, *,
    kind: str,
    user_message: str,
    extra_system: str = "",
    max_tokens: int = 1024,
) -> AiResponse:
    """统一入口: db 有覆盖 → 走 provider 抽象; 否则 → 老 _client 路径 (保留 test 契约)."""
    if not _has_db_ai_config(db, kind):
        return _call_claude(
            user_message=user_message, extra_system=extra_system, max_tokens=max_tokens,
        )
    cfg = settings_service.get_ai_config(db, kind)
    provider = build_provider(cfg)
    sys_text = SYSTEM_PROMPT if not extra_system else f"{SYSTEM_PROMPT}\n\n{extra_system}"
    return provider.chat(system=sys_text, user=user_message, max_tokens=max_tokens)


def _context_hash(exc: DataException) -> str:
    """生成稳定 hash, 剥掉所有非中文非空白字符 (=所有 ID/编码) 后做 sha256.

    例: 三条 description 只是订单号 / 物料编码 / 数量不同, 都规范化为
        '订单 <X> 引用了不存在的产品编码 <X>', 命中同一条 ai_knowledge.
    """
    norm = re.sub(r"[^一-龥\s]+", "<X>", exc.description or "")
    norm = re.sub(r"\s+", " ", norm).strip()
    payload = f"{exc.exception_type}|{norm}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _try_knowledge_cache(db: Session, exc: DataException) -> Optional[AiResponse]:
    h = _context_hash(exc)
    row = db.execute(
        select(AiKnowledge).where(
            AiKnowledge.exception_type == exc.exception_type,
            AiKnowledge.context_hash == h,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.usage_count = (row.usage_count or 0) + 1
    row.last_used_at = datetime.now(timezone.utc)
    db.flush()
    return AiResponse(
        text=row.solution_text,
        model=f"{row.model or 'unknown'} [cache hit]",
        input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
    )


def _write_knowledge_cache(db: Session, exc: DataException, ai: AiResponse) -> None:
    h = _context_hash(exc)
    if db.execute(
        select(AiKnowledge.id).where(
            AiKnowledge.exception_type == exc.exception_type,
            AiKnowledge.context_hash == h,
        )
    ).first():
        return
    db.add(AiKnowledge(
        exception_type=exc.exception_type,
        context_hash=h,
        solution_text=ai.text,
        source_exception_id=exc.id,
        source_description=exc.description,
        model=ai.model,
        usage_count=1,
        last_used_at=datetime.now(timezone.utc),
    ))
    db.flush()


def diagnose_exception(db: Session, exception_id: int) -> tuple[AiChatLog, Optional[AiResponse]]:
    """让 AI 分析一条异常，返回 (日志, 响应 or None)。

    先查知识库缓存 (plan §12.2)；缓存命中直接复用, 不打 API。
    会把诊断结果写回 data_exceptions.ai_analysis 字段。
    """
    exc = db.get(DataException, exception_id)
    if exc is None:
        raise ValueError(f"exception {exception_id} not found")

    # 先查知识库
    cached = _try_knowledge_cache(db, exc)
    if cached is not None:
        exc.ai_analysis = cached.text
        log = _log(
            db, action_type="diagnose",
            user_message=f"(cache lookup for exception {exception_id})",
            ai_response=cached.text,
            related_exception_id=exception_id,
            model=cached.model,
            extra={"cache_hit": True},
        )
        return log, cached

    context = (
        f"待分析异常 #{exc.id}\n"
        f"- 来源表: {exc.source_table}\n"
        f"- 主键: {exc.source_pk}\n"
        f"- 类型: {exc.exception_type}\n"
        f"- 严重度: {exc.severity}\n"
        f"- 系统给的描述: {exc.description}\n"
        f"- 建议动作 (系统初判): {exc.suggestion_action}\n"
        f"- 上下文 JSON: {exc.context}\n"
    )
    user_msg = f"{context}\n请按要求的输出格式分析这条异常。"

    try:
        ai = _call_ai(db, kind="diagnose", user_message=user_msg, max_tokens=800)
    except AiUnavailable as e:
        log = _log(
            db,
            action_type="diagnose",
            user_message=user_msg,
            related_exception_id=exception_id,
            error=str(e),
        )
        return log, None
    except Exception as e:  # pragma: no cover — 网络/上游错误
        log = _log(
            db,
            action_type="diagnose",
            user_message=user_msg,
            related_exception_id=exception_id,
            error=f"{type(e).__name__}: {e}",
        )
        return log, None

    # 写回异常表 + 知识库缓存 (plan §12.2)
    exc.ai_analysis = ai.text
    _write_knowledge_cache(db, exc, ai)

    log = _log(
        db,
        action_type="diagnose",
        user_message=user_msg,
        ai_response=ai.text,
        related_exception_id=exception_id,
        model=ai.model,
        usage={
            "input_tokens": ai.input_tokens,
            "output_tokens": ai.output_tokens,
            "cache_read_input_tokens": ai.cache_read_tokens,
            "cache_creation_input_tokens": ai.cache_creation_tokens,
        },
    )
    return log, ai


def chat(
    db: Session,
    *,
    user_message: str,
    session_id: Optional[str] = None,
    extra_system: str = "",
) -> tuple[AiChatLog, Optional[AiResponse]]:
    """通用对话：用户 → AI 一问一答。"""
    try:
        ai = _call_ai(
            db, kind="diagnose", user_message=user_message,
            extra_system=extra_system, max_tokens=1500,
        )
    except AiUnavailable as e:
        log = _log(db, action_type="chat", user_message=user_message, session_id=session_id, error=str(e))
        return log, None
    except Exception as e:  # pragma: no cover
        log = _log(
            db, action_type="chat", user_message=user_message, session_id=session_id,
            error=f"{type(e).__name__}: {e}",
        )
        return log, None

    log = _log(
        db, action_type="chat",
        user_message=user_message, ai_response=ai.text,
        session_id=session_id, model=ai.model,
        usage={
            "input_tokens": ai.input_tokens,
            "output_tokens": ai.output_tokens,
            "cache_read_input_tokens": ai.cache_read_tokens,
            "cache_creation_input_tokens": ai.cache_creation_tokens,
        },
    )
    return log, ai


def is_configured(db: Optional[Session] = None) -> bool:
    """env 有 key, 或 db 里写了任一 AI key (diagnose / ocr) 都算配置完成.

    两个槽位互相 fallback (见 settings_service.get_ai_config), 所以填了任一个
    即视为已配置, AI 助手 + 截图 OCR 都能用.
    """
    if settings.anthropic_api_key:
        return True
    if db is None:
        return False
    return bool(
        settings_service.get(db, "ai_diagnose_api_key", env_fallback=False)
        or settings_service.get(db, "ai_ocr_api_key", env_fallback=False)
    )
