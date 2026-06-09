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

# 图片类型 → 中文标签 / 入库去向
IMAGE_TYPES = {
    "order_table": "订单表(批量订单截图)",
    "order_image": "订单图(单个订单截图)",
    "supplier_note": "供应商送货单",
    "purchase": "采购单/进货单",
    "factory_recon": "工厂对账单截图",
    "alipay_flow": "支付宝流水截图",
}

_CLASSIFY_SYSTEM = (
    "你是图片分类助手。判断用户发来的图属于哪类，只输出 JSON。\n"
    "类型: order_table=淘宝/千牛批量订单列表截图; order_image=单个订单详情截图; "
    "supplier_note=供应商送货单(送来成品/货物的清单); "
    "purchase=采购单/进货单(我方向供应商买配件/物料的单据); "
    "factory_recon=工厂对账单(工厂列出的下单/账单/已付金额对账表); "
    "alipay_flow=支付宝账单/流水/收支明细截图; unknown=都不像。\n"
    '输出: {"kind": "order_table|order_image|supplier_note|purchase|factory_recon|alipay_flow|unknown", '
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
                _btn("取消", {"op": "cancel", "message_id": message_id}, "danger"),
            ]},
        ],
    }


def _confirm_card(message_id: str, kind: str, conf: float) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green",
                   "title": {"tag": "plain_text", "content": "📷 识别结果，确认入库？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"识别为 **{IMAGE_TYPES.get(kind, kind)}**（置信度 {conf:.0%}）。确认后入库。"}},
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


def _recent_suppliers(db: Session, limit: int = 9) -> list[tuple[int, str]]:
    """取最近/常用供应商 (id, name), 供送货单归属选择。"""
    from app.models.supplier import Supplier
    rows = db.execute(
        select(Supplier.id, Supplier.name).order_by(Supplier.id.desc()).limit(limit)
    ).all()
    return [(r[0], r[1]) for r in rows]


def _supplier_picker_card(message_id: str, suppliers: list[tuple[int, str]]) -> dict:
    """识别为送货单后, 追问"这是哪家供应商?" — 列出候选供应商让用户点选。"""
    if not suppliers:
        return _result_card(
            "请先建供应商",
            "识别为供应商送货单，但系统里还没有供应商。请先到 供应商 页新建供应商后再发图。", "orange")
    actions = [
        _btn(name[:18], {"op": "pick_supplier", "message_id": message_id, "supplier_id": sid}, "primary")
        for sid, name in suppliers
    ]
    actions.append(_btn("取消", {"op": "cancel", "message_id": message_id}, "danger"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📦 这是哪家供应商的送货单？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "请点选送货单归属的供应商："}},
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


def _import_orders(db: Session, parsed: dict) -> dict:
    """把 parse_qianniu_order 的结果入 Orders 表 (新单插入, 已存在跳过)。"""
    orders = parsed.get("orders") or []
    inserted = skipped = 0
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
            skipped += 1
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
    return {"inserted": inserted, "skipped": skipped,
            "warnings": parsed.get("ocr_warnings") or []}


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


def _dispatch_import(db: Session, kind: str, image_bytes: bytes, *,
                     supplier_id: Optional[int] = None) -> dict:
    """按类型解析图片并入库, 返回结果摘要 {ok, summary}。"""
    if kind in ("order_table", "order_image"):
        parsed = vision_ocr_service.parse_qianniu_order(db, image_bytes)
        r = _import_orders(db, parsed)
        msg = f"订单入库完成: 新增 **{r['inserted']}** 单, 跳过(已存在) {r['skipped']} 单。"
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


# ── 原图兜底归档 + 取图(失败回退归档副本) ────────────────────
# 分类 → 归档去向(imports/{kind}/年/月); 未知类一律进 screenshot 兜底, 绝不丢原图
_ARCHIVE_KIND = {
    "order_table": "orders", "order_image": "orders",
    "alipay_flow": "alipay", "supplier_note": "screenshot",
    "purchase": "purchase", "factory_recon": "factory_recon",
}


def _archive_bytes(db: Session, content: bytes, archive_kind: str, original_name: str) -> Optional[str]:
    """把飞书原文件按类型落盘归档(imports/{kind}/年/月)+ 登记 ImportedFile, 返回 stored_path。

    兜底用: 即使后续解析/入库失败或用户取消, 原件也已保存, 不会因飞书清理资源而丢失。
    归档失败只记日志, 绝不影响主流程。
    """
    try:
        from app.services import import_storage
        arch = import_storage.archive(
            db, content=content, original_name=original_name,
            kind=archive_kind, source="feishu",
        )
        return arch.file.stored_path
    except Exception as e:  # pragma: no cover
        _log.warning("飞书原件归档失败(不影响入库): %s", e)
        return None


def _archive_image(db: Session, img: bytes, kind: str) -> Optional[str]:
    """图片按分类归档(未知类→screenshot 兜底)。"""
    return _archive_bytes(db, img, _ARCHIVE_KIND.get(kind, "screenshot"), f"feishu_{kind}.jpg")


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


# ── Excel/CSV 文件(飞书 file 消息) ────────────────────────────
# 文件类型 → 中文标签 / 归档去向 / 导入服务
FILE_TYPES = {
    "order_xlsx": "订单表格",
    "factory_recon_xlsx": "工厂对账表",
}
_FILE_ARCHIVE_KIND = {"order_xlsx": "orders", "factory_recon_xlsx": "factory_recon"}


def _classify_filename(name: str) -> Optional[str]:
    """按文件名关键词粗判表格类型 (拿不准返回 None → 让用户选)。"""
    n = name or ""
    if any(k in n for k in ("对账", "工厂")):
        return "factory_recon_xlsx"
    if any(k in n for k in ("订单", "千牛", "淘宝", "销售明细")):
        return "order_xlsx"
    return None


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
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📄 这个表格是哪类？"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"`{file_name}` 请点选类型："}},
            {"tag": "action", "actions": [
                _btn("订单表格", {"op": "pick", "message_id": message_id, "kind": "order_xlsx"}, "primary"),
                _btn("工厂对账表", {"op": "pick", "message_id": message_id, "kind": "factory_recon_xlsx"}, "primary"),
                _btn("取消", {"op": "cancel", "message_id": message_id}, "danger"),
            ]},
        ],
    }


