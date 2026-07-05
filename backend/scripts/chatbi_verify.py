# -*- coding: utf-8 -*-
"""ChatBI 对数/冒烟验收脚本 (Plan4 v2 §9.2) —— 对活库(只读)跑, 留证。

在 api 容器内跑: docker exec panse-system-api-1 python scripts/chatbi_verify.py
覆盖: SQL 模板端到端 / service 模板真算 / net_revenue SQL 口径 vs 直算对数 / 红队全拒。
退出码 0=全过, 1=有失败。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlalchemy import text

from app.chatbi import service as chatbi_service
from app.chatbi.catalog import ALLOWED_VIEWS
from app.chatbi.sql_gate import SqlGateError, validate_readonly_select
from app.database import SessionLocal

PASS, FAIL = "PASS", "FAIL"
_fails = 0


def check(name: str, ok: bool, detail: str = ""):
    global _fails
    if not ok:
        _fails += 1
    print(f"[{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def main():
    db = SessionLocal()
    try:
        # ---- 1. service 模板真算 ----
        for q, key in [("本月净利润", "monthly_net_profit"),
                       ("产品毛利率排行", "product_margin_rank"),
                       ("卖得最好的产品", "product_revenue_rank"),
                       ("备货建议", "restock_advice")]:
            r = chatbi_service.ask(db, q, username="verify")
            check(f"service模板 [{q}]", r["template_key"] == key and r["badge"] == "verified",
                  f"route={r['route']} badge={r['badge']} rows={len(r['rows'])}")

        # ---- 2. SQL 模板端到端 (走真实视图) ----
        for q, key in [("退款率趋势", "refund_rate_trend"),
                       ("退款最多的产品", "refund_top_product"),
                       ("客单价趋势", "aov_trend"),
                       ("本月发货金额", "ship_stats"),
                       ("本月补单金额", "refill_stats"),
                       ("今天成交多少", "today_deals")]:
            r = chatbi_service.ask(db, q, username="verify")
            ok = r["template_key"] == key and r["badge"] == "verified" and r["route"] == "template"
            check(f"SQL模板 [{q}]", ok, f"badge={r['badge']} rows={len(r['rows'])} sql={(r.get('sql') or '')[:60]}")

        # ---- 3. 对数: net_revenue(近30天) SQL口径 vs 直算 ----
        end = date.today()
        start = end - timedelta(days=29)
        direct = db.execute(text(
            "SELECT COALESCE(SUM(paid_amount - COALESCE(refund_amount,0)),0) FROM chatbi_v_orders "
            "WHERE is_settled_sale AND NOT is_refill AND order_date BETWEEN :s AND :e"
        ), {"s": start, "e": end}).scalar()
        # 用半生成 assembler 同 spec 拼一条对照
        from app.chatbi import assembler
        from app.chatbi.executor import run_readonly
        from app.chatbi.time_parser import TimeRange
        aq = assembler.assemble({"metric": "net_revenue", "dimensions": []},
                                time_range=TimeRange(start, end, "day", "近30天"))
        cols, rows = run_readonly(aq.sql, db=db)
        via_chatbi = rows[0][0] if rows and rows[0] else 0
        check("对数 net_revenue(近30天) SQL口径一致",
              abs(float(direct or 0) - float(via_chatbi or 0)) < 0.01,
              f"直算={float(direct or 0):.2f} chatbi={float(via_chatbi or 0):.2f}")

        # ---- 4. 红队 (必须全拒) ----
        redteam = [
            "SELECT 1; DROP TABLE chatbi_v_orders",
            "UPDATE chatbi_v_orders SET paid_amount=0",
            "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
            "SELECT * FROM users",
            "SELECT * FROM system_settings",
            "SELECT * FROM chatbi_v_orders FOR UPDATE",
            "SELECT * FROM pg_catalog.pg_tables",
        ]
        rejected = 0
        for sql in redteam:
            try:
                validate_readonly_select(sql, ALLOWED_VIEWS)
            except SqlGateError:
                rejected += 1
        check("红队全部被拒", rejected == len(redteam), f"{rejected}/{len(redteam)}")

        # ---- 5. LIMIT 钳制 + EXPLAIN 干跑真跑一条 ----
        from app.chatbi.executor import run_readonly as _run
        g = validate_readonly_select("SELECT product_name FROM chatbi_v_orders LIMIT 999999", ALLOWED_VIEWS)
        check("LIMIT 钳到 1000", g.limited_to == 1000, str(g.limited_to))
        _, rr = _run(g.safe_sql, db=db)
        check("只读执行成功", isinstance(rr, list), f"rows={len(rr)}")

    finally:
        db.close()

    print(f"\n=== {'全部通过' if _fails == 0 else str(_fails) + ' 项失败'} ===")
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    main()
