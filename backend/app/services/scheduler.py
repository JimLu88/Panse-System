"""统一定时任务调度 (Phase 1A).

集中管理所有定时任务, 每个任务跑完写一条 ScheduledJobRun 日志, 给 UI "全部自动任务清单"
功能 18 提供数据源。

依赖: APScheduler AsyncIOScheduler — 跑在 FastAPI 的 event loop 内, 不需要单独 worker。

注册任务在 `_register_jobs()` 内, 由 startup hook 调一次。
当前注册:
    - daily_17_refund_check   每天 17:00       — 功能 11
    - hourly_data_baseline    每 60 分钟         — 功能 13 自动核对触发器
    - hourly_alert_expire     每 60 分钟         — Alert auto_resolve_until 过期清理
    - daily_06_forecast_ref   每天 06:00       — 功能 7 销售预测重算
    - daily_07_lowstock_scan  每天 07:00       — 功能 8 库存预警 + 滞销扫描
    - daily_08_activate_future 每天 08:00      — 功能 10 远期订单激活
    - daily_09_tracking_check 每天 09:00       — 功能 6 快递追踪 (占位)
    - daily_10_data_reconcile 每天 10:00       — 功能 19 财务公式对账
"""
from __future__ import annotations

import logging
import os
import time as time_mod
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy.orm import Session

_logger = logging.getLogger("panse.scheduler")

_SCHEDULER = None   # type: ignore[var-annotated]
_REGISTRY: dict[str, dict] = {}
# job_id -> {label, fn, kind: 'cron'/'interval', schedule_kwargs}


# ----------------------------- 任务运行包装 ----------------------- #


_FAILURE_ALERT_THRESHOLD = 3
_CRITICAL_PIPELINE_JOB_IDS = {
    "daily_0630_web_agent",
    "daily_1810_order_sheets",
    "pull_catchup_30min",
    "daily_2030_finance_agent",
    "finance_pull_retry_30min",
    "finance_pull_retry_2200",
}


def _maybe_alert_repeated_failure(db: Session, job_id: str, label: str) -> None:
    """定时任务连续失败到阈值时告警一次 (优化 #6)。只在"刚跨过阈值"时发, 避免每次刷屏。"""
    from sqlalchemy import select
    from app.models.scheduled_job import ScheduledJobRun
    n = _FAILURE_ALERT_THRESHOLD
    recent = db.execute(
        select(ScheduledJobRun.status).where(ScheduledJobRun.job_id == job_id)
        .order_by(ScheduledJobRun.id.desc()).limit(n)
    ).scalars().all()
    if len(recent) < n or any(s != "fail" for s in recent):
        return
    prior = db.execute(
        select(ScheduledJobRun.status).where(ScheduledJobRun.job_id == job_id)
        .order_by(ScheduledJobRun.id.desc()).offset(n).limit(1)
    ).scalars().first()
    if prior == "fail":
        return   # 已在阈值以上, 之前已告警过, 不重复刷
    try:
        from app.services import notify_service
        notify_service.notify(
            db, f"定时任务【{label}】({job_id}) 已连续失败 {n} 次, 请尽快检查。",
            level="error", title="定时任务连续失败")
    except Exception:  # pragma: no cover
        _logger.warning("发送任务失败告警出错")


def _run_with_logging(job_id: str, label: str, fn: Callable[[Session], dict]) -> None:
    """同步任务 wrapper. fn 接收一个 db session, 返回 dict 写到 result_summary."""
    from app.database import SessionLocal
    from app.models.scheduled_job import ScheduledJobRun

    started = datetime.now(timezone.utc)
    t0 = time_mod.time()
    error: Optional[str] = None
    result: Optional[dict] = None
    status = "ok"
    try:
        db = SessionLocal()
        try:
            r = fn(db)
            db.commit()
            if is_dataclass(r):
                result = asdict(r)
            elif isinstance(r, dict):
                result = r
            else:
                result = {"value": r}
            if isinstance(result, dict):
                requested_status = result.get("_run_status")
                if requested_status in ("fail", "skipped"):
                    status = requested_status
                    error = str(result.get("_error") or result.get("skipped") or "business gate")
        finally:
            db.close()
    except Exception as e:  # pragma: no cover — 内层任务自己应处理
        _logger.exception("定时任务 %s 失败", job_id)
        status = "fail"
        error = f"{type(e).__name__}: {e}"
    finally:
        duration = int((time_mod.time() - t0) * 1000)
        # 写日志 (独立 session)
        log_db = SessionLocal()
        try:
            log_db.add(ScheduledJobRun(
                job_id=job_id, job_label=label, status=status,
                duration_ms=duration, error=error, result_summary=result,
                started_at=started, completed_at=datetime.now(timezone.utc),
            ))
            log_db.commit()
            if status == "fail" and job_id not in _CRITICAL_PIPELINE_JOB_IDS:
                _maybe_alert_repeated_failure(log_db, job_id, label)
        except Exception as e:  # pragma: no cover
            _logger.warning("写 ScheduledJobRun 失败: %s", e)
        finally:
            log_db.close()


# ----------------------------- 公共注册接口 ----------------------- #


def register_job(
    job_id: str, label: str, fn: Callable[[Session], dict], *,
    cron: Optional[dict] = None, interval_minutes: Optional[int] = None,
    enabled: bool = True,
) -> None:
    """注册一个任务. cron 是 {hour, minute, day_of_week, ...} 给 CronTrigger.

    重复注册同 id 会覆盖。
    """
    if not cron and not interval_minutes:
        raise ValueError("必须提供 cron 或 interval_minutes")
    _REGISTRY[job_id] = {
        "label": label, "fn": fn,
        "cron": cron, "interval_minutes": interval_minutes,
        "enabled": enabled,
    }
    _logger.info("注册任务 %s (%s)", job_id, label)
    # 如果调度器已起来, 立即加入
    if _SCHEDULER is not None:
        _add_to_scheduler(job_id)


# 用户可在设置里覆盖每个任务的定时; 覆盖存 system_settings 的 scheduler_overrides (JSON):
#   { job_id: { "enabled": bool, "interval_minutes": int, "cron": {hour,minute,...} } }
_ALLOWED_CRON_KEYS = ("year", "month", "day", "week", "day_of_week", "hour", "minute", "second")


def _load_overrides() -> dict:
    """从设置读 scheduler_overrides (JSON)。缺失/损坏返回 {}。"""
    import json
    from app.database import SessionLocal
    from app.services import settings_service
    db = SessionLocal()
    try:
        raw = settings_service.get(db, "scheduler_overrides", env_fallback=False)
    except Exception:  # pragma: no cover - 读配置失败不应拖垮调度
        return {}
    finally:
        db.close()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):  # pragma: no cover
        return {}


def _effective_schedule(cfg: dict, ov: dict) -> tuple[str, dict, bool]:
    """合并默认计划与用户覆盖, 返回 (kind, schedule, enabled)。schedule 为最终生效计划。"""
    enabled = bool(ov.get("enabled", cfg.get("enabled", True)))
    if cfg["cron"]:
        merged = {**cfg["cron"], **{k: v for k, v in (ov.get("cron") or {}).items() if k in _ALLOWED_CRON_KEYS}}
        return "cron", merged, enabled
    interval = int(ov.get("interval_minutes") or cfg["interval_minutes"])
    return "interval", {"interval_minutes": max(1, interval)}, enabled


def _add_to_scheduler(job_id: str, overrides: Optional[dict] = None) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    cfg = _REGISTRY[job_id]
    ov = (overrides if overrides is not None else _load_overrides()).get(job_id, {})
    kind, schedule, enabled = _effective_schedule(cfg, ov)
    if not enabled:
        # 用户停用了该任务 — 若已在调度器中, 移除
        if _SCHEDULER is not None and _SCHEDULER.get_job(job_id):
            _SCHEDULER.remove_job(job_id)
        return
    if kind == "cron":
        trigger = CronTrigger(**schedule)
    else:
        trigger = IntervalTrigger(minutes=schedule["interval_minutes"])
    _SCHEDULER.add_job(
        _run_with_logging, trigger=trigger,
        args=(job_id, cfg["label"], cfg["fn"]),
        id=job_id, replace_existing=True,
        misfire_grace_time=300, max_instances=1, coalesce=True,
    )


# ----------------------------- 内置任务 -------------------------- #


def _job_alert_expire(db: Session) -> dict:
    """清理 auto_resolve_until 已过期的告警."""
    from app.services import alert_service
    n = alert_service.auto_expire(db)
    return {"expired": n}


def _job_refund_check(db: Session) -> dict:
    """17:00 退款订单检查 (业务需求 11) — 把 24h+ 仍在 aftersales 的退款订单 flag."""
    from app.services import factory_order_service
    refund = factory_order_service.check_refund_pending_orders(db)
    tracking = factory_order_service.check_missing_tracking(db)
    return {**refund, **tracking}


def _job_activate_future_orders(db: Session) -> dict:
    """业务需求 10: 远期订单到点激活. activate_at <= now 且 status=pending_payment → 走激活流程.

    Phase 6 修复: 必须走 order_service.transition 才能触发派生工厂单 + 锁库存联动,
    不能直接赋值绕过状态机.
    """
    from datetime import datetime as _dt
    from sqlalchemy import select
    from app.models.order import Order
    from app.services import order_service
    now = _dt.now(timezone.utc)
    rows = db.execute(
        select(Order).where(
            Order.activate_at.isnot(None),
            Order.activate_at <= now,
            Order.status == "pending_payment",
        )
    ).scalars().all()
    activated = 0
    for o in rows:
        try:
            order_service.transition(db, o, "paid", actor="scheduler")
            o.activate_at = None  # 激活后清空, 防重复
            activated += 1
        except Exception as e:  # pragma: no cover
            _logger.warning("远期订单 %s 激活失败: %s", o.order_no, e)
    return {"activated": activated}


def _job_lowstock_scan(db: Session) -> dict:
    """业务需求 8: 库存预警 / 滞销扫描."""
    from app.services import inventory_alert_service
    return inventory_alert_service.scan_all(db)


def _job_data_reconcile(db: Session) -> dict:
    """业务需求 19 + 功能 1 + 功能 C(自动对账+新差异推送): 全自动核对.

    1) 跑全部对账规则, 超阈值自动写异常池(幂等);
    2) 比对本次新出现的未归因差异(reconciliation_diff, open) → 站内通知(NotificationBell)
       + 运营待办台账动态待办, 提醒去对账诊断归因做平;
    3) 算财务公式 A vs B, 差额 > 100 生成 Alert。
    不单独推送群 — 汇总结果由 daily_10_comprehensive_report 统一推送。
    """
    from sqlalchemy import select

    from app.models.exception import DataException
    from app.services import (
        alert_service, asset_service, ops_checklist_service, reconciliation_service,
        settlement_import_service,
    )

    # 先把支付宝企业号订单级分账(T200P)路由进 order_settlements, 让支付宝订单也进逐笔对账 (用户拍板 2026-06-23)
    try:
        settlement_import_service.route_alipay_flows(db)
        db.flush()
    except Exception:  # noqa: BLE001 — 路由失败不该挡住后续对账
        db.rollback()

    def _open_recon_pks() -> set[str]:
        rows = db.execute(
            select(DataException.source_pk).where(
                DataException.exception_type == "reconciliation_diff",
                DataException.status == "open",
            )
        ).all()
        return {pk for (pk,) in rows if pk}

    before = _open_recon_pks()
    results = reconciliation_service.run_all(db, record_exceptions=True)
    db.flush()
    after = _open_recon_pks()
    new_pks = after - before

    by_rule = {
        name: {"error": r.error_count, "warning": r.warning_count, "ok": r.ok_count}
        for name, r in results.items()
    }

    # 功能 C: 新差异 → 站内通知 + 动态待办
    if new_pks:
        n = len(new_pks)
        sample = "; ".join(sorted(new_pks)[:5])
        body = f"本次对账新增 {n} 笔未归因差异: {sample}" + ("…" if n > 5 else "")
        alert_service.upsert(
            db,
            kind="reconciliation_diff",
            severity="warn",
            title=f"对账发现 {n} 笔新差异待归因",
            body=body,
            dedupe_key="recon_new_diff",
            related_url="/recon-diagnostics",
            context={"count": n, "pks": sorted(new_pks)[:50]},
        )
        try:
            ops_checklist_service.add_dynamic_todo(
                db,
                key="recon_diff_followup",
                title=f"归因做平 {n} 笔对账差异",
                detail="对账诊断/工厂逐单对账: 逐条填原因做平本次新增的未归因差异",
                route="/recon-diagnostics",
                freq="daily",
            )
        except Exception:  # pragma: no cover
            pass
        db.flush()

    formula = asset_service.check_formula_and_alert(db)
    return {"reconciliation": by_rule, "new_diff_count": len(new_pks), "formula": formula}


