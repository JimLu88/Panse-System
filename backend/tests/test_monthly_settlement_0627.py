"""月度对账中心 service 单测 (用户 2026-06-27, 方向三)。

纯函数覆盖 variance 数学 + 导出结构; DB 聚合(打包/运费/配件)已在 NAS 真数据验证
(2026-05 打包 预估¥11178 vs 实际¥11205, 差 +0.24%)。
"""
from decimal import Decimal

from app.services import monthly_settlement_service as mss


def test_variance_row_basic():
    r = mss._variance_row("2026-05", Decimal("100"), Decimal("120"), order_count=10)
    assert r["estimate"] == 100.0 and r["actual"] == 120.0
    assert r["variance"] == 20.0 and r["variance_pct"] == 20.0
    assert r["order_count"] == 10


def test_variance_row_actual_missing():
    r = mss._variance_row("2026-05", Decimal("100"), None)
    assert r["actual"] is None and r["variance"] is None and r["variance_pct"] is None


def test_variance_row_zero_estimate_no_pct():
    # 预估=0 不算百分比(避免除零), 但差异照算。
    r = mss._variance_row("2026-05", Decimal("0"), Decimal("50"))
    assert r["variance"] == 50.0 and r["variance_pct"] is None


def test_simple_domain_totals_and_missing_actual():
    est = {"2026-04": Decimal("8624"), "2026-05": Decimal("11178")}
    act = {"2026-05": Decimal("11205")}  # 4月无账单 → 未录
    cnt = {"2026-04": 52, "2026-05": 72}
    dom = mss._simple_domain("packing", "打包月结", "hint", est, act, cnt)
    assert dom["key"] == "packing" and len(dom["groups"]) == 1
    g = dom["groups"][0]
    rows = {r["period"]: r for r in g["rows"]}
    assert rows["2026-04"]["actual"] is None and rows["2026-04"]["variance"] is None
    assert rows["2026-05"]["variance"] == 27.0          # 11205 - 11178
    assert rows["2026-05"]["order_count"] == 72
    assert g["total_estimate"] == 19802.0               # 8624 + 11178
    assert g["total_actual"] == 11205.0                 # 只累计有账单的月
    assert g["total_variance"] == 11205.0 - 19802.0     # -8597


def test_export_workbook_sheets(monkeypatch):
    fake = {
        "domains": [
            {"key": "parts", "label": "配件月结", "settle_hint": "h",
             "groups": [{"key": "五金", "label": "五金",
                         "rows": [{"period": "2026-05", "estimate": 100.0, "actual": 110.0,
                                   "variance": 10.0, "variance_pct": 10.0, "order_count": 5}],
                         "total_estimate": 100.0, "total_actual": 110.0,
                         "total_variance": 10.0, "total_variance_pct": 10.0}]},
            {"key": "packing", "label": "打包月结", "settle_hint": "h",
             "groups": [{"key": "packing", "label": "打包",
                         "rows": [{"period": "2026-05", "estimate": 200.0, "actual": None,
                                   "variance": None, "variance_pct": None, "order_count": 7}],
                         "total_estimate": 200.0, "total_actual": 0.0,
                         "total_variance": -200.0, "total_variance_pct": None}]},
        ],
        "caliber": "x", "ship_date_basis": True,
    }
    monkeypatch.setattr(mss, "build_center", lambda db: fake)
    # 新版: 配件明细走 export_shipped_orders(真DB) + 打包/运费逐单 → 单测里 mock 成空, 只验结构
    monkeypatch.setattr(mss.prs, "export_shipped_orders",
                        lambda db, **kw: {"orders": [], "period": "test"})
    monkeypatch.setattr(mss, "_packing_freight_orders", lambda db, **kw: ([], []))
    wb = mss.build_export_workbook(db=None)
    titles = wb.sheetnames
    assert "月结汇总" in titles                          # 汇总页(美化)
    assert "配件-五金" in titles                          # 配件账户逐单明细页
    assert "打包月结明细" in titles and "运费月结明细" in titles   # 打包/运费逐单明细页
    summary_rows = list(wb["月结汇总"].iter_rows(values_only=True))
    assert summary_rows[0] == ("域", "分类", "月份", "预估应付", "实际账单", "差异", "差异%", "发货单数")
    # 未录的打包行: 实际列文案="未录"
    packing_rows = [r for r in summary_rows if r[0] == "打包月结"]
    assert packing_rows and packing_rows[0][4] == "未录"


# ── 打包导清单 xlsx: 订单号文本格式(防科学计数法) + 结构 (用户 2026-06-30) ──────
def test_packing_checklist_xlsx_text_order_no_and_structure(monkeypatch):
    fake = {
        "year_month": "2026-05", "order_count": 2,
        "total_est_packing": 300.0, "total_actual_packing": 320.0,
        "orders": [
            {"order_no": "3303143544284025474", "order_date": "2026-05-01",
             "ship_date": "2026-05-10", "customer_name": "张三", "product_name": "餐边柜",
             "sku": "CBG2", "est_packing": 130.0, "actual_packing": 140.0},
            {"order_no": "5116487257108037944", "order_date": "2026-05-02",
             "ship_date": "2026-05-11", "customer_name": "李四", "product_name": "电视柜",
             "sku": "DSG", "est_packing": 170.0, "actual_packing": 180.0},
        ],
    }
    monkeypatch.setattr(mss, "packing_checklist", lambda db, *, year_month: fake)
    wb, _ = mss.build_packing_checklist_xlsx(db=None, year_month="2026-05")
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert headers[0] == "订单号"                 # 订单号首列
    assert headers[-1] == "实际打包费"             # 末列=实际打包费(已配账单回填的实付)
    # 关键: 订单号列文本格式(@), 完整19位不变科学计数法
    assert ws.cell(2, 1).number_format == "@"
    assert ws.cell(2, 1).value == "3303143544284025474"
    # 合计行 = 实际打包费合计
    assert "合计" in str(ws.cell(ws.max_row, 1).value)
    assert ws.cell(ws.max_row, 7).value == 320.0
