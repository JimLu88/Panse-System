"""活动生命周期 P1 引擎测试 (2026-07-17 spec: docs/活动生命周期系统_执行plan.md)。

锁八件事:
① 动销分组 + no_sales 登记表同步 (新零动销自动登记 / 出单只提示转正不移除)
② 报名行: 报名价=日常价 / 占位=min(现行, floor(线/0.88)) / 无线保守值备注 / R4下架过滤 / R3整品完整性
③ 立减公式 spec §二 手算样例: 日常2827.5 / 大促1979.59 / 线1978.89 → 官方340 → 立减508.61
④ 中促 = 大促×1.03 就地计算 + 10% ceil 开关 campaign_official_ceil
⑤ 无动销: 立减 = 日常 − (中促+1); 占位不出行
⑥ R2: 贴线让幅 >1 元 → 剔除并建议轮换
⑦ preflight R1~R12 逐条输出 {rule, level, items}
⑧ 推送编排 (mock WA, 绝不真调 :8500): channel/phase/档期传参 + 状态机推进
"""
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

import openpyxl

from app.models.campaign import CampaignPlan
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_service as cs
from app.services import delisted_sku_service as ds
from app.services import no_sales_service as ns


def _mk(db, pc, code, item, sid, daily=None, big=None, *, placeholder=False,
        line=None, enrolled=None):
    db.add(PricingSku(product_code=pc, sku_code=code, sku=f"SKU{code}",
                      product_name=f"品{pc}",
                      daily_price=Decimal(str(daily)) if daily is not None else None,
                      is_custom_placeholder=placeholder))
    db.add(PricingSkuPromo(
        sku_code=code, taobao_item_id=item, taobao_sku_id=sid,
        big_buyer_price=Decimal(str(big)) if big is not None else None,
        coupon_floor_price=Decimal(str(line)) if line is not None else None,
        enrolled_floor_price=Decimal(str(enrolled)) if enrolled is not None else None))


def _plan(db, ctype="big88"):
    plan = CampaignPlan(name=f"测试{ctype}", campaign_type=ctype,
                        tier=cs.CAMPAIGN_TYPES[ctype][1],
                        start_at=datetime(2026, 7, 17, 20, 0, 0),
                        end_at=datetime(2026, 7, 19, 23, 59, 59), status="draft")
    db.add(plan)
    db.commit()
    return plan


def _order(db, no, pc, status="paid", days_ago=5, platform="淘宝"):
    db.add(Order(platform=platform, order_no=no, product_code=pc, status=status,
                 order_date=date.today() - timedelta(days=days_ago)))


# ── ① 动销分组 + 登记表同步 ──────────────────────────────────────────────────

def test_group_by_sales_and_registry_sync(db_session):
    # ⚠编码数字主体必须互异: brand_variants 按 core_of(剥字母前缀后的数字) 归并跨品牌销量
    _mk(db_session, "PPS26101", "PPS2610101", "9101", "71001", daily=1000, big=800)
    _mk(db_session, "PPS26202", "PPS2620201", "9102", "71002", daily=1000, big=800)
    _mk(db_session, "PPS26303", "PPS2630301", "9103", "71003", daily=1000, big=800)
    _order(db_session, "T1", "P26101")                        # 9101 有动销 (订单用品牌无关 P 形式)
    _order(db_session, "T2", "PPS26202", status="cancelled")  # 关闭单不算 → 9102 零动销
    _order(db_session, "T3", "PPS26303", status="signed")     # 9103 有动销(但已在登记表)
    _order(db_session, "T4", "PPS26101", days_ago=90)         # 60天窗口外不算
    db_session.commit()
    ns.add_no_sales(db_session, ["9103"])                     # 预登记: 后来卖出去了

    g = cs.group_by_sales(db_session)

    assert g["有动销"] == ["9101", "9103"]
    assert g["无动销"] == ["9102"]
    assert g["newly_registered"] == ["9102"]                  # 新零动销自动登记
    assert g["promote_candidates"] == ["9103"]                # 出单提示转正
    assert set(g["registered"]) == {"9102", "9103"}           # ★不自动移除 (R6 单行道)


# ── ② 报名行 builder ─────────────────────────────────────────────────────────