def _job_alipay_match_pipeline(db: Session) -> dict:
    """支付宝流水 ↔ 订单 自动逐笔匹配 (在 09:50 总额对账之前先把能对的对上)。

    顺序: 归类(reconciliation_type) → 按订单号回填 → 按金额匹配(4规则)。
    全部「只填空、保守、仅唯一命中」, 跑完仍对不上的由 08:00 数据扫描 / 09:50 对账 挂异常。
    """
    from app.services import (
        alipay_amount_match_service, alipay_backfill_service,
        alipay_flow_router_service, packing_payment_service, smart_matching_service,
    )
    smart_matching_service.run(db)
    packing_payment_service.auto_allocate(db)
    route = alipay_flow_router_service.run_all(db)
    db.flush()
    bf = alipay_backfill_service.backfill(db, only_missing=True)
    db.flush()
    am = alipay_amount_match_service.match(db)
    # 配件采购"备注识别"(零星采购, 用户 2026-06-27): 解析采购备注里的订单号→related_order_no→汇总进
    # actual_parts; 匿名付款码采购按备注人名改挂供应商。幂等只填空。
    from app.services import accessory_capture_service
    cap = accessory_capture_service.run_capture(db, apply=True)
    db.flush()
    return {
        "route": {"purchases_created": getattr(route, "purchases_created", 0),
                  "factory_flipped": getattr(route, "factory_flipped", 0)},
        "backfill": {"matched_orders": bf.matched_orders, "filled_flow_no": bf.filled_flow_no},
        "amount_match": {"matched": am.matched, "linked_flow_no": am.linked_flow_no,
                         "by_rule": am.by_rule},
        "accessory_capture": {"order_linked": cap["order_link"]["linked"],
                              "supplier_relabeled": cap["supplier_relabel"]["relabeled"]},
    }


def _job_tracking_check(db: Session) -> dict:
    """快递追踪 — 仅扫缺单号; 真正调 17track API 留给 Phase 5 扩展."""
    from app.services import factory_order_service
    return factory_order_service.check_missing_tracking(db)


def _job_accessory_tracking_refresh(db: Session) -> dict:
    """配件物流实时刷新 — 扫所有运输中配件, 调快递100 更新轨迹/签收状态。

    未配置物流 (kuaidi100) 时整体跳过, 不报错。
    """
    from app.services import logistics_tracking_service
    return logistics_tracking_service.refresh_in_transit(db)


def _job_shipments_refresh(db: Session) -> dict:
    """全表物流实时刷新 — ensure 所有带快递单号的业务记录进 shipments, 刷新在途,
    并把派生状态 (签收→订单签收 / 售后返厂二次入库) 实时回写各业务表。

    未配置物流时整体跳过, 不报错。
    """
    from app.services import shipment_service
    return shipment_service.sync_and_refresh(db)


def _job_accessory_alert_refresh(db: Session) -> dict:
    """配件到货预警刷新 — 按发货日临近度更新未到货配件的预警等级。"""
    from app.services import accessory_checklist_service
    n = accessory_checklist_service.refresh_all_alerts(db)
    return {"refreshed": n}


def _job_tax_report_pull(db: Session) -> dict:
    """涉税报送自动抓取 (用户拍板 2026-07-14): 经 Web-Agent 任务 tax_information 抓千牛
    涉税报送页各季度收入净额 → 税费按打款口径。任务未上线/Agent 离线 → 软失败记 summary。"""
    from app.services import tax_report_service
    return tax_report_service.pull_via_agent(db)


def _job_cost_recompute(db: Session) -> dict:
    """理论成本每日全自动兜底 (用户拍板 2026-06-17): BOM/定价表反推 + 查不到SKU成本的按
    实付×类目成本率(不足退全店)兜底 + 缺成本订单进异常待补。导入即时反推, 这里收尾并兜底缺编码单。"""
    from app.services import order_cost_service
    return order_cost_service.auto_cost_backfill(db)


def _job_accessory_backfill(db: Session) -> dict:
    """配件清单每日自动补建/对齐 — 进行中订单(已付款/已发货/售后)按 BOM 生成或重对齐,
    免得停留在"配件未建"。保留已填采购/物流进度。(用户拍板 2026-06-12: 全自动, 不靠首次查看才建)"""
    from app.services import accessory_checklist_service
    return accessory_checklist_service.backfill_all(db)


def _job_accessory_self_arrive(db: Session) -> dict:
    """自送/无物流号配件 3 天自动到货 (用户拍板 2026-06-12)。

    自送(工厂周边买/自己送)的配件没物流号, 无法靠快递100 自动签收。
    约定: 标「已下单」满 3 天仍无操作 → 自动标「已到货」(有物流号的交给快递追踪, 这里只管无号的)。
    有问题工厂会报, 用户可手动改回。updated_at 作采购时间代理(自送项标已下单后基本不再变动)。
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import or_, select
    from app.models.order import OrderAccessoryItem
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    rows = db.execute(select(OrderAccessoryItem).where(
        OrderAccessoryItem.status == "已下单",
        OrderAccessoryItem.is_factory_provided.is_(False),
        or_(OrderAccessoryItem.tracking_no.is_(None), OrderAccessoryItem.tracking_no == ""),
        OrderAccessoryItem.updated_at < cutoff,
    )).scalars().all()
    n = 0
    for it in rows:
        it.status = "已到货"
        it.self_delivered = True
        it.remark = ((it.remark + " | ") if it.remark else "") + "自动到货(自送满3天无异常)"
        n += 1
    if n:
        db.commit()
    return {"auto_arrived": n}


def _job_forecast_refresh(db: Session) -> dict:
    """销售预测重算，并同步订单数量异常到异常中心。"""
    from app.services import inventory_demand_service, product_inventory_service, sales_analytics
    forecast = sales_analytics.forecast_30d(db)
    anomalies = inventory_demand_service.sync_quantity_anomalies(
        db, cfg=product_inventory_service.get_forecast_config(db)
    )
    return {"sku_count": len(forecast), "quantity_anomalies": anomalies}


def _job_sales_rollup(db: Session) -> dict:
    """Phase 12 P3-13: 每日聚合昨日订单到 sales_daily_rollup, 加速大量数据查询."""
    from datetime import date as _date, timedelta as _td
    from app.services import sales_rollup_service
    target = _date.today() - _td(days=1)
    n = sales_rollup_service.rollup_day(db, target)
    return {"day": target.isoformat(), "rows": n}


def _job_daily_briefing(db: Session) -> dict:
    """Phase 8 Tier 1 #1: AI 每日经营简报 (昨日数据合成, 不单独推送 — 并入 10:00 综合报告)."""
    from app.services import briefing_service
    b = briefing_service.generate(db, push=False)
    return {"for_date": b.for_date.isoformat(),
            "content_len": len(b.content or ""), "model": b.model}


def _job_supplier_score(db: Session) -> dict:
    """Phase 8 Tier 1 #5: 月初算上月供应商评分."""
    from datetime import date as _date, timedelta as _td
    from app.services import supplier_score_service
    last = _date.today().replace(day=1) - _td(days=1)
    scores = supplier_score_service.compute_for_month(db, last.year, last.month)
    return {"year": last.year, "month": last.month, "supplier_count": len(scores)}


def _job_feishu_sync(db: Session) -> dict:
    """飞书双向同步 (仅 enabled 的绑定). 未配凭证则空跑."""
    from app.services import (
        factory_dispatch_feishu_service,
        feishu_client,
        feishu_sync_service,
    )
    try:
        feishu_client.get_credentials(db)
    except feishu_client.FeishuError:
        return {"skipped": "飞书未配置"}
    factory_dispatch = factory_dispatch_feishu_service.sync_if_enabled(db)
    results = feishu_sync_service.sync_all(db)
    return {
        "bindings": len(results),
        "pushed": sum(r.pushed for r in results),
        "pulled": sum(r.pulled for r in results),
        "conflicts": sum(r.conflicts for r in results),
        "factory_dispatch": factory_dispatch,
    }


def _job_data_quality(db: Session) -> dict:
    """每日数据完整性扫描 (B1-B11) + 数据扫描器(含配件采购价差) + 已修复项自愈销账 (用户 2026-07-08:
    价差扫描进每日体检)。扫描器把新异常写异常池, 复核把已修好的(如已更新物料库)自动销账。"""
    from app.services import data_quality_service, scanner_service
    from app.services.exception_recheck_service import bulk_close_resolved
    dq = data_quality_service.run_all(db)
    scan = scanner_service.run_all(db)          # 含 purchase_price_variance 采购价差
    db.flush()
    closed = bulk_close_resolved(db)            # 已修复(如已更新物料库) → 自愈销账
    return {"data_quality": dq,
            "scanners": {k: v.written for k, v in scan.items()},
            "self_healed": closed}


def _job_post_import_logic_check(db: Session) -> dict:
    """兜底: 每日对全量数据跑一次 AI 逻辑核查 (补导入时漏掉的)."""
    from app.services import post_import_ai_service
    return post_import_ai_service.run_after_import(db, summary={"source": "daily_scan"})


def _job_data_freshness_remind(db: Session) -> dict:
    """每日 09:00: 检查各数据源新鲜度, 过期则写 Alert (不单独推送 — 并入 10:00 综合报告)。"""
    from app.services import data_freshness_service
    return data_freshness_service.check_and_alert_only(db)


def _job_monthly_data_remind(db: Session) -> dict:
    """每月 1 号 08:30: 集中提醒上传上月全量数据 (流水/推广/账单/余额)。"""
    from app.services import data_freshness_service
    return data_freshness_service.monthly_batch_remind(db)


def _job_ops_checklist_overdue(db: Session) -> dict:
    """每日 09:05: 例行待办超时报警 — 物流(周)/打包/玻璃/岩板/电力轨道对账(月)等
    超时未完成则写 Alert (并入 10:00 综合日报统一推送, 不单独刷屏)。"""
    from app.services import ops_checklist_service
    return ops_checklist_service.check_and_alert_overdue(db)


def _job_email_poll_alipay(db: Session) -> dict:
    """每 6 小时轮询邮箱, 自动导入支付宝账单附件 CSV。"""
    from app.services import email_import_service
    r = email_import_service.poll_and_import(db)
    return {"scanned": r.scanned, "imported": r.imported,
            "skipped": r.skipped, "errors": r.errors}


def _job_monthly_report_push(db: Session) -> dict:
    """每月 1 号 09:00: 自动生成上月经营报告 + 销售汇总, 推送到群。"""
    from datetime import date as _date, timedelta as _td
    from decimal import Decimal
    from sqlalchemy import func, select
    from app.models.marketing import OutsourcingExpense, PromotionFlow
    from app.models.order import Order
    from app.services import notify_service, sales_analytics

    today = _date.today()
    # 上月: 回退到月初再减 1 天得上月末, 再取月初
    last_month_end = today.replace(day=1) - _td(days=1)
    last_month_start = last_month_end.replace(day=1)

    s = sales_analytics.summary(db, start=last_month_start, end=last_month_end)
    revenue = Decimal(s.revenue or 0)

    promo = db.execute(
        select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
            PromotionFlow.flow_type == "支出",
            PromotionFlow.transaction_date >= last_month_start,
            PromotionFlow.transaction_date <= last_month_end,
        )
    ).scalar() or 0
    personnel = db.execute(
        select(func.coalesce(func.sum(OutsourcingExpense.amount), 0)).where(
            OutsourcingExpense.payment_date >= last_month_start,
            OutsourcingExpense.payment_date <= last_month_end,
        )
    ).scalar() or 0

    cost = Decimal(s.cost or 0)
    net = Decimal(s.net_profit or 0)
    pct = lambda x: f"{float(Decimal(x or 0) / revenue * 100):.1f}%" if revenue else "N/A"

    lines = [
        f"📊 畔色 ERP | {last_month_start.year}年{last_month_start.month}月经营简报",
        f"",
        f"📦 订单数:   {s.order_count} 单",
        f"💰 销售额:   ¥{float(revenue):,.2f}",
        f"🏭 商品成本: ¥{float(cost):,.2f}  ({pct(cost)})",
        f"📣 推广费:   ¥{float(promo):,.2f}  ({pct(promo)})",
        f"👷 人员外包: ¥{float(personnel):,.2f}  ({pct(personnel)})",
        f"📈 净利润:   ¥{float(net):,.2f}  (净利率 {pct(net)})",
        f"",
    ]
    if s.top_products_by_profit:
        lines.append("🏆 利润 Top 5:")
        for i, p in enumerate(s.top_products_by_profit[:5], 1):
            name = (p.get("product_name") or p.get("product_code") or "?")[:10]
            pnl = float(p.get("net_profit") or 0)
            lines.append(f"  {i}. {name}  ¥{pnl:,.0f}")

    notify_service.notify(
        db,
        "\n".join(lines),
        level="info",
        title=f"畔色 ERP | {last_month_start.year}年{last_month_start.month}月经营简报",
    )
    return {
        "period": f"{last_month_start} ~ {last_month_end}",
        "revenue": float(revenue),
        "net_profit": float(net),
        "order_count": s.order_count,
    }


def _job_factory_daily_summary(db: Session) -> dict:
    """每天 18:00: 汇总已付款但无工厂单的订单, 推送生产通知。"""
    from app.services import factory_summary_service
    return factory_summary_service.daily_summary(db)


def _job_notify_retry(db: Session) -> dict:
    """每 30 分钟: 重发失败的飞书/webhook 通知 (指数退避, 最多 5 次)。"""
    from app.services import automation_pipeline_service, notify_service

    return {
        "default": notify_service.retry_pending(db),
        "critical_feishu": automation_pipeline_service.retry_pending_notifications(db),
    }


