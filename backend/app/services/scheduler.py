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
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy.orm import Session

_logger = logging.getLogger("panse.scheduler")

_SCHEDULER = None   # type: ignore[var-annotated]
_REGISTRY: dict[str, dict] = {}
# job_id -> {label, fn, kind: 'cron'/'interval', schedule_kwargs}


# ----------------------------- 任务运行包装 ----------------------- #


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
        except Exception as e:  # pragma: no cover
            _logger.warning("写 ScheduledJobRun 失败: %s", e)
        finally:
            log_db.close()


# ----------------------------- 公共注册接口 ----------------------- #


def register_job(
    job_id: str, label: str, fn: Callable[[Session], dict], *,
    cron: Optional[dict] = None, interval_minutes: Optional[int] = None,
) -> None:
    """注册一个任务. cron 是 {hour, minute, day_of_week, ...} 给 CronTrigger.

    重复注册同 id 会覆盖。
    """
    if not cron and not interval_minutes:
        raise ValueError("必须提供 cron 或 interval_minutes")
    _REGISTRY[job_id] = {
        "label": label, "fn": fn,
        "cron": cron, "interval_minutes": interval_minutes,
    }
    _logger.info("注册任务 %s (%s)", job_id, label)
    # 如果调度器已起来, 立即加入
    if _SCHEDULER is not None:
        _add_to_scheduler(job_id)