def test_signup_rows_price_placeholder_and_filters(db_session):
    plan = _plan(db_session, "big88")                          # lev = 0.12
    _mk(db_session, "PPSSA001", "PPSSA00101", "9201", "72001", daily=2827.5)
    _mk(db_session, "PPSSA001", "PPSSA00102", "9201", "72002", daily=1500)
    # 占位有线: 现行 = min(1000×0.9, 500) = 500; cap = floor(430/0.88) = 488 → 488
    _mk(db_session, "PPSSA001", "PPSSA00190", "9201", "72090", daily=1000,
        placeholder=True, line=430)
    # 下架SKU (R4): 过滤且不破坏整品完整性
    _mk(db_session, "PPSSA001", "PPSSA00103", "9201", "72003", daily=1600)
    ds.add_delisted(db_session, ["72003"])
    # 占位无线: 现行 500 < floor(1000×0.8/0.88)=909 → 500 + 备注
    _mk(db_session, "PPSSB001", "PPSSB00101", "9202", "72011", daily=2000)
    _mk(db_session, "PPSSB001", "PPSSB00190", "9202", "72091", daily=1000, placeholder=True)
    db_session.commit()

    rows, stats = cs.build_signup_rows(db_session, plan)
    by_sid = {r["taobao_sku_id"]: r for r in rows}

    assert by_sid["72001"]["price"] == 2827.5                 # 报名价 = 日常价 (铁则1)
    assert by_sid["72002"]["price"] == 1500.0
    assert by_sid["72090"]["price"] == 488.0                  # min(现行500, floor(线/0.88)=488)
    assert by_sid["72091"]["price"] == 500.0                  # 无线 → 保守值封顶不生效, 现行500
    assert by_sid["72091"]["remark"] and "0.8" in by_sid["72091"]["remark"]
    assert stats["placeholder_no_line"] == [
        {"sku_code": "PPSSB00190", "remark": by_sid["72091"]["remark"]}]
    assert "72003" not in by_sid and stats["skipped_delisted"] == 1   # R4 过滤
    assert stats["incomplete_items"] == []                    # 下架不算缺 → 整品仍完整
    assert stats["rows"] == len(rows) == 5


def test_signup_rows_exclude_registered_no_sales(db_session):
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSNS001", "PPSNS00101", "9209", "72901",
        daily=1200, big=800)
    db_session.commit()
    ns.add_no_sales(db_session, ["9209"])

    rows, stats = cs.build_signup_rows(db_session, plan)

    assert rows == []
    assert stats["excluded_no_sales_items"] == ["9209"]


def test_honey_sample_is_real_sku_and_stacks_discount(db_session):
    """蜜蜡色样块是正常商品，不得再按定制占位保护价报 18 元。"""
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPS2398001", "PPS2398001060614", "719436834260",
        "6282622238127", daily=30, big=20.41, placeholder=False)
    db_session.commit()

    signup, _ = cs.build_signup_rows(db_session, plan)
    discount, _ = cs.build_discount_rows(db_session, plan)

    assert signup == [{
        "taobao_item_id": "719436834260",
        "taobao_sku_id": "6282622238127",
        "sku_code": "PPS2398001060614",
        "price": 30.0,
        "is_placeholder": False,
        "remark": None,
    }]
    # 低价 SKU 的12%按平台精确值3.60元；30 - 3.60 - 5.99 = 20.41。
    assert discount[0]["official"] == 3.6
    assert discount[0]["deduct"] == 5.99
    assert discount[0]["target_price"] == 20.41


def test_super_reduce_low_price_uses_platform_exact_percent(db_session):
    """超级立减低价 SKU 也按精确10%：25×10%=2.50，不向上取整成3元。"""
    plan = _plan(db_session, "super_reduce")
    _mk(db_session, "PPSLOW10", "PPSLOW1001", "9300", "73010",
        daily=25, big=20.41)
    db_session.commit()

    rows, stats = cs.build_discount_rows(db_session, plan)

    assert rows[0]["official"] == 2.5
    assert rows[0]["deduct"] == 1.48
    assert rows[0]["target_price"] == 21.02
    assert stats["official_low_price_exact"] == 1


def test_signup_rows_r3_incomplete_item_dropped(db_session):
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSSC001", "PPSSC00101", "9203", "72021", daily=None)   # 缺日常价
    _mk(db_session, "PPSSC001", "PPSSC00102", "9203", "72022", daily=900)
    _mk(db_session, "PPSSD001", "PPSSD00101", "9204", "72031", daily=800)    # 对照: 完整品
    db_session.commit()

    rows, stats = cs.build_signup_rows(db_session, plan)

    sids = {r["taobao_sku_id"] for r in rows}
    assert sids == {"72031"}                                  # R3: 半套整品剔除
    assert len(stats["incomplete_items"]) == 1
    inc = stats["incomplete_items"][0]
    assert inc["taobao_item_id"] == "9203" and inc["ok_skus"] == 1
    assert "PPSSC00101" in inc["missing_skus"][0]


# ── ③④⑤⑥ 立减公式 ──────────────────────────────────────────────────────────

