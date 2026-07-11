"""活动上传编排 (ERP 侧, 2026-07-11 用户拍板): 生成表 → Web-Agent 挂到千牛(stage) →
在系统里出【比对表】(系统要的价 vs 千牛校验) → 用户在系统里确认 → commit 真提交(不可逆)。

- stage(): 不可逆动作前的预演 —— 挂文件+读千牛校验+建比对表, 停在提交前;
- commit(): ★不可逆★ 真点『确认设置/提交』, 只在用户看过比对表点确认后调。

渠道: single_item_discount(单品立减, 已通) / promo_signup(大促报名) / super_reduce(超级立减活动)。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

_CHANNELS = {
    "single_item_discount": "单品立减",
    "promo_signup": "大促活动报名",
    "super_reduce": "超级立减活动",
}


def _gen_xlsx(db: Session, channel: str, tier: str) -> tuple[bytes, dict]:
    from app.services import data_export_service as de
    if channel == "single_item_discount":
        bio, stats = de.build_single_item_discount_upload_xlsx(db, tier)
    elif channel == "promo_signup":
        bio, stats = de.build_promo_signup_upload_xlsx(db, tier)
    elif channel == "super_reduce":
        bio, stats = de.build_super_reduce_signup_upload_xlsx(db)
    else:
        raise ValueError(f"未知渠道 {channel}")
    return bio.getvalue(), stats


def _f(x):
    return None if x is None else float(x)


def _compare_rows(db: Session, channel: str, tier: str) -> list[dict]:
    """系统侧每个要上传 SKU 的目标值 —— 比对表左半(系统要的价), 右半(千牛校验)由 stage 汇总补。"""
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.services import pricing_calc_service, activity_preflight_service

    params = pricing_calc_service.get_promo_params(db)
    promo = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars().all()}
    bad = activity_preflight_service.bad_price_product_codes(db)
    rows: list[dict] = []
    for s in db.execute(select(PricingSku).order_by(
            PricingSku.product_code, PricingSku.sku_code)).scalars().all():
        p = promo.get(s.sku_code)
        if p is None or not p.taobao_item_id or not p.taobao_sku_id:
            continue
        if (s.product_code or "") in bad:
            continue
        ph = getattr(s, "is_custom_placeholder", False)
        sys_val = target_shoudao = None
        label = ""
        if channel == "single_item_discount":
            label = "立减金额"
            if ph:
                sys_val = round(_f(s.daily_price) * 0.1, 2) if s.daily_price else None
            else:
                d = pricing_calc_service.single_item_discounts(p, s.daily_price, params)
                sys_val = d.get("big_deduct")
            target_shoudao = _f(p.big_buyer_price)
        elif channel == "promo_signup":
            label = "报名价A"
            sys_val = (round(_f(s.daily_price) * 0.9, 2) if ph and s.daily_price
                       else pricing_calc_service.report_prices(p, params).get("report_price"))
            target_shoudao = _f(p.big_buyer_price)
        elif channel == "super_reduce":
            label = "补贴金额"
            A = (round(_f(s.daily_price) * 0.9, 2) if ph and s.daily_price
                 else pricing_calc_service.report_prices(p, params).get("report_price"))
            sys_val = round(_f(A) * 0.1, 2) if A is not None else None
            target_shoudao = _f(p.mid_buyer_price)
        if sys_val is None:
            continue
        rows.append({
            "sku_code": s.sku_code, "taobao_sku_id": str(p.taobao_sku_id),
            "name": (s.sku or s.product_name or s.sku_code),
            "value_label": label, "system_value": _f(sys_val),
            "target_shoudao": target_shoudao,
        })
    return rows


def stage(db: Session, channel: str, tier: str = "big") -> dict:
    """挂文件到千牛(不提交) + 建比对表。返回 {ok, compare_rows, validation, screenshot_base64, ...}。"""
    from app.services import web_agent_service
    if channel not in _CHANNELS:
        return {"ok": False, "error": f"未知渠道 {channel}"}
    xlsx, stats = _gen_xlsx(db, channel, tier)
    j = web_agent_service.upload_file(db, channel, "stage", xlsx, f"{channel}.xlsx")
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应, 无法上传")}
    final = web_agent_service.wait_job(db, j["job"], timeout_s=200)
    res = final.get("result") or {}
    if res.get("need_scan"):
        return {"ok": False, "need_scan": True, "message": "淘宝登录态过期, 请先扫码后再上传"}
    return {
        "ok": bool(res.get("ok")), "channel": channel,
        "channel_name": _CHANNELS[channel], "gen_stats": stats,
        "validation": res.get("validation"),
        "screenshot_base64": res.get("screenshot_base64"),
        "stopped_before": res.get("stopped_before"),
        "compare_rows": _compare_rows(db, channel, tier),
    }


def commit(db: Session, channel: str, tier: str = "big") -> dict:
    """★不可逆★ 真提交到千牛。只在用户看过比对表、系统里点确认后调用。"""
    from app.services import web_agent_service
    if channel not in _CHANNELS:
        return {"ok": False, "error": f"未知渠道 {channel}"}
    xlsx, _stats = _gen_xlsx(db, channel, tier)
    j = web_agent_service.upload_file(db, channel, "commit", xlsx, f"{channel}.xlsx")
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应")}
    final = web_agent_service.wait_job(db, j["job"], timeout_s=200)
    res = final.get("result") or {}
    return {
        "ok": bool(res.get("submitted")), "channel": channel,
        "channel_name": _CHANNELS[channel], "submitted": res.get("submitted"),
        "validation": res.get("validation"),
        "screenshot_base64": res.get("screenshot_base64"),
    }
