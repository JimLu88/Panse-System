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


def _expand_ids(p) -> list[str]:
    """一码多SKU: [主SKUID, *alt] 去重去空 —— 比对表覆盖每个【真上传】的 SKUID(与 builder 同口径)。"""
    ids: list[str] = []
    for _sid in [p.taobao_sku_id, *(p.alt_taobao_sku_ids or [])]:
        if _sid and str(_sid).strip() not in ids:
            ids.append(str(_sid).strip())
    return ids


def _compare_rows(db: Session, channel: str, tier: str) -> list[dict]:
    """系统侧每个要上传 SKU 的目标值 —— 比对表左半(系统要的价), 右半(千牛校验)由 stage 汇总补。
    promo_signup/super_reduce 直接走 data_export.collect_signup_rows(与 builder 同源: 占位封顶到
    已生效价 + 整商品完整性剔除), 行集与真上传 xlsx 恒等; single_item 逐字镜像其 builder。"""
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.services import pricing_calc_service, activity_preflight_service

    rows: list[dict] = []
    if channel in ("promo_signup", "super_reduce"):
        from app.services.data_export_service import collect_signup_rows, _PROMO_SIGNUP_TIERS
        if channel == "promo_signup":
            label, field = "报名价A", _PROMO_SIGNUP_TIERS[tier][1]
        else:
            label, field = "补贴金额", "report_price"
        entries, _stats = collect_signup_rows(db, field)
        for s, p, A in entries:
            if channel == "super_reduce":
                # 全新报名口径(2026-07-12): 补贴 = 活动价×10%, 占位的活动价=占位报名价(A 已是),
                # 与 builder 恒等 —— 占位不再用旧口径 现价×0.1。
                sys_val = round(A * 0.1, 2)
                target = (round(A * 0.9, 2) if getattr(s, "is_custom_placeholder", False)
                          else _f(p.mid_buyer_price))                       # 超级立减到手=中促到手
            else:
                sys_val = A
                target = _f(p.mid_buyer_price) if tier == "mid" else _f(p.big_buyer_price)
            for skuid in _expand_ids(p):
                rows.append({
                    "sku_code": s.sku_code, "taobao_sku_id": skuid,
                    "name": (s.sku or s.product_name or s.sku_code),
                    "value_label": label, "system_value": _f(sys_val),
                    "target_shoudao": target,
                })
        return rows

    # ── single_item_discount: 逐字镜像其 builder ──
    params = pricing_calc_service.get_promo_params(db)
    promo = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars().all()}
    bad = activity_preflight_service.bad_price_product_codes(db)
    from app.services.data_export_service import _TB_DISCOUNT_TIERS
    deduct_field = _TB_DISCOUNT_TIERS[tier][1]                              # mid/big/big618 各自档位
    for s in db.execute(select(PricingSku).order_by(
            PricingSku.product_code, PricingSku.sku_code)).scalars().all():
        p = promo.get(s.sku_code)
        if p is None or not p.taobao_item_id or not p.taobao_sku_id:
            continue
        if (s.product_code or "") in bad:
            continue
        if getattr(s, "is_custom_placeholder", False):
            sys_val = round(_f(s.daily_price) * 0.1, 2) if s.daily_price else None
        else:
            sys_val = pricing_calc_service.single_item_discounts(p, s.daily_price, params).get(deduct_field)
        if sys_val is None:
            continue
        target = _f(p.mid_buyer_price) if tier == "mid" else _f(p.big_buyer_price)
        for skuid in _expand_ids(p):
            rows.append({
                "sku_code": s.sku_code, "taobao_sku_id": skuid,
                "name": (s.sku or s.product_name or s.sku_code),
                "value_label": "立减金额", "system_value": _f(sys_val),
                "target_shoudao": target,
            })
    return rows


def _parse_uploaded_values(channel: str, xlsx_bytes: bytes) -> dict[str, float]:
    """从【真正要上传的那份 xlsx】里读出每个 SKUID 的上传值(立减金额/报名价/补贴金额)。
    比对表以此为"上传值"列 = 所见即所传, 消除比对表与真实上传文件之间的任何算法漂移。"""
    import io
    import openpyxl
    val_col = {"single_item_discount": 3, "promo_signup": 3, "super_reduce": 14}.get(channel)
    data_start = 4 if channel == "promo_signup" else 2   # 报名表模板前 3 行是表头, 数据从第 4 行
    if val_col is None:
        return {}
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = (wb["商品SKU导入列表"] if channel == "promo_signup" and "商品SKU导入列表" in wb.sheetnames
          else wb.worksheets[0])
    out: dict[str, float] = {}
    for row in ws.iter_rows(min_row=data_start, values_only=True):
        if not row or len(row) < val_col:
            continue
        skuid, val = row[1], row[val_col - 1]
        if skuid is None or val is None:
            continue
        try:
            out[str(skuid).strip()] = float(val)
        except (TypeError, ValueError):
            continue
    wb.close()
    return out


# 上传值 vs 系统应填值 判"出入"的浮点保护(1 分钱内视为一致; 超出即严格标红) —— 用户: 必须按系统价
_PRICE_MATCH_EPS = 0.005


def stage(db: Session, channel: str, tier: str = "big") -> dict:
    """挂文件到千牛(不提交) + 建比对表(上传值 vs 系统应填值, 0 容差核对)。
    返回 {ok, compare_rows, price_match_ok, mismatch_count, validation, screenshot_base64, ...}。"""
    from app.services import web_agent_service
    if channel not in _CHANNELS:
        return {"ok": False, "error": f"未知渠道 {channel}"}
    xlsx, stats = _gen_xlsx(db, channel, tier)
    # ★核对: system_value=系统应填(独立重算) vs uploaded_value=真正要上传那份 xlsx 里的数; 差>1分即标出入。
    rows = _compare_rows(db, channel, tier)
    uploaded = _parse_uploaded_values(channel, xlsx)
    mismatch_n = 0
    for row in rows:
        up = uploaded.get(row["taobao_sku_id"])
        sysv = row.get("system_value")
        bad = (up is None) or (sysv is None) or (abs(up - sysv) > _PRICE_MATCH_EPS)
        row["uploaded_value"] = up
        row["mismatch"] = bool(bad)
        if bad:
            mismatch_n += 1
    # 反向兜底: 任何【真上传了却没进比对表】的 SKUID 也当"出入"红行标出(两路未来再分叉也兜得住, commit 不可逆)。
    covered = {row["taobao_sku_id"] for row in rows}
    for skuid, up in uploaded.items():
        if skuid not in covered:
            rows.append({
                "sku_code": "?", "taobao_sku_id": skuid, "name": "⚠️ 仅上传·未进比对(请核实此SKUID)",
                "value_label": "", "system_value": None, "target_shoudao": None,
                "uploaded_value": up, "mismatch": True,
            })
            mismatch_n += 1
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
        "compare_rows": rows,
        "price_match_ok": mismatch_n == 0,
        "mismatch_count": mismatch_n,
        "compare_total": len(rows),
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