def _job_thumb_cache_cleanup(db: Session) -> dict:
    """每月 1 日 04:00: 清理 90 天未更新的图库缩略图缓存 (只增不减会吃满磁盘)。"""
    import time
    from pathlib import Path
    cache = Path("/app/storage/gallery_thumbs")
    if not cache.exists():
        return {"deleted": 0}
    cutoff = time.time() - 90 * 86400
    deleted = 0
    for f in cache.glob("*.webp"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            continue
    return {"deleted": deleted}


def _job_gallery_thumb_warm(db: Session) -> dict:
    """图库缩略图预热 (用户 2026-06-25: 全部先跑完直接秒开, 新图自动补)。

    增量幂等: 给缺缓存的图生成 缩略(320)+预览(1280) 两个尺寸, 已缓存跳过(纯 stat, 快)。每轮预算
    GALLERY_WARM_BUDGET(默认 800, 跨两尺寸合计), 多轮累计跑完全量, 稳态近空跑。白天每小时跑
    (cron hour=7-22), 避开夜间盘休眠。复用 gallery._compressed(限并发信号量), 不压垮弱 CPU NAS。
    新图(丢文件夹/上传)下轮自动补。试生成但全失败(存储只读/编码器坏)→ 抛错触发"连续失败"告警, 不静默。
    """
    import os as _os
    from app.services import gallery_warm_service
    budget = int(_os.environ.get("GALLERY_WARM_BUDGET", "800"))
    res = gallery_warm_service.warm_thumbnails(max_new=budget)
    if res.get("attempted", 0) > 0 and res.get("generated", 0) == 0:
        raise RuntimeError(f"图库预热: {res.get('attempted')} 张全部生成失败(疑似存储只读/编码器坏) {res}")
    return res


def _job_recon_snapshot(db: Session) -> dict:
    """每天 23:30: 留存当日对账结果快照 (看差异是在收敛还是恶化 — 对账建议 13)。"""
    from datetime import date as _date
    from decimal import Decimal

    from sqlalchemy import text as _sql

    from app.services import reconciliation_service
    results = reconciliation_service.run_all(db, record_exceptions=False)
    today = _date.today()
    for rule, r in results.items():
        total_abs = sum((abs(d.diff) for d in r.diffs if d.diff is not None), Decimal("0"))
        db.execute(_sql(
            "INSERT INTO recon_snapshots (snap_date, rule, ok_count, warning_count, error_count, total_diff_abs) "
            "VALUES (:d, :r, :ok, :w, :e, :t) "
            "ON CONFLICT (snap_date, rule) DO UPDATE SET ok_count=:ok, warning_count=:w, "
            "error_count=:e, total_diff_abs=:t"
        ), {"d": today, "r": rule, "ok": r.ok_count, "w": r.warning_count,
            "e": r.error_count, "t": total_abs})
    db.commit()

    # 做平金额警戒线 (用户拍板 2026-06-11 建议5): 累计做平超阈值 → 飞书提醒
    try:
        from app.models.exception import DataException
        from app.services import alert_service, settings_service
        rows = db.query(DataException).filter(
            DataException.source_table == "reconciliation",
            DataException.exception_type == "reconciliation_diff",
            DataException.status == "ignored",
        ).all()
        total_wo = Decimal("0")
        for r2 in rows:
            try:
                total_wo += abs(Decimal(str((r2.context or {}).get("diff", "0"))))
            except Exception:
                continue
        raw_th = settings_service.get(db, "writeoff_alert_threshold", env_fallback=False)
        threshold = Decimal(str(raw_th)) if raw_th else Decimal("50000")
        if total_wo > threshold:
            alert_service.upsert(
                db, kind="writeoff_excess", severity="critical",
                title="对账做平金额超警戒线",
                body=(f"累计人工做平 {len(rows)} 条差异, 金额合计 ¥{int(total_wo):,}, "
                      f"已超警戒线 ¥{int(threshold):,}。做平越多越可能掩盖系统性账务问题, "
                      "请到 财务→对账 复查 (明细见 工具→修改档案 搜「做平」)。"),
                dedupe_key="writeoff_excess",
            )
            db.commit()
    except Exception:  # pragma: no cover - 警戒检查失败不影响快照
        pass
    return {"rules": len(results)}


def _job_orders_maintain(db: Session) -> dict:
    """每天 02:30 订单数据自动维护 (用户拍板: 原订单页三个手动按钮收掉, 改全自动):
    反推理论成本(只补空) + 规范化订单状态 + 生成订单细节(增量)。全部幂等。"""
    from app.services import order_cost_service, order_detail_service, order_service
    cost = order_cost_service.recompute_all(db, only_missing=True)
    status = order_service.normalize_all_statuses(db)
    details = order_detail_service.generate(db, order_nos=None, only_missing=True)
    db.commit()
    return {
        "costs": cost, "statuses": status,
        "details_matched": getattr(details, "orders_matched", None),
    }


def _job_web_agent_daily(db: Session) -> dict:
    """每天 18:00: Web-Agent 订单专用取数编排。

    串行触发到期任务 → 等 job 完成 → 扫共享目录导入 → 飞书日报。
    Agent 离线/待人工的任务快速失败并标记, 不无限重试 (交接方案 §7.6)。
    """
    from app.services import agent_ingest_service, alert_service
    run_date = date.today()
    if agent_ingest_service.is_running():
        return {"skipped": "已有编排在跑 (手动触发未结束)"}
    # The 18:00 run must refresh orders after 18:00 even when a manual pull
    # happened earlier that day. Other report types keep their own intervals.
    r = agent_ingest_service.orchestrate(db, force_orders=True, orders_only=True)
    # #15: agent 离线/任务出错 → 明确告警(不再静默显示"正常"); 成功则消掉旧告警。
    offline = r.get("agent_offline")
    failed = [t.get("task", "") for t in (r.get("tasks") or []) if t.get("status") == "error"]
    pending_manual = [t.get("task", "") for t in (r.get("pending_manual") or [])]
    if offline or failed or pending_manual:
        alert_service.upsert(
            db, kind="web_agent_pull_failed", severity="warn",
            title="订单自动取数异常 (PC Agent 离线/任务出错)",
            body=("PC 取数 Agent 连不上或部分任务报错, 订单/余额可能没更新。"
                  + (f" Agent 离线: {str(offline)[:100]}" if offline else "")
                  + (f" 失败任务: {','.join(failed)}" if failed else "")
                  + (f" 需人工登录: {','.join(pending_manual)}" if pending_manual else "")
                  + " 请确认 PC 开着且 Panse-Web-Agent 在运行。"),
            dedupe_key="web_agent_pull_failed", related_url="/admin",
        )
        db.flush()
    else:
        try:
            alert_service.resolve_by_dedupe(db, "web_agent_pull_failed")
            db.flush()
        except Exception:  # pragma: no cover
            pass
    if offline:
        r["_run_status"] = "fail"
        r["_error"] = f"PC Web-Agent 离线: {str(offline)[:300]}"
    elif pending_manual:
        r["_run_status"] = "fail"
        reasons = "; ".join(
            f"{item.get('task', '')}:{item.get('reason', '')}"
            for item in (r.get("pending_manual") or [])
        )
        r["_error"] = f"取数任务需要重新登录或人工处理: {reasons[:500]}"
    elif failed:
        r["_run_status"] = "fail"
        details = [
            f"{item.get('task')}: {str(item.get('error') or '未返回原因')[:180]}"
            for item in (r.get("tasks") or [])
            if item.get("status") == "error"
        ]
        r["_error"] = "取数任务失败: " + "; ".join(details)
    elif (r.get("ingest") or {}).get("pending"):
        pending_files = [f.get("path", "") for f in (r.get("ingest", {}).get("files") or [])
                         if f.get("status") in ("pending_password", "pending")]
        r["_run_status"] = "fail"
        r["_error"] = f"取数文件待处理 {r['ingest']['pending']} 份: {','.join(pending_files)}"
    elif not agent_ingest_service.order_data_fresh(db, on=run_date, not_before_hour=18):
        r["_run_status"] = "fail"
        r["_error"] = "取数流程结束但没有产生18:00后的淘宝订单刷新时间"
    return r


def _today_retry_slots(times: tuple[tuple[int, int], ...]) -> list[datetime]:
    """把当天固定时分转换为本地时区时间，供失败通知展示和收口判断。"""
    now = datetime.now().astimezone()
    return [
        now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for hour, minute in times
    ]


_ORDER_RETRY_TIMES = ((19, 17), (20, 17), (21, 17))
_FINANCE_RETRY_TIMES = ((21, 0), (21, 30), (22, 0))


def _record_pipeline_result(
    db: Session,
    pipeline: str,
    result: dict,
    *,
    retry_times: tuple[tuple[int, int], ...],
) -> dict:
    """按业务结果记录成功/失败；失败通知由持久队列保证后续可补发。"""
    from app.services import automation_pipeline_service

    if result.get("_run_status") == "fail":
        event = automation_pipeline_service.record_failure(
            db,
            pipeline,
            str(result.get("_error") or "任务未返回明确原因"),
            retry_slots=_today_retry_slots(retry_times),
            max_failures=1 + len(retry_times),
        )
    else:
        event = automation_pipeline_service.record_success(
            db,
            pipeline,
            success_detail=result.get("_success_message"),
        )
    result["automation_pipeline"] = event
    return result


def _finance_outcomes(db: Session, result: dict) -> dict[str, tuple[bool, str]]:
    """把一次财务编排拆成余额、流水两条可独立重试和告警的结果。"""
    import json

    from sqlalchemy import select

    from app.models.finance import AccountBalance
    from app.services import agent_ingest_service, settings_service

    ok_status = {"done", "ok", "success"}
    task_status = {
        str(item.get("task") or ""): str(item.get("status") or "").lower()
        for item in (result.get("tasks") or [])
    }
    pending = {
        str(item.get("task") or ""): str(item.get("reason") or "")
        for item in (result.get("pending_manual") or [])
    }
    offline = result.get("agent_offline")
    ingest = result.get("ingest") or {}

    balance_reasons: list[str] = []
    flow_reasons: list[str] = []
    if offline:
        balance_reasons.append("PC Web-Agent离线")
        flow_reasons.append("PC Web-Agent离线")

    api_balance = result.get("alipay_balance")
    if not isinstance(api_balance, list) or not api_balance:
        balance_reasons.append("企业号余额接口未返回有效结果")
    elif any(item.get("error") for item in api_balance if isinstance(item, dict)):
        balance_reasons.append("企业号余额接口失败")

    balance_tasks = {
        "bal_taobao_aggregate",
        "bal_ads",
        "bal_wanshifu",
        "bal_alipay_main",
    }
    missing_balance = sorted(
        task for task in balance_tasks
        if task_status.get(task) not in ok_status
    )
    if missing_balance:
        detail = [
            f"{task}({pending.get(task) or task_status.get(task) or '未执行'})"
            for task in missing_balance
        ]
        balance_reasons.append("余额任务未完成: " + ",".join(detail))

    today = date.today()
    expected_accounts = {
        "支付宝-企业账号",
        "淘宝聚合账户",
        "淘宝推广账户",
        "万师傅",
        "主力号",
    }
    fresh_accounts = set(
        db.execute(
            select(AccountBalance.account_name).where(
                AccountBalance.as_of_date == today,
                AccountBalance.account_name.in_(expected_accounts),
            )
        ).scalars().all()
    )
    stale_accounts = sorted(expected_accounts - fresh_accounts)
    if stale_accounts:
        balance_reasons.append("余额日期未更新: " + ",".join(stale_accounts))

    daily_flow = result.get("alipay_daily")
    if not isinstance(daily_flow, dict) or daily_flow.get("error"):
        flow_reasons.append("企业号流水接口失败")
    elif daily_flow.get("skip"):
        flow_reasons.append("企业号流水账户未配置")
    elif int(daily_flow.get("fail") or 0) > 0:
        flow_reasons.append("企业号流水存在拉取失败日期")

    main_status = task_status.get(agent_ingest_service.MAIN_ALIPAY_FLOW_TASK)
    if main_status not in ok_status:
        main_reason = (
            pending.get(agent_ingest_service.MAIN_ALIPAY_FLOW_TASK)
            or main_status
            or "未执行"
        )
        scan_result = agent_ingest_service.get_scan_results(db).get(
            agent_ingest_service.MAIN_ALIPAY_FLOW_TASK,
        ) or {}
        try:
            scan_at = datetime.fromisoformat(str(scan_result.get("at") or ""))
            if (scan_result.get("status") == "failed"
                    and scan_at.date() == today
                    and scan_result.get("reason")):
                main_reason = f"扫码续跑失败: {scan_result['reason']}"
        except (TypeError, ValueError):
            pass
        flow_reasons.append(
            "主力号流水未完成: " + main_reason
        )
    try:
        state = json.loads(
            settings_service.get(
                db, agent_ingest_service.KEY_STATE, env_fallback=False
            ) or "{}"
        )
        main_at = datetime.fromisoformat(
            str(state.get(agent_ingest_service.STATE_MAIN_ALIPAY_FLOW) or "")
        )
        main_fresh = main_at.date() == today
    except (TypeError, ValueError, json.JSONDecodeError):
        main_fresh = False
    if not main_fresh:
        flow_reasons.append("主力号流水没有今日成功标记")

    for item in (ingest.get("files") or []):
        if item.get("status") != "error":
            continue
        if item.get("category") == "balance":
            balance_reasons.append("余额文件入库失败")
        elif item.get("category") == "alipay":
            flow_reasons.append("支付宝流水文件入库失败")

    return {
        "balance_pull": (
            not balance_reasons,
            "；".join(dict.fromkeys(balance_reasons)) or "余额数据已按今日口径更新",
        ),
        "flow_pull": (
            not flow_reasons,
            "；".join(dict.fromkeys(flow_reasons)) or "企业号与主力号流水均已完成",
        ),
    }


def _fresh_finance_browser_tasks(db: Session) -> set[str]:
    """Return browser tasks whose current-day artifacts are already in ERP."""
    import json

    from sqlalchemy import select

    from app.models.finance import AccountBalance
    from app.services import agent_ingest_service, settings_service

    today = date.today()
    account_to_task = {
        "淘宝聚合账户": "bal_taobao_aggregate",
        "淘宝推广账户": "bal_ads",
        "万师傅": "bal_wanshifu",
        "主力号": "bal_alipay_main",
    }
    fresh_accounts = set(
        db.execute(
            select(AccountBalance.account_name).where(
                AccountBalance.as_of_date == today,
                AccountBalance.account_name.in_(account_to_task),
            )
        ).scalars().all()
    )
    tasks = {account_to_task[name] for name in fresh_accounts}
    try:
        state = json.loads(
            settings_service.get(
                db, agent_ingest_service.KEY_STATE, env_fallback=False
            ) or "{}"
        )
        main_at = datetime.fromisoformat(
            str(state.get(agent_ingest_service.STATE_MAIN_ALIPAY_FLOW) or "")
        )
        if main_at.date() == today:
            tasks.add(agent_ingest_service.MAIN_ALIPAY_FLOW_TASK)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return tasks


def _reconcile_finance_success_from_persisted_evidence(db: Session) -> dict:
    """在启动下一次浏览器重试前，用已落库证据关闭旧失败状态。

    这覆盖“扫码线程已经产出并入库，但进程在写 pipeline success 前重启”的窄窗；
    没有逐任务成功证据时绝不猜测成功。
    """
    from sqlalchemy import select

    from app.models.finance import AccountBalance
    from app.services import agent_ingest_service, automation_pipeline_service

    today = date.today()
    closed: list[str] = []
    fresh_tasks = _fresh_finance_browser_tasks(db)
    balance_tasks = {
        "bal_taobao_aggregate", "bal_ads", "bal_wanshifu", "bal_alipay_main",
    }
    enterprise_fresh = db.execute(
        select(AccountBalance.id).where(
            AccountBalance.account_name == "支付宝-企业账号",
            AccountBalance.as_of_date == today,
        ).limit(1)
    ).scalar_one_or_none() is not None
    if enterprise_fresh and balance_tasks.issubset(fresh_tasks):
        automation_pipeline_service.record_success(
            db, "balance_pull", success_detail="今日全部账户余额已落库",
        )
        closed.append("balance_pull")

    scan_result = agent_ingest_service.get_scan_results(db).get(
        agent_ingest_service.MAIN_ALIPAY_FLOW_TASK,
    ) or {}
    try:
        scan_at = datetime.fromisoformat(str(scan_result.get("at") or ""))
        scan_success = (
            scan_result.get("status") == "success" and scan_at.date() == today
        )
    except (TypeError, ValueError):
        scan_success = False
    if (scan_success
            and agent_ingest_service.MAIN_ALIPAY_FLOW_TASK in fresh_tasks):
        automation_pipeline_service.record_success(
            db, "flow_pull", success_detail="扫码后的主力号流水成功证据已落库",
        )
        closed.append("flow_pull")
    if closed:
        db.commit()
    return {"closed": closed}


def _run_finance_pipeline(db: Session, *, retry: bool = False) -> dict:
    """强制跑当天余额和流水，并将两条链路分别收口。"""
    from app.services import agent_ingest_service, alert_service

    if agent_ingest_service.is_running():
        result = {
            "skipped": "已有编排在跑",
            "_run_status": "fail",
            "_error": "已有其他取数编排占用，财务任务未执行",
        }
        result["balance_pipeline"] = _record_pipeline_result(
            db, "balance_pull", dict(result), retry_times=_FINANCE_RETRY_TIMES
        )["automation_pipeline"]
        result["flow_pipeline"] = _record_pipeline_result(
            db, "flow_pull", dict(result), retry_times=_FINANCE_RETRY_TIMES
        )["automation_pipeline"]
        return result

    fresh_tasks = _fresh_finance_browser_tasks(db) if retry else set()
    r = agent_ingest_service.orchestrate(
        db,
        force_orders=False,
        force_finance=True,
        orders_only=False,
        quiet=True,
        skip_tasks=fresh_tasks,
    )
    if fresh_tasks:
        r.setdefault("tasks", []).extend(
            {"task": task_id, "status": "success", "fresh_reused": True}
            for task_id in sorted(fresh_tasks)
        )
    outcomes = _finance_outcomes(db, r)
    failures: list[str] = []
    for pipeline, (ok, detail) in outcomes.items():
        pipeline_result = (
            {"_run_status": "ok"}
            if ok
            else {"_run_status": "fail", "_error": detail}
        )
        r[f"{pipeline}_pipeline"] = _record_pipeline_result(
            db, pipeline, pipeline_result, retry_times=_FINANCE_RETRY_TIMES
        )["automation_pipeline"]
        if not ok:
            failures.append(f"{pipeline}: {detail}")

    if failures:
        detail = "；".join(failures)
        alert_service.upsert(
            db,
            kind="finance_agent_pull_failed",
            severity="warn",
            title="财务自动取数异常",
            body=detail,
            dedupe_key="finance_agent_pull_failed",
            related_url="/finance",
        )
        db.flush()
        r["_finance_status"] = "needs_attention"
        r["_finance_error"] = detail
        r["_run_status"] = "fail"
        r["_error"] = detail
    else:
        try:
            alert_service.resolve_by_dedupe(db, "finance_agent_pull_failed")
            db.flush()
        except Exception:  # pragma: no cover
            pass
        r["_finance_status"] = "ok"
    return r


def _job_web_agent_finance(db: Session) -> dict:
    """每天 20:30: 强制刷新余额和流水；两条链路分别失败告警和有限重试。"""
    return _run_finance_pipeline(db)


def _job_web_agent_finance_retry(db: Session) -> dict:
    """21:00/21:30/22:00: 只要余额或流水尚未成功，就整轮幂等补跑。"""
    from app.services import automation_pipeline_service

    reconciled = _reconcile_finance_success_from_persisted_evidence(db)
    pending = [
        pipeline
        for pipeline in ("balance_pull", "flow_pull")
        if automation_pipeline_service.needs_retry(db, pipeline)
    ]
    if not pending:
        return {
            "skipped": "finance_pipeline_closed",
            "_run_status": "skipped",
            "reconciled": reconciled["closed"],
        }
    result = _run_finance_pipeline(db, retry=True)
    result["retry_for"] = pending
    result["reconciled_before_retry"] = reconciled["closed"]
    return result


def _job_critical_automation_final(db: Session) -> dict:
    """22:20: 防重启/错过班次兜底，给仍未闭环的链路发送“今日失败”。"""
    from app.services import automation_pipeline_service

    return automation_pipeline_service.finalize_open_failures(db)


def _job_ingest_scan(db: Session) -> dict:
    """#15 每小时扫共享目录导入 PC 自跑下载的报表(不开浏览器、不驱动 agent)。
    解决"只靠 18:00 编排"的脆弱: PC 自己下载的文件每小时自动进库。幂等(file_hash 防重)。

    导入全部成功(无错误 + 无平台需扫码) → 立刻补生成下单图 (用户拍板 2026-06-17);
    有报错/需扫码 → 跳过, 等重新扫码全部成功后再生成。与 orchestrate 路径同一口径。
    generate_pending 幂等(已生成的不重复, 正常新单仍按 18:30 推飞书); 若新备注命中远期/激活词,
    则本次导入立即完成旧工厂号作废/远期编号/飞书通知或激活重排。"""
    from app.services import agent_ingest_service, web_agent_service
    ing = agent_ingest_service.run_ingest(db)
    try:
        hb = web_agent_service.health(db)
        tasks = (web_agent_service.list_tasks(db).get("tasks") or []) if hb.get("online") else []
        need_scan = [t for t in tasks
                     if not t.get("has_session") and not agent_ingest_service.SKIPPED_TASKS.get(t.get("id"))]
        if ing.get("errors", 0) == 0 and not need_scan and ing.get("imported", 0) > 0:
            from app.services import order_sheet_archive_service, order_sync_service
            # 生成下单图前先把编码回填: sku_code→product_code (孚格PFG单导入时无编码) + 标题→编码。
            # 否则下单图按 product_code 查图库/产品总表/配图全落空 → 无产品图 (用户实测 2026-06-18)。
            order_sync_service.backfill_product_code(db)
            order_sync_service.backfill_code_from_taobao_title(db)
            db.commit()
            remote = order_sheet_archive_service.void_remote_pushed(db)
            order_sheet_archive_service.repush_activated(db)
            order_sheet_archive_service.assign_remote_seqs(db)
            ing["remote_voided"] = remote["voided_remote"]
            ing["remote_transitions"] = remote["remote_transitions"]
            ing["remote_feishu_notified"] = remote["feishu_notified"]
            ing["remote_feishu_failed"] = remote["feishu_failed"]
            if remote["feishu_failed"]:
                details = "; ".join(
                    f"{x.get('order_no')}: {x.get('reason')}" for x in remote["feishu_failed"])
                ing["_run_status"] = "fail"
                ing["_error"] = f"远期改单已处理，但飞书作废通知失败: {details}"
            ing["order_sheets"] = order_sheet_archive_service.generate_pending(db)
        elif need_scan:
            ing["order_sheets"] = {"skipped": "有平台需重新扫码, 等全部成功后再生成下单图"}
    except Exception:  # noqa: BLE001
        pass
    return ing


def _sync_factory_dispatch_after_orders(db: Session, result: dict) -> dict:
    """订单数据和下单图收口后，立即刷新飞书系统下单表。

    首次失败直接并入 order_delivery 自动化失败原因，因此会按既有
    19:17 / 20:17 / 21:17 续跑并在飞书告知具体失败阶段。
    """
    from app.services import factory_dispatch_feishu_service

    synced = factory_dispatch_feishu_service.sync_if_enabled(db)
    result["factory_dispatch"] = synced
    if not synced.get("ok"):
        detail = "; ".join(str(x) for x in (synced.get("errors") or [])[:5])
        previous = str(result.get("_error") or "").strip()
        result["_run_status"] = "fail"
        result["_error"] = (
            f"{previous}；" if previous else ""
        ) + f"飞书系统下单表同步失败: {detail or '未知原因'}"
    return result


def _job_order_sheets_daily(db: Session) -> dict:
    """每天 18:00: 给已付款新订单补生成下单图 → 存导入档案 → 推飞书群 (用户拍板)。
    新鲜度门(2026-07-07): 今日订单未取数成功→暂缓推送, 防隔夜旧数据把已关闭单误推;
    等 pull_catchup 把 PC 取数补上、数据新鲜后再推。"""
    from app.services import agent_ingest_service, order_sheet_archive_service
    if not agent_ingest_service.order_data_fresh(db, not_before_hour=18):
        return _record_pipeline_result(
            db,
            "order_delivery",
            {
                "skipped": "stale_order_data",
                "note": "18:00后订单未取数, 暂缓推送(防旧数据误推)",
                "_run_status": "fail",
                "_error": "18:00后淘宝订单数据未刷新，未执行飞书推送",
            },
            retry_times=_ORDER_RETRY_TIMES,
        )
    result = order_sheet_archive_service.push_daily(db)
    result["_success_message"] = agent_ingest_service.format_order_change_message(
        agent_ingest_service.latest_order_pull_result(db).get("changes")
    )
    failed = int(result.get("images_failed") or 0)
    remaining = int(result.get("images_remaining") or 0)
    held_no_sku = result.get("held_no_sku") or []
    held_no_address = result.get("held_no_address") or []
    remote_notify_failed = result.get("remote_feishu_failed") or []
    reason = result.get("push_reason")
    if remote_notify_failed:
        details = "; ".join(f"{x.get('order_no')}: {x.get('reason')}" for x in remote_notify_failed)
        result["_run_status"] = "fail"
        result["_error"] = f"远期改单已处理，但飞书作废通知失败: {details}"
    elif reason in ("no_chat_id", "notify_disabled"):
        result["_run_status"] = "fail"
        result["_error"] = f"飞书推送不可用: {reason}"
    elif failed:
        result["_run_status"] = "fail"
        result["_error"] = f"飞书下单图发送失败 {failed} 张: {','.join(result.get('failed_order_nos') or [])}"
    elif held_no_sku:
        result["_run_status"] = "fail"
        result["_error"] = f"{len(held_no_sku)} 笔订单因SKU未回填暂缓推送: {','.join(held_no_sku)}"
    elif held_no_address:
        result["deferred_status"] = "address_masked"
        result["_success_message"] = (
            f"订单推送流程执行正常；{len(held_no_address)} 笔订单因淘宝地址脱敏暂缓，"
            f"地址完整后自动补推：" + ",".join(held_no_address)
        )
    elif remaining:
        result["_run_status"] = "fail"
        result["_error"] = f"仍有 {remaining} 张可推下单图未发送"
    _sync_factory_dispatch_after_orders(db, result)
    return _record_pipeline_result(
        db,
        "order_delivery",
        result,
        retry_times=_ORDER_RETRY_TIMES,
    )


def _job_order_sheets_catchup(db: Session) -> dict:
    """每小时: 导入新订单后补生成下单图 + 静默自愈补推飞书 (单张图即时到工厂群)。

    修复"三天两头坏"(用户 2026-06-26): 原来只生成不推, 推送只在 18:00 跑一次 ——
    与 18:00 的订单拉取撞车、或 api 重启误过 18:00 → 当天订单整天不进工厂群。
    改成每小时补推 (quiet: 不发 ZIP/无地址提醒, 那两样留 18:00 日报), 订单 1 小时内必达、自愈。
    新鲜度门(2026-07-07): 今日订单未取数成功→不生成/不推(防旧数据把已关闭单误推), 等续跑补取数。
    """
    from app.services import agent_ingest_service, order_sheet_archive_service as oss
    if not agent_ingest_service.order_data_fresh(db):
        return {"skipped": "stale_order_data"}
    remote = oss.void_remote_pushed(db)
    oss.repush_activated(db)
    oss.assign_remote_seqs(db)
    gen = oss.generate_pending(db)
    push = oss.push_pending_images(db, limit=20, include_baseline=False, quiet=True)
    result = {"generated": gen, "images_pushed": push["pushed"], "remaining": push["remaining"],
              "remote_voided": remote["voided_remote"],
              "remote_transitions": remote["remote_transitions"],
              "remote_feishu_notified": remote["feishu_notified"],
              "remote_feishu_failed": remote["feishu_failed"]}
    if remote["feishu_failed"]:
        details = "; ".join(f"{x.get('order_no')}: {x.get('reason')}" for x in remote["feishu_failed"])
        result["_run_status"] = "fail"
        result["_error"] = f"远期改单已处理，但飞书作废通知失败: {details}"
    return result


def _now_hour() -> int:
    """当前小时(0-23); 抽成函数便于测试 monkeypatch。"""
    from datetime import datetime as _dt
    return _dt.now().hour


def _job_pull_catchup(db: Session) -> dict:
    """每小时(仅19:00后至23:00): PC上线后续跑当天没完成的取数+推送 (用户 2026-07-07/08、2026-07-23)。
    背景: 订单取数每日仅 18:00 一次; 那会儿 PC 关机/重启(如17:30重启)→ 当天订单整天不刷新。
    机制(2026-07-23 收窄): **只在每日定时点(18:00)失败约1小时后**才补; 今日订单未刷新时每小时
    探测 PC 在线即重跑编排补取数, 取数成功(数据新鲜)后立即补推(含远期老单激活重推)+飞书告知一次;
    **成功即停**(下轮见新鲜→already_fresh 不再动), PC 仍离线则静默等下一轮(只失败才不停重试)。"""
    from app.services import (
        agent_ingest_service as ai,
        automation_pipeline_service,
        web_agent_service,
    )
    if not (18 <= _now_hour() < 23):
        return {"skipped": "off_window"}
    pipeline = automation_pipeline_service.get_pipeline(db, "order_delivery")
    if pipeline.get("success"):
        return {"skipped": "order_pipeline_closed", "_run_status": "skipped"}

    def _finish(result: dict) -> dict:
        held_no_address = result.get("held_no_address") or []
        if result.get("_run_status") != "fail" and held_no_address:
            result["deferred_status"] = "address_masked"
            result["_success_message"] = (
                f"订单推送流程执行正常；{len(held_no_address)} 笔订单因淘宝地址脱敏暂缓，"
                f"地址完整后自动补推：" + ",".join(held_no_address)
            )
        if result.get("_run_status") != "fail" and not result.get("_success_message"):
            last_pull = ai.latest_order_pull_result(db)
            result["_success_message"] = (
                last_pull.get("message") or "没有新增订单"
            )
        if result.get("_run_status") != "fail":
            _sync_factory_dispatch_after_orders(db, result)
        return _record_pipeline_result(
            db,
            "order_delivery",
            result,
            retry_times=_ORDER_RETRY_TIMES,
        )

    if ai.order_data_fresh(db, not_before_hour=18):
        # 数据新鲜不等于图片已送达。即使口令回调或 18:30 日报在发送阶段中断，
        # 每小时补跑仍用 pushed 幂等标记收口，不重复发已成功的图片。
        from app.services import order_sheet_archive_service as oss
        delivery = oss.reconcile_pending_delivery(db, limit=50, quiet=True)
        delivery["ok"] = "fresh_delivery_reconciled"
        return _finish(delivery)
    # ``final`` 只表示旧一轮重试已用尽，不得盖过后来由扫码/口令形成的
    # 成功证据。上面的新鲜度收口优先；确认仍旧失败后才停止无效重试。
    if pipeline.get("final"):
        return {"skipped": "order_pipeline_closed", "_run_status": "skipped"}
    if ai.is_running():
        return _finish({
            "skipped": "orchestrate_running",
            "_run_status": "fail",
            "_error": "已有其他取数编排占用，订单补跑未执行",
        })
    pending_password = ai.pending_shipping_password_files(db)
    if pending_password:
        password_result = ai.get_shipping_password_result(db)
        mismatch_reason = ""
        try:
            password_at = datetime.fromisoformat(
                str(password_result.get("at") or "")
            )
            if (password_result.get("status") == "password_mismatch"
                    and password_at.astimezone().date() == date.today()):
                mismatch_reason = str(password_result.get("reason") or "")
        except (TypeError, ValueError):
            pass
        if mismatch_reason:
            error = (
                "已收到口令，但未能解密待处理发货报表："
                + mismatch_reason
                + "；无新口令前已暂停自动重试"
            )
        else:
            error = (
                f"加密发货报表待飞书口令 {len(pending_password)} 份: "
                + ",".join(pending_password[:5])
                + "；请转发“发货密码 xxxx”，收到后自动解密，下个小时继续"
            )
        return _finish({
            "waiting": "shipping_password",
            "files": pending_password,
            "_run_status": "fail",
            "_error": error,
        })
    if not web_agent_service.health(db).get("online"):
        return _finish({
            "waiting": "pc_offline",
            "_run_status": "fail",
            "_error": "PC Web-Agent 离线，未执行订单取数；下个小时自动重试",
        })
    res = ai.orchestrate(db, quiet=True, force_orders=True, orders_only=True)  # 强制补18:00后的订单快照
    out = {"ran_orchestrate": True, "tasks": len(res.get("tasks", [])),
           "pending_manual": len(res.get("pending_manual", []))}
    if res.get("order_changes") is not None:
        out["order_changes"] = res["order_changes"]
        out["_success_message"] = res.get("order_message")
    if res.get("already_running"):
        return _finish({
            **out,
            "skipped": "orchestrate_running",
            "_run_status": "fail",
            "_error": "订单取数编排被其他任务占用，未完成补跑",
        })
    if ai.order_data_fresh(db, not_before_hour=18):  # 取数成功→数据新鲜→立即补生成+补推
        from app.services import order_sheet_archive_service as oss
        delivery = oss.reconcile_pending_delivery(db, limit=50, quiet=True)
        out.update(delivery)
        try:
            from app.services import notify_service
            notify_service.notify(
                db, f"✅ PC 已上线, 自动补取数完成并补推下单图 {delivery['images_pushed']} 张 (漏取数续跑)。",
                level="info", title="畔色 ERP [取数续跑]")
        except Exception:  # pragma: no cover
            pass
    else:
        out["still_stale"] = True                 # 需扫码/导出失败 → 数据仍陈旧, 下轮再试
        ingest = res.get("ingest") or {}
        reasons: list[str] = []
        if res.get("agent_offline"):
            reasons.append(f"PC Web-Agent 离线: {str(res['agent_offline'])[:160]}")
        if res.get("pending_manual"):
            reasons.append("需人工登录: " + ",".join(
                str(x.get("task") or "?") for x in res["pending_manual"]))
        failed_tasks = [
            item for item in (res.get("tasks") or [])
            if str(item.get("status") or "").lower() in ("error", "failed", "timeout")
        ]
        if failed_tasks:
            reasons.append("取数任务失败: " + "; ".join(
                f"{item.get('task')}: {str(item.get('error') or '未返回原因')[:180]}"
                for item in failed_tasks
            ))
        if ingest.get("pending"):
            pending_files = [
                f"{x.get('path', '?')}({x.get('status', 'pending')})"
                for x in (ingest.get("files") or [])
                if x.get("status") in ("pending", "pending_password")
            ]
            reasons.append(
                f"待处理文件 {ingest['pending']} 份"
                + (f": {','.join(pending_files[:5])}" if pending_files else ""))
        persistent_password = ingest.get("pending_password_files") or []
        if persistent_password:
            reasons.append(
                f"加密发货报表待飞书口令 {len(persistent_password)} 份: "
                + ",".join(str(x) for x in persistent_password[:5]))
        if ingest.get("errors"):
            error_files = [
                str(x.get("path") or "?")
                for x in (ingest.get("files") or [])
                if x.get("status") == "error"
            ]
            reasons.append(
                f"订单导入失败 {ingest['errors']} 份"
                + (f": {','.join(error_files[:5])}" if error_files else ""))
        if not reasons:
            reasons.append("订单取数结束，但未生成18:00后的三报表完整标记")
        out["_run_status"] = "fail"
        out["_error"] = "; ".join(reasons)
    return _finish(out)


def _job_void_sheets(db: Session) -> dict:
    """每天 10:00: 检查退款订单 (付款+已生成下单图+退款) → 作废图+删原图+推送。"""
    from app.services import order_sheet_archive_service
    return order_sheet_archive_service.push_void_daily(db)


def _job_aftersales_auto(db: Session) -> dict:
    """每天 09:00: 售后自动建条 (万师傅/支付宝流水/退款) + 日报。"""
    from app.services import aftersales_auto_service
    return aftersales_auto_service.run_daily(db)


def _job_aftersales_followup(db: Session) -> dict:
    """每天 14:00: 检查售后超时未处理记录, 按原因推送智能建议。"""
    from app.services import aftersales_followup_service
    return aftersales_followup_service.check_and_push(db)


def _job_flip_monitor(db: Session) -> dict:
    """灰度监控 (2026-07-09): 扫仍在"翻烧饼"(实付/状态被重导反复横跳)的订单记异常 + 复核销账已稳定的。
    部署幂等导入修复后盯一周: 异常页看 order_import_flip —— 修好则逐日清空, 仍翻的留人工定夺。"""
    from app.services import exception_recheck_service, import_flip_monitor_service
    scanned = import_flip_monitor_service.scan(db)
    closed = exception_recheck_service.bulk_close_resolved(db, types=["order_import_flip"])
    return {"scan": scanned, "closed": closed.get("order_import_flip", 0)}


def _job_weekly_purchase_remind(db: Session) -> dict:
    """每周一 09:00: 生成本周备货清单并推送。"""
    from app.services import notify_service, sales_analytics
    advice = sales_analytics.stock_advice(db)
    materials = [m for m in advice.get("materials", []) if (m.get("missing") or 0) > 0]
    materials.sort(key=lambda m: -(m.get("missing") or 0))
    top = materials[:15]
    if not top:
        return {"items_count": 0, "pushed": False}
    lines = ["📦 本周备货清单", f"共 {len(materials)} 项需要补货，以下为 Top {len(top)}：", ""]
    for m in top:
        name = m.get("material_name") or m.get("material_code") or "?"
        missing = m.get("missing") or 0
        alert_at = m.get("alert_at") or "尽快"
        lines.append(f"• {m.get('material_code', '')} {name}  缺 {missing:.0f}  建议 {alert_at} 前下单")
    notify_service.notify(db, "\n".join(lines), level="warn", title="畔色ERP | 本周备货清单")
    return {"items_count": len(top), "pushed": True}


def _job_monthly_inventory_plan(db: Session) -> dict:
    """月底推送下月成品备货数；次月 1 日同函数兜底，成功月份自动跳过。"""
    from app.services import inventory_monthly_report_service
    return inventory_monthly_report_service.send_monthly_report(db)


# 大促价保规则确认窗口。2026-07-26改为方案3加强版：
# 双11前不做SKU身份轮换；按活动实际说明确认价保期，未确认暂按19天。
_DEFAULT_ROTATION_WINDOWS = [
    {"name": "618大促", "start": "05-13", "end": "06-18"},
    {"name": "双11大促", "start": "10-10", "end": "11-11"},
]


def _rotation_windows(db: Session) -> list:
    import json
    from app.services import settings_service
    raw = settings_service.get(db, "promo_rotation_windows", env_fallback=False)
    try:
        w = json.loads(raw) if raw else _DEFAULT_ROTATION_WINDOWS
        return w if isinstance(w, list) and w else _DEFAULT_ROTATION_WINDOWS
    except (ValueError, TypeError):
        return _DEFAULT_ROTATION_WINDOWS


def _active_rotation_window(today: date, windows: list) -> Optional[dict]:
    """今天是否落在某个大促轮换窗口 (MM-DD, 跨年安全)。命中返回该窗口, 否则 None。"""
    for w in windows or []:
        try:
            sm, sd = (int(x) for x in str(w.get("start", "")).split("-"))
            em, ed = (int(x) for x in str(w.get("end", "")).split("-"))
        except (ValueError, TypeError):
            continue
        for year in (today.year - 1, today.year):
            try:
                s = date(year, sm, sd)
                e = date(year, em, ed)
            except ValueError:
                continue
            if e < s:                       # 跨年窗口 (如 12-20 ~ 01-05)
                e = date(year + 1, em, ed)
            if s <= today <= e:
                return w
    return None


def _job_promo_rotation_remind(db: Session) -> dict:
    """兼容旧任务ID：大促窗口内提醒核对价保规则，不再建议SKU轮换。"""
    from app.services import campaign_price_protection_service, notify_service, settings_service
    en = settings_service.get(db, "promo_rotation_remind_enabled", env_fallback=False)
    if en is not None and str(en).strip().lower() in ("0", "false", "off", "no"):
        return {"in_window": False, "pushed": False, "reason": "disabled"}
    w = _active_rotation_window(date.today(), _rotation_windows(db))
    if not w:
        return {"in_window": False, "pushed": False}
    name, end = w.get("name", "大促"), w.get("end", "")
    default_days = campaign_price_protection_service.DEFAULT_PRICE_PROTECTION_DAYS
    msg = (
        f"🛡️ 大促价保规则确认（{name}）\n"
        f"现在是「{name}」报名/备战期。SKU身份轮换已暂停，请不要把定制SKU改成真实规格或降价复用。\n"
        f"请把千牛本场活动的「价保说明/价保服务」页面链接发给运营负责人或 Codex，"
        f"再按页面实际期限更新活动计划；未确认前系统暂按{default_days}天冷静期。\n"
        f"（本提醒每天一次，持续到 {end}；冷静期可在活动计划中手动修改。）"
    )
    notify_service.notify(db, msg, level="warn", title="畔色ERP | 大促价保规则确认")
    return {"in_window": True, "window": name, "pushed": True}


def _job_daily_10_comprehensive_report(db: Session) -> dict:
    """每天 10:00: 综合日报 — 把所有常规检查结果合并成一条推送.

    包含: AI 简报摘要 / 对账状态 / 数据新鲜度 / 库存预警 / 未解决异常数。
    仅此一条推送, 不再单独推对账差异、数据新鲜度等子报告。
    """
    from datetime import date as _date
    from sqlalchemy import func as _func
    from app.models.exception import DataException as _DE
    from app.models.inventory import PartInventory, ProductInventory
    from app.models.daily_briefing import DailyBriefing
    from app.services import data_freshness_service, notify_service, reconciliation_service

    today = _date.today()

    # ── 1. 对账状态 ──────────────────────────────────────────────
    recon_results = reconciliation_service.run_all(db, record_exceptions=True)
    db.flush()
    rule_labels = {
        "factory_payment": "货款对账", "install_fee": "安装费", "promotion": "推广支出",
        "refill_compensation": "补单赔付", "inventory_value": "库存资产", "logistics_fee": "物流费",
        "revenue_alipay": "收入对账", "operating_expense": "经营支出", "purchase_payment": "采购付款",
    }
    recon_errors = [(rule_labels.get(n, n), r.error_count, r.warning_count)
                    for n, r in recon_results.items() if r.error_count > 0]
    recon_warnings = [(rule_labels.get(n, n), r.warning_count)
                      for n, r in recon_results.items()
                      if r.error_count == 0 and r.warning_count > 0]

    # ── 2. 未解决异常总数 ────────────────────────────────────────
    open_exc = db.query(_func.count(_DE.id)).filter(_DE.status == "open").scalar() or 0

    # ── 3. 库存预警 ──────────────────────────────────────────────
    prod_low = (db.query(_func.count(ProductInventory.id))
                .filter(ProductInventory.physical_qty <= 5).scalar() or 0)
    part_neg = (db.query(_func.count(PartInventory.id))
                .filter(PartInventory.physical_qty < 0).scalar() or 0)

    # ── 4. 数据新鲜度 ────────────────────────────────────────────
    stale_items = data_freshness_service.overdue_only(db)

    # ── 5. AI 简报摘要 (今日已生成则取摘要; 未生成则留空) ────────
    briefing_summary = ""
    try:
        b = db.query(DailyBriefing).filter(DailyBriefing.for_date == today).first()
        if b and b.content:
            # 截取前 200 字作为摘要
            briefing_summary = b.content[:200].strip()
            if len(b.content) > 200:
                briefing_summary += "…"
    except Exception as e:
        _logger.warning("日报摘要读取失败 (不影响其余日报): %s", e)

    # ── 6. 近 7/30 天销售 + TOP3 (用户 2026-07-06: 微信日报只要这几块) ──
    from app.services import sales_analytics
    w7 = sales_analytics.window_summary(db, days=7)
    w30 = sales_analytics.window_summary(db, days=30, top_n=3)

    # ── 组装消息 (用户 2026-07-06 精简: 只留 ①对账差异+未解决异常 ②近7天 ③近30天 ④销售榜TOP3) ──
    def _yuan(v):
        return f"¥{v:,.0f}"

    lines = [f"📊 畔色 ERP | {today.month}月{today.day}日 经营日报", ""]

    # ① 对账差异 + 未解决异常
    if recon_errors:
        lines.append("🚨 对账差异 (需处理)")
        for label, err, warn in recon_errors:
            w = f", 提示 {warn} 条" if warn else ""
            lines.append(f"  • {label}: 严重 {err} 条{w}")
    elif recon_warnings:
        lines.append("⚠️ 对账提示")
        for label, warn in recon_warnings:
            lines.append(f"  • {label}: 提示 {warn} 条")
    else:
        lines.append("✅ 对账: 全部规则正常")
    lines.append(f"📋 未解决异常: {open_exc} 条")
    lines.append("")

    # ②③ 近 7 天 / 近 30 天 销售(按下单日期, 排补单/退款)
    lines.append(f"📈 近7天: 销售额 {_yuan(w7['revenue'])} · {w7['order_count']} 单")
    lines.append(f"📈 近30天: 销售额 {_yuan(w30['revenue'])} · {w30['order_count']} 单")
    lines.append("")

    # ④ 销售排行榜 TOP3 (近30天, 按销售额)
    if w30["top"]:
        lines.append("🏆 销售榜 TOP3 (近30天)")
        medals = ["🥇", "🥈", "🥉"]
        for i, t in enumerate(w30["top"]):
            tag = medals[i] if i < len(medals) else f"{i + 1}."
            lines.append(f"  {tag} {t['name']} {_yuan(t['revenue'])}")
        lines.append("")
    lines.append("详情登录系统 → 首页大盘")

    level = "error" if recon_errors else ("warn" if recon_warnings else "info")
    notify_service.notify(db, "\n".join(lines), level=level,
                          title=f"畔色 ERP | {today.month}月{today.day}日 日报",
                          wechat_allowed=True)   # 经营日报=唯一放行到企微的推送(其余静默)

    return {
        "recon_errors": len(recon_errors),
        "recon_warnings": len(recon_warnings),
        "open_exceptions": open_exc,
        "product_low_stock": prod_low,
        "part_negative": part_neg,
        "stale_sources": len(stale_items),
        "briefing_included": bool(briefing_summary),
    }


def _job_data_backup(db: Session) -> dict:
    """每日 02:00 检查: 距上次自动备份满 interval_days (默认7) 才导出。

    间隔/目录/开关/起始日期均可在 后台→数据管理 配置。
    """
    from app.services import backup_service
    return backup_service.run_if_due(db)


def _job_monthly_reconcile_diagnose(db: Session) -> dict:
    """每月最后一天 20:00: 跑对账差异 AI 诊断并推送摘要。"""
    from app.services import ai_assistant, notify_service
    _log, ai_resp = ai_assistant.diagnose_reconciliation(db)
    if ai_resp and ai_resp.text:
        notify_service.notify(
            db, ai_resp.text[:1200],
            level="warn", title="畔色ERP | 月底对账差异诊断",
        )
        return {"diagnosis_len": len(ai_resp.text), "pushed": True}
    return {"diagnosis_len": 0, "pushed": False}


def _job_audit_prune(db: Session) -> dict:
    """审计日志留存清理 (优化 #9): 删除超过留存期的 audit_logs。"""
    from app.api.audit import prune_audit_logs
    return {"deleted": prune_audit_logs(db)}


def _job_monthly_financial_report(db: Session) -> dict:
    """每月 1 号推送上月财务报表 (优化 #10)。"""
    from app.services import financial_report_service, notify_service
    now = datetime.now()
    y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    summary = financial_report_service.monthly_summary(db, y, m)
    notify_service.notify(db, financial_report_service.summary_text(summary),
                          level="info", title="月度财务报表")
    return summary


def _job_refill_rederive(db: Session) -> dict:
    """补单自动打标兜底 (Plan L3): 以补单记录为准全量重判 is_refill + 重算成本."""
    from dataclasses import asdict
    from app.services import order_sync_service
    res = order_sync_service.rederive_refill_flags(db)
    return asdict(res)


def _job_factory_payment_backfill(db: Session) -> dict:
    """工厂付款回填常态化 (Plan L2).

    规则A(有流水号/付款日证据)每天实跑; 规则B(超结算期推断)默认只 dry_run 预览,
    结果进 result_summary 供观察, system_settings.factory_backfill_apply_inference
    置 1/true 后切换为实跑。
    """
    from app.services import factory_payment_service, settings_service
    raw = settings_service.get(db, "factory_backfill_apply_inference", env_fallback=False) or ""
    apply_b = str(raw).strip().lower() in ("1", "true", "on", "yes")
    res = factory_payment_service.backfill_payment_status(db, apply_settled_inference=apply_b)
    res["inference_enabled"] = apply_b
    if not apply_b:
        preview = factory_payment_service.backfill_payment_status(
            db, apply_settled_inference=True, dry_run=True)
        res["inference_dry_run_preview"] = {
            k: preview.get(k) for k in ("by_settled", "still_unpaid") if k in preview
        }
    return res


def _job_weekly_sales_report(db: Session) -> dict:
    """销售周报推送 (Plan F7): 周一 09:30 推上周摘要 (文字条形图卡片)."""
    from app.services import report_push_service
    return report_push_service.push_weekly_sales(db)


def _job_promo_price_check(db: Session) -> dict:
    """活动报名价对照 (Plan F1): 报名价 vs 定价渠道价, 超差记异常+critical."""
    from app.services import promo_price_check_service
    return promo_price_check_service.check_all(db)


def _job_campaign_discovery(db: Session) -> dict:
    """活动生命周期 P4 (spec §四/§五): 每天 18:40 (抓单编排后) WA 抓千牛营销活动列表
    → 落 CampaignCalendar → 距开始<3天飞书提醒运营报名 (同活动一天只提醒一次)。
    WA 失败 → 飞书报错「活动发现抓取失败请手动查看」。"""
    from app.services import campaign_discovery_service
    result = campaign_discovery_service.run_daily_discovery(db)
    if result.get("ok") is False:
        result["_run_status"] = "fail"
        result["_error"] = result.get("reason") or result.get("error")
    return result


def _job_campaign_auto_execute(db: Session) -> dict:
    """活动发现后自动推进：预检→单品立减→差集报名；任一安全门失败即停并飞书。"""
    from app.services import campaign_automation_service
    result = campaign_automation_service.run_auto_execute(db)
    if result.get("failed"):
        result["_run_status"] = "fail"
        result["_error"] = f"{result['failed']} 个活动计划自动执行失败"
    elif result.get("processed", 0) == 0:
        result["_run_status"] = "skipped"
    return result


def _job_campaign_price_protection_rule_remind(db: Session) -> dict:
    """未来14天活动缺价保说明链接时，飞书提醒运营提供；送达后按计划去重。"""
    from app.services import campaign_price_protection_service

    result = campaign_price_protection_service.remind_upcoming_missing_rules(db)
    if result.get("scanned", 0) == 0 or (
            result.get("sent", 0) == 0 and result.get("deduped", 0) > 0):
        result["_run_status"] = "skipped"
    return result


def _job_campaign_auto_recon(db: Session) -> dict:
    """活动生命周期 P4 (spec §四.6/§五): 每30分钟扫 status='signup_pushed' 且创建2小时内的
    活动计划 → WA 导出「活动商品导出」→ 自动核对 (>2元红榜+飞书)。
    WA 导出失败 → 飞书报错 + 计划保持 signup_pushed 等手动上传兜底。"""
    from app.services import campaign_recon_service
    result = campaign_recon_service.auto_recon_scan(db)
    if result.get("failed"):
        result["_run_status"] = "fail"
        result["_error"] = f"{result['failed']} 个活动计划核对未完成"
    elif result.get("scanned", 0) == 0:
        result["_run_status"] = "skipped"
    return result


def _job_ingest_health_check(db: Session) -> dict:
    """每天 20:00 取数体检 (用户拍板 2026-06-29): 检查今日订单/余额/导入是否真的更新了。

    背景: 18:00 编排可能"跑成功了"但没拉到新数据 (订单报表无新单 / 加密发货报表缺口令 /
    PC Agent 离线)。这类"静默没更新"光看任务状态(ok)发现不了。本任务专门核对结果是否真的前进,
    有问题就主动推 飞书 + 企业微信 (区别于只写站内 alert, 确保人能收到)。
    一天一条, 无异常则完全静默不打扰。'今日无新订单'必推 (用户明确要求)。
    """
    import json as _json
    import os as _os
    from datetime import date as _date, datetime as _dt
    from sqlalchemy import func, select
    from app.models.order import Order
    from app.services import agent_ingest_service, settings_service, web_agent_service

    today = _date.today()
    new_orders = db.execute(
        select(func.count(Order.id)).where(func.date(Order.created_at) == today)
    ).scalar() or 0
    last_order_at = db.execute(select(func.max(Order.created_at))).scalar()

    try:
        state = _json.loads(settings_service.get(db, "web_agent_state", env_fallback=False) or "{}")
    except (ValueError, TypeError):
        state = {}

    def _stale_days(key: str) -> Optional[int]:
        v = state.get(key)
        if not v:
            return None
        try:
            return (_dt.now() - _dt.fromisoformat(v)).days
        except ValueError:
            return None

    bal_stale = _stale_days("balance")

    try:
        online = bool(web_agent_service.health(db).get("online"))
    except Exception:  # noqa: BLE001 — 探活失败按离线算, 不抛
        online = False

    pending_password_files = agent_ingest_service.pending_shipping_password_files(db, on=today)

    problems: list[str] = []
    if new_orders == 0:
        lo = last_order_at.strftime("%m-%d %H:%M") if last_order_at else "无记录"
        problems.append(f"今日无新订单 (最近订单 {lo})")
    if not online:
        problems.append("PC 取数 Agent 离线 (订单/余额无法自动拉取)")
    if bal_stale is not None and bal_stale >= 4:
        problems.append(f"余额已 {bal_stale} 天未更新")
    if pending_password_files:
        problems.append(
            f"加密发货报表待口令 {len(pending_password_files)} 份 "
            "(口令不按时间失效；收到对应口令后会自动解密并续推)"
        )

    result = {
        "date": today.isoformat(),
        "new_orders_today": int(new_orders),
        "last_order_at": last_order_at.isoformat() if last_order_at else None,
        "agent_online": online,
        "balance_stale_days": bal_stale,
        "last_ingest_at": state.get("last_ingest_at"),
        "pending_shipping_password_files": pending_password_files,
        "problems": problems,
        "pushed": [],
    }
    if not problems:
        return result

    lines = [f"⚠️ {today.month}月{today.day}日 自动取数体检发现问题:", ""]
    lines += [f"• {p}" for p in problems]
    lines += ["", "请确认: 千牛是否有新单 / PC 是否开机且 Web-Agent 在跑 / 发货口令是否已转发飞书。"]
    msg = "\n".join(lines)

    if _os.environ.get("PANSE_DISABLE_NOTIFY"):
        result["pushed"] = ["disabled"]
        return result

    from app.services import notify_service
    r = notify_service.broadcast_text(db, msg, level="warn", title="畔色 ERP | 取数体检异常")
    result["pushed"] = [k for k, v in r.items() if v is True]
    return result


def _job_npd_stage_remind(db: Session) -> dict:
    """每天 09:15: 新品开发阶段截止提醒 (用户拍板 2026-06-30: 飞书催设计师推进下一步)。

    扫所有进行中项目的当前阶段实例: 距 deadline ≤ critical_days → critical(站内+飞书);
    ≤ warn_days → warn(站内, 并入日报)。逾期同 critical。一阶段一条(dedupe), 不刷屏。
    """
    import os as _os
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.npd import NpdProject, NpdStage, NpdStageInstance
    from app.services import alert_service

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(NpdStageInstance, NpdProject, NpdStage)
        .join(NpdProject, NpdStageInstance.project_id == NpdProject.id)
        .join(NpdStage, NpdStageInstance.stage_id == NpdStage.id)
        .where(NpdStageInstance.status == "active",
               NpdProject.state.in_(["active", "rework"]))
    ).all()
    due: list[tuple] = []
    for inst, proj, stage in rows:
        if not inst.deadline:
            continue
        dl = inst.deadline if inst.deadline.tzinfo else inst.deadline.replace(tzinfo=timezone.utc)
        days_left = (dl - now).days
        if days_left <= stage.critical_days:
            level = "critical"
        elif days_left <= stage.warn_days:
            level = "warn"
        else:
            continue
        due.append((proj, stage, days_left, level))
        try:
            alert_service.upsert(
                db, kind="npd_stage_due",
                severity=("critical" if level == "critical" else "warn"),
                title=f"新品阶段临期: {proj.name}",
                body=(f"{proj.code} {proj.name} 阶段「{stage.name}」"
                      + ("已逾期 %d 天" % (-days_left) if days_left < 0 else "剩 %d 天" % days_left)
                      + ", 请尽快推进下一步。"),
                dedupe_key=f"npd_stage_due:{proj.id}:{stage.code}",
                related_url=f"/npd/{proj.id}",
            )
        except Exception:  # noqa: BLE001
            _logger.warning("NPD 阶段提醒 alert 失败", exc_info=True)
    db.flush()

    pushed = False
    if due and not _os.environ.get("PANSE_DISABLE_NOTIFY"):
        lines = ["🛎 新品开发阶段催办:"]
        for proj, stage, days_left, level in sorted(due, key=lambda x: x[2]):
            tag = ("🔴逾期%d天" % (-days_left)) if days_left < 0 else \
                  (("🟠剩%d天" % days_left) if level == "critical" else ("🟡剩%d天" % days_left))
            owner = (" @" + proj.owner) if proj.owner else ""
            lines.append(f"{tag} {proj.name}「{stage.name}」{owner}")
        msg = "\n".join(lines)
        from app.services import notify_service
        r = notify_service.broadcast_text(db, msg, level="warn", title="新品开发阶段催办")
        pushed = any(v is True for v in r.values())
    db.commit()
    return {"due": len(due),
            "critical": sum(1 for d in due if d[3] == "critical"), "pushed": pushed}


