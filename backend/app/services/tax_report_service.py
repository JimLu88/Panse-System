# -*- coding: utf-8 -*-
"""淘宝涉税信息报送 → 税费真源 (用户拍板 2026-07-14)。

口径纠正: 税费此前按【下单时间】聚季估算, 税务局实际按【打款/结算】口径 —— 唯一真源是
千牛「财务→收支账单→涉税信息报送账单」页: 报送年度+季度+主体(2026-Q2起=义乌市畔色贸易商行),
收入净额 = 收入总额 − 退款金额, 税 = 净额 × 2%。

链路: Web-Agent 任务 `tax_information`(农场端定义, 规格见 docs/web-agent-tax-task.md)
逐季抓取 → 本服务落库 system_settings[tax_report_quarters] → cash_flow._quarterly_tax
对已报送季度用报送净额, 未报送季度(当季)回退订单估算。Agent 离线/任务未上线 → 软失败不拖垮。
"""
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service, web_agent_service

_log = logging.getLogger("panse.tax_report")

SETTING_KEY = "tax_report_quarters"
TASK_ID = "tax_information"
ENTITY = "义乌市畔色贸易商行(个体工商户)"


def get_reported(db: Session) -> dict:
    """已落库的报送季度: {"2026-Q1": {"net_income": "491255.80", ...}, ...}。缺失/坏 JSON → {}。"""
    try:
        raw = settings_service.get(db, SETTING_KEY, env_fallback=False)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 配置坏不拦现金流
        return {}


def ingest(db: Session, quarters: dict, *, source: str = "taobao涉税报送") -> dict:
    """合并写入报送季度(同季覆盖)。quarters: {"2026-Q1": {"net_income": 491255.80, ...}}。
    只收能转成 Decimal 的 net_income; 附 as_of/source 供审计。返回落库后的全量。"""
    cur = get_reported(db)
    stamp = date.today().isoformat()
    accepted = 0
    for q, v in (quarters or {}).items():
        try:
            net = Decimal(str((v or {}).get("net_income")))
        except Exception:  # noqa: BLE001 - 单季坏数据跳过不拦其余
            continue
        row = {"net_income": str(net), "as_of": stamp, "source": source}
        for extra in ("gross", "refund"):
            if (v or {}).get(extra) is not None:
                row[extra] = str(v[extra])
        if (v or {}).get("provisional"):
            row["provisional"] = True   # 当季预计算(收支账单按月, 三层口径第②层), 报送出数后被覆盖
        cur[str(q)] = row
        accepted += 1
    settings_service.set_value(db, SETTING_KEY, json.dumps(cur, ensure_ascii=False))
    _log.info("涉税报送落库: %d 季 (%s)", accepted, ",".join(sorted(quarters or {})))
    return cur


def pull_via_agent(db: Session, *, year: Optional[int] = None, timeout_s: int = 900) -> dict:
    """经 Web-Agent 抓当年已报送季度。任务未上线/Agent 离线 → {"ok": False} 软失败。

    契约(农场端按 docs/web-agent-tax-task.md 实现): job 结果含
    {"quarters": {"2026-Q1": {"net_income": 491255.80, "gross": ..., "refund": ...}, ...}}。"""
    y = year or date.today().year
    cur_q = (date.today().month - 1) // 3 + 1
    r = web_agent_service.run_task(db, TASK_ID, {
        "year": y, "quarters": list(range(1, cur_q + 1)), "entity": ENTITY,
    })
    if not r.get("ok") or not r.get("job"):
        return {"ok": False, "error": r.get("error") or "任务未上线/Agent离线", "stage": "run"}
    done = web_agent_service.wait_job(db, r["job"], timeout_s=timeout_s)
    if not done.get("ok"):
        return {"ok": False, "error": done.get("error") or "job 未完成", "stage": "wait"}
    payload = done.get("result") or done.get("data") or done
    quarters = (payload or {}).get("quarters")
    if not isinstance(quarters, dict) or not quarters:
        return {"ok": False, "error": "job 结果缺 quarters", "stage": "parse", "raw": str(payload)[:200]}
    stored = ingest(db, quarters)
    return {"ok": True, "ingested": sorted(quarters.keys()), "stored_total": len(stored)}
