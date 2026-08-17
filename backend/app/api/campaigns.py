"""活动生命周期 API (P1, 2026-07-17 spec: docs/活动生命周期系统_执行plan.md §五)。

GET    /api/campaigns                     计划列表
POST   /api/campaigns                     建计划 (类型点选 + 档期精确到秒)
GET    /api/campaigns/no-sales-group      动销分组 (含登记表同步结果)
POST   /api/campaigns/no-sales-group/notify  无动销名单推飞书 (spec §四.2a)
GET    /api/campaigns/{id}                计划详情
PUT    /api/campaigns/{id}                改计划
DELETE /api/campaigns/{id}                删计划 (admin)
POST   /api/campaigns/{id}/precheck       R0~R19 预检
GET    /api/campaigns/{id}/rows           行预览 (kind=signup|discount)
POST   /api/campaigns/{id}/push-discount  推单品立减 (phase=stage|commit, admin)
POST   /api/campaigns/{id}/push-signup    已禁用 (仅自动报名程序内部可执行)
POST   /api/campaigns/{id}/recon          核对 (multipart 手动上传三种导出兜底)
GET    /api/campaigns/{id}/recon-reports  核对报告列表

页面权限: /api/campaigns 挂 permKey "pricing" (活动自动填写 wizard 在定价页, 见 page_permissions)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.campaign import CampaignPlan, CampaignReconReport
from app.services import campaign_service

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignPlanIn(BaseModel):
    name: str
    campaign_type: str
    start_at: datetime          # 档期精确到秒 (spec §四.4)
    end_at: datetime
    qn_campaign_title: Optional[str] = None
    price_protection_days: int = 19
    price_protection_rule_url: Optional[str] = None
    remark: Optional[str] = None


class CampaignPlanUpdate(BaseModel):
    name: Optional[str] = None
    campaign_type: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    qn_campaign_title: Optional[str] = None
    price_protection_days: Optional[int] = None
    price_protection_rule_url: Optional[str] = None
    remark: Optional[str] = None


class SuperReduceRepairIn(BaseModel):
    item_ids: list[str]
    phase: str = "stage"


def _plan_out(p: CampaignPlan) -> dict:
    from app.services import campaign_price_protection_service
    until = campaign_price_protection_service.protection_until(p)
    return {
        "id": p.id, "name": p.name, "campaign_type": p.campaign_type, "tier": p.tier,
        "campaign_type_name": campaign_service.CAMPAIGN_TYPES.get(p.campaign_type, ("?",))[0],
        "start_at": p.start_at.isoformat(sep=" ") if p.start_at else None,
        "end_at": p.end_at.isoformat(sep=" ") if p.end_at else None,
        "qn_campaign_title": p.qn_campaign_title,
        "price_protection_days": campaign_price_protection_service.protection_days(p),
        "price_protection_rule_url": p.price_protection_rule_url,
        "price_protection_confirmed_at": (
            p.price_protection_confirmed_at.isoformat(sep=" ")
            if p.price_protection_confirmed_at else None),
        "price_protection_until": until.isoformat(sep=" ") if until else None,
        "status": p.status, "remark": p.remark,
    }


def _get_plan(db: Session, plan_id: int) -> CampaignPlan:
    plan = db.get(CampaignPlan, plan_id)
    if plan is None:
        raise HTTPException(404, f"计划 {plan_id} 不存在")
    return plan


def _xlsx_download_response(content: bytes, filename: str, *,
                            metadata: Optional[dict] = None) -> Response:
    safe_name = str(filename or "campaign_feedback.xlsx").replace("\r", "").replace("\n", "")
    headers = {
        "Content-Disposition": (
            "attachment; filename=campaign_feedback.xlsx; "
            f"filename*=UTF-8''{quote(safe_name)}"
        )
    }
    for key, value in (metadata or {}).items():
        if value is not None:
            headers[f"X-Panse-{key}"] = str(value).lower() if isinstance(value, bool) else str(value)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


def _validate_type_and_window(campaign_type: str, start_at, end_at) -> str:
    if campaign_type not in campaign_service.CAMPAIGN_TYPES:
        raise HTTPException(422, f"未知活动类型 {campaign_type!r}; "
                                 f"可选 {list(campaign_service.CAMPAIGN_TYPES)}")
    if start_at and end_at and end_at <= start_at:
        raise HTTPException(422, "档期结束时间必须晚于开始时间")
    return campaign_service.CAMPAIGN_TYPES[campaign_type][1]


def _validate_price_protection(days: Optional[int], url: Optional[str]) -> None:
    if days is not None and not 1 <= int(days) <= 365:
        raise HTTPException(422, "价保冷静期必须是1至365天")
    if url and not str(url).strip().lower().startswith(("http://", "https://")):
        raise HTTPException(422, "价保说明链接必须以 http:// 或 https:// 开头")


@router.get("/policy")
def get_campaign_policy(_: User = Depends(get_current_user)):
    """Root policy used by every signup generator and shown in the wizard."""
    from app.services import campaign_policy_service
    try:
        return campaign_policy_service.public_policy()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("")
def list_plans(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    plans = db.execute(select(CampaignPlan).order_by(CampaignPlan.id.desc())).scalars().all()
    return {"items": [_plan_out(p) for p in plans],
            "types": {k: v[0] for k, v in campaign_service.CAMPAIGN_TYPES.items()}}


@router.post("")
def create_plan(body: CampaignPlanIn, db: Session = Depends(get_db),
                _: User = Depends(require_role("admin", "operator"))):
    tier = _validate_type_and_window(body.campaign_type, body.start_at, body.end_at)
    _validate_price_protection(body.price_protection_days, body.price_protection_rule_url)
    plan = CampaignPlan(name=body.name, campaign_type=body.campaign_type, tier=tier,
                        start_at=body.start_at, end_at=body.end_at,
                        qn_campaign_title=body.qn_campaign_title,
                        price_protection_days=body.price_protection_days,
                        price_protection_rule_url=body.price_protection_rule_url,
                        price_protection_confirmed_at=(
                            datetime.now() if body.price_protection_rule_url else None),
                        remark=body.remark,
                        status="draft")
    db.add(plan)
    db.commit()
    return _plan_out(plan)


@router.get("/no-sales-group")
def no_sales_group(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """动销检查与分组 (spec §四.1): 近60天淘宝订单聚合 + no_sales 登记表同步。"""
    return campaign_service.group_by_sales(db)


@router.post("/no-sales-group/notify")
@router.post("/no-sales-group/push-feishu")     # 前端契约别名 (同一 handler)
def notify_no_sales_group(db: Session = Depends(get_db),
                          _: User = Depends(require_role("admin", "operator"))):
    """无动销名单一键飞书推送给运营促成交 (spec §四.2a)。"""
    from app.services import notify_service
    grouping = campaign_service.group_by_sales(db)
    items = grouping["无动销"]
    if not items:
        return {"ok": True, "sent": False, "message": "当前没有无动销商品"}
    names = grouping.get("item_names", {})
    lines = [f"📉 无动销商品名单（近{grouping['days']}天零成交, 共 {len(items)} 个）:"]
    lines += [f"- {iid} {names.get(iid, '')}" for iid in items]
    lines.append("请运营跟进促成交; 卖出1单后系统会提示转正(撤 nosales 立减→报名大促)。")
    result = notify_service.broadcast_text(db, "\n".join(lines), title="无动销名单", level="warning")
    return {"ok": True, "sent": True, "count": len(items), "notify_result": result}


@router.get("/no-sales-group/export.xlsx")
def export_no_sales_group(db: Session = Depends(get_db),
                          _: User = Depends(get_current_user)):
    """无动销名单一键导出 xlsx (spec §四.2a): 产品名/产品编码/淘宝商品ID/近60天单量/建议动作。"""
    import io
    import openpyxl
    from fastapi.responses import Response
    rows = campaign_service.no_sales_export_rows(db)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "无动销名单"
    headers = ("产品名", "产品编码", "淘宝商品ID", "近60天单量", "建议动作")
    for ci, h in enumerate(headers, start=1):
        ws.cell(1, ci, h)
    for ri, r in enumerate(rows, start=2):
        ws.cell(ri, 1, r["product_name"])
        ws.cell(ri, 2, r["product_codes"])
        ws.cell(ri, 3, r["taobao_item_id"]).number_format = "@"
        ws.cell(ri, 4, r["sales_60d"])
        ws.cell(ri, 5, r["action"])
    out = io.BytesIO()
    wb.save(out)
    return Response(
        content=out.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="no_sales_group.xlsx"'})


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db),
             _: User = Depends(get_current_user)):
    return _plan_out(_get_plan(db, plan_id))


@router.put("/{plan_id}")
def update_plan(plan_id: int, body: CampaignPlanUpdate, db: Session = Depends(get_db),
                _: User = Depends(require_role("admin", "operator"))):
    plan = _get_plan(db, plan_id)
    data = body.model_dump(exclude_unset=True)
    if "price_protection_days" in data and data["price_protection_days"] is None:
        raise HTTPException(422, "价保冷静期不能为空")
    _validate_price_protection(
        data.get("price_protection_days"),
        data.get("price_protection_rule_url"))
    if "campaign_type" in data:
        plan.tier = _validate_type_and_window(
            data["campaign_type"], data.get("start_at", plan.start_at),
            data.get("end_at", plan.end_at))
    for k, v in data.items():
        setattr(plan, k, v)
    if "price_protection_rule_url" in data:
        plan.price_protection_rule_url = (
            str(data["price_protection_rule_url"]).strip()
            if data["price_protection_rule_url"] else None)
        plan.price_protection_confirmed_at = (
            datetime.now() if plan.price_protection_rule_url else None)
    db.commit()
    return _plan_out(plan)


@router.post("/{plan_id}/price-protection/remind")
def remind_price_protection_rule(
        plan_id: int,
        db: Session = Depends(get_db),
        _: User = Depends(require_role("admin", "operator"))):
    from app.services import campaign_price_protection_service

    plan = _get_plan(db, plan_id)
    return campaign_price_protection_service.notify_rule_link_needed(db, plan, force=True)


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db),
                _: User = Depends(require_role("admin"))):
    plan = _get_plan(db, plan_id)
    db.delete(plan)
    db.commit()
    return {"ok": True}


@router.post("/{plan_id}/precheck")
def precheck(plan_id: int, db: Session = Depends(get_db),
             _: User = Depends(require_role("admin", "operator"))):
    """R0~R17 预检 (spec §三)。只有全无 error 才进入 precheck 状态。"""
    plan = _get_plan(db, plan_id)
    checks = campaign_service.preflight(db, plan)
    has_error = any(c["level"] == "error" for c in checks)
    if plan.status == "draft" and not has_error:
        plan.status = "precheck"
        db.commit()
    from app.services import campaign_policy_service
    return {
        "plan": _plan_out(plan),
        "checks": checks,
        "has_error": has_error,
        "policy": campaign_policy_service.public_policy() if checks[0]["rule"] == "R0"
                  and checks[0]["level"] == "pass" else None,
    }


@router.get("/{plan_id}/rows")
def preview_rows(plan_id: int, kind: str = Query("signup"), db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)):
    """行预览: kind=signup(报名行) / discount(单品立减行)。"""
    plan = _get_plan(db, plan_id)
    if kind == "signup":
        rows, stats = campaign_service.build_signup_rows(db, plan)
    elif kind == "discount":
        rows, stats = campaign_service.build_discount_rows(db, plan)
    else:
        raise HTTPException(422, "kind 必须是 signup 或 discount")
    return {"rows": rows, "stats": stats}


@router.post("/{plan_id}/push-discount")
def push_discount(plan_id: int, phase: str = Query("stage"), db: Session = Depends(get_db),
                  _: User = Depends(require_role("admin"))):
    """推单品立减。phase=stage 挂文件停在提交前; commit ★不可逆★ (R12, 用户确认后才调)。"""
    if phase not in ("stage", "commit"):
        raise HTTPException(422, "phase 必须是 stage 或 commit")
    plan = _get_plan(db, plan_id)
    return campaign_service.push_discount(db, plan, phase=phase)


@router.post("/{plan_id}/push-signup")
def push_signup(plan_id: int, db: Session = Depends(get_db),
                _: User = Depends(require_role("admin"))):
    """Direct signup is disabled; only the scheduled campaign program may submit."""
    _get_plan(db, plan_id)
    raise HTTPException(
        409,
        "活动报名只由 ERP 自动报名程序执行；页面或 AI 直推已禁用。错误先报告并等待用户决定。",
    )


@router.get("/{plan_id}/single-discount-error-file.xlsx")
def download_single_discount_error_file(
        plan_id: int, activity_id: str = Query(..., min_length=1),
        db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Read-only proxy for the original QianNiu single-discount error file."""
    from app.services import web_agent_service

    _get_plan(db, plan_id)
    result = web_agent_service.single_discount_error_file(db, activity_id)
    if not result.get("ok"):
        raise HTTPException(422, detail=result)
    return _xlsx_download_response(
        result["xlsx_bytes"], result.get("filename") or "单品立减错误文件.xlsx",
        metadata={
            "Activity-Id": result.get("activity_id"),
            "Operation-Time": result.get("operation_time"),
            "Success-Count": result.get("success_count"),
            "Failed-Count": result.get("failed_count"),
            "Detail-Rows": result.get("detail_rows"),
            "Empty-Detail": result.get("empty_detail"),
        },
    )