def _job_review_asset_remind(db: Session) -> dict:
    """每天 09:00: 评价资产折叠倒计时提醒 (Plan1 v2)。

    多级 30·14·7 折叠提醒 + 待评价超时催办 + 产品低覆盖预警, 一条飞书汇总。
    补单=刷单资产台账, 只提醒不产生经营数字。09:00 白天不受 PANSE_QUIET_HOURS 影响。
    """
    import os as _os
    from app.services import review_asset_service as _ras

    res = _ras.run_daily_scan(db)
    db.commit()
    pushed = False
    if res["has_content"]:
        msg = _ras.format_reminder(res)
        lvl = res["max_level"] or "info"
        from app.services import notify_service
        r = notify_service.broadcast_text(db, msg, title="评价资产日报", level=lvl)
        pushed = any(v is True for v in r.values())
    return {
        "fold_error": len(res["fold_notify"]["error"]),
        "fold_warn": len(res["fold_notify"]["warn"]),
        "fold_info": len(res["fold_notify"]["info"]),
        "pending": len(res["pending"]),
        "low_coverage": len(res["low_coverage"]),
        "pushed": pushed,
    }


def _register_default_jobs() -> None:
    register_job("hourly_alert_expire", "告警自动过期清理",
                 _job_alert_expire, interval_minutes=60)
    # 夜间模式 (2026-06-23): 深夜任务挪到 22:3x-22:5x 批量跑完, 让 NAS 盘 23:00-06:30 连续休眠
    # (job_id 保留旧名防丢调度覆盖配置; 实际执行时间以此处 cron 为准)。
    register_job("daily_03_audit_prune", "审计日志留存清理",
                 _job_audit_prune, cron={"hour": 22, "minute": 40})
    register_job("monthly_01_financial_report", "月度财务报表推送",
                 _job_monthly_financial_report, cron={"day": 1, "hour": 9, "minute": 10})
    register_job("daily_17_refund_check", "17:00 退款订单检查",
                 _job_refund_check, cron={"hour": 17, "minute": 0})
    register_job("daily_08_activate_future", "远期订单激活",
                 _job_activate_future_orders, cron={"hour": 8, "minute": 0})
    register_job("daily_07_lowstock_scan", "库存 / 滞销扫描",
                 _job_lowstock_scan, cron={"hour": 7, "minute": 0})
    register_job("daily_09_tracking_check", "快递追踪 (Phase 3 启用)",
                 _job_tracking_check, cron={"hour": 9, "minute": 0})
    register_job("daily_0940_alipay_match", "支付宝流水↔订单 自动匹配 (归类+按单号+按金额)",
                 _job_alipay_match_pipeline, cron={"hour": 9, "minute": 40})
    register_job("daily_10_data_reconcile", "数据自动核对 (写异常池)",
                 _job_data_reconcile, cron={"hour": 9, "minute": 50})
    register_job("daily_10_comprehensive_report", "每日综合日报推送",
                 _job_daily_10_comprehensive_report, cron={"hour": 10, "minute": 0})
    register_job("daily_06_forecast_refresh", "销售预测重算",
                 _job_forecast_refresh, cron={"hour": 6, "minute": 0})
    # Phase 8 Tier 1
    # AI 每日经营简报已停用 (用户 2026-07-01: 报表顶部每日总结不要了, 别每天调 AI 生成)。
    # 不再注册此定时任务 → 不再每天 9:30 调用 AI; _job_daily_briefing 函数保留, 需要时可恢复此行。
    # register_job("daily_09_briefing", "AI 每日经营简报",
    #              _job_daily_briefing, cron={"hour": 9, "minute": 30})
    register_job("monthly_supplier_score", "月初供应商评分",
                 _job_supplier_score, cron={"day": 1, "hour": 10, "minute": 0})
    register_job("daily_06_sales_rollup", "每日销售汇总 (rollup)",
                 _job_sales_rollup, cron={"hour": 6, "minute": 30})
    register_job("feishu_sync_30min", "飞书双向同步 (每6小时)",
                 _job_feishu_sync, interval_minutes=360)
    register_job("daily_11_ai_logic_check", "AI 数据逻辑核查 (兜底)",
                 _job_post_import_logic_check, cron={"hour": 11, "minute": 0})
    register_job("daily_09_data_freshness", "数据新鲜度提醒",
                 _job_data_freshness_remind, cron={"hour": 9, "minute": 0})
    register_job("monthly_01_data_remind", "月初数据更新提醒",
                 _job_monthly_data_remind, cron={"day": 1, "hour": 8, "minute": 30})
    register_job("daily_0905_ops_overdue", "例行待办超时报警 (物流/打包/玻璃/岩板/电力轨道对账)",
                 _job_ops_checklist_overdue, cron={"hour": 9, "minute": 5})
    register_job("daily_08_data_quality", "数据完整性扫描 (B1-B11)",
                 _job_data_quality, cron={"hour": 8, "minute": 0})
    register_job("email_poll_alipay_6h", "邮箱轮询支付宝流水",
                 _job_email_poll_alipay, interval_minutes=360)
    register_job("monthly_01_report_push", "月度经营简报推送",
                 _job_monthly_report_push, cron={"day": 1, "hour": 9, "minute": 0})
    register_job("daily_18_factory_summary", "每日待生产工厂单汇总",
                 _job_factory_daily_summary, cron={"hour": 18, "minute": 0})
    # 2026-06-14 用户改: Web-Agent 取数挪到 18:00 左右(那时 PC 一般开着, 群晖才连得上 PC 的浏览器农场)。
    # 保留 job_id 不变以免孤立已存的 override / 运行历史。
    register_job("daily_0630_web_agent", "Web-Agent 自动取数编排(18:00)",
                 _job_web_agent_daily, cron={"hour": 18, "minute": 0})
    register_job("daily_2030_finance_agent", "Web-Agent 财务取数编排(20:30, 与订单解耦)",
                 _job_web_agent_finance, cron={"hour": 20, "minute": 30})
    register_job("finance_pull_retry_30min", "余额/流水失败补跑(21:00/21:30, 成功即停)",
                 _job_web_agent_finance_retry, cron={"hour": 21, "minute": "0,30"})
    register_job("finance_pull_retry_2200", "余额/流水当日最后补跑(22:00)",
                 _job_web_agent_finance_retry, cron={"hour": 22, "minute": 0})
    register_job("hourly_ingest_scan", "共享目录导入扫描 (18:15-22:15每小时)",
                 _job_ingest_scan, cron={"hour": "18-22", "minute": 15})
    register_job("daily_2235_flip_monitor", "导入翻烧饼灰度监控(仍横跳→异常, 稳定→销账)",
                 _job_flip_monitor, cron={"hour": 22, "minute": 35})
    register_job("daily_2000_ingest_health", "取数体检(今日无新订单/Agent离线/报表待口令 → 微信+飞书)",
                 _job_ingest_health_check, cron={"hour": 20, "minute": 0})
    register_job("daily_0915_npd_stage_remind", "新品开发阶段截止提醒 (飞书催设计师)",
                 _job_npd_stage_remind, cron={"hour": 9, "minute": 15})
    # 评价资产台账已废弃(2026-07-10拍板): 功能迁至独立评价程序 panse-review-program, job 不再注册
    register_job("daily_1810_order_sheets", "下单图自动生成+归档+飞书日报(18:30, 取数后)",
                 _job_order_sheets_daily, cron={"hour": 18, "minute": 30})
    register_job("daily_1000_void_sheets", "退款下单图作废检查(10:00)",
                 _job_void_sheets, cron={"hour": 10, "minute": 0})
    register_job("daily_0900_aftersales_auto", "售后自动建条(万师傅/流水/退款)",
                 _job_aftersales_auto, cron={"hour": 9, "minute": 0})
    register_job("daily_0230_orders_maintain", "订单自动维护(成本/状态/细节)",
                 _job_orders_maintain, cron={"hour": 22, "minute": 50})   # 夜间模式: 挪 02:30→22:50
    register_job("notify_retry_30min", "失败通知重发(指数退避)",
                 _job_notify_retry, interval_minutes=30)
    register_job("daily_2220_critical_automation_final", "订单/余额/流水当日失败收口",
                 _job_critical_automation_final, cron={"hour": 22, "minute": 20})
    register_job("monthly_thumb_cleanup", "图库缩略图缓存月度清理",
                 _job_thumb_cache_cleanup, cron={"day": 1, "hour": 22, "minute": 35})   # 夜间模式: 挪 04:00→22:35
    register_job("hourly_gallery_thumb_warm", "图库缩略图每日兜底 (上传时已即时生成)",
                 _job_gallery_thumb_warm, cron={"hour": 4, "minute": 10})
    register_job("daily_2330_recon_snapshot", "对账结果每日快照",
                 _job_recon_snapshot, cron={"hour": 22, "minute": 45})   # 夜间模式: 挪 23:30→22:45
    # 2026-07-08 用户拍板: 去掉"每小时补推"—— 推送只在每日 18:30(取数成功后)一次; 失败才由
    # pull_catchup 在 18:00 失败约1小时后开始按小时重试, 成功即停。故不再注册 hourly_order_sheets_catchup。
    # 保留旧 job_id，避免割裂历史日志和用户覆盖。
    register_job("pull_catchup_30min", "PC上线续跑取数+补推送 (19:17-21:17每小时, 成功即停)",
                 _job_pull_catchup, cron={"hour": "19-21", "minute": 17})
    register_job("daily_14_aftersales_followup", "售后超时智能追踪",
                 _job_aftersales_followup, cron={"hour": 14, "minute": 0})
    register_job("weekly_mon_purchase_remind", "每周备货清单提醒",
                 _job_weekly_purchase_remind, cron={"day_of_week": "mon", "hour": 9, "minute": 0})
    register_job("monthly_last_inventory_plan", "月底下月成品备货计划飞书推送",
                 _job_monthly_inventory_plan, cron={"day": "last", "hour": 19, "minute": 20})
    register_job("monthly_01_inventory_plan_retry", "月度成品备货计划次月兜底",
                 _job_monthly_inventory_plan, cron={"day": 1, "hour": 8, "minute": 10})
    register_job("daily_0905_promo_rotation_remind", "大促价保规则确认提醒(兼容旧任务ID)",
                 _job_promo_rotation_remind, cron={"hour": 9, "minute": 5})
    register_job("monthly_last_reconcile_diagnose", "月底对账差异AI诊断",
                 _job_monthly_reconcile_diagnose, cron={"day": "last", "hour": 20, "minute": 0})
    register_job("daily_02_data_backup", "全量数据备份 (按配置间隔, 默认7天)",
                 _job_data_backup, cron={"hour": 22, "minute": 55})   # 夜间模式: 挪 02:00→22:55
    register_job("accessory_tracking_2h", "配件物流实时刷新 (快递100)",
                 _job_accessory_tracking_refresh, interval_minutes=120)
    register_job("shipments_tracking_6h", "全表物流实时刷新 (shipments: 订单/售后/工厂/补单/采购)",
                 _job_shipments_refresh, interval_minutes=360, enabled=False)
    register_job("daily_0730_accessory_alert", "配件到货预警刷新",
                 _job_accessory_alert_refresh, cron={"hour": 7, "minute": 30})
    register_job("daily_0740_self_arrive", "自送配件3天自动到货",
                 _job_accessory_self_arrive, cron={"hour": 7, "minute": 40})
    register_job("daily_0650_cost_recompute", "理论成本兜底反推 (补未反推)",
                 _job_cost_recompute, cron={"hour": 6, "minute": 50})
    register_job("weekly_tax_report_pull", "涉税报送抓取(税费打款口径, 周日19:00)",
                 _job_tax_report_pull, cron={"day_of_week": "sun", "hour": 19, "minute": 0})
    register_job("daily_0655_accessory_backfill", "配件清单自动补建/对齐 (进行中订单)",
                 _job_accessory_backfill, cron={"hour": 6, "minute": 55})
    # Plan 阶段一: L3 补单打标兜底 + L2 工厂付款回填常态化
    register_job("daily_0645_refill_rederive", "补单自动打标兜底 (is_refill 重判)",
                 _job_refill_rederive, cron={"hour": 6, "minute": 45})
    register_job("daily_0710_factory_payment_backfill", "工厂付款状态回填 (规则A实跑/规则B看开关)",
                 _job_factory_payment_backfill, cron={"hour": 7, "minute": 10})
    # Plan 阶段三: F7 销售周报 (文字条形图) + F1 活动报名价对照
    register_job("weekly_mon_0930_sales_report", "销售周报推送 (上周摘要)",
                 _job_weekly_sales_report, cron={"day_of_week": "mon", "hour": 9, "minute": 30})
    register_job("daily_0830_promo_price_check", "活动报名价 vs 定价渠道价 对照",
                 _job_promo_price_check, cron={"hour": 8, "minute": 30})
    # 活动生命周期 P4 (2026-07-17 spec §五): 发现在抓单编排(18:00)后跑; 自动核对每30分钟心跳
    register_job("campaign_daily_discovery", "千牛活动发现(晚间每小时重试+提醒)",
                 _job_campaign_discovery, cron={"hour": "18-22", "minute": 40})
    register_job("campaign_price_protection_rule_remind", "活动价保说明链接提醒",
                 _job_campaign_price_protection_rule_remind, cron={"hour": 18, "minute": 45})
    register_job("campaign_auto_execute", "营销活动自动报名(安全门+差集+失败飞书)",
                 _job_campaign_auto_execute, cron={"hour": "18-22", "minute": 50})
    register_job("campaign_auto_recon", "活动报名后自动核对(signup_pushed·6小时内)",
                 _job_campaign_auto_recon, interval_minutes=30)


