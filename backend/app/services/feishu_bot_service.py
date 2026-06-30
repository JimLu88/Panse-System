"""飞书机器人: 发图 → 自动识别(订单表/订单图/供应商送货单) → 不确定发卡片让用户选 → 确认入库。

流程:
  1. 用户在群里 @机器人 / 私聊发图片 → 飞书推 ``im.message.receive_v1`` 事件 (feishu_webhook_service 分发到这里)。
  2. 下载图片 → ``classify_image`` 用 vision 模型判类型 + 置信度。
  3. 置信度高 → 直接发"确认入库"卡片; 不确定 → 发"选类型"卡片。把待处理项暂存 system_settings。
  4. 用户点卡片按钮 → 飞书推 ``card.action.trigger`` → ``on_card_action`` 重新下载图片、按选定类型解析入库 → 回结果卡片。

凭证 (app_id/secret/verification_token) 存 system_settings, 后台可配; 未配置时优雅降级(记录日志, 不崩)。
AI(OCR vision) 未配置时分类返回 unknown → 走选类型卡片。

⚠️ 需用户提供飞书自建应用凭证 + 在开放平台「事件订阅」加 ``im.message.receive_v1``、
   并开「机器人」能力 + ``im:message``/``im:resource`` 权限后才能真正联调。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import feishu_client, settings_service, vision_ocr_service
from app.services.ai_provider import AiUnavailable

_log = logging.getLogger("panse.feishu_bot")

_PENDING_KEY = "feishu_bot_pending"   # 待处理图片暂存 (message_id -> {...})
_CONFIDENT = 0.75                     # 置信度阈值: 高于此直接确认, 否则让用户选
# 同一会话同一人, 3 分钟内连发的图自动并成"一批"(只问一次类型); 超时/想另起 → 重新 @我
_BATCH_WINDOW_SEC = 180
# 采购单各式各样、易和送货单/其它单据混, 用更高门槛(否则一律让用户点选核对)
_CONFIDENT_BY_KIND = {"purchase": 0.88}


def _threshold(kind: str) -> float:
    return _CONFIDENT_BY_KIND.get(kind, _CONFIDENT)

# 图片类型 → 中文标签 / 入库去向
IMAGE_TYPES = {
    "order_table": "订单表(批量订单截图)",
    "order_image": "订单图(单个订单截图)",
    "supplier_note": "供应商送货单",
    "purchase": "采购单/进货单",
    "factory_recon": "工厂对账单截图",
    "alipay_flow": "支付宝流水截图",
    "packing_bill": "打包费手写账单",
}

_CLASSIFY_SYSTEM = (
    "你是图片分类助手。判断用户发来的图属于哪类，只输出 JSON。\n"
    "类型: order_table=淘宝/千牛批量订单列表截图; order_image=单个订单详情截图; "
    "supplier_note=供应商送货单(送来成品/货物的清单); "
    "purchase=采购单/进货单(我方向供应商买配件/物料的单据); "
    "factory_recon=工厂对账单(工厂列出的下单/账单/已付金额对账表); "
    "alipay_flow=支付宝账单/流水/收支明细截图; "
    "packing_bill=打包工手写的打包费本/便签(手写体, 逐行 客户名+省+产品+打包费金额, 常有'合计'数字); "
    "unknown=都不像。\n"
    '输出: {"kind": "order_table|order_image|supplier_note|purchase|factory_recon|alipay_flow|packing_bill|unknown", '
    '"confidence": 0~1}'
)

# 默认支付宝账户标签 (截图录入与 CSV 导入分账户去重, 避免互相覆盖)
_ALIPAY_SCREENSHOT_ACCOUNT = "支付宝(截图)"


# ── 暂存 (system_settings JSON) ───────────────────────────────
def _load_pending(db: Session) -> dict:
    raw = settings_service.get(db, _PENDING_KEY, env_fallback=False)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_pending(db: Session, data: dict) -> None:
    # 只保留最近 50 条, 防无限增长
    if len(data) > 50:
        for k in sorted(data, key=lambda k: data[k].get("at", ""))[: len(data) - 50]:
            data.pop(k, None)
    settings_service.set_value(db, _PENDING_KEY, json.dumps(data), description="飞书机器人待处理图片")


def _stage(db: Session, message_id: str, info: dict) -> None:
    data = _load_pending(db)
    info["at"] = datetime.now(timezone.utc).isoformat()
    data[message_id] = info
    _save_pending(db, data)


# ── 分类 ──────────────────────────────────────────────────────
def classify_image(db: Session, image_bytes: bytes, *, mime: str = "image/jpeg") -> tuple[str, float]:
    """用 vision 模型判图片类型。返回 (kind, confidence)。AI 未配置 → ('unknown', 0)。"""
    from app.services.ai_provider import build_provider
    try:
        provider = build_provider(settings_service.get_ai_config(db, "ocr"))
    except AiUnavailable:
        return "unknown", 0.0
    try:
        resp = provider.chat_with_image(
            system=_CLASSIFY_SYSTEM, user="判断类型，只输出 JSON。",
            image_bytes=image_bytes, mime=mime, max_tokens=200,
        )
        data = vision_ocr_service._extract_json(resp.text)
        kind = data.get("kind", "unknown")
        conf = float(data.get("confidence") or 0)
        if kind not in IMAGE_TYPES:
            kind, conf = "unknown", 0.0
        return kind, conf
    except Exception as e:  # pragma: no cover - 分类失败退化为让用户选
        _log.warning("飞书机器人分类失败: %s", e)
        return "unknown", 0.0


# ── 卡片 ──────────────────────────────────────────────────────
def _btn(text: str, value: dict, btype: str = "default") -> dict:
    return {"tag": "button", "text": {"tag": "plain_text", "content": text},
            "type": btype, "value": value}


def _picker_card(message_id: str, *, hint: str = "我不太确定这张图的类型，请点选：") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": "📷 收到图片 — 这是哪种？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": hint}},
            {"tag": "action", "actions": [
                _btn("订单表(批量)", {"op": "pick", "message_id": message_id, "kind": "order_table"}, "primary"),
                _btn("订单图(单个)", {"op": "pick", "message_id": message_id, "kind": "order_image"}, "primary"),
                _btn("供应商送货单", {"op": "pick", "message_id": message_id, "kind": "supplier_note"}),
                _btn("采购单/进货单", {"op": "pick", "message_id": message_id, "kind": "purchase"}),
                _btn("工厂对账单", {"op": "pick", "message_id": message_id, "kind": "factory_recon"}),
                _btn("支付宝流水", {"op": "pick", "message_id": message_id, "kind": "alipay_flow"}),
                _btn("打包费手写账单", {"op": "pick", "message_id": message_id, "kind": "packing_bill"}),
                _btn("取消", {"op": "cancel", "message_id": message_id}, "danger"),
            ]},
        ],
    }


def _batch_hint(n: int) -> str:
    """多图整批认不准/不同类时的引导语: 说清「同类型一次发、不同类型分批发」, 并请用户点选整批类型。"""
    return (f"这条消息里有 **{n}** 张图，我没法确定都是同一类（或没认准）。\n"
            f"**同一种类型的图一次性发、不同类型分批发**最稳。\n"
            f"这一批整体是哪类？点一下，我就按这个类型把 **{n}** 张**全部**入库：")


def _confirm_card(message_id: str, kind: str, conf: float, *, n: int = 1) -> dict:
    label = IMAGE_TYPES.get(kind, kind)
    if n > 1:
        title = "📷 识别结果 — 确认全部入库？"
        body = (f"这条消息里 **{n}** 张图都识别为 **{label}**（最低置信度 {conf:.0%}）。"
                f"确认后我把 **{n}** 张**全部**入库。")
    else:
        title = "📷 识别结果，确认入库？"
        body = f"识别为 **{label}**（置信度 {conf:.0%}）。确认后入库。"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green",
                   "title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "action", "actions": [
                _btn("✅ 确认入库", {"op": "pick", "message_id": message_id, "kind": kind}, "primary"),
                _btn("换个类型", {"op": "repick", "message_id": message_id}),
                _btn("取消", {"op": "cancel", "message_id": message_id}, "danger"),
            ]},
        ],
    }


def _result_card(title: str, content: str, template: str = "green") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }


_HELP_CONTENT = (
    "你好，我是畔色 ERP 录入助手 🤖 —— 直接把**图片**或**表格文件**发给我，我会自动识别类型并入库。\n\n"
    "**📷 发图片（截图）**\n"
    "淘宝/千牛订单 · 支付宝流水 · 采购单/进货单 · 工厂对账 · 供应商送货单\n\n"
    "**📄 发表格（Excel / CSV）**\n"
    "订单 · 工厂对账 · 万师傅 · 物流 · 推广 · 微信账单 · 代付台账 · 补单 · 账户余额 · 支付宝\n\n"
    "**🗂️ 多张图一次发**\n"
    "同一类型的图**连着发**就行：**3 分钟内**你发来的图我算作**一批**，只问你**一次**类型、一次性全入库；"
    "**不同类型请分批发**。隔太久或想另起一批，**重新 @我** 即可。\n\n"
    "发来后我会弹卡片让你**确认**；认不准会让你**点选类型**。所有原文件都会按类型归档，"
    "在系统「数据工具 → 导入档案」可随时回看下载。\n\n"
    "> 群里记得 **@我** 再带上图片/文件；私聊我的话直接发就行。"
)


def _help_card() -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📋 使用指南"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": _HELP_CONTENT}}],
    }


def _recent_suppliers(db: Session, limit: int = 9) -> list[tuple[int, str]]:
    """取最近/常用供应商 (id, name), 供送货单归属选择。"""
    from app.models.supplier import Supplier
    rows = db.execute(
        select(Supplier.id, Supplier.name).order_by(Supplier.id.desc()).limit(limit)
    ).all()
    return [(r[0], r[1]) for r in rows]


def _supplier_picker_card(message_id: str, suppliers: list[tuple[int, str]], *, n: int = 1) -> dict:
    """识别为送货单后, 追问"这是哪家供应商?" — 列出候选供应商让用户点选。

    n>1 时表示一整批送货单, 选定后整批归到同一家供应商。
    """
    if not suppliers:
        return _result_card(
            "请先建供应商",
            "识别为供应商送货单，但系统里还没有供应商。请先到 供应商 页新建供应商后再发图。", "orange")
    actions = [
        _btn(name[:18], {"op": "pick_supplier", "message_id": message_id, "supplier_id": sid}, "primary")
        for sid, name in suppliers
    ]
    actions.append(_btn("取消", {"op": "cancel", "message_id": message_id}, "danger"))
    tip = (f"这 **{n}** 张送货单都归到哪家供应商？点一下，我按这家把 {n} 张全部入库："
           if n > 1 else "请点选送货单归属的供应商：")
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📦 这是哪家供应商的送货单？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": tip}},
            {"tag": "action", "actions": actions},
        ],
    }


# ── 入库 ──────────────────────────────────────────────────────
def _to_decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").replace("元", "").strip())
    except (InvalidOperation, ValueError):
        return None


# 千牛/淘宝把收货信息打码用的星号(含全角); 姓名/电话/地址任一带星号 = 被脱敏未解密
_MASK_CHARS = ("*", "＊", "✱", "∗")


def _is_masked_contact(o: dict) -> bool:
    """收货信息是否被千牛/淘宝星号脱敏(姓名/电话/地址任一含 *)。

    脱敏地址无法发货; 更要命的是若先占位入库, 之后解密重发会被订单号去重挡掉
    (真地址永远进不来)。故识别到脱敏一律不入, 让用户在千牛「解密」后重发。
    """
    blob = (
        f"{o.get('customer_name') or ''}"
        f"{o.get('customer_phone') or ''}"
        f"{o.get('customer_address') or ''}"
    )
    return any(ch in blob for ch in _MASK_CHARS)


def _import_orders(db: Session, parsed: dict) -> dict:
    """把 parse_qianniu_order 的结果入 Orders 表 (新单插入, 已存在/脱敏跳过)。"""
    orders = parsed.get("orders") or []
    inserted = skipped = skipped_masked = 0
    masked_nos: list[str] = []
    seen: set[str] = set()
    for o in orders:
        ono = (o.get("order_no") or "").strip()
        if not ono:
            continue
        if ono in seen:          # 同批重复(本批还没 flush, 不能只靠 DB 查)
            skipped += 1
            continue
        seen.add(ono)
        if db.execute(select(Order.id).where(Order.order_no == ono)).first():
            skipped += 1         # 已入库(此前已有真地址) → 静默跳过, 不误报脱敏
            continue
        if _is_masked_contact(o):
            # 收货信息星号脱敏 → 不占位入库(否则解密重发会被去重挡掉), 收集待提示用户解密
            skipped_masked += 1
            masked_nos.append(ono)
            continue
        od = None
        if o.get("order_date"):
            try:
                od = datetime.strptime(str(o["order_date"])[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        db.add(Order(
            platform=o.get("platform") or "淘宝", order_no=ono, order_date=od,
            customer_name=o.get("customer_name"), customer_phone=o.get("customer_phone"),
            customer_address=o.get("customer_address"), product_name=o.get("product_name"),
            sku=o.get("sku"), sku_code=o.get("sku_code"), qty=int(o.get("qty") or 1),
            paid_amount=_to_decimal(o.get("paid_amount")), status="pending_payment",
        ))
        inserted += 1
    db.flush()
    return {"inserted": inserted, "skipped": skipped, "skipped_masked": skipped_masked,
            "masked_nos": masked_nos, "warnings": parsed.get("ocr_warnings") or []}


def _import_alipay_flows(db: Session, parsed: dict) -> dict:
    """把 parse_alipay_flow_screenshot 的结果入 alipay_flows (默认截图账户, 带去重)。"""
    from app.services import alipay_import
    flows = parsed.get("flows") or []
    rep = alipay_import.import_alipay_rows(
        db, flows, account=_ALIPAY_SCREENSHOT_ACCOUNT, commit=False,
    )
    return {"inserted": rep.inserted, "skipped_duplicate": rep.skipped_duplicate,
            "skipped_invalid": rep.skipped_invalid,
            "warnings": parsed.get("ocr_warnings") or []}


def _infer_packing_month(rows: list) -> str:
    """从打包费行的日期推断账期 YYYY-MM(取众数); 全无日期 → 当月。"""
    from collections import Counter
    from datetime import date
    months = [str(r.get("row_date") or "")[:7] for r in rows
              if len(str(r.get("row_date") or "")) >= 7 and str(r.get("row_date"))[4:5] == "-"]
    return Counter(months).most_common(1)[0][0] if months else date.today().strftime("%Y-%m")


def _dispatch_import(db: Session, kind: str, image_bytes: bytes, *,
                     supplier_id: Optional[int] = None) -> dict:
    """按类型解析图片并入库, 返回结果摘要 {ok, summary}。"""
    if kind in ("order_table", "order_image"):
        parsed = vision_ocr_service.parse_qianniu_order(db, image_bytes)
        r = _import_orders(db, parsed)
        masked = r.get("skipped_masked", 0)
        nos = "、".join(r.get("masked_nos", [])[:5])
        # 全部因脱敏没入(典型: 单个订单详情被星号打码) → 不硬塞, 让用户解密重发
        if r["inserted"] == 0 and masked > 0 and r["skipped"] == 0:
            return {"ok": False, "summary": (
                "收货信息被加密(星号 ****)，没法发货也没法入库。\n"
                "请在**千牛后台把收货信息「解密」**后，再截图发我。\n"
                f"涉及订单: {nos}")}
        msg = f"订单入库完成: 新增 **{r['inserted']}** 单, 跳过(已存在) {r['skipped']} 单。"
        if masked > 0:
            msg += (f"\n⚠️ 另有 **{masked}** 单收货信息被加密(星号)已跳过 —— "
                    f"请在千牛「解密」后重发: {nos}")
        if r["warnings"]:
            msg += f"\n⚠️ OCR 提示: {'; '.join(map(str, r['warnings'][:3]))}"
        return {"ok": True, "summary": msg}
    if kind == "alipay_flow":
        parsed = vision_ocr_service.parse_alipay_flow_screenshot(db, image_bytes)
        r = _import_alipay_flows(db, parsed)
        msg = (f"支付宝流水入库完成: 新增 **{r['inserted']}** 笔, 重复 {r['skipped_duplicate']}, "
               f"无效 {r['skipped_invalid']} (账户「{_ALIPAY_SCREENSHOT_ACCOUNT}」)。")
        warns = list(r["warnings"]) + ["长账单截图可能漏行, 完整流水建议用 CSV 导出 / 邮箱自动收取。"]
        msg += f"\n⚠️ {'; '.join(map(str, warns[:3]))}"
        return {"ok": True, "summary": msg}
    if kind == "purchase":
        parsed = vision_ocr_service.parse_purchase_invoice(db, image_bytes)
        from app.services import screenshot_ingest_service
        r = screenshot_ingest_service.commit_purchase_parsed(db, parsed)
        # 仔细核对: 没解析出任何采购明细行 → 多半不是采购单, 不硬塞, 让用户换类型
        if r["inserted"] == 0 and r["skipped"] == 0:
            return {"ok": False, "summary": (
                "没识别到采购明细(物料/数量/单价)。这可能不是采购单 —— "
                "请点「换个类型」重选, 或换张更清晰的采购/进货单。")}
        sup = f"(供应商: {r['supplier']})" if r.get("supplier") else ""
        msg = f"采购单入库完成: 新增 **{r['inserted']}** 行, 跳过 {r['skipped']} 行 {sup}。"
        if r["warnings"]:
            msg += f"\n⚠️ OCR 提示: {'; '.join(map(str, r['warnings'][:3]))}"
        return {"ok": True, "summary": msg}
    if kind == "factory_recon":
        parsed = vision_ocr_service.parse_factory_reconciliation(db, image_bytes)
        from app.services import screenshot_ingest_service
        r = screenshot_ingest_service.commit_factory_recon_parsed(db, parsed)
        msg = f"工厂对账入库完成: 新增 **{r['inserted']}** 行, 跳过(已存在) {r.get('skipped', 0)} 行。"
        if r["warnings"]:
            msg += f"\n⚠️ OCR 提示: {'; '.join(map(str, r['warnings'][:3]))}"
        return {"ok": True, "summary": msg}
    if kind == "packing_bill":
        parsed = vision_ocr_service.parse_packing_bill(db, image_bytes)
        rows = parsed.get("rows") or []
        from app.services import packing_bill_service
        bm = _infer_packing_month(rows)
        r = packing_bill_service.commit_packing_parsed(
            db, rows, bill_month=bm, declared_total=parsed.get("declared_total"))
        if r["inserted"] == 0 and r["skipped"] == 0:
            return {"ok": False, "summary": (
                "没识别到打包费明细(客户名/金额)。换张更清晰的手写账单, 或点「换个类型」。")}
        msg = (f"打包费账单入库({bm}): 新增 **{r['inserted']}** 行, 配单 {r['matched']}, "
               f"剔除(改客户/不计入) {r['excluded']}, 当月应付 **¥{r['payable_total']:.0f}**。")
        if r.get("total_mismatch"):
            msg += (f"\n⚠️ 本子合计与系统应付差 ¥{r['total_mismatch']:.0f}, **已挂异常**, "
                    f"请核对手写金额。")
        msg += "\n（手写识别难免出错 → 到 物流→打包费账单 复核 / 改账期。）"
        return {"ok": True, "summary": msg}
    if kind == "supplier_note":
        if not supplier_id:
            return {"ok": False, "summary": "供应商送货单需先选择归属供应商。"}
        from app.models.supplier import Supplier
        from app.services import delivery_note_service
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            return {"ok": False, "summary": f"供应商不存在: {supplier_id}"}
        note = delivery_note_service.create_from_image(
            db, supplier=supplier, content=image_bytes, mime="image/jpeg",
            original_name="feishu.jpg", uploaded_by="feishu_bot",
        )
        n_lines = len(getattr(note, "lines", []) or [])
        msg = (f"送货单已入库到供应商 **{supplier.name}**: {n_lines} 行"
               f"{f', 合计 ¥{note.total_amount}' if note.total_amount else ''} (待在供应商页确认)。")
        if note.ocr_warnings:
            msg += f"\n⚠️ {'; '.join(map(str, note.ocr_warnings[:2]))}"
        return {"ok": True, "summary": msg}
    return {"ok": False, "summary": f"未知类型: {kind}"}


def _dispatch_batch(db: Session, message_id: str, kind: str, pending: dict, *,
                    supplier_id: Optional[int] = None) -> dict:
    """一条富文本里的多张图, 按同一 kind 逐张入库并汇总。

    单张异常只跳过该张、不连累其余; AiUnavailable 整批同因 → 往外抛, 由调用方统一提示配 OCR。
    """
    images = pending.get("images") or []
    done = failed = 0
    lines: list[str] = []
    for i, im in enumerate(images, 1):
        try:
            img = _load_image(db, message_id, im)   # im 自带 file_key/archived_path, 直接当 pending 用
            r = _dispatch_import(db, kind, img, supplier_id=supplier_id)
        except AiUnavailable:
            raise
        except Exception as e:  # pragma: no cover - 单张坏不连累其余
            failed += 1
            lines.append(f"• 第{i}张：出错，已跳过")
            _log.warning("批量第%d张入库失败: %s", i, e)
            continue
        if r.get("ok"):
            done += 1
        else:
            failed += 1
        first_line = (r.get("summary") or "").splitlines()[0] if r.get("summary") else ""
        lines.append(f"• 第{i}张：{first_line}")
    label = IMAGE_TYPES.get(kind, kind)
    head = (f"批量【{label}】共 {len(images)} 张：✅ 成功 **{done}** 张"
            + (f"，⚠️ 未入 {failed} 张" if failed else "") + "。")
    body = "\n".join(lines[:8]) + (f"\n…共 {len(lines)} 条明细" if len(lines) > 8 else "")
    return {"ok": done > 0, "summary": head + ("\n" + body if body else ""),
            "done": done, "failed": failed}


# ── 原图兜底归档 + 取图(失败回退归档副本) ────────────────────
# 分类 → 归档去向(imports/{kind}/年/月); 未知类一律进 screenshot 兜底, 绝不丢原图
_ARCHIVE_KIND = {
    "order_table": "orders", "order_image": "orders",
    "alipay_flow": "alipay", "supplier_note": "screenshot",
    "purchase": "purchase", "factory_recon": "factory_recon",
}


def _archive_bytes(db: Session, content: bytes, archive_kind: str, original_name: str,
                   *, uploaded_by: Optional[str] = None) -> Optional[tuple[str, int]]:
    """把飞书原文件按类型落盘归档(imports/{kind}/年/月)+ 登记 ImportedFile, 返回 (stored_path, file_id)。

    兜底用: 即使后续解析/入库失败或用户取消, 原件也已保存, 不会因飞书清理资源而丢失。
    uploaded_by: 记录发图人(飞书 sender id), 供「导入档案」显示上传人。
    归档失败只记日志, 绝不影响主流程。
    """
    try:
        from app.services import import_storage
        arch = import_storage.archive(
            db, content=content, original_name=original_name,
            kind=archive_kind, source="feishu", uploaded_by=uploaded_by,
        )
        return arch.file.stored_path, arch.file.id
    except Exception as e:  # pragma: no cover
        _log.warning("飞书原件归档失败(不影响入库): %s", e)
        return None


def _archive_image(db: Session, img: bytes, kind: str,
                   *, uploaded_by: Optional[str] = None) -> Optional[tuple[str, int]]:
    """图片按分类归档(未知类→screenshot 兜底)。返回 (stored_path, file_id)。"""
    return _archive_bytes(db, img, _ARCHIVE_KIND.get(kind, "screenshot"),
                          f"feishu_{kind}.jpg", uploaded_by=uploaded_by)


def _sender_label(db: Session, event: dict) -> Optional[str]:
    """发图人(作 上传人)。优先用 open_id 查通讯录解析真实姓名(已开 contact:user.base:readonly);
    查不到再回退 id。"""
    sid = (event.get("sender") or {}).get("sender_id") or {}
    open_id = sid.get("open_id")
    if open_id:
        try:
            name = feishu_client.get_user_name(db, open_id)
            if name:
                return name
        except Exception:  # pragma: no cover - 解析失败不影响入库
            pass
    uid = open_id or sid.get("user_id") or sid.get("union_id")
    return f"飞书:{uid}" if uid else "飞书"


def _pending_file_ids(pending: dict) -> list[int]:
    """该暂存项归档的 ImportedFile id 列表 (批量含每张图)。"""
    if pending.get("is_batch"):
        return [im.get("archived_file_id") for im in (pending.get("images") or [])]
    return [pending.get("archived_file_id")]


def _mark_archive_result(db: Session, file_ids: list[int], ok: bool, note: str) -> None:
    """把导入是否成功回写到归档文件的 row_summary, 供「导入档案」显示导入结果。失败不影响主流程。"""
    from app.services import import_storage
    head = (note or "").splitlines()[0] if note else ""
    for fid in file_ids:
        if not fid:
            continue
        try:
            import_storage.update_summary(db, fid, {"ok": bool(ok), "note": head[:140]})
        except Exception as e:  # pragma: no cover
            _log.warning("回写导入结果失败(忽略): %s", e)


def _load_image(db: Session, message_id: str, pending: dict) -> bytes:
    """取原件: 先从飞书下载(图片/文件按 is_file 区分); 失败则回退读归档副本(防飞书资源过期)。"""
    type_ = "file" if pending.get("is_file") else "image"
    try:
        return feishu_client.download_message_resource(
            db, message_id, pending["file_key"], type_=type_)
    except Exception as e:
        ap = pending.get("archived_path")
        if ap:
            try:
                from app.services import import_storage
                _log.warning("飞书取件失败, 回退归档副本: %s", e)
                return import_storage.read(ap)
            except Exception:  # pragma: no cover
                pass
        raise


# ── Excel/CSV 文件(飞书 file 消息) — 类型注册/识别/路由见 table_ingest_service ──
from app.services import table_ingest_service as _tis

# key → 中文标签 (供 confirm/picker/路由判断; FILE_TYPES 成员即"是表格文件类型")
FILE_TYPES = {k: t["label"] for k, t in _tis.TABLE_TYPES.items()}


def _file_archive_kind(fkind: Optional[str]) -> str:
    t = _tis.TABLE_TYPES.get(fkind or "")
    return t["archive"] if t else "generic"


def _file_confirm_card(message_id: str, fkind: str, file_name: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green", "title": {"tag": "plain_text", "content": "📄 收到表格，确认导入？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"`{file_name}` 识别为 **{FILE_TYPES.get(fkind, fkind)}**。确认后导入。"}},
            {"tag": "action", "actions": [
                _btn("✅ 确认导入", {"op": "pick", "message_id": message_id, "kind": fkind}, "primary"),
                _btn("换个类型", {"op": "repick_file", "message_id": message_id}),
                _btn("取消", {"op": "cancel", "message_id": message_id}, "danger"),
            ]},
        ],
    }


def _file_picker_card(message_id: str, file_name: str) -> dict:
    """拿不准类型时, 列出全部表格类型让用户点选(从注册表动态生成)。"""
    actions = [
        _btn(t["label"], {"op": "pick", "message_id": message_id, "kind": k}, "primary")
        for k, t in _tis.TABLE_TYPES.items()
    ]
    actions.append(_btn("取消", {"op": "cancel", "message_id": message_id}, "danger"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📄 这个表格是哪类？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"`{file_name}` 没认准，请点选类型："}},
            {"tag": "action", "actions": actions},
        ],
    }


def _dispatch_file(db: Session, kind: str, content: bytes, file_name: Optional[str]) -> dict:
    """按表格类型导入 (复用既有导入服务, CSV/xlsx 通吃)。"""
    return _tis.import_table(db, kind, content, file_name)


def _on_file_message(db: Session, msg: dict, *, uploaded_by: Optional[str] = None) -> Optional[dict]:
    """飞书 file 消息(Excel/CSV)→ 归档兜底 + 按文件名粗判类型 → 确认/选类型卡。"""
    message_id = msg.get("message_id")
    try:
        content_meta = json.loads(msg.get("content") or "{}")
    except Exception:
        content_meta = {}
    file_key = content_meta.get("file_key")
    file_name = content_meta.get("file_name") or ""
    if not (message_id and file_key):
        return None
    if not file_name.lower().endswith((".xlsx", ".xls", ".csv")):
        _safe_reply(db, message_id, _result_card(
            "暂不支持该文件", f"目前只认 Excel/CSV 表格(.xlsx/.xls/.csv)。收到: {file_name}", "orange"))
        return {"ignored": True, "file_name": file_name}

    fkind = None
    archived_path = None
    archived_file_id = None
    downloaded = False
    try:
        data = feishu_client.download_message_resource(db, message_id, file_key, type_="file")
        downloaded = True
        fkind = _tis.classify_table(file_name, data)   # 文件名 + 表头结构 结合判类型
        arch = _archive_bytes(db, data, _file_archive_kind(fkind), file_name, uploaded_by=uploaded_by)
        if arch:
            archived_path, archived_file_id = arch
    except Exception as e:
        _log.warning("飞书取文件失败: %s", e)
    if not downloaded:
        # 连原件都没取到 → 别给确认卡误导用户, 直接让其重发
        _safe_reply(db, message_id, _result_card(
            "文件获取失败", f"`{file_name}` 从飞书下载失败, 请重新发一次。", "red"))
        return {"message_id": message_id, "error": "download_failed"}

    _stage(db, message_id, {"file_key": file_key, "is_file": True, "file_name": file_name,
                            "kind": fkind, "archived_path": archived_path,
                            "archived_file_id": archived_file_id})
    card = _file_confirm_card(message_id, fkind, file_name) if fkind \
        else _file_picker_card(message_id, file_name)
    _safe_reply(db, message_id, card)
    return {"message_id": message_id, "file_kind": fkind, "card_sent": True}


# ── 富文本(post)解析: 群里 @机器人 并带图时, 飞书发的是 post, 图片内嵌其中 ──
def _post_image_keys(content_meta: dict) -> list[str]:
    """从富文本(post)消息内容里按顺序抽出所有内嵌图片的 image_key。

    post content 结构: {"title":.., "content": [[{"tag":"img","image_key":..},
    {"tag":"at",..}, {"tag":"text",..}], ...]}  —— content 是「行」的列表, 行是「段」的列表。
    """
    keys: list[str] = []
    for line in content_meta.get("content") or []:
        if not isinstance(line, list):
            continue
        for seg in line:
            if isinstance(seg, dict) and seg.get("tag") == "img" and seg.get("image_key"):
                keys.append(seg["image_key"])
    return keys


# ── 多图归批: 同一会话同一人 3 分钟内连发的图算一批, 只问一次类型 ──
def _batch_key(event: dict, msg: dict) -> Optional[str]:
    """归批键 = 会话 + 发送人。同一群/单聊里同一个人连发的图才会归到一批。取不到则不归批。"""
    chat = msg.get("chat_id") or ""
    sid = (event.get("sender") or {}).get("sender_id") or {}
    sender = sid.get("open_id") or sid.get("user_id") or sid.get("union_id") or ""
    return f"{chat}:{sender}" if (chat or sender) else None


def _decide_batch_kind(images: list[dict]) -> tuple[str, float]:
    """整批判一个类型: 每张都可信且都同一类 → 该类型; 否则 unknown(让用户点选)。返回(kind, 最低置信度)。"""
    confident_same = (
        len(images) > 0
        and all(it["kind"] in IMAGE_TYPES and it["conf"] >= _threshold(it["kind"]) for it in images)
        and len({it["kind"] for it in images}) == 1
    )
    kind = images[0]["kind"] if confident_same else "unknown"
    return kind, min((it["conf"] for it in images), default=0.0)


def _build_batch_card(db: Session, anchor_id: str, images: list[dict],
                      batch_kind: str, min_conf: float) -> dict:
    """按整批类型选卡片: 送货单→选供应商; 同一可信类型→确认卡; 否则→选类型卡。n=1 退化为单图措辞。"""
    n = len(images)
    if batch_kind == "supplier_note":
        return _supplier_picker_card(anchor_id, _recent_suppliers(db), n=n)
    if batch_kind != "unknown":
        return _confirm_card(anchor_id, batch_kind, min_conf, n=n)
    hint = _batch_hint(n) if n > 1 else "我不太确定这张图的类型，请点选："
    return _picker_card(anchor_id, hint=hint)


def _find_open_batch(db: Session, batch_key: Optional[str]) -> Optional[str]:
    """找同一会话同一人、3 分钟内、尚未入库的开放批次锚点 message_id(取最近一条); 没有则 None。"""
    if not batch_key:
        return None
    data = _load_pending(db)
    now = datetime.now(timezone.utc)
    best: Optional[str] = None
    for mid, rec in data.items():
        if rec.get("batch_key") != batch_key or rec.get("decided") or rec.get("is_file"):
            continue
        try:
            age = (now - datetime.fromisoformat(rec["at"])).total_seconds()
        except Exception:
            continue
        if age > _BATCH_WINDOW_SEC:
            continue
        if best is None or rec["at"] > data[best]["at"]:
            best = mid
    return best


def _append_to_batch(db: Session, anchor_id: str, item: dict) -> Optional[dict]:
    """把新图并入开放批次(必要时把单图锚点升级为批次), 刷新原卡片张数/类型。锚点已不在 → None。"""
    data = _load_pending(db)
    rec = data.get(anchor_id)
    if not rec:
        return None
    if not rec.get("is_batch"):   # 单图锚点 → 升级为批次(把它自己作为第一张)
        rec["images"] = [{"file_key": rec.get("file_key"), "kind": rec.get("kind"),
                          "conf": rec.get("conf", 0.0), "archived_path": rec.get("archived_path"),
                          "archived_file_id": rec.get("archived_file_id")}]
        rec["is_batch"] = True
    rec["images"].append(item)
    rec["at"] = datetime.now(timezone.utc).isoformat()
    batch_kind, min_conf = _decide_batch_kind(rec["images"])
    rec["kind"] = batch_kind
    data[anchor_id] = rec
    _save_pending(db, data)
    _patch_card_safe(db, rec.get("card_msg_id"),
                     _build_batch_card(db, anchor_id, rec["images"], batch_kind, min_conf))
    return {"message_id": anchor_id, "grouped_into": anchor_id,
            "n": len(rec["images"]), "kind": batch_kind}


def _process_image(db: Session, message_id: str, image_key: str, *,
                   batch_key: Optional[str] = None, uploaded_by: Optional[str] = None) -> dict:
    """下载图 → 分类 → 兜底归档 → (并入 3 分钟内同会话批次 / 否则新建) → 回卡。

    单聊直接发图(image) 与 群里 @机器人 带图(post 内嵌) 共用此路径。
    """
    kind, conf = "unknown", 0.0
    archived_path: Optional[str] = None
    archived_file_id: Optional[int] = None
    try:
        img = feishu_client.download_message_resource(db, message_id, image_key)
        kind, conf = classify_image(db, img)
        # 收到即按类型归档原图(兜底): 即便后续取消/失败, 原图也不丢
        arch = _archive_image(db, img, kind, uploaded_by=uploaded_by)
        if arch:
            archived_path, archived_file_id = arch
    except Exception as e:  # 下载/分类失败 → 仍让用户选类型, 不崩
        _log.warning("飞书机器人取图/分类失败: %s", e)

    item = {"file_key": image_key, "kind": kind, "conf": conf,
            "archived_path": archived_path, "archived_file_id": archived_file_id}
    # 3 分钟内同一会话同一人已有开放批次 → 并进去(不再单独弹卡), 只刷新原卡片
    if batch_key:
        anchor = _find_open_batch(db, batch_key)
        if anchor:
            r = _append_to_batch(db, anchor, item)
            if r is not None:
                return r
    # 新建: 先按单图存(顶层留 file_key 兼容); 若 3 分钟内再来图会自动并成批
    batch_kind, min_conf = _decide_batch_kind([item])
    card = _build_batch_card(db, message_id, [item], batch_kind, min_conf)
    card_msg_id = _safe_reply(db, message_id, card)
    _stage(db, message_id, {"file_key": image_key, "kind": kind, "conf": conf,
                            "archived_path": archived_path, "archived_file_id": archived_file_id,
                            "batch_key": batch_key, "card_msg_id": card_msg_id})
    return {"message_id": message_id, "kind": kind, "confidence": conf, "card_sent": True}


def _process_batch(db: Session, message_id: str, image_keys: list[str], *,
                   batch_key: Optional[str] = None, uploaded_by: Optional[str] = None) -> dict:
    """一条富文本里的多张图: 逐张下载+分类+兜底归档, 判一个整批类型, 出一张卡。

    - 全部同一可信类型 → "确认全部入库"卡(送货单则先选供应商, 应用到整批)。
    - 认不准/类型不一 → 选类型卡(说明同类型一次发/不同类型分批发), 点一次整批入库。
    点选后由 _dispatch_batch 逐张入库 —— 一条消息里的图都不丢。
    """
    items: list[dict] = []
    for k in image_keys:
        kind, conf, ap, afid = "unknown", 0.0, None, None
        try:
            img = feishu_client.download_message_resource(db, message_id, k)
            kind, conf = classify_image(db, img)
            arch = _archive_image(db, img, kind, uploaded_by=uploaded_by)   # 收到即归档(兜底)
            if arch:
                ap, afid = arch
        except Exception as e:
            _log.warning("批量取图/分类失败: %s", e)
        items.append({"file_key": k, "kind": kind, "conf": conf,
                      "archived_path": ap, "archived_file_id": afid})
    batch_kind, min_conf = _decide_batch_kind(items)
    card = _build_batch_card(db, message_id, items, batch_kind, min_conf)
    card_msg_id = _safe_reply(db, message_id, card)
    _stage(db, message_id, {"is_batch": True, "batch_key": batch_key, "images": items,
                            "kind": batch_kind, "conf": min_conf, "card_msg_id": card_msg_id})
    return {"message_id": message_id, "batch": len(items), "kind": batch_kind, "card_sent": True}


# ── 事件入口 (feishu_webhook_service / feishu_ws_service 调用) ──
# 兼容多种顺手写法: 「发货密码 xxx」「密码xxx」「密码：xxx」「发货密码:xxx」
# 必须带「密码」前缀(发货可选), 防裸口令误判。分隔符(空格/冒号)可有可无, 故「密码0Sd4SDS7」也认。
_SHIPPING_PWD_RE = re.compile(r"^\s*(?:发货)?密码\s*[:：]?\s*(\S+)\s*$")


def _extract_shipping_password(text: str) -> Optional[str]:
    """从文本里取发货报表口令。需带「密码」前缀(发货可选), 分隔符可省, 防裸消息误判。"""
    m = _SHIPPING_PWD_RE.match(text or "")
    return m.group(1) if m else None


def apply_shipping_password(db: Session, pwd: str) -> dict:
    """存最新发货报表口令 + 时间戳, 立刻用它重试解密待解的加密发货报表; 返回 reingest 结果。

    纯落地(不回飞书卡、不转发), 供本地飞书入站处理 + 跨机转发端点共用。
    修复 (2026-06-15): 收到口令即 reingest_pending_shipping 自动解密入库, 打通"发口令→入库"。"""
    settings_service.set_value(db, "taobao_shipping_pwd_latest", pwd,
                               description="淘宝发货报表最新解密口令(一次一密)")
    settings_service.set_value(db, "taobao_shipping_pwd_at",
                               datetime.now(timezone.utc).isoformat(),
                               description="发货报表口令收到时间")
    db.commit()
    try:
        from app.services import agent_ingest_service
        r = agent_ingest_service.reingest_pending_shipping(db)
    except Exception:  # noqa: BLE001
        return {"imported": 0, "tried": 0}
    # 飞书成功推送 (用户 2026-06-28): 解密成功 → 推到飞书群, 清晰可见(不只回卡)。
    # 在本核心做 → 无论本地飞书入站还是跨机转发(NAS reingest), 实际解密成功的那台都会推。
    imp = r.get("imported") or 0
    if imp:
        # 解密补上收货地址后, 自动重推此前【缺地址被推】的下单图。
        # 根治用户 2026-06-30 反馈"飞书里解密后为什么没有进一步自动重新发下单图":
        # 缺地址的下单图推过一次即标 pushed, 自动推送永久跳过, 地址回来也不再发。
        repushed = 0
        try:
            from app.services import order_sheet_archive_service as _osa
            repushed = _osa.repush_after_address_fill(db, quiet=True).get("repushed", 0)
        except Exception:  # noqa: BLE001 —— 重推失败不阻断解密入库
            logging.getLogger("panse.feishu_bot").warning("解密后重推下单图失败", exc_info=True)
        r["repushed"] = repushed
        try:
            chat = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
            if chat:
                msg = (f"✅ 发货报表已自动解密 {imp} 份(更新订单 {r.get('updated') or 0} 单),"
                       f"收货地址已入库")
                msg += (f", 并已自动重推 {repushed} 张此前缺地址的下单图到工厂群。"
                        if repushed else ",可正常发下单图。")
                feishu_client.send_text(db, chat, msg)
        except Exception:  # noqa: BLE001 —— 推送失败不阻断解密
            logging.getLogger("panse.feishu_bot").warning("解密成功飞书推送失败")
    return r


def _relay_shipping_password(db: Session, pwd: str) -> Optional[dict]:
    """根治飞书↔报表分离 (用户 2026-06-27): 飞书事件进取数机(PC), 但加密发货报表+生产库在 NAS。
    取数机上配 ``shipping_pwd_relay_url`` 指向生产机 → 收到口令即转发, 让报表所在机解密入库。
    生产机不配此项 → 不转发(同份代码两机共用, 靠配置区分)。机器间用飞书 verification_token 当共享密钥。
    返回 None=未配置/转发失败; dict=生产机 reingest 结果。"""
    url = (settings_service.get(db, "shipping_pwd_relay_url", env_fallback=False) or "").strip()
    if not url:
        return None
    token = settings_service.get(db, "feishu_verification_token", env_fallback=False) or ""
    try:
        import httpx
        resp = httpx.post(url.rstrip("/") + "/api/feishu/relay-shipping-password",
                          json={"pwd": pwd}, headers={"X-Relay-Token": token}, timeout=120)
        if resp.status_code == 200:
            return resp.json()
        logging.getLogger("panse.feishu_bot").warning(
            "发货口令转发失败 HTTP %s: %s", resp.status_code, resp.text[:120])
    except Exception as e:  # noqa: BLE001 —— 转发失败不阻断本地处理
        logging.getLogger("panse.feishu_bot").warning("发货口令转发异常: %s", e)
    return None


def _capture_shipping_password(db: Session, message_id: str, pwd: str) -> dict:
    """收到飞书发货口令: 本地落地(存+解密) + 转发生产机(若配置) + 回执卡片。"""
    r = apply_shipping_password(db, pwd)
    imported = r.get("imported") or 0
    if imported:
        body = (f"已用口令自动解密并导入 {imported} 份发货报表 (更新订单 {r.get('updated') or 0} 单)。")
    elif r.get("tried"):
        body = "口令已存, 但本机待解密的发货报表用它仍打不开 (一报一密)。"
    else:
        body = "下次导入加密发货报表时将自动用它解密 (一次一密, 仅最近一条有效)。"
    # 根治飞书↔报表分离: 把口令转发给报表所在的生产机
    relay = _relay_shipping_password(db, pwd)
    if relay is not None:
        ri = relay.get("imported") or 0
        if ri:
            body += f" 已同步生产机并解密 {ri} 份(更新 {relay.get('updated') or 0} 单)。"
            imported = imported or ri
        else:
            body += " 已同步生产机(暂无可解报表)。"
    _safe_reply(db, message_id, _result_card("已收到发货报表口令", body, "green"))
    return {"message_id": message_id, "kind": "shipping_password",
            "captured": True, "imported": imported}


# ── 飞书「售后」关键词多步录入 (2026-06-12) ──
# 流程: 发「售后」→ 回"请录入售后单号" → 发单号(标记售后)→ 回"请输入售后备注"
#       → 发备注 → 回"售后录入已完成"。标记后该单在 订单视图 显示为「售后中」(见 orders.list_orders)。
KEY_AS_FLOW = "feishu_as_flow"   # 售后录入会话状态前缀 (按 chat_id)


def _as_flow_key(chat_id: str) -> str:
    return f"{KEY_AS_FLOW}:{chat_id}"


def _get_as_flow(db: Session, chat_id: str) -> Optional[dict]:
    if not chat_id:
        return None
    raw = settings_service.get(db, _as_flow_key(chat_id), env_fallback=False)
    if not raw:
        return None
    try:
        st = json.loads(raw)
    except json.JSONDecodeError:
        return None
    from datetime import datetime, timedelta
    at = st.get("at")
    if at:   # 30 分钟未续 → 视为过期, 防卡死会话
        try:
            ts = datetime.fromisoformat(at)
            now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
            if now - ts > timedelta(minutes=30):
                return None
        except ValueError:
            pass
    return st


def _set_as_flow(db: Session, chat_id: str, st: Optional[dict]) -> None:
    from datetime import datetime, timezone
    if st is None:
        settings_service.set_value(db, _as_flow_key(chat_id), "")
    else:
        st["at"] = datetime.now(timezone.utc).isoformat()
        settings_service.set_value(db, _as_flow_key(chat_id), json.dumps(st, ensure_ascii=False),
                                   description="飞书售后录入会话状态")
    db.commit()


def _mark_order_aftersales(db: Session, order_no: str) -> tuple[int, bool]:
    """为订单建/取「活跃」售后条目, 返回 (after_sales_id, 是否匹配到订单)。幂等(同单复用)。"""
    from sqlalchemy import select
    from app.models.marketing import AfterSales
    from app.models.order import Order
    from datetime import date
    matched = db.execute(
        select(Order.id).where(Order.order_no == order_no)).scalar_one_or_none() is not None
    existing = db.execute(
        select(AfterSales).where(
            AfterSales.platform_order_no == order_no,
            AfterSales.reason == "人工标记(飞书)",
            (AfterSales.status.is_(None)) | (AfterSales.status != "已完成"),
        ).order_by(AfterSales.id.desc())
    ).scalars().first()
    if existing:
        return existing.id, matched
    a = AfterSales(platform_order_no=order_no, reason="人工标记(飞书)",
                   status="处理中", processed_at=date.today(), remark="飞书录入")
    db.add(a)
    db.flush()
    return a.id, matched


def _apply_aftersales_remark(db: Session, as_id: Optional[int], remark: str) -> None:
    from app.models.marketing import AfterSales
    if not as_id:
        return
    a = db.get(AfterSales, as_id)
    if a:
        a.remark = f"飞书录入: {remark}" if remark else "飞书录入"
        db.flush()


def _handle_aftersales_flow(db: Session, msg: dict, message_id: str, text: str) -> Optional[dict]:
    """「售后」关键词多步录入。处理了返回结果 dict; 与售后无关返回 None。"""
    chat_id = msg.get("chat_id") or ""
    flow = _get_as_flow(db, chat_id)
    if text in ("售后", "/售后", "售后录入"):   # 启动 (精确匹配防误触)
        _set_as_flow(db, chat_id, {"step": "order"})
        _safe_reply(db, message_id, _result_card("售后录入", "请录入售后单号。", "blue"))
        return {"message_id": message_id, "kind": "aftersales_flow", "step": "order"}
    if not flow:
        return None
    step = flow.get("step")
    if step == "order":
        order_no = (text or "").strip()
        if not order_no:
            _safe_reply(db, message_id, _result_card("售后录入", "请录入售后单号(直接发订单号)。", "blue"))
            return {"message_id": message_id, "kind": "aftersales_flow", "step": "order"}
        as_id, matched = _mark_order_aftersales(db, order_no)
        db.commit()
        flow.update({"step": "remark", "order_no": order_no, "as_id": as_id})
        _set_as_flow(db, chat_id, flow)
        note = "" if matched else "\n(注: 该单号未在订单表找到, 售后条目已登记)"
        _safe_reply(db, message_id, _result_card(
            "已标记售后", f"订单 {order_no} 已标记为售后。{note}\n请输入售后备注。", "orange"))
        return {"message_id": message_id, "kind": "aftersales_flow", "step": "remark"}
    if step == "remark":
        _apply_aftersales_remark(db, flow.get("as_id"), (text or "").strip())
        db.commit()
        _set_as_flow(db, chat_id, None)
        _safe_reply(db, message_id, _result_card(
            "售后录入已完成",
            f"订单 {flow.get('order_no')} 售后已登记、备注已保存。\n"
            "现在可在 订单看板 → 订单视图 的「售后中」看到。", "green"))
        return {"message_id": message_id, "kind": "aftersales_flow", "step": "done"}
    return None


def _remember_push_chat(db: Session, msg: dict) -> bool:
    """把当前会话 chat_id 记为外发目标 (二维码/提醒推这里)。已设则不覆盖。
    返回是否本次新设 (供首次设置时给用户明确反馈)。"""
    try:
        chat_id = msg.get("chat_id")
        if chat_id and not settings_service.get(db, "feishu_push_chat_id", env_fallback=False):
            settings_service.set_value(db, "feishu_push_chat_id", chat_id,
                                       description="飞书外发目标会话(二维码/文件提醒)")
            db.commit()
            return True
    except Exception:  # pragma: no cover - 记忆失败不影响主流程
        db.rollback()
    return False


def on_message_event(db: Session, event: dict) -> Optional[dict]:
    """im.message.receive_v1: 按消息类型路由。

      - image          单聊直接发图          → 识别入库
      - post (富文本)   群里 @机器人 + 带图    → 取内嵌图识别入库; 无图则回使用指南
      - file           Excel/CSV 表格        → 识别导入
      - 其它(text 等)   @机器人 说话           → 回使用指南

    返回处理结果, 或 None(无 message_id 等无法处理)。
    """
    msg = event.get("message") or {}
    mtype = msg.get("message_type")
    message_id = msg.get("message_id")
    try:
        content = json.loads(msg.get("content") or "{}")
    except Exception:
        content = {}
    bkey = _batch_key(event, msg)   # 会话+发送人, 用于把 3 分钟内连发的图归一批
    uploader = _sender_label(db, event)  # 发图人 → 解析真实姓名记到归档「上传人」

    # 记住"最近和机器人对话的会话" → 作为二维码/提醒的外发目标 (用户无需手填 chat_id)
    newly_set_push = _remember_push_chat(db, msg)

    # 发货报表口令: 文本「发货密码 xxx」→ 存设置, 供导入加密发货报表时解密 (2026-06-12)
    if mtype == "text" and message_id:
        # 群里 @机器人 时文本带 "@_user_N" / "@_all" 占位, 去掉再识别关键词/口令
        text = re.sub(r"@_user_\d+|@_all|@\S+", "", content.get("text", "") or "").strip()
        # 售后录入流程 (「售后」关键词启动的多步会话) 优先 — 进行中时拦截后续单号/备注
        as_flow = _handle_aftersales_flow(db, msg, message_id, text)
        if as_flow is not None:
            return as_flow
        pwd = _extract_shipping_password(text)
        if pwd:
            return _capture_shipping_password(db, message_id, pwd)
        # 「扫码」关键词 → 启动待扫码任务 (发大二维码, 浏览器开等扫≤10分钟) (2026-06-12)
        if text in ("扫码", "扫码登录", "开始扫码", "/扫码"):
            from app.services import agent_ingest_service
            res = agent_ingest_service.start_pending_scans(db)
            if res.get("started"):
                tip = "已启动扫码 — 二维码马上发到本群, 请在 10 分钟内用对应 App 扫。"
            else:
                tip = res.get("reason", "当前没有待扫码的任务")
            _safe_reply(db, message_id, _result_card("扫码", tip, "green"))
            return {"message_id": message_id, "kind": "scan_trigger", "card_sent": True}
        if newly_set_push:
            _safe_reply(db, message_id, _result_card(
                "本群已设为推送目标 ✅",
                "以后二维码 / 文件提醒会发到这里。\n"
                "· 发图片 → 我帮你识别入库\n"
                "· 发「发货密码 xxx」→ 我自动解密发货报表", "green"))
            return {"message_id": message_id, "kind": "push_chat_set", "card_sent": True}

    if mtype == "file":
        return _on_file_message(db, msg, uploaded_by=uploader)   # Excel/CSV 表格
    if mtype == "image":
        file_key = content.get("image_key")
        if not (message_id and file_key):
            return None
        return _process_image(db, message_id, file_key, batch_key=bkey, uploaded_by=uploader)
    if mtype == "post":
        # 群里 @机器人 并带图 → 富文本(post), 图片内嵌。单图走单图流程; 多图整批按同一类型处理(不丢图)。
        keys = _post_image_keys(content)
        if message_id and keys:
            if len(keys) == 1:
                return _process_image(db, message_id, keys[0], batch_key=bkey, uploaded_by=uploader)
            return _process_batch(db, message_id, keys, batch_key=bkey, uploaded_by=uploader)
        # 富文本里没有图(纯 @+文字) → 回使用指南
        if message_id:
            _safe_reply(db, message_id, _help_card())
            return {"message_id": message_id, "kind": "help", "card_sent": True}
        return None
    # 文本/其它消息(如 @机器人 说句话) → 回使用指南, 不再沉默
    if message_id:
        _safe_reply(db, message_id, _help_card())
        return {"message_id": message_id, "kind": "help", "card_sent": True}
    return None


def on_card_action(db: Session, event: dict) -> Optional[dict]:
    """card.action.trigger: 用户点了卡片按钮 → 解析入库 → 回结果卡片。"""
    action = event.get("action") or {}
    value = action.get("value") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    op = value.get("op")
    orig_id = value.get("message_id")
    if not op or not orig_id:
        return None

    if op == "cancel":
        _safe_reply(db, orig_id, _result_card("已取消", "好的，这张图不入库。", "grey"))
        return {"op": "cancel"}
    if op == "repick":
        pending = _load_pending(db).get(orig_id) or {}
        imgs = pending.get("images") or []
        hint = (_batch_hint(len(imgs)) if pending.get("is_batch") and len(imgs) > 1
                else "我不太确定这张图的类型，请点选：")
        _safe_reply(db, orig_id, _picker_card(orig_id, hint=hint))
        return {"op": "repick"}
    if op == "repick_file":
        pending = _load_pending(db).get(orig_id) or {}
        _safe_reply(db, orig_id, _file_picker_card(orig_id, pending.get("file_name", "")))
        return {"op": "repick_file"}
    # 用户选了具体供应商 → 按该供应商把送货单入库
    if op == "pick_supplier":
        supplier_id = value.get("supplier_id")
        pending = _load_pending(db).get(orig_id)
        if not pending:
            _safe_reply(db, orig_id, _result_card("已过期", "这张图的会话已过期，请重新发一次。", "red"))
            return {"op": "pick_supplier", "error": "expired"}
        try:
            if pending.get("is_batch"):
                result = _dispatch_batch(db, orig_id, "supplier_note", pending, supplier_id=supplier_id)
            else:
                img = _load_image(db, orig_id, pending)
                result = _dispatch_import(db, "supplier_note", img, supplier_id=supplier_id)
            _drop_pending(db, orig_id)
            db.commit()
        except AiUnavailable as e:
            _safe_reply(db, orig_id, _result_card("OCR 未配置", f"请先到 管理 → AI 集成 配 vision 模型。\n{e}", "red"))
            return {"op": "pick_supplier", "error": "ai_unavailable"}
        except Exception as e:  # pragma: no cover
            db.rollback()
            _log.error("飞书机器人送货单入库失败: %s", e)
            _safe_reply(db, orig_id, _result_card("入库失败", f"出错了: {e}", "red"))
            return {"op": "pick_supplier", "error": str(e)}
        _safe_reply(db, orig_id, _result_card(
            "✅ 处理完成" if result["ok"] else "未入库", result["summary"],
            "green" if result["ok"] else "orange"))
        return {"op": "pick_supplier", "supplier_id": supplier_id, **result}

    if op != "pick":
        return None

    kind = value.get("kind")
    pending = _load_pending(db).get(orig_id)
    if not pending:
        _safe_reply(db, orig_id, _result_card("已过期", "这张图的会话已过期，请重新发一次。", "red"))
        return {"op": "pick", "error": "expired"}
    # 表格文件(Excel/CSV): 按类型导入 (按 is_file 区分, 避免与图片 kind 同名冲突)
    if pending.get("is_file"):
        try:
            content = _load_image(db, orig_id, pending)
            result = _dispatch_file(db, kind, content, pending.get("file_name"))
            db.commit()
        except Exception as e:  # pragma: no cover
            db.rollback()
            _log.error("飞书机器人表格导入失败: %s", e)
            _safe_reply(db, orig_id, _result_card("导入失败", f"出错了: {e}", "red"))
            return {"op": "pick", "error": str(e)}
        _safe_reply(db, orig_id, _result_card(
            "✅ 处理完成" if result["ok"] else "未导入", result["summary"],
            "green" if result["ok"] else "orange"))
        return {"op": "pick", "kind": kind, **result}
    # 富文本多图批量: 整批按选定 kind 逐张入库 (送货单批量先选供应商, 应用到整批)
    if pending.get("is_batch"):
        if kind == "supplier_note":
            _safe_reply(db, orig_id, _supplier_picker_card(
                orig_id, _recent_suppliers(db), n=len(pending.get("images") or [])))
            return {"op": "pick", "kind": kind, "await": "supplier"}
        try:
            result = _dispatch_batch(db, orig_id, kind, pending)
            _drop_pending(db, orig_id)
            db.commit()
        except AiUnavailable as e:
            _safe_reply(db, orig_id, _result_card("OCR 未配置", f"请先到 管理 → AI 集成 配 vision 模型。\n{e}", "red"))
            return {"op": "pick", "error": "ai_unavailable"}
        except Exception as e:  # pragma: no cover
            db.rollback()
            _log.error("飞书机器人批量入库失败: %s", e)
            _safe_reply(db, orig_id, _result_card("入库失败", f"出错了: {e}", "red"))
            return {"op": "pick", "error": str(e)}
        _safe_reply(db, orig_id, _result_card(
            "✅ 处理完成" if result["ok"] else "未入库", result["summary"],
            "green" if result["ok"] else "orange"))
        return {"op": "pick", "kind": kind, **result}
    # 送货单: 先追问"哪家供应商", 选定后再入库 (见 pick_supplier)
    if kind == "supplier_note":
        _safe_reply(db, orig_id, _supplier_picker_card(orig_id, _recent_suppliers(db)))
        return {"op": "pick", "kind": kind, "await": "supplier"}
    try:
        img = _load_image(db, orig_id, pending)
        result = _dispatch_import(db, kind, img)
        _drop_pending(db, orig_id)
        db.commit()
    except AiUnavailable as e:
        _safe_reply(db, orig_id, _result_card("OCR 未配置", f"请先到 管理 → AI 集成 配 vision 模型。\n{e}", "red"))
        return {"op": "pick", "error": "ai_unavailable"}
    except Exception as e:  # pragma: no cover
        db.rollback()
        _log.error("飞书机器人入库失败: %s", e)
        _safe_reply(db, orig_id, _result_card("入库失败", f"出错了: {e}", "red"))
        return {"op": "pick", "error": str(e)}
    _safe_reply(db, orig_id, _result_card(
        "✅ 处理完成" if result["ok"] else "未入库", result["summary"],
        "green" if result["ok"] else "orange"))
    return {"op": "pick", "kind": kind, **result}


def _safe_reply(db: Session, message_id: str, card: dict) -> Optional[str]:
    """发卡片, 返回卡片消息 id(供后续 patch 更新); 失败只记日志不抛, 返回 None。"""
    try:
        data = feishu_client.reply_card(db, message_id, card)
        return (data or {}).get("message_id")
    except Exception as e:  # pragma: no cover
        _log.warning("飞书机器人回卡片失败(凭证未配置?): %s", e)
        return None


def _patch_card_safe(db: Session, card_msg_id: Optional[str], card: dict) -> None:
    """更新一张已发出的卡片(并批时刷新张数/类型); 失败只记日志。"""
    if not card_msg_id:
        return
    try:
        feishu_client.patch_card(db, card_msg_id, card)
    except Exception as e:  # pragma: no cover
        _log.warning("飞书机器人刷新卡片失败: %s", e)


def _drop_pending(db: Session, message_id: str) -> None:
    """入库后丢弃该暂存项: 避免 3 分钟窗口内后到的图再并进"已处理完"的批次(应另起一批)。"""
    data = _load_pending(db)
    if data.pop(message_id, None) is not None:
        _save_pending(db, data)


# ── 长连接(WebSocket)异步处理: 卡片回调要 3 秒内 ack, 入库放后台 + patch 更新卡片 ──
def process_pick(orig_image_msg_id: str, kind: str, card_message_id: Optional[str] = None) -> dict:
    """卡片"确认入库"的异步处理: 自带 DB 会话, 调 _do_pick。"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return _do_pick(db, orig_image_msg_id, kind, card_message_id)
    finally:
        db.close()


