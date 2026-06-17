"""运营待办台账 (SOP): 每日 / 每周 / 每月 例行工作清单 + 完成状态。

完成状态用 settings 存 JSON (key=ops_checklist_done), 无需建表/迁移。
mark = "<task_key>@<period_key>"; period_key: 日=YYYY-MM-DD, 周=YYYY-Www, 月=YYYY-MM。
每进入新周期(新的一天/周/月), 旧 mark 自然不再命中 → 清单自动重置为"未做"。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service

_SETTING = "ops_checklist_done"
_DYN = "ops_checklist_dynamic"   # 动态待办(系统自动生成, 如对账新差异)

# 例行工作项 (按畔色 ERP 实际流程定制); 含: 做什么 / 导哪张表 / 做什么记录 / 截图上传
OPS_TASKS = [
    # —— 每日 ——
    {"key": "daily_import_orders", "freq": "daily", "title": "导入当日淘宝订单", "route": "/orders",
     "detail": "订单→CSV导入: 上传当天淘宝订单表(销售明细/订单CSV), 自动补金额+客户"},
    {"key": "daily_screenshot", "freq": "daily", "title": "截图录单", "route": "/screenshots",
     "detail": "把零散/手工订单截图上传(截图录单), 录入系统"},
    {"key": "daily_logistics", "freq": "daily", "title": "处理发货 / 查物流", "route": "/orders/kanban",
     "detail": "订单看板看待发货 → 发货并查快递; 处理在途异常"},
    {"key": "daily_aftersales", "freq": "daily", "title": "处理售后 / 退货", "route": "/aftersales",
     "detail": "退货/售后页: 新建退货、确认签收入库、登记 客户退回/我方补发 快递"},
    # —— 每周 ——
    {"key": "weekly_part_stock", "freq": "weekly", "title": "配件库存盘点 / 补货", "route": "/inventory",
     "detail": "配件库存→智能预警: 看缺货/低于预警线, 下采购补货"},
    {"key": "weekly_recon", "freq": "weekly", "title": "对账(订单 ↔ 收款)", "route": "/settlements",
     "detail": "逐笔核对订单实付 ↔ 支付宝企业号(9A)/微信账单 到账, 标差额"},
    {"key": "weekly_price", "freq": "weekly", "title": "竞品调价", "route": "/pricing",
     "detail": "定价表: 按竞品多选批量改大促价 / 套用公式"},
    # —— 每月 ——
    {"key": "monthly_alipay", "freq": "monthly", "title": "导入支付宝流水", "route": "/alipay",
     "detail": "导入 企业号(9A) / 爱群号(9C) 当月支付宝流水"},
    {"key": "monthly_wechat_bill", "freq": "monthly", "title": "导入微信账单(billDetail)", "route": "/settlements",
     "detail": "导入当月微信支付订单账单明细(billDetail xlsx)"},
    {"key": "monthly_factory_recon", "freq": "monthly", "title": "导入工厂对账单(逐单)", "route": "/factory-recon",
     "detail": "工厂逐单对账: 上传工厂侧对账单 xlsx(价格=工厂结算价=成本), 逐月对账并对差异填原因做平; 自动回填订单成本"},
    {"key": "monthly_wanshifu_bill", "freq": "monthly", "title": "导入万师傅安装账单", "route": "/wanshifu-bills",
     "detail": "万师傅安装账单 CSV: 上传当月安装费明细, 供安装费对账(应付↔实付)"},
    {"key": "monthly_logistics_bill", "freq": "monthly", "title": "导入物流公司账单", "route": "/logistics-bills",
     "detail": "物流公司账单 CSV: 上传当月运费明细, 供物流费对账"},
    {"key": "monthly_promotion", "freq": "monthly", "title": "导入推广费流水(直通车/万相台)", "route": "/promotion-flows",
     "detail": "推广费流水: 上传当月直通车/万相台 充值+支出明细 CSV, 供推广ROI核算"},
    {"key": "monthly_prepay_ledger", "freq": "monthly", "title": "导入代付台账(补单佣金/快递/售后打款)", "route": "/prepay-ledger",
     "detail": "代付台账 CSV: 分别上传 补单佣金 / 补单快递 / 售后打款 三类实际打款, 作为这三类对账的进项来源"},
    {"key": "monthly_refill_records", "freq": "monthly", "title": "导入补单对账表", "route": "/refill-records",
     "detail": "补单记录 CSV: 上传当月补单(刷单)明细, 供补单佣金/快递对账核对"},
    {"key": "monthly_account_balance", "freq": "monthly", "title": "填写所有账户余额", "route": "/account-balances",
     "detail": "账户余额: 录入各账户(支付宝企业/爱群/聚合/推广/银行卡)当月期末余额 + 统计日期"},
    {"key": "monthly_shop_deposit", "freq": "monthly", "title": "核对平台保证金", "route": "/shop-deposits",
     "detail": "平台保证金: 维护各平台/店铺保证金条目(新增/改/删), 合计自动并入可用资金"},
    {"key": "monthly_recon", "freq": "monthly", "title": "月度对账 + 推广ROI", "route": "/settlements",
     "detail": "全月逐笔对账; 算推广支出占正式销售额(不含补单)的占比"},
    {"key": "monthly_investment", "freq": "monthly", "title": "更新总投资费用", "route": "/cash-flow",
     "detail": "财务→剩余流水: 编辑总投资费用, 重新测算可用资金"},
    {"key": "monthly_product_stock", "freq": "monthly", "title": "成品库存月度盘点", "route": "/product-inventory",
     "detail": "成品库存: 核对现货/可用, 修正盘库偏差"},
]

_FREQ_LABEL = {"daily": "每日", "weekly": "每周", "monthly": "每月"}


def _period_key(freq: str, today: date) -> str:
    if freq == "daily":
        return today.isoformat()
    if freq == "weekly":
        y, w, _ = today.isocalendar()
        return f"{y}-W{w:02d}"
    return today.strftime("%Y-%m")  # monthly


def _load(db: Session) -> dict:
    raw = settings_service.get(db, _SETTING, env_fallback=False)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _load_dynamic(db: Session) -> list[dict]:
    raw = settings_service.get(db, _DYN, env_fallback=False)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def add_dynamic_todo(
    db: Session, *, key: str, title: str, detail: str = "",
    route: Optional[str] = None, freq: str = "daily",
) -> None:
    """系统自动生成一条动态待办(按 key 去重 upsert); 与静态项一样按周期自动重置。

    用于 功能 C: 对账发现新差异时, 在运营待办台账挂一条"去归因做平"的待办。
    """
    if freq not in ("daily", "weekly", "monthly"):
        freq = "daily"
    items = _load_dynamic(db)
    now = datetime.now(timezone.utc).isoformat()
    found = next((i for i in items if i.get("key") == key), None)
    if found:
        found.update({"title": title, "detail": detail, "route": route, "freq": freq, "updated_at": now})
    else:
        items.append({"key": key, "title": title, "detail": detail, "route": route,
                      "freq": freq, "created_at": now, "updated_at": now})
    settings_service.set_value(db, _DYN, json.dumps(items), description="运营待办-动态项")


def remove_dynamic_todo(db: Session, key: str) -> None:
    items = [i for i in _load_dynamic(db) if i.get("key") != key]
    settings_service.set_value(db, _DYN, json.dumps(items), description="运营待办-动态项")


def _auto_done(db: Session, today: date) -> set[str]:
    """按数据自动判定哪些"导入类"例行项已自动完成(本周期已有数据进库)。
    用户拍板 2026-06-17: 自动完成的就别当待办催了, 每天导入时自动检查, 只把没导的留着提醒。"""
    from sqlalchemy import func, select as _sel
    done: set[str] = set()
    y, m = today.year, today.month

    def _has(stmt) -> bool:
        try:
            return bool(db.execute(stmt).scalar())
        except Exception:
            return False

    # 每日: 今天导入过淘宝订单 (今天 created_at 的订单)
    try:
        from app.models.order import Order
        if _has(_sel(func.count()).select_from(Order).where(func.date(Order.created_at) == today)):
            done.add("daily_import_orders")
    except Exception:
        pass

    # 每月: 当月已有数据 = 已导入
    try:
        from app.models.finance import (AccountBalance, AlipayFlow, FactoryReconciliation,
                                         LogisticsBill, RefillRecord, WanshifuBill)
        from app.models.marketing import PromotionFlow
        month_checks = [
            ("monthly_alipay", AlipayFlow, AlipayFlow.transaction_time),
            ("monthly_promotion", PromotionFlow, PromotionFlow.transaction_date),
            ("monthly_refill_records", RefillRecord, RefillRecord.refill_date),
            ("monthly_logistics_bill", LogisticsBill, LogisticsBill.bill_date),
            ("monthly_wanshifu_bill", WanshifuBill, WanshifuBill.bill_date),
            ("monthly_factory_recon", FactoryReconciliation, FactoryReconciliation.period_end),
        ]
        for key, model, col in month_checks:
            if _has(_sel(func.count()).select_from(model).where(
                    func.extract("year", col) == y, func.extract("month", col) == m)):
                done.add(key)
        if _has(_sel(func.count()).select_from(AccountBalance).where(
                AccountBalance.period_year == y, AccountBalance.period_month == m)):
            done.add("monthly_account_balance")
    except Exception:
        pass
    return done


# 各平台 web-agent 取数源 → 中文名 (登录状态用)
_PLATFORMS = [
    ("taobao_report", "淘宝 (订单/报表)"),
    ("promotion", "推广 (直通车/万相台)"),
    ("wanshifu", "万师傅"),
    ("settlement", "结算账单"),
    ("alipay", "支付宝流水"),
]


def platform_login_status(db: Session) -> list[dict]:
    """各平台登录状态 (给待办台账, 用户拍板 2026-06-17):
       - web_agent_pending_scan 里有该平台 → 需扫码, 列出扫码内容;
       - 否则 → "现在可以登录，无需扫码", 附最近成功取数时间。"""
    state: dict = {}
    pending: list = []
    try:
        state = json.loads(settings_service.get(db, "web_agent_state", env_fallback=False) or "{}")
    except Exception:
        state = {}
    try:
        pending = json.loads(settings_service.get(db, "web_agent_pending_scan", env_fallback=False) or "[]")
    except Exception:
        pending = []
    pending_map: dict = {}
    for p in pending if isinstance(pending, list) else []:
        if isinstance(p, dict):
            k = p.get("source") or p.get("platform") or p.get("key") or ""
            pending_map[str(k)] = p
        elif isinstance(p, str):
            pending_map[p] = {"message": "需要扫码登录"}
    out: list[dict] = []
    for key, label in _PLATFORMS:
        ps = pending_map.get(key)
        if ps:
            out.append({"platform": label, "need_scan": True,
                        "message": ps.get("message") or ps.get("content") or "需要扫码登录",
                        "scan_url": ps.get("url") or ps.get("qr"),
                        "last_ok": state.get(key) if isinstance(state, dict) else None})
        else:
            out.append({"platform": label, "need_scan": False,
                        "message": "现在可以登录，无需扫码",
                        "last_ok": state.get(key) if isinstance(state, dict) else None})
    return out


def status(db: Session) -> dict:
    today = date.today()
    done = _load(db)
    auto = _auto_done(db, today)
    groups: dict[str, list] = {"daily": [], "weekly": [], "monthly": []}
    for t in OPS_TASKS:
        pk = _period_key(t["freq"], today)
        mark = f"{t['key']}@{pk}"
        is_auto = t["key"] in auto
        groups[t["freq"]].append({
            "key": t["key"], "title": t["title"], "detail": t["detail"],
            "route": t.get("route"),
            "done": (mark in done) or is_auto, "done_at": done.get(mark),
            "dynamic": False, "auto": is_auto,
        })
    for t in _load_dynamic(db):
        freq = t.get("freq", "daily")
        if freq not in groups:
            freq = "daily"
        pk = _period_key(freq, today)
        mark = f"{t['key']}@{pk}"
        groups[freq].append({
            "key": t["key"], "title": t.get("title", t["key"]), "detail": t.get("detail", ""),
            "route": t.get("route"),
            "done": mark in done, "done_at": done.get(mark), "dynamic": True,
        })
    out = []
    for f in ("daily", "weekly", "monthly"):
        items = groups[f]
        out.append({
            "freq": f, "label": _FREQ_LABEL[f], "period_key": _period_key(f, today),
            "done_count": sum(1 for i in items if i["done"]), "total": len(items),
            "tasks": items,
        })
    return {"groups": out, "today": today.isoformat(),
            "login_status": platform_login_status(db)}


def toggle(db: Session, task_key: str, done: bool, actor: Optional[str] = None) -> dict:
    today = date.today()
    task = next((t for t in OPS_TASKS if t["key"] == task_key), None)
    freq = task["freq"] if task else None
    if freq is None:
        dyn = next((t for t in _load_dynamic(db) if t.get("key") == task_key), None)
        if dyn is None:
            raise ValueError(f"未知任务: {task_key}")
        freq = dyn.get("freq", "daily")
    mark = f"{task_key}@{_period_key(freq, today)}"
    state = _load(db)
    if done:
        state[mark] = datetime.now(timezone.utc).isoformat()
    else:
        state.pop(mark, None)
    settings_service.set_value(db, _SETTING, json.dumps(state), description="运营待办完成状态")
    return status(db)