# ----------------------------- 生命周期 -------------------------- #


# ── 关键任务错过触发补跑 (用户 2026-07-13 "现在补上") ──────────────────────
# 调度器跑在 api 进程里(内存存储), 部署/重启撞上触发点 → 该班直接丢, 不自愈。
# 实锤两次: 07-11(部署战)/07-13(用户部署) 的 18:00 取数被打断 → 发货报表/推送断供。
# 启动 60s 后查名单: 最近一班应触发时刻已过、仍在宽限内、且 scheduled_job_runs 无该班记录
# (含 fail — 失败算跑过, 不补跑防崩溃循环) → 按名单顺序同步补跑(取数在日报前=天然依赖序)。
# 30 分钟心跳类任务自愈, 无需入名单。
_CATCHUP_JOBS: dict[str, int] = {          # job_id → 宽限小时(错过太久不追, 免撞下一班)
    "daily_0630_web_agent": 6,             # 取数编排(18:00) — 日报的前置
    "daily_1810_order_sheets": 6,          # 下单图日报+发货报表推送(18:30)
    "daily_2030_finance_agent": 3,          # 余额/流水取数(20:30)
    "daily_0230_orders_maintain": 8,       # 订单自动维护(22:50)
    "daily_0650_cost_recompute": 8,        # 理论成本兜底反推(06:50)
}