def _dispatch_file(db: Session, kind: str, content: bytes, file_name: Optional[str]) -> dict:
    """按表格类型导入 (复用既有导入服务)。"""
    if kind == "factory_recon_xlsx":
        from app.services import factory_recon_import_service as fr
        rep = fr.import_factory_recon_xlsx(db, content)
        msg = (f"工厂对账单导入完成: 新增 **{rep.inserted}** 行, 重复 {rep.skipped_duplicate}, "
               f"无效 {rep.skipped_invalid}, 回填成本 {rep.backfilled_cost} 单。")
        if rep.errors:
            return {"ok": False, "summary": f"导入失败: {'; '.join(map(str, rep.errors[:2]))}"}
        return {"ok": True, "summary": msg}
    if kind == "order_xlsx":
        from app.services import taobao_order_import
        rep = taobao_order_import.import_taobao_orders(db, file_name or "orders.xlsx", content)
        msg = (f"订单表导入完成({rep.detected_format}): 新增 **{rep.inserted}**, 更新 {rep.updated}, "
               f"重复 {rep.skipped_duplicate}, 无效 {rep.skipped_invalid}。")
        if rep.errors:
            msg += f"\n⚠️ {'; '.join(map(str, rep.errors[:2]))}"
        return {"ok": True, "summary": msg}
    return {"ok": False, "summary": f"未知表格类型: {kind}"}