def test_discount_formula_big_spec_sample(db_session):
    """spec §二 手算样例: 日常2827.5 × 12% = 339.30 → ceil 340;
    贴线 min(1979.59, 1978.89) = 1978.89; 立减 = 2827.5 − 340 − 1978.89 = 508.61。"""
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSDA001", "PPSDA00101", "9301", "73001",
        daily=2827.5, big=1979.59, line=1978.89)
    _mk(db_session, "PPSDB001", "PPSDB00101", "9302", "73002", daily=2000, big=1500)  # 无线
    db_session.commit()

    rows, stats = cs.build_discount_rows(db_session, plan)
    by_sid = {r["taobao_sku_id"]: r for r in rows}

    assert by_sid["73001"]["deduct"] == 508.61
    assert by_sid["73001"]["official"] == 340.0               # R9 向上取整到元
    assert by_sid["73001"]["target_price"] == 1978.89         # 贴线
    assert stats["line_concessions"] == [{"sku_code": "PPSDA00101", "target": 1979.59,
                                          "line": 1978.89, "concession": 0.7}]
    assert by_sid["73002"]["deduct"] == 260.0                 # 2000 − ceil(240)=240 − 1500


def test_discount_mid_ratio_and_ceil_switch(db_session):
    """超级立减: 中促 = 大促×1.03 就地算 (850→875.5); 10% ceil 开关 campaign_official_ceil。"""
    from app.services import settings_service
    plan = _plan(db_session, "super_reduce")
    _mk(db_session, "PPSDC001", "PPSDC00101", "9303", "73011", daily=995, big=850)
    db_session.commit()

    rows, stats = cs.build_discount_rows(db_session, plan)
    assert stats["official_ceil"] is True                     # 默认向上取整
    assert rows[0]["deduct"] == 19.5                          # 995 − ceil(99.5)=100 − 875.5
    assert rows[0]["target_price"] == 875.5                   # 850 × 1.03

    settings_service.set_value(db_session, cs.OFFICIAL_CEIL_KEY, "0")
    db_session.commit()
    rows2, stats2 = cs.build_discount_rows(db_session, plan)
    assert stats2["official_ceil"] is False
    assert rows2[0]["deduct"] == 20.0                         # 995 − 99.5 − 875.5


def test_discount_nosales_big_direct_and_placeholder_skip(db_session):
    """无动销大促场不报名，单品立减直接到 ERP 大促价 2650；占位不出行。"""
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSDD001", "PPSDD00101", "9304", "73021", daily=3000, big=2650)
    _mk(db_session, "PPSDD001", "PPSDD00190", "9304", "73090", daily=500,
        placeholder=True)                                     # 占位不出行
    db_session.commit()
    ns.add_no_sales(db_session, ["9304"])

    rows, stats = cs.build_discount_rows(db_session, plan)

    assert len(rows) == 1
    assert rows[0]["kind"] == "nosales"
    assert rows[0]["target_price"] == 2650.0
    assert rows[0]["deduct"] == 350.0                         # 3000 − 大促价2650
    assert stats["skipped_placeholder"] == 1


def test_discount_rotation_when_concession_over_one_yuan(db_session):
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSDE001", "PPSDE00101", "9305", "73031", daily=3000, big=2000, line=1990)
    db_session.commit()

    rows, stats = cs.build_discount_rows(db_session, plan)

    assert rows == []                                         # R2: 让幅10元>1 → 不贴线不出行
    assert stats["rotation_suggested"] == [{"sku_code": "PPSDE00101", "target": 2000.0,
                                            "line": 1990.0, "concession": 10.0}]


def test_big_campaign_low_price_uses_platform_exact_percent(db_session):
    """¥30样块的12%平台实测为¥3.60，不套普通商品的整元向上取整。"""
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSLOW01", "PPSLOW0101", "9306", "73041",
        daily=30, big=20.41)
    db_session.commit()

    rows, stats = cs.build_discount_rows(db_session, plan)

    assert rows[0]["official"] == 3.6
    assert rows[0]["deduct"] == 5.99
    assert stats["official_low_price_exact"] == 1


# ── ⑦ preflight ─────────────────────────────────────────────────────────────

def test_preflight_outputs_r1_to_r12(db_session):
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSPA001", "PPSPA00101", "9401", "74001",
        daily=1200, big=1000, enrolled=1100)                  # R1: 日常价 > 已生效价硬底
    _mk(db_session, "PPSPB001", "PPSPB00101", "9402", "74011", daily=None)   # R3: 缺价
    _mk(db_session, "PPSPB001", "PPSPB00102", "9402", "74012", daily=800)
    _mk(db_session, "PPSPC001", "PPSPC00101", "9403", "74021",
        daily=2000, big=1600, line=1590)                      # R2: 让幅10>1 → 轮换
    db_session.commit()
    ns.add_no_sales(db_session, ["9404"])                     # R6 名单

    checks = cs.preflight(db_session, plan)
    by_rule = {c["rule"]: c for c in checks}

    assert [c["rule"] for c in checks] == [f"R{i}" for i in range(1, 13)]
    assert all({"rule", "level", "title", "items"} <= set(c) for c in checks)
    assert by_rule["R1"]["level"] == "warn"
    assert by_rule["R1"]["items"][0]["sku_code"] == "PPSPA00101"
    assert by_rule["R2"]["level"] == "error"
    assert by_rule["R2"]["items"][0]["sku_code"] == "PPSPC00101"
    assert by_rule["R3"]["level"] == "error"
    assert by_rule["R3"]["items"][0]["taobao_item_id"] == "9402"
    assert by_rule["R6"]["level"] == "warn" and "9404" in by_rule["R6"]["items"]
    assert by_rule["R9"]["items"] == [{"official_ceil": True}]
    assert by_rule["R11"]["level"] == "warn" and by_rule["R12"]["level"] == "warn"