def _last_fire_dt(schedule: dict, now: datetime) -> Optional[datetime]:
    """cron {hour,minute} 的最近一个 ≤now 应触发时刻(今天没到点则取昨天); 非固定时分返回 None。"""
    from datetime import timedelta
    h, m = schedule.get("hour"), schedule.get("minute", 0)
    if not isinstance(h, int) or not isinstance(m, int):
        return None
    fire = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if fire > now:
        fire -= timedelta(days=1)
    return fire


def missed_catchup_jobs(db: Session, now: Optional[datetime] = None,
                        overrides: Optional[dict] = None) -> list[str]:
    """启动补跑决策(纯判定, 供测试): 返回需补跑的 job_id, 保持 _CATCHUP_JOBS 声明顺序。"""
    from datetime import timedelta
    from sqlalchemy import func as _f, select as _sel
    from app.models.scheduled_job import ScheduledJobRun
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    ov = overrides if overrides is not None else _load_overrides()
    out: list[str] = []
    for jid, grace_h in _CATCHUP_JOBS.items():
        cfg = _REGISTRY.get(jid)
        if not cfg or not cfg.get("cron"):
            continue
        kind, schedule, enabled = _effective_schedule(cfg, ov.get(jid, {}))
        if not enabled or kind != "cron":
            continue
        fire = _last_fire_dt(schedule, now)
        if fire is None or (now - fire) > timedelta(hours=grace_h):
            continue
        last = db.execute(
            _sel(_f.max(ScheduledJobRun.started_at)).where(ScheduledJobRun.job_id == jid)
        ).scalar()
        if last is not None:
            # sqlite 返回 naive(库里本就存 UTC), postgres 返回 aware — 统一按 UTC 换算比较
            last_ts = (last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last).timestamp()
            if last_ts >= fire.timestamp() - 300:
                continue   # 该班(或之后)已有运行记录 → 不补
        out.append(jid)
    return out