@router.get("/{plan_id}/operation-feedback.xlsx")
def download_campaign_operation_feedback(
        plan_id: int, db: Session = Depends(get_db),
        _: User = Depends(get_current_user)):
    """Download the original latest operation-feedback workbook, read-only."""
    from app.services import web_agent_service

    plan = _get_plan(db, plan_id)
    campaign_id, united_activity_id = campaign_service.plan_campaign_ids(plan)
    if campaign_id and united_activity_id:
        result = web_agent_service.campaign_feedback(
            db,
            plan.qn_campaign_title or plan.name,
            campaign_id=campaign_id,
            united_activity_id=united_activity_id,
        )
    elif plan.campaign_type == "super_reduce":
        # The long-running Super Reduce page is itself an exact activity entry;
        # this read-only fallback is needed for older plans created before the
        # immutable IDs were persisted in their remark.
        result = web_agent_service.super_reduce_feedback(db)
    else:
        raise HTTPException(422, "计划缺少千牛 campaignId / unitedActivityId，无法安全下载")
    if not result.get("ok"):
        raise HTTPException(422, detail=result)
    if not result.get("xlsx_bytes"):
        raise HTTPException(422, "Web-Agent 已解析反馈，但未返回平台原始文件；请更新本机 Web-Agent")
    feedback = result.get("feedback") or {}
    return _xlsx_download_response(
        result["xlsx_bytes"], result.get("filename") or "活动报名操作反馈.xlsx",
        metadata={
            "Failed-Rows": len(feedback.get("failed") or []),
            "Failure-Groups": len(feedback.get("by_reason") or []),
        },
    )