def _on_file_message(db: Session, msg: dict) -> Optional[dict]:
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

    fkind = _classify_filename(file_name)
    archived_path = None
    downloaded = False
    try:
        data = feishu_client.download_message_resource(db, message_id, file_key, type_="file")
        downloaded = True
        archived_path = _archive_bytes(db, data, _FILE_ARCHIVE_KIND.get(fkind, "generic"), file_name)
    except Exception as e:
        _log.warning("飞书取文件失败: %s", e)
    if not downloaded:
        # 连原件都没取到 → 别给确认卡误导用户, 直接让其重发
        _safe_reply(db, message_id, _result_card(
            "文件获取失败", f"`{file_name}` 从飞书下载失败, 请重新发一次。", "red"))
        return {"message_id": message_id, "error": "download_failed"}

    _stage(db, message_id, {"file_key": file_key, "is_file": True, "file_name": file_name,
                            "kind": fkind, "archived_path": archived_path})
    card = _file_confirm_card(message_id, fkind, file_name) if fkind \
        else _file_picker_card(message_id, file_name)
    _safe_reply(db, message_id, card)
    return {"message_id": message_id, "file_kind": fkind, "card_sent": True}


# ── 事件入口 (feishu_webhook_service 调用) ─────────────────────
def on_message_event(db: Session, event: dict) -> Optional[dict]:
    """im.message.receive_v1: 收到图片 → 分类 → 暂存 → 回卡片。返回回复结果(或 None 表示忽略非图片)。"""
    msg = event.get("message") or {}
    mtype = msg.get("message_type")
    if mtype == "file":
        return _on_file_message(db, msg)   # Excel/CSV 表格
    if mtype != "image":
        return None  # 只处理图片 / 文件消息
    message_id = msg.get("message_id")
    try:
        content = json.loads(msg.get("content") or "{}")
    except Exception:
        content = {}
    file_key = content.get("image_key")
    if not (message_id and file_key):
        return None

    kind, conf = "unknown", 0.0
    archived_path: Optional[str] = None
    try:
        img = feishu_client.download_message_resource(db, message_id, file_key)
        kind, conf = classify_image(db, img)
        # 收到即按类型归档原图(兜底): 即便后续取消/失败, 原图也不丢
        archived_path = _archive_image(db, img, kind)
    except Exception as e:  # 下载/分类失败 → 仍让用户选类型, 不崩
        _log.warning("飞书机器人取图/分类失败: %s", e)

    _stage(db, message_id, {"file_key": file_key, "kind": kind, "conf": conf,
                            "archived_path": archived_path})
    if kind in IMAGE_TYPES and conf >= _CONFIDENT:
        # 送货单即使识别确定, 也要追问"哪家供应商"才能正确归属入库
        if kind == "supplier_note":
            card = _supplier_picker_card(message_id, _recent_suppliers(db))
        else:
            card = _confirm_card(message_id, kind, conf)
    else:
        card = _picker_card(message_id)
    _safe_reply(db, message_id, card)
    return {"message_id": message_id, "kind": kind, "confidence": conf, "card_sent": True}


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
        _safe_reply(db, orig_id, _picker_card(orig_id))
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
            img = _load_image(db, orig_id, pending)
            result = _dispatch_import(db, "supplier_note", img, supplier_id=supplier_id)
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
    # 表格文件(Excel/CSV): 按类型导入
    if kind in FILE_TYPES:
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
    # 送货单: 先追问"哪家供应商", 选定后再入库 (见 pick_supplier)
    if kind == "supplier_note":
        _safe_reply(db, orig_id, _supplier_picker_card(orig_id, _recent_suppliers(db)))
        return {"op": "pick", "kind": kind, "await": "supplier"}
    try:
        img = _load_image(db, orig_id, pending)
        result = _dispatch_import(db, kind, img)
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


def _safe_reply(db: Session, message_id: str, card: dict) -> None:
    """发卡片, 失败只记日志不抛 (凭证未配置/网络问题时机器人不该让 webhook 500)。"""
    try:
        feishu_client.reply_card(db, message_id, card)
    except Exception as e:  # pragma: no cover
        _log.warning("飞书机器人回卡片失败(凭证未配置?): %s", e)


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
        content = _load_image(db, orig_image_msg_id, pending)
        if kind in FILE_TYPES:
            result = _dispatch_file(db, kind, content, pending.get("file_name"))
        else:
            result = _dispatch_import(db, kind, content)
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
        img = _load_image(db, orig_image_msg_id, pending)
        result = _dispatch_import(db, "supplier_note", img, supplier_id=supplier_id)
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