def _startup_catchup() -> None:
    """启动后一次性任务: 依名单顺序同步补跑错过的班次(顺序执行, 天然满足 取数→日报 依赖)。"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        missed = missed_catchup_jobs(db)
    except Exception:  # pragma: no cover - 判定失败不拖垮启动
        _logger.warning("启动补跑判定失败, 跳过", exc_info=True)
        return
    finally:
        db.close()
    if not missed:
        _logger.info("启动补跑: 无错过的关键班次")
        return
    _logger.warning("启动补跑: 检测到错过的班次 %s, 依序补跑", missed)
    for jid in missed:
        cfg = _REGISTRY[jid]
        _run_with_logging(jid, cfg["label"] + "(重启补跑)", cfg["fn"])


def start(timezone_name: Optional[str] = None) -> None:
    """FastAPI startup 调一次. 重入安全."""
    global _SCHEDULER
    if _SCHEDULER is not None:
        return
    if os.environ.get("DISABLE_SCHEDULER") == "1":
        _logger.info("DISABLE_SCHEDULER=1, 跳过启动")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:  # pragma: no cover
        _logger.warning("APScheduler 未安装, 调度器不启用")
        return

    tz = timezone_name or os.environ.get("PANSE_TZ", "Asia/Shanghai")
    # 群晖 2G 内存: 限制同时并发的同步任务数, 避免早高峰多个全表重算挤在一起把内存打爆。
    # coalesce=错过的多次触发合并成一次; max_instances=1=同一任务不重叠跑; misfire 1h 宽限。
    from apscheduler.executors.pool import ThreadPoolExecutor as _APThreadPool
    _SCHEDULER = AsyncIOScheduler(
        timezone=tz,
        executors={"default": _APThreadPool(max_workers=int(os.environ.get("SCHED_MAX_WORKERS", "2")))},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    _register_default_jobs()
    overrides = _load_overrides()
    for job_id in _REGISTRY:
        _add_to_scheduler(job_id, overrides)
    _SCHEDULER.start()
    # 启动补跑 (用户 2026-07-13 "现在补上"): 60s 后查一遍错过的关键班次(部署/重启撞触发点该班即丢),
    # 在名单宽限内且无运行记录 → 依序补跑。60s 让应用先热身, 也避开与 lifespan 其余初始化抢资源。
    from datetime import timedelta as _td
    _SCHEDULER.add_job(
        _startup_catchup, "date",
        run_date=datetime.now().astimezone() + _td(seconds=60),
        id="startup_catchup", max_instances=1,
    )
    _logger.info("调度器已启动, tz=%s, %d 个任务 (+启动补跑检查@60s)", tz, len(_REGISTRY))


def shutdown() -> None:
    global _SCHEDULER
    if _SCHEDULER is not None:
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:  # pragma: no cover
            pass
        _SCHEDULER = None


def list_jobs() -> list[dict]:
    """API: 返回 {job_id, label, kind, schedule(生效), default_schedule, enabled, next_run_at} 列表."""
    overrides = _load_overrides()
    out = []
    for jid, cfg in _REGISTRY.items():
        kind, schedule, enabled = _effective_schedule(cfg, overrides.get(jid, {}))
        info = {
            "job_id": jid,
            "label": cfg["label"],
            "kind": kind,
            "schedule": schedule,
            "default_schedule": cfg["cron"] or {"interval_minutes": cfg["interval_minutes"]},
            "enabled": enabled,
            "next_run_at": None,
        }
        if _SCHEDULER is not None:
            job = _SCHEDULER.get_job(jid)
            if job and job.next_run_time:
                info["next_run_at"] = job.next_run_time.isoformat()
        out.append(info)
    return out


def trigger_now(job_id: str) -> bool:
    """admin UI "立即执行" 按钮. 返回是否成功 schedule."""
    if job_id not in _REGISTRY:
        return False
    cfg = _REGISTRY[job_id]
    if _SCHEDULER is None:
        # 没启动调度器时, 直接同步跑一次 (便于测试)
        _run_with_logging(job_id, cfg["label"], cfg["fn"])
        return True
    # 加一个一次性 job
    from datetime import datetime as _dt, timedelta as _td
    _SCHEDULER.add_job(
        _run_with_logging,
        args=(job_id, cfg["label"], cfg["fn"]),
        run_date=_dt.now() + _td(seconds=1),
        id=f"{job_id}_manual_{int(time_mod.time())}",
        max_instances=2,
    )
    return True


def set_schedule(db: Session, job_id: str, *, interval_minutes: Optional[int] = None,
                 cron: Optional[dict] = None, enabled: Optional[bool] = None) -> dict:
    """用户在设置里改某任务的定时: 写入 scheduler_overrides 并立即重排。返回该任务最新信息。

    - interval 任务: 传 interval_minutes (分钟)。
    - cron 任务: 传 cron={"hour":H,"minute":M,...} (只合并白名单字段, 其余沿用默认)。
    - enabled: 停用/启用该任务。
    """
    import json
    from app.services import settings_service
    if job_id not in _REGISTRY:
        raise ValueError(f"job {job_id} 未注册")
    cfg = _REGISTRY[job_id]

    overrides = _load_overrides()
    entry = dict(overrides.get(job_id, {}))
    if enabled is not None:
        entry["enabled"] = bool(enabled)
    if interval_minutes is not None and not cfg["cron"]:
        iv = int(interval_minutes)
        if iv < 1:
            raise ValueError("间隔必须 ≥ 1 分钟")
        entry["interval_minutes"] = iv
    if cron is not None and cfg["cron"]:
        entry["cron"] = {k: v for k, v in cron.items() if k in _ALLOWED_CRON_KEYS}
    overrides[job_id] = entry
    settings_service.set_value(db, "scheduler_overrides", json.dumps(overrides, ensure_ascii=False))
    db.commit()

    if _SCHEDULER is not None:
        _add_to_scheduler(job_id, overrides)

    kind, schedule, en = _effective_schedule(cfg, entry)
    next_run = None
    if _SCHEDULER is not None:
        job = _SCHEDULER.get_job(job_id)
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    return {
        "job_id": job_id, "label": cfg["label"], "kind": kind, "schedule": schedule,
        "default_schedule": cfg["cron"] or {"interval_minutes": cfg["interval_minutes"]},
        "enabled": en, "next_run_at": next_run,
    }