def _add_to_scheduler(job_id: str) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    cfg = _REGISTRY[job_id]
    label = cfg["label"]
    fn = cfg["fn"]
    if cfg["cron"]:
        trigger = CronTrigger(**cfg["cron"])
    else:
        trigger = IntervalTrigger(minutes=cfg["interval_minutes"])
    _SCHEDULER.add_job(
        _run_with_logging, trigger=trigger,
        args=(job_id, label, fn),
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
    """业务需求 19 + 功能 1: 全自动核对.

    1) 跑全部 6 条对账规则 (货款/安装/推广/补单/库存/物流), 超阈值自动写异常池 + 报警;
    2) 算财务公式 A vs B, 差额 > 100 生成 Alert。
    """
    from app.services import asset_service, reconciliation_service
    results = reconciliation_service.run_all(db, record_exceptions=True)
    db.flush()
    by_rule = {
        name: {"error": r.error_count, "warning": r.warning_count, "ok": r.ok_count}
        for name, r in results.items()
    }
    formula = asset_service.check_formula_and_alert(db)
    return {"reconciliation": by_rule, "formula": formula}


def _job_tracking_check(db: Session) -> dict:
    """快递追踪 — 仅扫缺单号; 真正调 17track API 留给 Phase 5 扩展."""
    from app.services import factory_order_service
    return factory_order_service.check_missing_tracking(db)


def _job_forecast_refresh(db: Session) -> dict:
    """销售预测重算 — 主要是触发 forecast_30d 缓存. 当前是即时计算, 占位返回 0."""
    from app.services import sales_analytics
    forecast = sales_analytics.forecast_30d(db)
    return {"sku_count": len(forecast)}


def _job_sales_rollup(db: Session) -> dict:
    """Phase 12 P3-13: 每日聚合昨日订单到 sales_daily_rollup, 加速大量数据查询."""
    from datetime import date as _date, timedelta as _td
    from app.services import sales_rollup_service
    target = _date.today() - _td(days=1)
    n = sales_rollup_service.rollup_day(db, target)
    return {"day": target.isoformat(), "rows": n}


def _job_daily_briefing(db: Session) -> dict:
    """Phase 8 Tier 1 #1: AI 每日经营简报 (昨日数据合成)."""
    from app.services import briefing_service
    b = briefing_service.generate(db, push=True)
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
    from app.services import feishu_client, feishu_sync_service
    try:
        feishu_client.get_credentials(db)
    except feishu_client.FeishuError:
        return {"skipped": "飞书未配置"}
    results = feishu_sync_service.sync_all(db)
    return {
        "bindings": len(results),
        "pushed": sum(r.pushed for r in results),
        "pulled": sum(r.pulled for r in results),
        "conflicts": sum(r.conflicts for r in results),
    }


def _job_data_quality(db: Session) -> dict:
    """每日数据完整性扫描 (B1-B11)."""
    from app.services import data_quality_service
    return data_quality_service.run_all(db)


def _job_post_import_logic_check(db: Session) -> dict:
    """兜底: 每日对全量数据跑一次 AI 逻辑核查 (补导入时漏掉的)."""
    from app.services import post_import_ai_service
    return post_import_ai_service.run_after_import(db, summary={"source": "daily_scan"})


def _job_data_freshness_remind(db: Session) -> dict:
    """每日 09:00: 检查各数据源新鲜度, 过期则推送通知 + 写 Alert。"""
    from app.services import data_freshness_service
    return data_freshness_service.check_and_remind(db)


def _job_monthly_data_remind(db: Session) -> dict:
    """每月 1 号 08:30: 集中提醒上传上月全量数据 (流水/推广/账单/余额)。"""
    from app.services import data_freshness_service
    return data_freshness_service.monthly_batch_remind(db)


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


def _job_aftersales_followup(db: Session) -> dict:
    """每天 14:00: 检查售后超时未处理记录, 按原因推送智能建议。"""
    from app.services import aftersales_followup_service
    return aftersales_followup_service.check_and_push(db)


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


def _register_default_jobs() -> None:
    register_job("hourly_alert_expire", "告警自动过期清理",
                 _job_alert_expire, interval_minutes=60)
    register_job("daily_17_refund_check", "17:00 退款订单检查",
                 _job_refund_check, cron={"hour": 17, "minute": 0})
    register_job("daily_08_activate_future", "远期订单激活",
                 _job_activate_future_orders, cron={"hour": 8, "minute": 0})
    register_job("daily_07_lowstock_scan", "库存 / 滞销扫描",
                 _job_lowstock_scan, cron={"hour": 7, "minute": 0})
    register_job("daily_09_tracking_check", "快递追踪 (Phase 3 启用)",
                 _job_tracking_check, cron={"hour": 9, "minute": 0})
    register_job("daily_10_data_reconcile", "数据自动核对",
                 _job_data_reconcile, cron={"hour": 10, "minute": 0})
    register_job("daily_06_forecast_refresh", "销售预测重算",
                 _job_forecast_refresh, cron={"hour": 6, "minute": 0})
    # Phase 8 Tier 1
    register_job("daily_09_briefing", "AI 每日经营简报",
                 _job_daily_briefing, cron={"hour": 9, "minute": 30})
    register_job("monthly_supplier_score", "月初供应商评分",
                 _job_supplier_score, cron={"day": 1, "hour": 10, "minute": 0})
    register_job("daily_06_sales_rollup", "每日销售汇总 (rollup)",
                 _job_sales_rollup, cron={"hour": 6, "minute": 30})
    register_job("feishu_sync_30min", "飞书双向同步",
                 _job_feishu_sync, interval_minutes=30)
    register_job("daily_11_ai_logic_check", "AI 数据逻辑核查 (兜底)",
                 _job_post_import_logic_check, cron={"hour": 11, "minute": 0})
    register_job("daily_09_data_freshness", "数据新鲜度提醒",
                 _job_data_freshness_remind, cron={"hour": 9, "minute": 0})
    register_job("monthly_01_data_remind", "月初数据更新提醒",
                 _job_monthly_data_remind, cron={"day": 1, "hour": 8, "minute": 30})
    register_job("daily_08_data_quality", "数据完整性扫描 (B1-B11)",
                 _job_data_quality, cron={"hour": 8, "minute": 0})
    register_job("email_poll_alipay_6h", "邮箱轮询支付宝流水",
                 _job_email_poll_alipay, interval_minutes=360)
    register_job("monthly_01_report_push", "月度经营简报推送",
                 _job_monthly_report_push, cron={"day": 1, "hour": 9, "minute": 0})
    register_job("daily_18_factory_summary", "每日待生产工厂单汇总",
                 _job_factory_daily_summary, cron={"hour": 18, "minute": 0})
    register_job("daily_14_aftersales_followup", "售后超时智能追踪",
                 _job_aftersales_followup, cron={"hour": 14, "minute": 0})
    register_job("weekly_mon_purchase_remind", "每周备货清单提醒",
                 _job_weekly_purchase_remind, cron={"day_of_week": "mon", "hour": 9, "minute": 0})
    register_job("monthly_last_reconcile_diagnose", "月底对账差异AI诊断",
                 _job_monthly_reconcile_diagnose, cron={"day": "last", "hour": 20, "minute": 0})


# ----------------------------- 生命周期 -------------------------- #


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
    _SCHEDULER = AsyncIOScheduler(timezone=tz)
    _register_default_jobs()
    for job_id in _REGISTRY:
        _add_to_scheduler(job_id)
    _SCHEDULER.start()
    _logger.info("调度器已启动, tz=%s, %d 个任务", tz, len(_REGISTRY))


def shutdown() -> None:
    global _SCHEDULER
    if _SCHEDULER is not None:
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:  # pragma: no cover
            pass
        _SCHEDULER = None


def list_jobs() -> list[dict]:
    """API: 返回 {job_id, label, next_run_at, kind, schedule} 列表."""
    out = []
    for jid, cfg in _REGISTRY.items():
        info = {
            "job_id": jid,
            "label": cfg["label"],
            "kind": "cron" if cfg["cron"] else "interval",
            "schedule": cfg["cron"] or {"interval_minutes": cfg["interval_minutes"]},
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
