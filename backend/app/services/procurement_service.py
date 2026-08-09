"""智能采购询价业务逻辑。

本服务负责计划、话术实验、追问节奏和反馈归档，不直接控制任何平台。
桌面/浏览器执行器必须在确认实际发送成功后调用 ``mark_message_sent`` 回写。
"""
from __future__ import annotations

import json
import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement import (
    ProcurementAgentState,
    ProcurementInquiry,
    ProcurementMessage,
    ProcurementTask,
)
from app.services import ai_provider, settings_service


CHANNELS = ("taobao", "1688", "pinduoduo", "xiaohongshu")
CATEGORIES = ("daily", "photo", "production")
MANUAL_REPLY_PATTERNS = {
    "商家要求加微信": re.compile(r"(加.{0,4}微信|微信.{0,5}(?:号|联系)|微\s*信号|(?:vx|v信)\s*[:：]?)", re.I),
    "商家要求电话沟通": re.compile(r"(电话.{0,6}联系|打(?:个)?电话|手机号.{0,6}联系|加.{0,4}手机号)"),
    "商家要求人工验证": re.compile(r"(验证码|滑块验证|人机验证|账号异常|安全验证)"),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_message(value: Optional[str]) -> str:
    """仅折叠无意义空白，用于判断人工稿是否真的发生变化。"""
    return re.sub(r"\s+", " ", (value or "").strip())


def next_task_no(db: Session) -> str:
    """生成 PRQ{YYYYMMDD}{NN} 询价任务号。"""
    prefix = f"PRQ{date.today():%Y%m%d}"
    last = db.execute(
        select(ProcurementTask.task_no)
        .where(ProcurementTask.task_no.like(f"{prefix}%"))
        .order_by(ProcurementTask.task_no.desc())
        .limit(1)
    ).scalar_one_or_none()
    seq = 1
    if last:
        try:
            seq = int(last[len(prefix):]) + 1
        except (TypeError, ValueError):
            seq = 1
    return f"{prefix}{seq:02d}"


def _clean_channels(channels: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for channel in channels:
        if channel not in CHANNELS:
            raise ValueError(f"不支持的采购渠道: {channel}")
        if channel not in cleaned:
            cleaned.append(channel)
    if not cleaned:
        raise ValueError("至少选择一个采购渠道")
    return cleaned


def build_search_queries(
    item_name: str,
    specification: Optional[str] = None,
    requirements: Optional[str] = None,
) -> list[str]:
    """生成可编辑的多轮搜索词；不在这里访问任何外部平台。"""
    name = re.sub(r"\s+", " ", (item_name or "").strip())
    spec = re.sub(r"\s+", " ", (specification or "").strip())
    requirement = re.sub(r"\s+", " ", (requirements or "").strip())
    candidates = [
        name,
        f"{name} {spec}" if spec else "",
        f"{name} 厂家 批发",
        f"{name} 定制 交期",
        f"{name} 源头工厂",
        f"{name} {spec} 含运" if spec else f"{name} 含运",
    ]
    if requirement:
        candidates.append(f"{name} {requirement[:80]}")
    result: list[str] = []
    for value in candidates:
        clean = re.sub(r"\s+", " ", value).strip()[:255]
        if clean and clean not in result:
            result.append(clean)
    return result


def clean_search_queries(values: Iterable[str], *, fallback: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()[:255]
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= 20:
            break
    return result or fallback


def create_task(db: Session, payload: dict[str, Any], *, created_by: str) -> ProcurementTask:
    channels = _clean_channels(payload.get("channels") or ["taobao"])
    planned = int(payload.get("planned_merchant_count") or 10)
    sample = int(payload.get("ab_test_sample_size") or 0)
    if not 1 <= planned <= 50:
        raise ValueError("计划询问商家数必须在 1 到 50 之间")
    if not 0 <= int(payload.get("max_followup_rounds") or 0) <= 5:
        raise ValueError("自动追问轮数必须在 0 到 5 之间")
    if payload.get("ab_test_enabled", True):
        if sample < 2 or sample > planned:
            raise ValueError("A/B 测试商家数必须在 2 到计划询问数之间")
    else:
        sample = 0

    default_limits = {"taobao": 10, "1688": 5, "pinduoduo": 5, "xiaohongshu": 3}
    default_intervals = {
        "taobao": 12, "1688": 12, "pinduoduo": 12, "xiaohongshu": 24,
    }
    limits = {**default_limits, **(payload.get("channel_daily_limits") or {})}
    intervals = {**default_intervals, **(payload.get("followup_intervals_hours") or {})}
    for channel in CHANNELS:
        limits[channel] = max(1, min(int(limits[channel]), 30))
        intervals[channel] = max(1, min(int(intervals[channel]), 168))

    default_queries = build_search_queries(
        str(payload["item_name"]),
        payload.get("specification"),
        payload.get("requirements"),
    )
    task = ProcurementTask(
        task_no=next_task_no(db),
        title=str(payload["title"]).strip(),
        category=payload.get("category") or "daily",
        item_name=str(payload["item_name"]).strip(),
        specification=(payload.get("specification") or "").strip() or None,
        quantity=Decimal(str(payload.get("quantity") or 1)),
        unit=(payload.get("unit") or "件").strip(),
        target_unit_price=(
            Decimal(str(payload["target_unit_price"]))
            if payload.get("target_unit_price") is not None
            else None
        ),
        requirements=(payload.get("requirements") or "").strip() or None,
        search_queries=clean_search_queries(
            payload.get("search_queries") or [], fallback=default_queries
        ),
        execution_mode=payload.get("execution_mode") or "assisted",
        taobao_client_mode=payload.get("taobao_client_mode") or "desktop",
        channels=channels,
        channel_daily_limits=limits,
        followup_intervals_hours=intervals,
        planned_merchant_count=planned,
        max_followup_rounds=int(payload.get("max_followup_rounds") or 0),
        ab_test_enabled=bool(payload.get("ab_test_enabled", True)),
        ab_test_sample_size=sample,
        script_a=(payload.get("script_a") or "").strip() or None,
        script_b=(payload.get("script_b") or "").strip() or None,
        status="draft",
        created_by=created_by,
    )
    if task.category not in CATEGORIES:
        raise ValueError(f"不支持的采购类型: {task.category}")
    if task.execution_mode not in {"assisted", "agent"}:
        raise ValueError("工作模式必须是人工辅助或代理队列")
    if task.taobao_client_mode not in {"desktop", "chrome"}:
        raise ValueError("淘宝执行端必须是桌面版或 Chrome 采购账号")
    db.add(task)
    db.flush()
    return task


def fallback_scripts(task: ProcurementTask) -> dict[str, str]:
    """AI 不可用时仍给出能直接编辑/使用的两套基础话术。"""
    spec = f"，规格是{task.specification}" if task.specification else ""
    target = (
        f"，我们的目标含运单价是每{task.unit}{task.target_unit_price}元左右"
        if task.target_unit_price is not None
        else ""
    )
    requirement = f" 另外请留意：{task.requirements}" if task.requirements else ""
    script_a = (
        f"您好，想采购{task.item_name}{spec}，本次预计需要{task.quantity:g}{task.unit}{target}。"
        "麻烦分别报一下含运价、起订量、交期，以及是否可以先寄样；如果有阶梯价也请一起发我。"
        f"{requirement}"
    )
    script_b = (
        f"您好，我们正在筛选{task.item_name}的长期合作供应商{spec}。"
        f"这次先采购{task.quantity:g}{task.unit}做质量和交期确认。"
        "请问您这款的材质/工艺、常规成交价、批量优惠、发货时效和售后标准分别是什么？"
        f"{target}{requirement}"
    )
    return {"script_a": script_a, "script_b": script_b}


def _extract_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", clean)
        if not match:
            raise ValueError("AI 返回中没有 JSON 对象")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI 返回格式不是对象")
    return data


def generate_scripts(db: Session, task: ProcurementTask) -> dict[str, Any]:
    """AI 生成 A/B 话术；调用失败时明确回落到本地模板。"""
    fallback = fallback_scripts(task)
    cfg = settings_service.get_ai_config(db, "diagnose")
    if not cfg.get("api_key"):
        task.script_a = fallback["script_a"]
        task.script_b = fallback["script_b"]
        task.script_a_ai_draft = task.script_a
        task.script_b_ai_draft = task.script_b
        task.scripts_reviewed_at = None
        task.scripts_reviewed_by = None
        task.ai_suggestion_note = "AI 未配置，已使用本地基础模板，可手动修改"
        return {
            "script_a": task.script_a,
            "script_b": task.script_b,
            "ai_used": False,
            "model": None,
            "note": task.ai_suggestion_note,
        }

    system = (
        "你是家具企业采购询价助手。只输出 JSON 对象，字段为 script_a、script_b、note。"
        "两套首轮话术必须有可测量差异：A 偏直接报价，B 偏长期合作与需求澄清；"
        "每套不超过180个汉字，语气自然，不虚构采购量、合作历史、认证或价格。"
        "要询问含税/含运口径、起订量、交期、样品、材质工艺和阶梯价。"
        "不要索要私人联系方式，不要承诺付款或下单。"
    )
    user = json.dumps(
        {
            "采购类型": task.category,
            "品名": task.item_name,
            "规格": task.specification,
            "数量": str(task.quantity),
            "单位": task.unit,
            "目标单价": str(task.target_unit_price) if task.target_unit_price is not None else None,
            "补充要求": task.requirements,
            "渠道": task.channels,
        },
        ensure_ascii=False,
    )
    try:
        response = ai_provider.build_provider(cfg).chat(
            system=system, user=user, max_tokens=700
        )
        parsed = _extract_json(response.text)
        script_a = str(parsed.get("script_a") or "").strip()
        script_b = str(parsed.get("script_b") or "").strip()
        if not script_a or not script_b:
            raise ValueError("AI 未返回完整的 A/B 话术")
        task.script_a = script_a
        task.script_b = script_b
        task.script_a_ai_draft = script_a
        task.script_b_ai_draft = script_b
        task.scripts_reviewed_at = None
        task.scripts_reviewed_by = None
        task.ai_model = response.model
        task.ai_suggestion_note = str(parsed.get("note") or "AI 已生成两组可测试话术")
        return {
            "script_a": script_a,
            "script_b": script_b,
            "ai_used": True,
            "model": response.model,
            "note": task.ai_suggestion_note,
        }
    except (ai_provider.AiUnavailable, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        task.script_a = fallback["script_a"]
        task.script_b = fallback["script_b"]
        task.script_a_ai_draft = task.script_a
        task.script_b_ai_draft = task.script_b
        task.scripts_reviewed_at = None
        task.scripts_reviewed_by = None
        task.ai_suggestion_note = f"AI 暂不可用，已回落基础模板：{str(exc)[:160]}"
        return {
            "script_a": task.script_a,
            "script_b": task.script_b,
            "ai_used": False,
            "model": None,
            "note": task.ai_suggestion_note,
        }


def review_scripts(
    db: Session,
    task: ProcurementTask,
    *,
    script_a: str,
    script_b: Optional[str],
    reviewed_by: str,
) -> ProcurementTask:
    """保存采购人员明确确认的首轮话术，并解锁商家队列。

    人工确认不等于必须改字。若 AI 原稿已经准确，采购人员可以原样确认；
    真正发送前仍需在商家询价记录上进行逐条最终确认。
    """
    actual_a = (script_a or "").strip()
    actual_b = (script_b or "").strip()
    if not actual_a:
        raise ValueError("A 组话术不能为空")
    if task.ab_test_enabled and not actual_b:
        raise ValueError("启用 A/B 测试时，B 组话术不能为空")
    task.script_a = actual_a
    task.script_b = actual_b or None
    task.scripts_reviewed_at = utcnow()
    task.scripts_reviewed_by = reviewed_by
    pending = db.execute(
        select(ProcurementInquiry).where(
            ProcurementInquiry.task_id == task.id,
            ProcurementInquiry.first_sent_at.is_(None),
        )
    ).scalars().all()
    for inquiry in pending:
        inquiry.approved_message = None
        inquiry.approved_message_base = None
        inquiry.approved_action_key = None
        inquiry.message_reviewed_at = None
        inquiry.message_reviewed_by = None
    db.flush()
    return task


def prepare_inquiries(
    db: Session,
    task: ProcurementTask,
    merchant_seeds: Optional[list[dict[str, Any]]] = None,
) -> list[ProcurementInquiry]:
    """创建询价队列；已存在时幂等返回，不删除既有进度。"""
    existing = db.execute(
        select(ProcurementInquiry)
        .where(ProcurementInquiry.task_id == task.id)
        .order_by(ProcurementInquiry.slot_no)
    ).scalars().all()
    if existing:
        return list(existing)
    if task.scripts_reviewed_at is None:
        raise ValueError("请先人工确认 A/B 话术，再生成商家询价队列")
    if not task.script_a or (task.ab_test_enabled and not task.script_b):
        raise ValueError("已确认话术不完整，请重新检查 A/B 文案")

    seeds = merchant_seeds or []
    channels = _clean_channels(task.channels or ["taobao"])
    rows: list[ProcurementInquiry] = []
    seen_candidate_keys: set[str] = set()
    for index in range(task.planned_merchant_count):
        seed = seeds[index] if index < len(seeds) else {}
        channel = seed.get("channel") or channels[index % len(channels)]
        if channel not in channels:
            raise ValueError(f"商家 {index + 1} 的渠道不在本任务渠道中")
        if task.ab_test_enabled and index < task.ab_test_sample_size:
            variant = "A" if index % 2 == 0 else "B"
            status = "ready"
        elif task.ab_test_enabled:
            variant = "winner_pending"
            status = "waiting_winner"
        else:
            variant = "A"
            status = "ready"
        has_candidate = any(
            (
                seed.get("merchant_name"),
                seed.get("merchant_url"),
                seed.get("product_url"),
                seed.get("merchant_external_id"),
            )
        )
        dedupe_key = (
            candidate_dedupe_key(
                channel=channel,
                merchant_external_id=seed.get("merchant_external_id"),
                merchant_name=seed.get("merchant_name"),
                merchant_url=seed.get("merchant_url"),
                product_url=seed.get("product_url"),
            )
            if has_candidate
            else None
        )
        if dedupe_key and dedupe_key in seen_candidate_keys:
            raise ValueError(f"商家 {index + 1} 与本任务前面的候选重复")
        if dedupe_key:
            seen_candidate_keys.add(dedupe_key)
        row = ProcurementInquiry(
            task_id=task.id,
            slot_no=index + 1,
            channel=channel,
            merchant_name=(seed.get("merchant_name") or "").strip() or None,
            merchant_url=(seed.get("merchant_url") or "").strip() or None,
            product_url=(seed.get("product_url") or "").strip() or None,
            merchant_external_id=(
                (seed.get("merchant_external_id") or "").strip() or None
            ),
            message_variant=variant,
            status=(
                "discovery_ready"
                if task.execution_mode == "agent"
                and not has_candidate
                else status
            ),
            quote_payload={},
            candidate_snapshot={},
            candidate_dedupe_key=dedupe_key,
            discovered_at=utcnow() if has_candidate else None,
        )
        db.add(row)
        rows.append(row)
    task.status = "ready"
    db.flush()
    return rows


def experiment_metrics(db: Session, task: ProcurementTask) -> dict[str, Any]:
    rows = db.execute(
        select(ProcurementInquiry).where(
            ProcurementInquiry.task_id == task.id,
            ProcurementInquiry.message_variant.in_(("A", "B")),
        )
    ).scalars().all()
    result: dict[str, Any] = {}
    for variant in ("A", "B"):
        group = [row for row in rows if row.message_variant == variant]
        sent = [row for row in group if row.first_sent_at is not None]
        replied = [row for row in sent if row.first_response_at is not None]
        quotes = [row for row in replied if row.quote_complete]
        reply_rate = len(replied) / len(sent) if sent else 0.0
        quote_rate = len(quotes) / len(sent) if sent else 0.0
        score = reply_rate * 0.6 + quote_rate * 0.4
        result[variant] = {
            "assigned": len(group),
            "sent": len(sent),
            "replied": len(replied),
            "quote_complete": len(quotes),
            "wechat_handoff": sum(1 for row in replied if row.requires_wechat),
            "reply_rate": round(reply_rate, 4),
            "quote_rate": round(quote_rate, 4),
            "score": round(score, 4),
        }
    winner = None
    reason = "两组都至少发送 1 家后再判断"
    if result["A"]["sent"] and result["B"]["sent"]:
        if result["A"]["score"] > result["B"]["score"]:
            winner, reason = "A", "A 组回复率与完整报价综合得分更高"
        elif result["B"]["score"] > result["A"]["score"]:
            winner, reason = "B", "B 组回复率与完整报价综合得分更高"
        else:
            reason = "当前两组综合得分相同，建议继续收集样本"
    return {"A": result["A"], "B": result["B"], "winner": winner, "reason": reason}


def apply_winner(db: Session, task: ProcurementTask, variant: Optional[str] = None) -> dict[str, Any]:
    metrics = experiment_metrics(db, task)
    selected = variant or metrics["winner"]
    if selected not in ("A", "B"):
        raise ValueError("当前没有明确优胜话术，请继续测试或手动选择 A/B")
    rows = db.execute(
        select(ProcurementInquiry).where(
            ProcurementInquiry.task_id == task.id,
            ProcurementInquiry.message_variant == "winner_pending",
        )
    ).scalars().all()
    for row in rows:
        row.message_variant = selected
        row.status = "ready"
    task.winning_variant = selected
    task.status = "ready"
    db.flush()
    return {"winner": selected, "activated": len(rows), "metrics": metrics}


def initial_message(task: ProcurementTask, inquiry: ProcurementInquiry) -> str:
    if inquiry.message_variant == "A":
        base = task.script_a or fallback_scripts(task)["script_a"]
        return _merchant_specific_message(base, inquiry)
    if inquiry.message_variant == "B":
        base = task.script_b or fallback_scripts(task)["script_b"]
        return _merchant_specific_message(base, inquiry)
    raise ValueError("该商家仍在等待 A/B 优胜话术，暂不可发送")


def _merchant_specific_message(base: str, inquiry: ProcurementInquiry) -> str:
    """加入可核对的商家称呼，不做规避平台识别的随机扰动。"""
    merchant = re.sub(r"\s+", " ", (inquiry.merchant_name or "").strip())
    if not merchant:
        return base.strip()
    if base.strip().startswith("您好"):
        return f"{merchant}您好，{base.strip()[2:].lstrip('，, ')}"
    return f"{merchant}您好，{base.strip()}"


def followup_message(task: ProcurementTask, inquiry: ProcurementInquiry) -> str:
    """根据缺失信息生成短追问；后续可由 AI 执行器覆盖 content。"""
    last = inquiry.last_inbound_message or ""
    if not last:
        return (
            f"您好，再跟进一下前面咨询的{task.item_name}，方便时请回复一下"
            "价格、起订量和交期，谢谢。"
        )
    if inquiry.normalized_unit_price is None:
        return "收到，谢谢。再确认一下：这个报价是否含税、含运？换算到我们采购单位后的单价是多少？"
    if not inquiry.quote_complete:
        return "价格收到。还麻烦补充一下起订量、交期、样品政策和批量阶梯价，便于我们统一评估。"
    return "信息收到，我们先做内部评估；如进入下一步会再联系您，谢谢。"


def _action_key(inquiry: ProcurementInquiry) -> str:
    if inquiry.first_sent_at is None:
        return "initial:0"
    return f"followup:{inquiry.followup_round + 1}"


def _reviewed_action_content(
    task: ProcurementTask,
    inquiry: ProcurementInquiry,
    *,
    suggested: Optional[str] = None,
) -> tuple[str, bool]:
    """返回当前轮次唯一允许发送的内容，以及是否已通过人工门禁。"""
    key = _action_key(inquiry)
    if (
        inquiry.approved_action_key == key
        and inquiry.message_reviewed_at is not None
        and _normalize_message(inquiry.approved_message)
    ):
        return (inquiry.approved_message or "").strip(), True
    if inquiry.first_sent_at is None:
        return (suggested or initial_message(task, inquiry)).strip(), False
    return (suggested or followup_message(task, inquiry)).strip(), False


def review_inquiry_message(
    db: Session,
    task: ProcurementTask,
    inquiry: ProcurementInquiry,
    *,
    content: str,
    reviewed_by: str,
) -> dict[str, Any]:
    """为某个商家的当前轮次保存一份可审计的人工确认稿。"""
    overdue_waiting_reply = False
    if inquiry.status == "waiting_reply" and inquiry.next_followup_at is not None:
        due_at = inquiry.next_followup_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        overdue_waiting_reply = due_at <= utcnow()
    if inquiry.status not in {"ready", "followup_ready"} and not overdue_waiting_reply:
        raise ValueError(f"当前状态 {inquiry.status} 没有可审核的待发消息")
    if inquiry.first_sent_at is None and task.scripts_reviewed_at is None:
        raise ValueError("请先在任务顶部修改并确认 A/B 首轮话术")
    base = (
        initial_message(task, inquiry)
        if inquiry.first_sent_at is None
        else followup_message(task, inquiry)
    )
    actual = (content or "").strip()
    if not actual:
        raise ValueError("确认文案不能为空")
    inquiry.approved_message = actual
    inquiry.approved_message_base = base
    inquiry.approved_action_key = _action_key(inquiry)
    inquiry.message_reviewed_at = utcnow()
    inquiry.message_reviewed_by = reviewed_by
    if overdue_waiting_reply:
        inquiry.status = "followup_ready"
    db.flush()
    return {
        "inquiry_id": inquiry.id,
        "action_key": inquiry.approved_action_key,
        "approved_message": actual,
        "reviewed_at": inquiry.message_reviewed_at,
        "reviewed_by": inquiry.message_reviewed_by,
    }


def _interval_hours(task: ProcurementTask, channel: str) -> int:
    intervals = task.followup_intervals_hours or {}
    return max(1, int(intervals.get(channel) or (24 if channel == "xiaohongshu" else 12)))


def mark_message_sent(
    db: Session,
    task: ProcurementTask,
    inquiry: ProcurementInquiry,
    *,
    content: Optional[str] = None,
    is_ai_generated: bool = False,
    sent_at: Optional[datetime] = None,
    external_message_id: Optional[str] = None,
    message_meta: Optional[dict[str, Any]] = None,
) -> ProcurementMessage:
    """仅供执行器在确认平台发送成功后回写。"""
    if inquiry.status in {"waiting_winner", "needs_manual", "completed"}:
        raise ValueError(f"当前状态 {inquiry.status} 不允许自动发送")
    suggested = (
        initial_message(task, inquiry)
        if inquiry.first_sent_at is None
        else followup_message(task, inquiry)
    )
    approved, reviewed = _reviewed_action_content(
        task, inquiry, suggested=suggested
    )
    if not reviewed:
        raise ValueError("本轮消息尚未经过人工确认，不能标记发送或交给代理")
    if content is not None and (
        _normalize_message(content) != _normalize_message(approved)
    ):
        raise ValueError("发送内容与 ERP 中最后确认的文案不一致")
    now = sent_at or utcnow()
    first = inquiry.first_sent_at is None
    if first:
        actual = approved
        round_no = 0
        inquiry.first_sent_at = now
    else:
        if inquiry.followup_round >= task.max_followup_rounds:
            raise ValueError("已达到本任务最大追问轮数")
        actual = approved
        inquiry.followup_round += 1
        round_no = inquiry.followup_round
    if not actual:
        raise ValueError("发送内容不能为空")

    inquiry.last_outbound_message = actual
    inquiry.last_message_at = now
    inquiry.status = "waiting_reply"
    if inquiry.followup_round < task.max_followup_rounds:
        inquiry.next_followup_at = now + timedelta(
            hours=_interval_hours(task, inquiry.channel)
        )
    else:
        inquiry.next_followup_at = None
    task.status = "running"
    message = ProcurementMessage(
        inquiry_id=inquiry.id,
        direction="outbound",
        round_no=round_no,
        content=actual,
        is_ai_generated=is_ai_generated,
        event_at=now,
        external_message_id=external_message_id,
        message_meta={
            "channel": inquiry.channel,
            "confirmed_sent": True,
            "human_reviewed": True,
            "reviewed_by": inquiry.message_reviewed_by or task.scripts_reviewed_by,
            "reviewed_at": (
                inquiry.message_reviewed_at.isoformat()
                if inquiry.message_reviewed_at
                else task.scripts_reviewed_at.isoformat()
                if task.scripts_reviewed_at
                else None
            ),
            **(message_meta or {}),
        },
    )
    inquiry.approved_message = None
    inquiry.approved_message_base = None
    inquiry.approved_action_key = None
    inquiry.message_reviewed_at = None
    inquiry.message_reviewed_by = None
    db.add(message)
    db.flush()
    return message


def record_reply(
    db: Session,
    task: ProcurementTask,
    inquiry: ProcurementInquiry,
    *,
    content: str,
    received_at: Optional[datetime] = None,
    quote_complete: bool = False,
    quote_amount: Optional[Decimal] = None,
    normalized_unit_price: Optional[Decimal] = None,
    quote_payload: Optional[dict[str, Any]] = None,
    response_quality: Optional[int] = None,
    wechat_contact: Optional[str] = None,
    external_message_id: Optional[str] = None,
    message_meta: Optional[dict[str, Any]] = None,
) -> ProcurementMessage:
    """归档商家回复、识别人工接管点，并决定是否继续追问。"""
    actual = content.strip()
    if not actual:
        raise ValueError("回复内容不能为空")
    now = received_at or utcnow()
    inquiry.last_inbound_message = actual
    inquiry.last_message_at = now
    inquiry.first_response_at = inquiry.first_response_at or now
    inquiry.quote_complete = quote_complete
    inquiry.quote_amount = quote_amount
    inquiry.normalized_unit_price = normalized_unit_price
    inquiry.quote_payload = quote_payload or {}
    inquiry.response_quality = response_quality
    inquiry.wechat_contact = (wechat_contact or "").strip() or None
    inquiry.approved_message = None
    inquiry.approved_message_base = None
    inquiry.approved_action_key = None
    inquiry.message_reviewed_at = None
    inquiry.message_reviewed_by = None

    manual_reason = None
    for reason, pattern in MANUAL_REPLY_PATTERNS.items():
        if pattern.search(actual):
            manual_reason = reason
            break
    if inquiry.wechat_contact:
        manual_reason = "商家要求加微信"
    if (
        manual_reason is None
        and not quote_complete
        and (
            (response_quality is not None and response_quality < 30)
            or bool((quote_payload or {}).get("parse_error"))
        )
    ):
        manual_reason = "商家回复无法可靠理解"
    inquiry.requires_wechat = manual_reason == "商家要求加微信"
    inquiry.manual_reason = manual_reason
    if manual_reason:
        inquiry.status = "needs_manual"
        inquiry.next_followup_at = None
        task.status = "needs_review"
    elif quote_complete:
        inquiry.status = "completed"
        inquiry.next_followup_at = None
    elif inquiry.followup_round < task.max_followup_rounds:
        inquiry.status = "followup_ready"
        inquiry.next_followup_at = now
    else:
        inquiry.status = "replied"
        inquiry.next_followup_at = None

    message = ProcurementMessage(
        inquiry_id=inquiry.id,
        direction="inbound",
        round_no=inquiry.followup_round,
        content=actual,
        event_at=now,
        requires_manual_review=bool(manual_reason),
        external_message_id=external_message_id,
        message_meta={
            "channel": inquiry.channel,
            "manual_reason": manual_reason,
            **(message_meta or {}),
        },
    )
    db.add(message)
    db.flush()
    return message


def due_actions(
    db: Session,
    *,
    task_id: Optional[int] = None,
    now: Optional[datetime] = None,
    limit: int = 100,
    agent_only: bool = False,
) -> list[dict[str, Any]]:
    """给未来外部执行器的只读动作队列；这里不执行发送。"""
    now = now or utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_q = (
        select(
            ProcurementInquiry.task_id,
            ProcurementInquiry.channel,
            func.count(ProcurementMessage.id),
        )
        .join(
            ProcurementInquiry,
            ProcurementInquiry.id == ProcurementMessage.inquiry_id,
        )
        .where(
            ProcurementMessage.direction == "outbound",
            ProcurementMessage.event_at >= day_start,
        )
        .group_by(ProcurementInquiry.task_id, ProcurementInquiry.channel)
    )
    if task_id is not None:
        sent_q = sent_q.where(ProcurementInquiry.task_id == task_id)
    sent_today = {
        (row_task_id, channel): int(count)
        for row_task_id, channel, count in db.execute(sent_q).all()
    }
    q = select(ProcurementInquiry, ProcurementTask).join(
        ProcurementTask, ProcurementTask.id == ProcurementInquiry.task_id
    )
    if task_id is not None:
        q = q.where(ProcurementInquiry.task_id == task_id)
    if agent_only:
        q = q.where(ProcurementTask.execution_mode == "agent")
    q = q.where(
        ProcurementTask.status.in_(("ready", "running")),
        (
            ProcurementInquiry.status.in_(("ready", "followup_ready"))
            | (
                (ProcurementInquiry.status == "waiting_reply")
                & (ProcurementInquiry.next_followup_at.is_not(None))
                & (ProcurementInquiry.next_followup_at <= now)
            )
        ),
        (
            ProcurementInquiry.lease_until.is_(None)
            | (ProcurementInquiry.lease_until < now)
        ),
    ).order_by(
        ProcurementInquiry.next_followup_at.asc().nulls_first(),
        ProcurementInquiry.id,
    ).limit(min(max(limit * 5, 100), 1000))
    actions = []
    remaining: dict[tuple[int, str], int] = {}
    for inquiry, task in db.execute(q).all():
        key = (task.id, inquiry.channel)
        if key not in remaining:
            daily_limit = int((task.channel_daily_limits or {}).get(inquiry.channel, 1))
            remaining[key] = max(0, daily_limit - sent_today.get(key, 0))
        if remaining[key] <= 0:
            continue
        if inquiry.first_sent_at is None:
            kind = "initial_message"
            suggested = initial_message(task, inquiry)
        else:
            kind = (
                "check_reply_then_follow_up"
                if inquiry.channel == "xiaohongshu"
                else "follow_up"
            )
            suggested = followup_message(task, inquiry)
        approved_message, reviewed = _reviewed_action_content(
            task, inquiry, suggested=suggested
        )
        actions.append(
            {
                "task_id": task.id,
                "inquiry_id": inquiry.id,
                "channel": inquiry.channel,
                "action": kind,
                "suggested_message": suggested,
                "approved_message": approved_message if reviewed else None,
                "review_required": not reviewed,
                "action_key": _action_key(inquiry),
                "message_reviewed_at": (
                    inquiry.message_reviewed_at.isoformat()
                    if inquiry.message_reviewed_at
                    else None
                ),
                "followup_round": inquiry.followup_round,
                "max_followup_rounds": task.max_followup_rounds,
                "daily_limit": int((task.channel_daily_limits or {}).get(inquiry.channel, 1)),
                "requires_confirmed_send_callback": True,
            }
        )
        remaining[key] -= 1
        if len(actions) >= limit:
            break
    return actions


def _required_capability(task: ProcurementTask, inquiry: ProcurementInquiry) -> str:
    if inquiry.channel == "taobao":
        return f"taobao_{task.taobao_client_mode}"
    return f"{inquiry.channel}_chrome"


def _clear_lease(inquiry: ProcurementInquiry) -> None:
    inquiry.lease_token = None
    inquiry.leased_by = None
    inquiry.lease_until = None


def _canonical_candidate_value(value: Optional[str]) -> str:
    clean = (value or "").strip()
    if not clean:
        return ""
    try:
        parts = urlsplit(clean)
        if parts.scheme and parts.netloc:
            identity_keys = {
                "id", "item_id", "itemid", "goods_id", "goodsid", "mall_id",
                "mallid", "offerid", "shop_id", "shopid", "seller_id", "sellerid",
            }
            identity_query = [
                (key.casefold(), item)
                for key, item in parse_qsl(parts.query, keep_blank_values=False)
                if key.casefold() in identity_keys
            ]
            return urlunsplit(
                (
                    parts.scheme.lower(),
                    parts.netloc.lower(),
                    parts.path.rstrip("/"),
                    urlencode(sorted(identity_query)),
                    "",
                )
            )
    except ValueError:
        pass
    return re.sub(r"\s+", " ", clean).casefold()


def candidate_dedupe_key(
    *,
    channel: str,
    merchant_external_id: Optional[str] = None,
    merchant_name: Optional[str] = None,
    merchant_url: Optional[str] = None,
    product_url: Optional[str] = None,
) -> str:
    """同一任务内优先按店铺身份去重，防止重复联系同一商家。"""
    identity = (
        _canonical_candidate_value(merchant_external_id)
        or _canonical_candidate_value(merchant_url)
        or _canonical_candidate_value(merchant_name)
        or _canonical_candidate_value(product_url)
    )
    if not identity:
        raise ValueError("候选商家至少需要商家名称、店铺链接、商品链接或平台商家 ID")
    return hashlib.sha256(f"{channel}:{identity}".encode("utf-8")).hexdigest()


def _safe_candidate_snapshot(value: Optional[dict[str, Any]]) -> dict[str, Any]:
    """只接收展示字段，避免 Cookie、令牌或页面存储被误传进 ERP。"""
    allowed = {
        "title",
        "price_text",
        "sales_text",
        "location",
        "shop_score",
        "seller_level",
        "product_image_url",
        "captured_at",
    }
    source = value or {}
    result: dict[str, Any] = {}
    for key in allowed:
        item = source.get(key)
        if item is None:
            continue
        if isinstance(item, (int, float, bool)):
            result[key] = item
        else:
            result[key] = str(item)[:2048]
    return result


def claim_discovery_actions(
    db: Session,
    *,
    agent_id: str,
    mode: str,
    capabilities: list[str],
    max_actions: int = 1,
    lease_seconds: int = 180,
) -> list[dict[str, Any]]:
    """领取候选商家搜索槽位；dry_run 仅展示搜索词，不创建租约。"""
    if mode not in {"dry_run", "review", "live"}:
        raise ValueError("执行器模式无效")
    max_actions = max(1, min(int(max_actions), 10))
    lease_seconds = max(60, min(int(lease_seconds), 900))
    now = utcnow()
    rows = db.execute(
        select(ProcurementInquiry, ProcurementTask)
        .join(ProcurementTask, ProcurementTask.id == ProcurementInquiry.task_id)
        .where(
            ProcurementTask.execution_mode == "agent",
            ProcurementTask.status.in_(("ready", "running")),
            ProcurementInquiry.status == "discovery_ready",
            (
                ProcurementInquiry.lease_until.is_(None)
                | (ProcurementInquiry.lease_until < now)
            ),
        )
        .order_by(ProcurementInquiry.id)
        .limit(max_actions * 10)
    ).all()
    actions: list[dict[str, Any]] = []
    for inquiry, task in rows:
        capability = _required_capability(task, inquiry)
        if capability not in capabilities:
            continue
        queries = task.search_queries or build_search_queries(
            task.item_name, task.specification, task.requirements
        )
        query_index = (inquiry.slot_no - 1 + inquiry.discovery_attempts) % len(queries)
        excluded = db.execute(
            select(
                ProcurementInquiry.merchant_name,
                ProcurementInquiry.merchant_url,
                ProcurementInquiry.product_url,
            ).where(
                ProcurementInquiry.task_id == task.id,
                ProcurementInquiry.candidate_dedupe_key.is_not(None),
            )
        ).all()
        payload = {
            "task_id": task.id,
            "task_no": task.task_no,
            "inquiry_id": inquiry.id,
            "slot_no": inquiry.slot_no,
            "channel": inquiry.channel,
            "search_query": queries[query_index],
            "search_queries": queries,
            "item_name": task.item_name,
            "specification": task.specification,
            "quantity": str(task.quantity),
            "unit": task.unit,
            "requirements": task.requirements,
            "required_capability": capability,
            "excluded_candidates": [
                {
                    "merchant_name": name,
                    "merchant_url": merchant_url,
                    "product_url": product_url,
                }
                for name, merchant_url, product_url in excluded
            ],
        }
        if mode == "dry_run":
            payload.update({"preview": True, "lease_token": None, "lease_until": None})
        else:
            inquiry = db.execute(
                select(ProcurementInquiry)
                .where(ProcurementInquiry.id == inquiry.id)
                .with_for_update()
            ).scalar_one()
            if inquiry.status != "discovery_ready":
                continue
            if inquiry.lease_until is not None:
                current_lease_until = inquiry.lease_until
                if current_lease_until.tzinfo is None:
                    current_lease_until = current_lease_until.replace(tzinfo=timezone.utc)
                if current_lease_until >= now:
                    continue
            token = secrets.token_urlsafe(24)
            inquiry.lease_token = token
            inquiry.leased_by = agent_id
            inquiry.lease_until = now + timedelta(seconds=lease_seconds)
            inquiry.discovery_attempts += 1
            inquiry.last_executor_mode = mode
            payload.update(
                {
                    "preview": False,
                    "lease_token": token,
                    "lease_until": inquiry.lease_until.isoformat(),
                }
            )
        actions.append(payload)
        if len(actions) >= max_actions:
            break
    db.flush()
    return actions


def record_discovery_candidate(
    db: Session,
    *,
    inquiry: ProcurementInquiry,
    agent_id: str,
    lease_token: str,
    merchant_name: Optional[str] = None,
    merchant_url: Optional[str] = None,
    product_url: Optional[str] = None,
    merchant_external_id: Optional[str] = None,
    discovery_query: Optional[str] = None,
    candidate_score: Optional[Decimal] = None,
    candidate_reason: Optional[str] = None,
    candidate_snapshot: Optional[dict[str, Any]] = None,
    source_rank: Optional[int] = None,
) -> tuple[ProcurementInquiry, bool]:
    """幂等登记搜索结果；重复店铺释放槽位后改搜下一候选。"""
    _validate_lease(inquiry, agent_id=agent_id, lease_token=lease_token)
    if inquiry.status != "discovery_ready":
        raise ValueError(f"当前状态 {inquiry.status} 不接受候选商家")
    key = candidate_dedupe_key(
        channel=inquiry.channel,
        merchant_external_id=merchant_external_id,
        merchant_name=merchant_name,
        merchant_url=merchant_url,
        product_url=product_url,
    )
    duplicate = db.execute(
        select(ProcurementInquiry).where(
            ProcurementInquiry.task_id == inquiry.task_id,
            ProcurementInquiry.candidate_dedupe_key == key,
            ProcurementInquiry.id != inquiry.id,
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        inquiry.last_discovery_error = f"候选重复，已存在于槽位 {duplicate.slot_no}"
        _clear_lease(inquiry)
        if inquiry.discovery_attempts >= 3:
            inquiry.status = "needs_manual"
            inquiry.manual_reason = "连续搜索到重复候选，请人工补充搜索词"
        db.flush()
        return duplicate, True

    score = Decimal(str(candidate_score)) if candidate_score is not None else None
    if score is not None and not Decimal("0") <= score <= Decimal("100"):
        raise ValueError("候选评分必须在 0 到 100 之间")
    inquiry.merchant_name = (merchant_name or "").strip()[:128] or None
    inquiry.merchant_url = (merchant_url or "").strip()[:1024] or None
    inquiry.product_url = (product_url or "").strip()[:1024] or None
    inquiry.merchant_external_id = (
        (merchant_external_id or "").strip()[:255] or None
    )
    inquiry.discovery_query = (discovery_query or "").strip()[:255] or None
    inquiry.discovered_at = utcnow()
    inquiry.candidate_score = score
    inquiry.candidate_reason = (candidate_reason or "").strip()[:2000] or None
    inquiry.candidate_snapshot = _safe_candidate_snapshot(candidate_snapshot)
    inquiry.candidate_dedupe_key = key
    inquiry.source_rank = max(1, int(source_rank)) if source_rank is not None else None
    inquiry.last_discovery_error = None
    inquiry.status = (
        "waiting_winner" if inquiry.message_variant == "winner_pending" else "ready"
    )
    _clear_lease(inquiry)
    db.flush()
    return inquiry, False


def discovery_failure(
    db: Session,
    *,
    inquiry: ProcurementInquiry,
    agent_id: str,
    lease_token: str,
    error: str,
    retryable: bool,
) -> ProcurementInquiry:
    _validate_lease(inquiry, agent_id=agent_id, lease_token=lease_token)
    detail = (error or "").strip()[:1000] or "执行器未提供搜索失败原因"
    inquiry.last_discovery_error = detail
    _clear_lease(inquiry)
    if retryable and inquiry.discovery_attempts < 3:
        inquiry.status = "discovery_ready"
    else:
        inquiry.status = "needs_manual"
        inquiry.manual_reason = f"候选搜索失败：{detail[:200]}"
        task = db.get(ProcurementTask, inquiry.task_id)
        if task is not None:
            task.status = "needs_review"
    db.add(
        ProcurementMessage(
            inquiry_id=inquiry.id,
            direction="system",
            round_no=0,
            content=f"候选搜索失败：{detail}",
            requires_manual_review=inquiry.status == "needs_manual",
            event_at=utcnow(),
            message_meta={
                "agent_id": agent_id,
                "operation": "discover",
                "retryable": retryable,
                "attempt": inquiry.discovery_attempts,
            },
        )
    )
    db.flush()
    return inquiry


def heartbeat_agent(
    db: Session,
    *,
    agent_id: str,
    mode: str,
    capabilities: list[str],
    display_name: Optional[str] = None,
    host_label: Optional[str] = None,
    version: Optional[str] = None,
    status: str = "online",
    current_inquiry_id: Optional[int] = None,
    last_error: Optional[str] = None,
    counters: Optional[dict[str, Any]] = None,
) -> ProcurementAgentState:
    """登记 sidecar 心跳；只保存运行信息，不接收账号凭据。"""
    if mode not in {"dry_run", "review", "live"}:
        raise ValueError("执行器模式必须是 dry_run、review 或 live")
    if status not in {"online", "busy", "paused", "error"}:
        raise ValueError("不支持的执行器状态")
    row = db.execute(
        select(ProcurementAgentState).where(
            ProcurementAgentState.agent_id == agent_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProcurementAgentState(
            agent_id=agent_id,
            last_seen_at=utcnow(),
            capabilities=[],
            counters={},
        )
        db.add(row)
    row.display_name = display_name
    row.host_label = host_label
    row.version = version
    row.mode = mode
    row.status = status
    row.capabilities = sorted(set(capabilities))
    row.current_inquiry_id = current_inquiry_id
    row.last_seen_at = utcnow()
    row.last_error = last_error
    row.counters = counters or {}
    db.flush()
    return row


def _action_payload(
    task: ProcurementTask,
    inquiry: ProcurementInquiry,
    action: dict[str, Any],
) -> dict[str, Any]:
    return {
        **action,
        "task_no": task.task_no,
        "task_title": task.title,
        "item_name": task.item_name,
        "specification": task.specification,
        "quantity": str(task.quantity),
        "unit": task.unit,
        "target_unit_price": (
            str(task.target_unit_price)
            if task.target_unit_price is not None
            else None
        ),
        "requirements": task.requirements,
        "merchant_name": inquiry.merchant_name,
        "merchant_url": inquiry.merchant_url,
        "product_url": inquiry.product_url,
        "external_thread_id": inquiry.external_thread_id,
        "required_capability": _required_capability(task, inquiry),
    }


def claim_agent_actions(
    db: Session,
    *,
    agent_id: str,
    mode: str,
    capabilities: list[str],
    max_actions: int = 1,
    lease_seconds: int = 180,
) -> list[dict[str, Any]]:
    """领取待办。

    dry_run 只预览、不加租约；review/live 才领取。租约让多个 Windows
    执行器不会重复操作同一家商家。
    """
    if mode not in {"dry_run", "review", "live"}:
        raise ValueError("执行器模式无效")
    max_actions = max(1, min(int(max_actions), 10))
    lease_seconds = max(60, min(int(lease_seconds), 900))
    candidates = due_actions(
        db,
        limit=max_actions * 10,
        agent_only=True,
    )
    claimed: list[dict[str, Any]] = []
    now = utcnow()
    for action in candidates:
        if action.get("review_required"):
            continue
        inquiry = db.execute(
            select(ProcurementInquiry)
            .where(ProcurementInquiry.id == action["inquiry_id"])
            .with_for_update()
        ).scalar_one_or_none()
        if inquiry is None:
            continue
        task = db.get(ProcurementTask, inquiry.task_id)
        if task is None:
            continue
        required = _required_capability(task, inquiry)
        if required not in capabilities:
            continue
        # 没有任何商家定位信息时不能让桌面代理盲找，留在 ERP 等人工补齐。
        if not any((inquiry.merchant_name, inquiry.merchant_url, inquiry.product_url)):
            continue
        if inquiry.lease_until is not None:
            lease_until = inquiry.lease_until
            if lease_until.tzinfo is None:
                lease_until = lease_until.replace(tzinfo=timezone.utc)
            if lease_until >= now:
                continue
        approved_action = {
            **action,
            "suggested_message": action.get("approved_message")
            or action["suggested_message"],
        }
        payload = _action_payload(task, inquiry, approved_action)
        if mode == "dry_run":
            payload.update({"preview": True, "lease_token": None, "lease_until": None})
        else:
            token = secrets.token_urlsafe(24)
            inquiry.lease_token = token
            inquiry.leased_by = agent_id
            inquiry.lease_until = now + timedelta(seconds=lease_seconds)
            inquiry.execution_attempts += 1
            inquiry.last_executor_mode = mode
            payload.update(
                {
                    "preview": False,
                    "lease_token": token,
                    "lease_until": inquiry.lease_until.isoformat(),
                }
            )
        claimed.append(payload)
        if len(claimed) >= max_actions:
            break
    db.flush()
    return claimed


def _validate_lease(
    inquiry: ProcurementInquiry,
    *,
    agent_id: str,
    lease_token: str,
) -> None:
    if not lease_token or not secrets.compare_digest(
        inquiry.lease_token or "", lease_token
    ):
        raise ValueError("执行器租约无效")
    if inquiry.leased_by != agent_id:
        raise ValueError("该任务由其他执行器领取")
    if inquiry.lease_until is None:
        raise ValueError("执行器租约已释放")
    lease_until = inquiry.lease_until
    if lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    if lease_until < utcnow():
        _clear_lease(inquiry)
        raise ValueError("执行器租约已过期，请重新领取")


def confirm_agent_sent(
    db: Session,
    *,
    inquiry: ProcurementInquiry,
    agent_id: str,
    lease_token: str,
    content: str,
    external_message_id: str,
    external_thread_id: Optional[str] = None,
    sent_at: Optional[datetime] = None,
) -> tuple[ProcurementMessage, bool]:
    """平台确认发送成功后的幂等回写。"""
    duplicate = db.execute(
        select(ProcurementMessage).where(
            ProcurementMessage.inquiry_id == inquiry.id,
            ProcurementMessage.direction == "outbound",
            ProcurementMessage.external_message_id == external_message_id,
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        return duplicate, True
    _validate_lease(inquiry, agent_id=agent_id, lease_token=lease_token)
    task = db.get(ProcurementTask, inquiry.task_id)
    if task is None:
        raise ValueError("采购任务不存在")
    row = mark_message_sent(
        db,
        task,
        inquiry,
        content=content,
        sent_at=sent_at,
        external_message_id=external_message_id,
        message_meta={"agent_id": agent_id, "source": "procurement_agent"},
    )
    inquiry.external_thread_id = external_thread_id or inquiry.external_thread_id
    inquiry.external_message_id = external_message_id
    inquiry.last_execution_error = None
    inquiry.last_observed_at = sent_at or utcnow()
    _clear_lease(inquiry)
    db.flush()
    return row, False


def record_agent_reply(
    db: Session,
    *,
    inquiry: ProcurementInquiry,
    agent_id: str,
    content: str,
    external_message_id: str,
    received_at: Optional[datetime] = None,
    quote_complete: bool = False,
    quote_amount: Optional[Decimal] = None,
    normalized_unit_price: Optional[Decimal] = None,
    quote_payload: Optional[dict[str, Any]] = None,
    response_quality: Optional[int] = None,
    wechat_contact: Optional[str] = None,
) -> tuple[ProcurementMessage, bool]:
    """执行器观察到新回复后的幂等回写，不需要持有发送租约。"""
    duplicate = db.execute(
        select(ProcurementMessage).where(
            ProcurementMessage.inquiry_id == inquiry.id,
            ProcurementMessage.direction == "inbound",
            ProcurementMessage.external_message_id == external_message_id,
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        return duplicate, True
    task = db.get(ProcurementTask, inquiry.task_id)
    if task is None:
        raise ValueError("采购任务不存在")
    if task.execution_mode != "agent":
        raise ValueError("该询价不属于采购执行器任务")
    if inquiry.first_sent_at is None:
        raise ValueError("该商家尚无已确认发送记录，不能回写平台回复")
    state = db.execute(
        select(ProcurementAgentState).where(
            ProcurementAgentState.agent_id == agent_id
        )
    ).scalar_one_or_none()
    required = _required_capability(task, inquiry)
    if state is None or required not in (state.capabilities or []):
        raise ValueError(f"执行器未登记所需能力 {required}")
    row = record_reply(
        db,
        task,
        inquiry,
        content=content,
        received_at=received_at,
        quote_complete=quote_complete,
        quote_amount=quote_amount,
        normalized_unit_price=normalized_unit_price,
        quote_payload=quote_payload,
        response_quality=response_quality,
        wechat_contact=wechat_contact,
        external_message_id=external_message_id,
        message_meta={"agent_id": agent_id, "source": "procurement_agent"},
    )
    inquiry.last_observed_at = received_at or utcnow()
    db.flush()
    return row, False


def agent_failure(
    db: Session,
    *,
    inquiry: ProcurementInquiry,
    agent_id: str,
    lease_token: str,
    error: str,
    retryable: bool,
) -> ProcurementInquiry:
    """失败最多自动重领三次；验证、账号和定位问题直接转人工。"""
    _validate_lease(inquiry, agent_id=agent_id, lease_token=lease_token)
    detail = error.strip()[:1000] or "执行器未提供错误信息"
    inquiry.last_execution_error = detail
    _clear_lease(inquiry)
    if retryable and inquiry.execution_attempts < 3:
        inquiry.status = "ready" if inquiry.first_sent_at is None else "followup_ready"
        inquiry.manual_reason = None
    else:
        inquiry.status = "needs_manual"
        inquiry.manual_reason = f"执行器失败：{detail[:200]}"
        task = db.get(ProcurementTask, inquiry.task_id)
        if task is not None:
            task.status = "needs_review"
    db.add(
        ProcurementMessage(
            inquiry_id=inquiry.id,
            direction="system",
            round_no=inquiry.followup_round,
            content=f"执行器 {agent_id} 失败：{detail}",
            requires_manual_review=inquiry.status == "needs_manual",
            event_at=utcnow(),
            message_meta={
                "agent_id": agent_id,
                "retryable": retryable,
                "attempt": inquiry.execution_attempts,
            },
        )
    )
    db.flush()
    return inquiry


def agent_manual_handoff(
    db: Session,
    *,
    inquiry: ProcurementInquiry,
    agent_id: str,
    lease_token: str,
    reason: str,
) -> ProcurementInquiry:
    _validate_lease(inquiry, agent_id=agent_id, lease_token=lease_token)
    detail = reason.strip()[:255] or "执行器请求人工接管"
    inquiry.status = "needs_manual"
    inquiry.manual_reason = detail
    inquiry.last_execution_error = detail
    _clear_lease(inquiry)
    task = db.get(ProcurementTask, inquiry.task_id)
    if task is not None:
        task.status = "needs_review"
    db.add(
        ProcurementMessage(
            inquiry_id=inquiry.id,
            direction="system",
            round_no=inquiry.followup_round,
            content=f"执行器转人工：{detail}",
            requires_manual_review=True,
            event_at=utcnow(),
            message_meta={"agent_id": agent_id},
        )
    )
    db.flush()
    return inquiry


def agent_watch_list(
    db: Session,
    *,
    capabilities: list[str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """返回需要持续检查回复的会话，尤其是小红书慢回复。"""
    rows = db.execute(
        select(ProcurementInquiry, ProcurementTask)
        .join(ProcurementTask, ProcurementTask.id == ProcurementInquiry.task_id)
        .where(
            ProcurementTask.execution_mode == "agent",
            ProcurementInquiry.first_sent_at.is_not(None),
            ProcurementInquiry.status.in_(("waiting_reply", "followup_ready", "replied")),
        )
        .order_by(
            ProcurementInquiry.last_observed_at.asc().nulls_first(),
            ProcurementInquiry.id,
        )
        .limit(min(max(limit * 3, 100), 500))
    ).all()
    result = []
    for inquiry, task in rows:
        required = _required_capability(task, inquiry)
        if required not in capabilities:
            continue
        result.append(
            {
                "task_id": task.id,
                "inquiry_id": inquiry.id,
                "channel": inquiry.channel,
                "merchant_name": inquiry.merchant_name,
                "merchant_url": inquiry.merchant_url,
                "product_url": inquiry.product_url,
                "external_thread_id": inquiry.external_thread_id,
                "last_inbound_message": inquiry.last_inbound_message,
                "last_observed_at": (
                    inquiry.last_observed_at.isoformat()
                    if inquiry.last_observed_at
                    else None
                ),
                "required_capability": required,
            }
        )
        if len(result) >= limit:
            break
    return result


def agent_runtime_status(db: Session) -> dict[str, Any]:
    now = utcnow()
    agents = db.execute(
        select(ProcurementAgentState).order_by(
            ProcurementAgentState.last_seen_at.desc()
        )
    ).scalars().all()
    out = []
    for agent in agents:
        seen = agent.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        online = now - seen <= timedelta(seconds=90)
        out.append(
            {
                "agent_id": agent.agent_id,
                "display_name": agent.display_name,
                "host_label": agent.host_label,
                "version": agent.version,
                "mode": agent.mode,
                "status": agent.status if online else "offline",
                "online": online,
                "capabilities": agent.capabilities or [],
                "current_inquiry_id": agent.current_inquiry_id,
                "last_seen_at": agent.last_seen_at.isoformat(),
                "last_error": agent.last_error,
                "counters": agent.counters or {},
            }
        )
    now_expr = now
    active_leases = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.lease_until.is_not(None),
            ProcurementInquiry.lease_until >= now_expr,
        )
    ).scalar_one()
    return {"agents": out, "active_leases": int(active_leases or 0)}


def set_inquiry_decision(
    db: Session,
    inquiry: ProcurementInquiry,
    *,
    status: str,
    decided_by: str,
    note: Optional[str] = None,
    supplier_id: Optional[int] = None,
    part_purchase_id: Optional[int] = None,
) -> ProcurementInquiry:
    """记录人工选择结果；关联采购单不等于自动下单或付款。"""
    from app.models.order import PartPurchase
    from app.models.supplier import Supplier

    if status not in {"pending", "shortlisted", "selected", "rejected"}:
        raise ValueError("不支持的供应商决策状态")
    if supplier_id is not None and db.get(Supplier, supplier_id) is None:
        raise ValueError("关联供应商不存在")
    if part_purchase_id is not None and db.get(PartPurchase, part_purchase_id) is None:
        raise ValueError("关联采购记录不存在")
    if part_purchase_id is not None and status != "selected":
        raise ValueError("只有已选择的候选才能关联实际采购记录")
    inquiry.decision_status = status
    inquiry.decision_note = (note or "").strip()[:2000] or None
    inquiry.decided_by = decided_by
    inquiry.decided_at = utcnow()
    inquiry.supplier_id = supplier_id
    inquiry.part_purchase_id = part_purchase_id
    db.flush()
    return inquiry


def quote_comparison(db: Session, task_id: int) -> list[dict[str, Any]]:
    """返回可直接用于 ERP 比价表的统一字段，原始回复始终保留。"""
    rows = db.execute(
        select(ProcurementInquiry)
        .where(
            ProcurementInquiry.task_id == task_id,
            ProcurementInquiry.discovered_at.is_not(None),
        )
        .order_by(
            ProcurementInquiry.quote_complete.desc(),
            ProcurementInquiry.normalized_unit_price.asc().nulls_last(),
            ProcurementInquiry.candidate_score.desc().nulls_last(),
            ProcurementInquiry.id,
        )
    ).scalars().all()
    result: list[dict[str, Any]] = []
    for row in rows:
        quote = row.quote_payload or {}
        result.append(
            {
                "inquiry_id": row.id,
                "channel": row.channel,
                "merchant_name": row.merchant_name,
                "merchant_url": row.merchant_url,
                "product_url": row.product_url,
                "status": row.status,
                "decision_status": row.decision_status,
                "candidate_score": row.candidate_score,
                "quote_complete": row.quote_complete,
                "quote_amount": row.quote_amount,
                "normalized_unit_price": row.normalized_unit_price,
                "freight": quote.get("freight"),
                "lead_time": quote.get("lead_time"),
                "minimum_order_quantity": quote.get("minimum_order_quantity"),
                "material": quote.get("material"),
                "specification": quote.get("specification"),
                "tax_included": quote.get("tax_included"),
                "missing_fields": quote.get("missing_fields") or [],
                "reply_original": row.last_inbound_message,
                "manual_reason": row.manual_reason,
                "part_purchase_id": row.part_purchase_id,
            }
        )
    return result


def daily_summary(
    db: Session,
    *,
    summary_date: Optional[date] = None,
) -> dict[str, Any]:
    """采购日报；“已采购”只统计已关联既有 PartPurchase 的记录。"""
    target = summary_date or date.today()
    business_timezone = timezone(timedelta(hours=8))
    start = datetime.combine(target, datetime.min.time(), tzinfo=business_timezone)
    end = start + timedelta(days=1)
    discovered = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.discovered_at >= start,
            ProcurementInquiry.discovered_at < end,
        )
    ).scalar_one()
    review_pending = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.status.in_(("ready", "followup_ready")),
            ProcurementInquiry.approved_action_key.is_(None),
        )
    ).scalar_one()
    sent = db.execute(
        select(func.count(ProcurementMessage.id)).where(
            ProcurementMessage.direction == "outbound",
            ProcurementMessage.event_at >= start,
            ProcurementMessage.event_at < end,
        )
    ).scalar_one()
    replied = db.execute(
        select(func.count(ProcurementMessage.id)).where(
            ProcurementMessage.direction == "inbound",
            ProcurementMessage.event_at >= start,
            ProcurementMessage.event_at < end,
        )
    ).scalar_one()
    manual = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.status == "needs_manual",
            ProcurementInquiry.updated_at >= start,
            ProcurementInquiry.updated_at < end,
        )
    ).scalar_one()
    selected = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.decision_status == "selected",
            ProcurementInquiry.decided_at >= start,
            ProcurementInquiry.decided_at < end,
        )
    ).scalar_one()
    purchased = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.part_purchase_id.is_not(None),
            ProcurementInquiry.decided_at >= start,
            ProcurementInquiry.decided_at < end,
        )
    ).scalar_one()
    pending = db.execute(
        select(func.count(ProcurementInquiry.id)).where(
            ProcurementInquiry.status.in_(
                (
                    "discovery_ready",
                    "ready",
                    "waiting_winner",
                    "waiting_reply",
                    "followup_ready",
                    "needs_manual",
                )
            )
        )
    ).scalar_one()
    return {
        "date": target.isoformat(),
        "discovered": int(discovered or 0),
        "review_pending": int(review_pending or 0),
        "sent": int(sent or 0),
        "replied": int(replied or 0),
        "manual": int(manual or 0),
        "selected": int(selected or 0),
        "purchased": int(purchased or 0),
        "pending_total": int(pending or 0),
    }


def task_counts(db: Session, task_id: int) -> dict[str, int]:
    total, sent, replied, candidates = db.execute(
        select(
            func.count(ProcurementInquiry.id),
            func.count(ProcurementInquiry.first_sent_at),
            func.count(ProcurementInquiry.first_response_at),
            func.count(ProcurementInquiry.discovered_at),
        ).where(ProcurementInquiry.task_id == task_id)
    ).one()
    status_rows = db.execute(
        select(ProcurementInquiry.status, func.count(ProcurementInquiry.id))
        .where(ProcurementInquiry.task_id == task_id)
        .group_by(ProcurementInquiry.status)
    ).all()
    counts = {status: int(count) for status, count in status_rows}
    return {
        "total": int(total or 0),
        "discovery_pending": counts.get("discovery_ready", 0),
        "candidates": int(candidates or 0),
        "sent": int(sent or 0),
        "replied": int(replied or 0),
        "needs_manual": counts.get("needs_manual", 0),
        "completed": counts.get("completed", 0),
    }
