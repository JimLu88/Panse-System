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
    {"key": "monthly_account_balance", "freq": "monthly", "title": "填写所有账户余额", "route": "/account-balances",
     "detail": "账户余额: 录入各账户(支付宝企业/爱群/聚合/推广/银行卡)当月期末余额 + 统计日期"},
    {"key": "monthly_recon", "freq": "monthly", "title": "月度对账 + 推广ROI", "route": "/settlements",
     "detail": "全月逐笔对账; 算推广支出占正式销售额(不含补单)的占比"},
    {"key": "monthly_investment", "freq": "monthly", "title": "更新总投资费用", "route": "/cash-flow",
     "detail": "财务→剩余流水: 编辑总投资费用/保证金, 重新测算可用资金"},
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


def status(db: Session) -> dict:
    today = date.today()
    done = _load(db)
    groups: dict[str, list] = {"daily": [], "weekly": [], "monthly": []}
    for t in OPS_TASKS:
        pk = _period_key(t["freq"], today)
        mark = f"{t['key']}@{pk}"
        groups[t["freq"]].append({
            "key": t["key"], "title": t["title"], "detail": t["detail"],
            "route": t.get("route"),
            "done": mark in done, "done_at": done.get(mark),
        })
    out = []
    for f in ("daily", "weekly", "monthly"):
        items = groups[f]
        out.append({
            "freq": f, "label": _FREQ_LABEL[f], "period_key": _period_key(f, today),
            "done_count": sum(1 for i in items if i["done"]), "total": len(items),
            "tasks": items,
        })
    return {"groups": out, "today": today.isoformat()}


def toggle(db: Session, task_key: str, done: bool, actor: Optional[str] = None) -> dict:
    today = date.today()
    task = next((t for t in OPS_TASKS if t["key"] == task_key), None)
    if not task:
        raise ValueError(f"未知任务: {task_key}")
    mark = f"{task_key}@{_period_key(task['freq'], today)}"
    state = _load(db)
    if done:
        state[mark] = datetime.now(timezone.utc).isoformat()
    else:
        state.pop(mark, None)
    settings_service.set_value(db, _SETTING, json.dumps(state), description="运营待办完成状态")
    return status(db)