@router.post("/{plan_id}/repair-super-reduce-activation")
def repair_super_reduce_activation(
        plan_id: int, payload: SuperReduceRepairIn,
        db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    """User-authorized correction through the ERP campaign program."""
    plan = _get_plan(db, plan_id)
    result = campaign_service.repair_super_reduce_early_activation(
        db,
        plan,
        payload.item_ids,
        phase=payload.phase,
        execution_source="campaign_automation_repair",
    )
    if not result.get("ok"):
        raise HTTPException(422, detail=result)
    return result


@router.post("/{plan_id}/recon")
async def recon(plan_id: int,
                activity_file: Optional[UploadFile] = File(None),
                discount_file: Optional[UploadFile] = File(None),
                product_file: Optional[UploadFile] = File(None),
                db: Session = Depends(get_db),
                _: User = Depends(require_role("admin", "operator"))):
    """核对 (spec §四.6, §五「/recon (自动+手动上传兜底)」):
    - 带文件 → 手动上传兜底 (三种导出任传);
    - 不带文件 → 自动: WA campaign_export_items 按活动标题导出「活动商品导出」再比对。"""
    from app.services import campaign_recon_service
    plan = _get_plan(db, plan_id)
    activity_bytes = await activity_file.read() if activity_file else None
    discount_bytes = await discount_file.read() if discount_file else None
    product_bytes = await product_file.read() if product_file else None
    source = "manual"
    if not any((activity_bytes, discount_bytes, product_bytes)):
        from app.services import web_agent_service
        exp = web_agent_service.campaign_export_items(
            db, plan.qn_campaign_title or plan.name)
        if not exp.get("ok"):
            err = exp.get("error") or exp.get("message") or "未知原因"
            raise HTTPException(422, f"WA 自动导出失败（{err}）; 请自己去千牛导出后走手动上传兜底")
        activity_bytes, source = exp["xlsx_bytes"], "auto"
    result = campaign_recon_service.reconcile(
        db, plan, activity_bytes=activity_bytes, discount_bytes=discount_bytes,
        product_bytes=product_bytes, source=source)
    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "核对失败"))
    return result


@router.get("/{plan_id}/recon-reports")
def recon_reports(plan_id: int, db: Session = Depends(get_db),
                  _: User = Depends(get_current_user)):
    reports = db.execute(
        select(CampaignReconReport).where(CampaignReconReport.plan_id == plan_id)
        .order_by(CampaignReconReport.id.desc())).scalars().all()
    return {"items": [{"id": r.id, "source": r.source, "summary": r.summary,
                       "alarm_count": r.alarm_count,
                       "created_at": r.created_at.isoformat() if r.created_at else None}
                      for r in reports]}