# ── ⑧ 推送编排 (mock WA) ─────────────────────────────────────────────────────

def _mock_wa(monkeypatch, calls):
    from app.services import web_agent_service

    def fake_upload(db, channel, phase, xlsx_bytes, filename, **kw):
        calls.append({"channel": channel, "phase": phase, "filename": filename,
                      "xlsx_len": len(xlsx_bytes), **kw})
        return {"ok": True, "job": "job-1"}

    def fake_wait(db, job_id, **kw):
        return {"status": "done", "result": {"ok": True, "submitted": True,
                                             "validation": {
                                                 "total_items": 1, "ok": 1,
                                                 "failed": 0, "terminal": True,
                                                 "failed_items": []}}}

    wb = openpyxl.Workbook()
    ws = wb.active
    for r in range(1, 4):
        ws.cell(r, 1, f"表头{r}")
    bio = io.BytesIO()
    wb.save(bio)

    monkeypatch.setattr(web_agent_service, "upload_file", fake_upload)
    monkeypatch.setattr(web_agent_service, "wait_job", fake_wait)
    monkeypatch.setattr(web_agent_service, "campaign_export_items",
                        lambda db, title, **kw: {
                            "ok": True, "xlsx_bytes": bio.getvalue(),
                            "filename": "当前活动.xlsx"})


def test_push_discount_orchestration(db_session, monkeypatch):
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSQA001", "PPSQA00101", "9501", "75001", daily=2827.5, big=1979.59)
    db_session.commit()
    calls = []
    _mock_wa(monkeypatch, calls)

    res = cs.push_discount(db_session, plan)                  # 默认 stage: 不推进状态机
    assert res["ok"] is True and plan.status == "draft"
    assert calls[0]["channel"] == "single_item_discount" and calls[0]["phase"] == "stage"
    assert calls[0]["start_dt"] == "2026-07-17 20:00:00"      # 档期精确到秒
    assert calls[0]["end_dt"] == "2026-07-19 23:59:59"

    res2 = cs.push_discount(db_session, plan, phase="commit")
    assert res2["ok"] is True and plan.status == "discount_pushed"
    assert calls[1]["phase"] == "commit"


def test_push_signup_orchestration_and_empty_guard(db_session, monkeypatch):
    plan = _plan(db_session, "big88")
    calls = []
    _mock_wa(monkeypatch, calls)

    empty = cs.push_signup(db_session, plan)                  # 没行: 显式报错不打 WA
    assert empty["ok"] is False and calls == []

    _mk(db_session, "PPSQB001", "PPSQB00101", "9502", "75011", daily=1500)
    db_session.commit()
    res = cs.push_signup(db_session, plan)
    assert res["ok"] is True
    assert plan.status == "signup_pushed"                     # R12: stage 即生效
    assert calls[0]["channel"] == "promo_signup" and calls[0]["phase"] == "stage"


def test_push_signup_zero_zero_is_failure_and_feishu_deduped(db_session, monkeypatch):
    plan = _plan(db_session, "big88")
    _mk(db_session, "PPSQZ001", "PPSQZ00101", "9503", "75021",
        daily=1500, big=1000)
    db_session.commit()

    calls = []
    _mock_wa(monkeypatch, calls)
    from app.services import notify_service, web_agent_service
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda db, job_id, **kw: {
            "status": "done",
            "result": {
                "ok": True, "attached": True,
                "validation": {
                    "total_items": 1, "ok": 0, "failed": 0,
                    "terminal": False,
                },
            },
        })
    notices = []
    monkeypatch.setattr(
        notify_service, "broadcast_text",
        lambda db, text, **kw: notices.append({"text": text, **kw})
        or {"feishu": True})

    first = cs.push_signup(db_session, plan)
    second = cs.push_signup(db_session, plan)

    assert first["ok"] is False
    assert "未进入终态" in first["error"]
    assert plan.status == "draft"
    assert len(notices) == 1
    assert "总1品，成功0品，失败0品" in notices[0]["text"]
    assert second["notification"] == {"deduped": True}
