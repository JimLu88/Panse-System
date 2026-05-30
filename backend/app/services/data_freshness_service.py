"""数据新鲜度检查 + 定时填写提醒。

每个数据源都有建议的更新周期; 超期未更新时生成提醒推送。
每日 09:00 由调度器调用 check_and_remind()。
每月月初 (1号) 额外跑月度提醒集中推送。

数据源                  建议周期        提醒时机
--------------------   ----------     -----------------------------------------
支付宝流水              月度           1号提醒上传上月全量
万师傅安装账单          月度           5号提醒 (账单通常月结)
物流费账单              月度           5号提醒
推广记录                月度           3号提醒
账户余额                月度           2号提醒更新期末余额
淘宝订单                每日           订单超 2 天未录入则提醒
补单对账                每周           周一提醒核对上周
售后表                  有售后即录      有 aftersales 状态订单超 3 天未处理则提醒
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session


@dataclass
class FreshnessItem:
    source: str          # 数据源名
    last_date: Optional[date]  # 最后一条数据的日期
    days_stale: int      # 距今天数
    threshold_days: int  # 超出此天数视为过期
    overdue: bool
    message: str         # 用于通知的提醒文字


def _days_since(d: Optional[date]) -> int:
    if d is None:
        return 9999
    return (date.today() - d).days


def check_all(db: Session) -> list[FreshnessItem]:
    """检查所有数据源的新鲜度, 返回所有数据源状态 (含未过期的)。"""
    from app.models.finance import AccountBalance, AlipayFlow, LogisticsBill, RefillRecord, WanshifuBill
    from app.models.marketing import AfterSales, PromotionFlow
    from app.models.order import Order

    today = date.today()
    items: list[FreshnessItem] = []

    # 1. 支付宝流水 — 月度; 超 35 天提醒
    last_flow = db.execute(
        select(func.max(AlipayFlow.transaction_time))
    ).scalar()
    last_flow_date = last_flow.date() if last_flow else None
    stale = _days_since(last_flow_date)
    items.append(FreshnessItem(
        source="支付宝流水", last_date=last_flow_date, days_stale=stale,
        threshold_days=35, overdue=stale > 35,
        message=f"支付宝流水最后一条为 {last_flow_date or '无'}，已 {stale} 天未导入。请在各支付宝账户导出上月全量 CSV 后上传至 POST /api/finance/alipay-flows/import-csv",
    ))

    # 2. 万师傅安装账单 — 月度; 超 40 天提醒 (留账单寄到时间)
    last_ws = db.execute(select(func.max(WanshifuBill.bill_date))).scalar()
    stale = _days_since(last_ws)
    items.append(FreshnessItem(
        source="万师傅安装账单", last_date=last_ws, days_stale=stale,
        threshold_days=40, overdue=stale > 40,
        message=f"万师傅账单最后一条为 {last_ws or '无'}，已 {stale} 天未导入。请在万师傅商户后台下载上月账单 CSV 后上传至 POST /api/finance/wanshifu-bills/import-csv",
    ))

    # 3. 物流费账单 — 月度; 超 40 天提醒
    last_lb = db.execute(select(func.max(LogisticsBill.bill_date))).scalar()
    stale = _days_since(last_lb)
    items.append(FreshnessItem(
        source="物流费账单", last_date=last_lb, days_stale=stale,
        threshold_days=40, overdue=stale > 40,
        message=f"物流账单最后一条为 {last_lb or '无'}，已 {stale} 天未导入。请向物流公司索取上月月结账单 CSV 后上传至 POST /api/finance/logistics-bills/import-csv",
    ))

    # 4. 推广记录 — 月度; 超 35 天提醒
    last_promo = db.execute(select(func.max(PromotionFlow.transaction_date))).scalar()
    stale = _days_since(last_promo)
    items.append(FreshnessItem(
        source="推广记录", last_date=last_promo, days_stale=stale,
        threshold_days=35, overdue=stale > 35,
        message=f"推广记录最后一条为 {last_promo or '无'}，已 {stale} 天未录入。请从直通车/万相台后台导出上月充值+消耗记录 CSV 后上传至 POST /api/finance/promotion-flows/import-csv",
    ))

    # 5. 账户余额 — 月度; 超 40 天提醒
    last_bal_row = db.execute(
        select(AccountBalance.period_year, AccountBalance.period_month)
        .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
        .limit(1)
    ).first()
    if last_bal_row:
        last_bal_date = date(last_bal_row[0], last_bal_row[1], 1)
        stale = _days_since(last_bal_date)
    else:
        last_bal_date = None
        stale = 9999
    items.append(FreshnessItem(
        source="账户余额", last_date=last_bal_date, days_stale=stale,
        threshold_days=40, overdue=stale > 40,
        message=f"账户余额最后更新为 {(str(last_bal_row[0]) + '-' + str(last_bal_row[1])) if last_bal_row else '无'}，请在各账户对账单到达后更新期末余额，上传至 POST /api/finance/accounts/import-csv",
    ))

    # 6. 淘宝订单 — 每日; 超 2 天提醒
    last_order = db.execute(select(func.max(Order.order_date))).scalar()
    stale = _days_since(last_order)
    items.append(FreshnessItem(
        source="淘宝订单", last_date=last_order, days_stale=stale,
        threshold_days=2, overdue=stale > 2,
        message=f"最新订单日期为 {last_order or '无'}，已 {stale} 天未录入。请在千牛导出昨日订单 Excel 后通过智能导入上传，或使用截图 OCR 录入。",
    ))

    # 7. 补单对账 — 每周; 超 9 天 (上周 + 缓冲) 提醒
    last_refill = db.execute(select(func.max(RefillRecord.refill_date))).scalar()
    stale = _days_since(last_refill)
    items.append(FreshnessItem(
        source="补单对账", last_date=last_refill, days_stale=stale,
        threshold_days=9, overdue=stale > 9,
        message=f"补单对账最后一条为 {last_refill or '无'}，已 {stale} 天未录入。请整理上周补单记录 CSV 后上传至 POST /api/finance/refill-records/import-csv",
    ))

    # 8. 售后表 — 有 aftersales 状态的订单超 3 天未处理则提醒
    cutoff = date.today() - timedelta(days=3)
    overdue_count = db.execute(
        select(func.count(Order.id)).where(
            Order.status == "aftersales",
            Order.order_date <= cutoff,
        )
    ).scalar() or 0
    items.append(FreshnessItem(
        source="售后表", last_date=None, days_stale=0,
        threshold_days=3, overdue=overdue_count > 0,
        message=f"有 {overdue_count} 笔订单处于售后状态超过 3 天未处理，请及时录入售后详情 (POST /api/aftersales 或 POST /api/aftersales/import-csv)。",
    ))

    return items


def overdue_only(db: Session) -> list[FreshnessItem]:
    return [i for i in check_all(db) if i.overdue]


def check_and_remind(db: Session) -> dict:
    """调度器调用: 检查所有源, 对过期项生成 Alert + 推送通知。"""
    from app.services import alert_service, notify_service
    items = overdue_only(db)
    if not items:
        return {"overdue": 0, "reminded": 0}

    reminded = 0
    for item in items:
        alert_service.upsert(
            db,
            kind="data_freshness",
            severity="warn",
            title=f"数据待更新: {item.source}",
            body=item.message,
            dedupe_key=f"data_freshness:{item.source}",
            related_url="/finance",
            context={"source": item.source, "days_stale": item.days_stale},
            auto_resolve_after_minutes=60 * 24 * 2,  # 2 天后自动过期
        )
        reminded += 1

    # 把过期项汇总推送一条通知 (而不是每条单独推, 避免刷屏)
    if reminded:
        summary = "\n".join(f"• {i.source}: {i.days_stale} 天未更新" for i in items)
        notify_service.notify(
            db,
            f"以下数据源需要更新，请尽快补录：\n{summary}",
            level="warn",
            title=f"畔色 ERP | {reminded} 项数据待更新",
        )

    db.flush()
    return {"overdue": len(items), "reminded": reminded}


def monthly_batch_remind(db: Session) -> dict:
    """月初集中提醒: 每月1号额外调用, 即使未超阈值也强制提醒月度数据源。"""
    from app.services import notify_service
    monthly_sources = ["支付宝流水", "万师傅安装账单", "物流费账单", "推广记录", "账户余额", "补单对账"]
    notify_service.notify(
        db,
        "新月开始，请上传上月数据：\n"
        "① 各支付宝账户流水 CSV\n"
        "② 万师傅安装账单 CSV (月结通常 5 号前到)\n"
        "③ 物流公司月结账单 CSV\n"
        "④ 直通车/万相台推广记录 CSV\n"
        "⑤ 补单对账汇总 CSV\n"
        "⑥ 各账户余额期末数据 CSV",
        level="info",
        title="畔色 ERP | 月度数据更新提醒",
    )
    return {"reminded_sources": monthly_sources}