def _do_pick(db: Session, orig_image_msg_id: str, kind: str,
             card_message_id: Optional[str] = None) -> dict:
    """下载原图 → 按类型入库 → patch 更新卡片。(db 由调用方提供, 便于测试)"""
    try:
        pending = _load_pending(db).get(orig_image_msg_id)
        if not pending:
            _patch_result(db, card_message_id, "已过期", "会话已过期，请重新发一次。", "red")
            return {"error": "expired"}
        if pending.get("is_batch"):
            result = _dispatch_batch(db, orig_image_msg_id, kind, pending)
        elif pending.get("is_file"):
            content = _load_image(db, orig_image_msg_id, pending)
            result = _dispatch_file(db, kind, content, pending.get("file_name"))
        else:
            content = _load_image(db, orig_image_msg_id, pending)
            result = _dispatch_import(db, kind, content)
        _mark_archive_result(db, _pending_file_ids(pending), result["ok"], result.get("summary", ""))
        _drop_pending(db, orig_image_msg_id)
        db.commit()
        _patch_result(db, card_message_id,
                      "✅ 处理完成" if result["ok"] else "未入库", result["summary"],
                      "green" if result["ok"] else "orange")
        return {"kind": kind, **result}
    except AiUnavailable as e:
        db.rollback()
        _patch_result(db, card_message_id, "OCR 未配置", f"请到 管理→AI 集成 配 vision 模型。\n{e}", "red")
        return {"error": "ai_unavailable"}
    except Exception as e:  # pragma: no cover
        db.rollback()
        _log.error("飞书机器人异步入库失败: %s", e)
        _patch_result(db, card_message_id, "入库失败", f"出错了: {e}", "red")
        return {"error": str(e)}


