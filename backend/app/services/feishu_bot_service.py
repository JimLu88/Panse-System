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

    fkind = None
    archived_path = None
    downloaded = False
    try:
        data = feishu_client.download_message_resource(db, message_id, file_key, type_="file")
        downloaded = True
        fkind = _tis.classify_table(file_name, data)   # 文件名 + 表头结构 结合判类型
        archived_path = _archive_bytes(db, data, _file_archive_kind(fkind), file_name)
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


def _process_image(db: Session, message_id: str, image_key: str) -> dict:
    """下载图 → 分类 → 兜底归档 → 暂存 → 回确认/选类型卡。

    单聊直接发图(image 消息) 与 群里 @机器人 带图(post 消息内嵌) 共用此路径。
    """
    kind, conf = "unknown", 0.0
    archived_path: Optional[str] = None
    try:
        img = feishu_client.download_message_resource(db, message_id, image_key)
        kind, conf = classify_image(db, img)
        # 收到即按类型归档原图(兜底): 即便后续取消/失败, 原图也不丢
        archived_path = _archive_image(db, img, kind)
    except Exception as e:  # 下载/分类失败 → 仍让用户选类型, 不崩
        _log.warning("飞书机器人取图/分类失败: %s", e)

    _stage(db, message_id, {"file_key": image_key, "kind": kind, "conf": conf,
                            "archived_path": archived_path})
    if kind in IMAGE_TYPES and conf >= _threshold(kind):
        # 送货单即使识别确定, 也要追问"哪家供应商"才能正确归属入库
        if kind == "supplier_note":
            card = _supplier_picker_card(message_id, _recent_suppliers(db))
        else:
            card = _confirm_card(message_id, kind, conf)
    else:
        card = _picker_card(message_id)
    _safe_reply(db, message_id, card)
    return {"message_id": message_id, "kind": kind, "confidence": conf, "card_sent": True}


def _process_batch(db: Session, message_id: str, image_keys: list[str]) -> dict:
    """一条富文本里的多张图: 逐张下载+分类+兜底归档, 判定整批是否同一可信类型, 出一张卡。

    - 全部都同一可信类型 → 一张"确认全部入库"卡 (送货单则先选供应商, 应用到整批)。
    - 认不准/类型不一 → 一张选类型卡(说明同类型一次发/不同类型分批发), 用户点一次整批入库。
    确认/点选后由 _dispatch_batch 逐张入库 —— 一条消息里的图都不丢。
    """
    items: list[dict] = []
    for k in image_keys:
        kind, conf, ap = "unknown", 0.0, None
        try:
            img = feishu_client.download_message_resource(db, message_id, k)
            kind, conf = classify_image(db, img)
            ap = _archive_image(db, img, kind)   # 收到即归档(兜底), 即便后续取消也不丢原图
        except Exception as e:
            _log.warning("批量取图/分类失败: %s", e)
        items.append({"file_key": k, "kind": kind, "conf": conf, "archived_path": ap})
    n = len(items)
    # 仅当"每一张都可信、且都是同一类型"才自动确认; 否则一律让用户点选整批类型(宁可多问一次)
    confident_same = (
        n > 0
        and all(it["kind"] in IMAGE_TYPES and it["conf"] >= _threshold(it["kind"]) for it in items)
        and len({it["kind"] for it in items}) == 1
    )
    batch_kind = items[0]["kind"] if confident_same else "unknown"
    min_conf = min((it["conf"] for it in items), default=0.0)
    _stage(db, message_id, {"is_batch": True, "images": items, "kind": batch_kind, "conf": min_conf})
    if batch_kind == "supplier_note":
        card = _supplier_picker_card(message_id, _recent_suppliers(db), n=n)
    elif batch_kind != "unknown":
        card = _confirm_card(message_id, batch_kind, min_conf, n=n)
    else:
        card = _picker_card(message_id, hint=_batch_hint(n))
    _safe_reply(db, message_id, card)
    return {"message_id": message_id, "batch": n, "kind": batch_kind, "card_sent": True}


# ── 事件入口 (feishu_webhook_service / feishu_ws_service 调用) ──
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

    if mtype == "file":
        return _on_file_message(db, msg)   # Excel/CSV 表格
    if mtype == "image":
        file_key = content.get("image_key")
        if not (message_id and file_key):
            return None
        return _process_image(db, message_id, file_key)
    if mtype == "post":
        # 群里 @机器人 并带图 → 富文本(post), 图片内嵌。单图走单图流程; 多图整批按同一类型处理(不丢图)。
        keys = _post_image_keys(content)
        if message_id and keys:
            if len(keys) == 1:
                return _process_image(db, message_id, keys[0])
            return _process_batch(db, message_id, keys)
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
        if pending.get("is_batch"):
            result = _dispatch_batch(db, orig_image_msg_id, kind, pending)
        elif pending.get("is_file"):
            content = _load_image(db, orig_image_msg_id, pending)
            result = _dispatch_file(db, kind, content, pending.get("file_name"))
        else:
            content = _load_image(db, orig_image_msg_id, pending)
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
        if pending.get("is_batch"):
            result = _dispatch_batch(db, orig_image_msg_id, "supplier_note", pending, supplier_id=supplier_id)
        else:
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