def process_pick_supplier(orig_image_msg_id: str, supplier_id: int,
                          card_message_id: Optional[str] = None) -> dict:
    """长连接: 用户在供应商选择卡上点了某供应商 → 后台按该供应商把送货单入库。"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return _do_pick_supplier(db, orig_image_msg_id, supplier_id, card_message_id)
    finally:
        db.close()


def _do_pick_supplier(db: Session, orig_image_msg_id: str, supplier_id: int,
                      card_message_id: Optional[str] = None) -> dict:
    try:
        pending = _load_pending(db).get(orig_image_msg_id)
        if not pending:
            _patch_result(db, card_message_id, "已过期", "图片会话已过期，请重新发一次。", "red")
            return {"error": "expired"}
        if pending.get("is_batch"):
            result = _dispatch_batch(db, orig_image_msg_id, "supplier_note", pending, supplier_id=supplier_id)
        else:
            img = _load_image(db, orig_image_msg_id, pending)
            result = _dispatch_import(db, "supplier_note", img, supplier_id=supplier_id)
        _mark_archive_result(db, _pending_file_ids(pending), result["ok"], result.get("summary", ""))
        _drop_pending(db, orig_image_msg_id)
        db.commit()
        _patch_result(db, card_message_id,
                      "✅ 处理完成" if result["ok"] else "未入库", result["summary"],
                      "green" if result["ok"] else "orange")
        return {"supplier_id": supplier_id, **result}
    except AiUnavailable as e:
        db.rollback()
        _patch_result(db, card_message_id, "OCR 未配置", f"请到 管理→AI 集成 配 vision 模型。\n{e}", "red")
        return {"error": "ai_unavailable"}
    except Exception as e:  # pragma: no cover
        db.rollback()
        _log.error("飞书机器人送货单异步入库失败: %s", e)
        _patch_result(db, card_message_id, "入库失败", f"出错了: {e}", "red")
        return {"error": str(e)}


def _patch_result(db: Session, card_message_id: Optional[str], title: str, content: str, template: str) -> None:
    if not card_message_id:
        return
    try:
        feishu_client.patch_card(db, card_message_id, _result_card(title, content, template))
    except Exception as e:  # pragma: no cover
        _log.warning("飞书机器人更新卡片失败: %s", e)
