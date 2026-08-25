# -*- coding: utf-8 -*-
"""工厂下单图自动生成、归档与飞书图片推送。

D: 规范化版式 (尺寸/整数数量/木作命名/单件×N/发货=下单+25天/备注完整) — 数据在
   factory_sheet.build, 这里负责渲染成独立可打印 HTML。
E: 订单 (order_date >= 2026-06-06) 自动生成下单图 → 存导入档案 (kind=order_sheet),
   飞书订单群只发图片；运行状态改走微信 Push。
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from html import escape
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.services import factory_sheet, import_storage

_logger = logging.getLogger("panse.order_sheet")

AUTO_SINCE = date(2026, 6, 6)   # 用户指定: 从这天的订单开始自动生成
_UPDATE_COMPLETE_NOTICE_KEY = "order_group_update_complete_notice_date"


def send_order_update_complete_notice(db: Session, *, on_date: date | None = None) -> dict:
    """订单刷新成功但无新增下单图时，向订单群发一条按日幂等的完成回执。"""
    import os
    from app.services import feishu_client, settings_service

    day = on_date or date.today()
    day_key = day.isoformat()
    if settings_service.get(db, _UPDATE_COMPLETE_NOTICE_KEY, env_fallback=False) == day_key:
        return {"sent": False, "already_sent": True, "date": day_key}
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return {"sent": False, "disabled": True, "date": day_key}
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if not chat_id:
        return {"sent": False, "reason": "no_chat_id", "date": day_key}
    text = f"{day.year}年{day.month}月{day.day}日订单已完成更新，暂无新增需推送下单图"
    try:
        feishu_client.send_text(db, chat_id, text)
    except Exception as exc:  # noqa: BLE001 - 发送失败不能写幂等标记，留给后续补偿
        _logger.warning("订单更新完成回执发送失败", exc_info=True)
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"[:300], "date": day_key}
    settings_service.set_value(
        db,
        _UPDATE_COMPLETE_NOTICE_KEY,
        day_key,
        description="飞书订单群最近一次订单更新完成且无新增下单图回执日期",
    )
    db.commit()
    return {"sent": True, "text": text, "date": day_key}


def send_no_order_update_notice(db: Session, *, on_date: date | None = None) -> dict:
    """兼容旧调用；语义已改为“刷新完成且无新增下单图”，不得用于失败/处理中状态。"""
    return send_order_update_complete_notice(db, on_date=on_date)

# 用户拍板 (2026-06-11): 下单图生成条件必须是已付款订单
_PAID_STATUSES = {"paid", "production", "shipped", "signed", "aftersales"}


def _is_paid(o: Order) -> bool:
    return (o.paid_amount or 0) > 0 or (o.status or "") in _PAID_STATUSES


def _is_active_factory_order(o: Order) -> bool:
    """Only unfinished paid/production orders may change factory assignment."""
    from app.services import order_service

    normalized = order_service.normalize_status(o.status)
    return normalized == "paid" or (o.status or "").strip().lower() == "production"


def _is_refunded(o: Order) -> bool:
    """是否退款作废: 状态关闭/退货, 或退款状态含退款/退货, 或【全额(≥90%)退款】。
    小额差价/运费退款(如 ¥5)不算作废 (用户拍板 2026-06-12: 避免误把差价单作废/拦推送)。"""
    rs = o.refund_status or ""
    # 2026-07-08 修子串误判: 淘宝『没有申请退款』(正常无退款态)带"退款"二字被 `"退款" in rs`
    # 误判成已退款 → 误拦下单图生成+工厂推送。先排除这些【含"退款"字样但实际没退】的状态。
    _NOT_REFUND = ("没有申请退款", "未申请退款", "无退款", "退款关闭", "退款失败", "撤销退款", "买家撤销")
    _refund_kw = (any(k in rs for k in ("退款", "退货", "关闭"))
                  and not any(k in rs for k in _NOT_REFUND))
    if (o.status or "") == "cancelled" or _refund_kw:
        return True
    ra = Decimal(str(o.refund_amount or 0))
    pa = Decimal(str(o.paid_amount or 0))
    return ra > 0 and pa > 0 and ra >= pa * Decimal("0.9")


def _partial_refund_child_lines(db: Session, order: Order) -> list[OrderDetail]:
    """Return resolved child rows when one child remains active and another is refunded.

    A legacy ``Order`` stores only one representative product.  For a mixed
    main order that representative can be the refunded child, so the main-order
    sheet is never safe once authoritative child rows are available.
    """
    from app.services import order_line_delivery_service as line_delivery

    lines = db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no == order.order_no,
            OrderDetail.source == "import",
            OrderDetail.sub_order_no.isnot(None),
            OrderDetail.sku_code.isnot(None),
        ).order_by(OrderDetail.id.asc())
    ).scalars().all()
    if not lines:
        return []
    refunded = [line for line in lines if line_delivery.line_is_refunded(line)]
    active = [line for line in lines if not line_delivery.line_is_refunded(line)]
    return lines if refunded and active else []


def _promote_partial_refund_child_delivery(db: Session, order: Order) -> bool:
    """Move an unsent mixed-refund legacy order onto child-order delivery.

    Existing sent main-order evidence is deliberately not rewritten or
    replayed here; it requires explicit correction because Feishu may already
    have acted on it.  Unsent/pending rows are safely switched before rendering.
    """
    lines = _partial_refund_child_lines(db, order)
    if not lines:
        return False
    if order.order_no in _pushed_sheet_evidence(db):
        return False
    for line in lines:
        line.factory_delivery_required = True
    return True


def _int_qty(v) -> str:
    """2.0000 → 2; 2.5 保留小数。用户要求数量别带一串零。"""
    d = Decimal(str(v or 0))
    if d == d.to_integral_value():
        return str(int(d))
    return str(d.normalize())


def _gallery_data_uri(rel, max_w: int = 900):
    """图库相对路径 → 缩放后的 base64 data URI, 内嵌进下单图。

    wkhtmltoimage / 浏览器都能可靠渲染, 不依赖 /api/gallery/file 的会话鉴权 (PNG 渲染取不到 cookie)。
    读不到 / 出错一律返回 None (图库问题绝不阻断下单图生成)。
    """
    if not rel:
        return None
    try:
        import base64
        import io

        from PIL import Image

        from app.services.gallery_lookup import _root
        p = _root() / rel
        if not p.is_file():
            return None
        im = Image.open(p).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, max(1, int(im.height * max_w / im.width))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - 图库读取/解码失败不阻断
        return None


def _fmt_size(s: Optional[str]) -> Optional[str]:
    """成品尺寸紧凑化: 长度：450mm；深度：400mm；高度：450mm → 450mm（长）* 400mm（深）* 450mm（高）。"""
    if not s:
        return None
    _short = {"长度": "长", "深度": "深", "高度": "高", "宽度": "宽", "直径": "径"}
    out = []
    for seg in re.split(r"[；;]", s):
        m = re.match(r"\s*([^：:]+)[：:]\s*(.+)", seg)
        if m:
            out.append(f"{m.group(2).strip()}({_short.get(m.group(1).strip(), m.group(1).strip())})")
        elif seg.strip():
            out.append(seg.strip())
    return "*".join(out) if out else s


def _cn_date(d) -> str:
    """date → 2026年6月20日; 非日期/空 → 原值/'-'。"""
    if d is None:
        return "-"
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return f"{d.year}年{d.month}月{d.day}日"
    return str(d)


_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"


def _spec_items(sheet) -> list:
    """产品规格分项: ① 主材, 其后辅材按「短标签：值」拆分编号 (值含空格不被拆)。"""
    out = []
    mm = getattr(sheet, "main_material", None)
    am = getattr(sheet, "aux_material", None)
    if mm and str(mm).strip():
        out.append(str(mm).strip())
    if am and str(am).strip():
        for seg in re.split(r"\s+(?=[一-龥A-Za-z]{1,6}[：:])", str(am).strip()):
            if seg.strip():
                out.append(seg.strip())
    return out


def render_html(sheet: "factory_sheet.FactorySheet", *, header_style: str = "bar") -> str:
    """下单图 HTML — A4 横版工厂生产单 (黑白线框版, 打印友好; 2026-06-21 改版)。

    去掉藏青蓝填充色 → 黑线框 + 红字强调(尺寸/发货/敲章), 打印不费墨。
    header_style 头部 3 选 1: classic(公文双线) / bar(左竖标) / outline(描边框)。
    头部加高加大; 制单日期入头部; 发货日期入页脚(下单日期下方, 红字大字);
    产品规格 ①②③ 编号分项; 定制单自动敲「确认定制单」红章; 加急敲「加急」红章。
    """
    e = escape
    A = "#1a1a1a"  # 主线条色 (黑, 打印友好; 原藏青蓝填充已去)
    made, ship, odate = sheet.made_date, sheet.ship_date, sheet.order_date
    ship_text = ("待补地址后通知"
                 if getattr(sheet, "ship_date_pending", False)
                 else _cn_date(ship))
    # 头部右: 畔色 N 单 + 制单日期 + 订单编号
    if sheet.factory_no:
        # 与 ERP / 飞书下单表第一列逐字一致，统一为「畔色329单」。
        no_html = f"<div class='no'>畔色{sheet.factory_no}单</div>"
    else:
        no_html = "<div class='no' style='color:#dc2626'>未能匹配工厂订单号</div>"
    made_html = f"<div class='mk'>制单日期：{e(_cn_date(made))}</div>"
    # 成品尺寸 (无 → 红字"未对应尺寸")
    _szt = _fmt_size(sheet.size_info)
    if _szt:
        _n = len(_szt)
        _szfs = 52 if _n <= 15 else (40 if _n <= 30 else 30)
        _nw = "white-space:nowrap;" if _n <= 30 else ""
        size_html = f"<div class='sz' style='font-size:{_szfs}px;{_nw}'>{e(_szt)}</div>"
    else:
        size_html = "<div class='sz'>未对应尺寸</div>"
    # 产品规格分项 (①②③ 编号)
    _items = _spec_items(sheet)
    if _items:
        mat_txt = "　　".join(
            f"<b style='color:{A}'>{_CIRCLED[i] if i < len(_CIRCLED) else str(i + 1)}</b> {e(it)}"
            for i, it in enumerate(_items)
        )
    else:
        mat_txt = "—"
    # 辅料 BOM: 去木作 + "名称 ×数量 单位"
    bom = []
    for m in sheet.materials:
        code = (m.material_code or "").upper()
        nm = m.material_name or m.material_code or ""
        if code.startswith("WD") or "木作" in nm:
            continue
        bom.append(f"{e(nm)}　×{_int_qty(m.total_qty)} {e(m.unit or '件')}")
    bom_txt = "<br>".join(bom) if bom else "—"
    # 图纸: 优先 SKU 尺寸图(高清内嵌), 回退主图
    _sku = _gallery_data_uri(getattr(sheet, "sku_image", None))
    _main = (sheet.image_url if (sheet.image_url and str(sheet.image_url).startswith("http"))
             else _gallery_data_uri(getattr(sheet, "gallery_main_image", None)))
    _img = _sku or _main
    pic_html = f"<img src='{e(_img)}'>" if _img else "<div class='noimg'>无产品图纸</div>"
    # 敲章: 加急 + 定制(自动识别 is_custom_variant)
    stamps = ""
    if sheet.urgent:
        stamps += "<div class='stamp stamp-urgent'>加急</div>"
    if getattr(sheet, "is_custom_variant", False):
        stamps += "<div class='stamp stamp-custom'>确认定制单</div>"
    # 收货: 全空 → 红字提示, 编号照常
    if sheet.customer_name or sheet.customer_phone or sheet.customer_address:
        ship_to = (f"{e(sheet.customer_name or '')}　{e(sheet.customer_phone or '')}"
                   f"<br>{e(sheet.customer_address or '—')}")
    else:
        ship_to = "<span style='color:#dc2626;font-weight:800'>⚠ 没有抓取到收货地址（淘宝解密额度不足，待提升后重拉）</span>"
    # 客户备注/生产备注: 定制/补拍链单的真实需求常只写在备注里(产品名是无意义的"补拍专链"),
    # 必须显示给工厂, 否则工厂拿到的是空白单不知道做什么 (用户 2026-07-09)。
    _notes = []
    if getattr(sheet, "remark", None):
        _notes.append(f"<b>客户备注</b> {e(sheet.remark)}")
    if getattr(sheet, "production_note", None):
        _notes.append(f"<b>生产备注</b> {e(sheet.production_note)}")
    note_html = ("<div class='z'><div class='zt'>客户备注　NOTE</div>"
                 f"<div class='zb' style='color:#dc2626;font-weight:700'>{'<br>'.join(_notes)}</div></div>") if _notes else ""
    # 头部样式 3 选 1 (无填充, 仅黑线)
    if header_style == "bar":
        hd_extra = f".hd{{border-bottom:2px solid {A};}}.hd .co{{border-left:14px solid {A};padding-left:22px;}}"
    elif header_style == "outline":
        hd_extra = f".hd{{border:2.5px solid {A};}}"
    else:  # classic
        hd_extra = f".hd{{border-bottom:5px double {A};}}"
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"/>
<title>{e(sheet.sheet_title)}</title><style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:"Microsoft YaHei","PingFang SC",sans-serif;}}
body{{background:#fff;}}
.page{{width:1684px;height:1190px;background:#fff;padding:22px;}}
.card{{position:relative;width:1640px;height:1146px;background:#fff;border:3px solid {A};}}
table{{border-collapse:collapse;}}
.hd{{width:100%;height:150px;background:none;color:#000;}}
{hd_extra}
.hd .co{{font-size:46px;font-weight:900;padding-left:30px;color:#000;vertical-align:middle;}}
.hd .co small{{display:block;font-size:18px;font-weight:600;color:#444;letter-spacing:2px;margin-top:6px;}}
.hd .co .ono{{display:block;font-size:20px;color:#555;margin-top:8px;font-family:monospace;letter-spacing:1px;font-weight:400;}}
.hd .r{{text-align:right;padding-right:30px;vertical-align:middle;}}
.hd .no{{font-size:58px;font-weight:900;color:#1f3a5f;}}
.hd .mk{{font-size:30px;font-weight:900;color:#dc2626;margin-top:10px;}}
.mid{{width:100%;height:706px;}}
.mid .pic{{width:660px;border-right:3px solid {A};text-align:center;vertical-align:middle;}}
.mid .pic img{{max-width:640px;max-height:676px;}}
.mid .noimg{{color:#bbb;font-size:30px;}}
.zwrap{{vertical-align:top;}}
.z{{border-bottom:2px solid {A};}}
.zt{{background:#f0f0f0;color:#000;font-size:22px;font-weight:800;padding:10px 24px;letter-spacing:1px;border-bottom:1px solid #ccc;}}
.zb{{padding:16px 26px;font-size:30px;line-height:1.4;word-break:break-all;overflow-wrap:anywhere;}}
.sz{{font-size:56px;font-weight:900;color:#dc2626;letter-spacing:0;line-height:1.15;}}
.ft{{width:100%;border-top:3px solid {A};}}
.ft td{{padding:18px 26px;vertical-align:top;font-size:30px;}}
.ft .l{{font-size:20px;color:#000;font-weight:800;letter-spacing:1px;}}
.ft .odt{{font-size:30px;}}
.ft .shipdt{{font-size:40px;font-weight:900;color:#dc2626;margin-top:4px;}}
.stamp{{position:absolute;border:5px double #dc2626;color:#dc2626;font-weight:900;
        transform:rotate(-13deg);border-radius:10px;background:rgba(255,255,255,.45);z-index:9;}}
.stamp-urgent{{top:175px;right:720px;font-size:46px;padding:3px 20px;letter-spacing:8px;}}
.stamp-custom{{top:30px;left:340px;font-size:34px;padding:3px 16px;letter-spacing:3px;}}
@media print{{.page{{padding:14px;}}}}
</style></head><body><div class="page"><div class="card">
<table class="hd" style="width:100%"><tr>
  <td class="co">畔色木作<small>工厂生产单 · PRODUCTION ORDER</small><div class="ono">订单编号：{e(sheet.order_no)}</div></td>
  <td class="r">{no_html}{made_html}</td>
</tr></table>
<table class="mid"><tr>
  <td class="pic">{pic_html}</td>
  <td class="zwrap">
    <div class="z"><div class="zt">产品 / 规格　PRODUCT</div><div class="zb">{e(sheet.product_name or '-')}　<span style="font-family:monospace;font-size:23px;color:#555">{e(sheet.product_code or '-')}</span><br>{mat_txt}</div></div>
    {note_html}
    <div class="z"><div class="zt">成品尺寸　FINISHED SIZE (mm)</div><div class="zb">{size_html}</div></div>
    <div class="z" style="border-bottom:none"><div class="zt">辅料清单　BOM</div><div class="zb">{bom_txt}</div></div>
  </td></tr></table>
<table class="ft" style="width:100%"><tr>
  <td><div class="l">收货信息 SHIP TO</div>{ship_to}</td>
  <td style="text-align:right;width:400px;border-left:2px solid #ccc">
    <div class="l">下单日期</div><div class="odt">{e(_cn_date(odate))}</div>
    <div class="l" style="margin-top:14px">发货日期</div><div class="shipdt">{e(ship_text)}</div>
  </td>
</tr></table>
{stamps}
</div></div></body></html>"""


def _archived_order_nos(db: Session) -> set[str]:
    """已归档下单图的订单号集合 (兼容 下单图_X.html / {date}_X.jpg 两种命名)。"""
    names = db.execute(
        select(ImportedFile.original_filename).where(ImportedFile.kind == "order_sheet")
    ).scalars().all()
    return {no for n in names if (no := _order_no_from_name(n))}


def generate_for_order(db: Session, order: Order, *, source: str = "auto") -> Optional[dict]:
    """生成一张下单图 JPEG 并归档。

    返回结构化结果，单张失败也必须带回订单号和原因。批量调用方据此阻止
    “少生成一张但整批仍显示成功”的假成功。待付款订单保留 ``None`` 兼容
    既有直接调用；批量入口本身会在调用前过滤。

    用户拍板 2026-06-19: 存档直接存成图片 (非 HTML), 日期+订单号命名, 打开即看、可直接转发工厂。
    """
    # 铁律 (用户拍板 2026-06-20): 未付款单【永不】生成工厂下单图 —— 付了定金(paid_amount>0)
    # 但 status=pending_payment 的也算未付款, 大概率会取消, 绝不能给工厂下单/编号。
    if (order.status or "") == "pending_payment":
        return None
    try:
        # 归档阶段如果触发数据库异常，只回滚这一张，不污染同批此前已成功行。
        with db.begin_nested():
            sheet = factory_sheet.build(db, order.id)
            jpg = render_png(sheet)   # render_png 现已输出 JPEG 字节
            d = order.order_date or date.today()
            res = import_storage.archive(
                db, content=jpg,
                original_name=f"{d.isoformat()}_{order.order_no}.jpg",
                kind="order_sheet", source=source,
                on_date=order.order_date,
            )
        return {
            "status": "generated",
            "order_no": order.order_no,
            "file_id": res.file.id,
            "duplicate": res.is_duplicate,
        }
    except Exception as exc:  # noqa: BLE001 - 结构化返回，批量继续并在收口处失败
        _logger.warning("下单图生成失败 %s", order.order_no, exc_info=True)
        return {
            "status": "failed",
            "order_no": order.order_no,
            "duplicate": False,
            "error_type": type(exc).__name__,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


def _activated_memo_filter():
    """SQL 粗筛: 任一备注字段含【激活词】(开始制作/排产/投产…)。用于让【已激活的老远期单】
    突破 order_date 起始线被补生成 —— 远期单本就早下(装修等), 激活时往往已老于 AUTO_SINCE,
    若仍按日期切掉就永远不进工厂 (用户 2026-07-08 报: 5116855…3029039 备注开始制作却没推)。
    精确判定(否定前缀等)仍靠循环内 order_flags.is_remote/is_activated。"""
    from app.services.order_flags import ACTIVATE_KW
    cols = (Order.remark, Order.production_note, Order.buyer_message, Order.seller_memo)
    return or_(*[c.like(f"%{k}%") for k in ACTIVATE_KW for c in cols])


def generate_pending(db: Session, *, limit: int = 200) -> dict:
    """给 2026-06-06 起、还没归档过下单图的订单批量补生成 (导入兜底 + 日常增量)。
    另含【已激活的老远期单】(备注开始制作等, 不受 AUTO_SINCE 日期线限制) (用户 2026-07-08)。"""
    done = _archived_order_nos(db)
    orders = db.execute(
        select(Order).where(
            or_(Order.order_date >= AUTO_SINCE, _activated_memo_filter()),
            Order.is_refill == False,            # noqa: E712 - 补单不发工厂
        ).order_by(Order.id.desc()).limit(500)
    ).scalars().all()
    generated: list[str] = []
    failures: list[dict] = []
    attempted = 0
    for o in orders:
        # 部分退款的多子订单绝不能再走“主订单代表商品”旧图。若尚未发送，
        # 在渲染之前切到逐子订单链；退款行会被行级资格判断排除。
        if _promote_partial_refund_child_delivery(db, o):
            continue
        # 已切到子订单工厂链的主订单不得再生成“代表整单”的旧图，否则一单
        # 多商品会同时走主单图和子单图，造成重复生产。
        if db.execute(
            select(OrderDetail.id).where(
                OrderDetail.order_no == o.order_no,
                OrderDetail.source == "import",
                OrderDetail.factory_delivery_required.is_(True),
            ).limit(1)
        ).scalar_one_or_none() is not None:
            continue
        if o.order_no in done or attempted >= limit:
            continue
        if (o.status or "") in ("cancelled", "pending_payment"):
            continue   # 取消 + 未付款(含付定金的待付款)永不生成 (用户拍板 2026-06-20 铁律)
        if not _is_paid(o):   # 用户拍板: 必须已付款才生成下单图
            continue
        if _is_refunded(o):   # 已退款/退货/关闭 → 不生成也不推送 (改走作废图流程)
            continue
        from app.services import order_flags
        if order_flags.is_remote(o):
            continue   # 远期挂起单: 不生成下单图, 等激活(备注开始制作)后再以新号推 (用户 2026-07-08)
        attempted += 1
        r = generate_for_order(db, o)
        if r and r.get("status") == "failed":
            failures.append({
                "order_no": r.get("order_no") or o.order_no,
                "error_type": r.get("error_type"),
                "error": r.get("error") or "下单图生成失败但未返回原因",
            })
        elif r and not r["duplicate"]:
            generated.append(r["order_no"])
    db.commit()
    return {
        "generated": len(generated),
        "attempted": attempted,
        "order_nos": generated[:50],
        "generation_failed": len(failures),
        "generation_failed_order_nos": [x["order_no"] for x in failures],
        "generation_failures": failures,
    }


def _html_to_png(html: str, *, width: int = 820) -> bytes:
    """wkhtmltoimage 把下单图 HTML 渲染成 PNG (Debian 版需 xvfb-run 提供无头显示)。"""
    import os
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        hp = os.path.join(d, "sheet.html")
        op = os.path.join(d, "sheet.png")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)
        # 官方 patched-qt 版本自带无头, 直接跑 (不要 xvfb-run, 会冲突报错)
        subprocess.run(
            ["wkhtmltoimage", "--format", "jpeg", "--quality", "82",
             "--encoding", "utf-8", "--width", str(width), "--disable-smart-width", hp, op],
            check=True, capture_output=True, timeout=90,
        )
        with open(op, "rb") as f:
            return f.read()


def render_png(sheet) -> bytes:
    """下单图 → PNG 字节 (发飞书图片用)。A4 横版工单宽 1684px (方案C·藏青蓝)。"""
    return _html_to_png(render_html(sheet), width=1684)


def render_void_png(sheet) -> bytes:
    """标准横版作废下单图；尺寸与正常工厂下单图完全一致。"""
    html = render_html(sheet).replace("</body>", _VOID_OVERLAY + "</body>")
    return _html_to_png(html, width=1684)


def archive_sent_snapshot(
    db: Session,
    order: Order,
    content: bytes,
    *,
    source: str = "factory_push",
    backfilled: bool = False,
) -> ImportedFile:
    """归档实际发给工厂的最终图片，作为工厂下单表唯一可信图片来源。"""
    from app.services import order_flags

    factory_no = getattr(order, "factory_no", None)
    if factory_no is None:
        raise ValueError(f"订单 {order.order_no} 没有正式工厂单号")
    result = import_storage.archive(
        db,
        content=content,
        original_name=f"{date.today().isoformat()}_{order.order_no}_畔色{factory_no}单.jpg",
        kind="order_sheet_sent",
        source=source,
        on_date=date.today(),
        row_summary={
            "order_no": order.order_no,
            "factory_no_at_render": int(factory_no),
            "factory_label_at_render": f"畔色{factory_no}单",
            "render_width": 1684,
            "pushed": True,
            "activated": order_flags.is_activated(order),
            "backfilled": bool(backfilled),
        },
    )
    return result.file


def archive_sent_line_snapshot(
    db: Session,
    order: Order,
    line: OrderDetail,
    content: bytes,
    *,
    source: str = "factory_push",
) -> ImportedFile:
    """归档已发送的子订单商品图；这是新链路的唯一送达凭证。"""
    from app.services import order_flags

    if not line.sub_order_no or line.order_no != order.order_no:
        raise ValueError("工厂商品行缺少有效子订单编号")
    if line.factory_no is None:
        raise ValueError(f"子订单 {line.sub_order_no} 没有正式工厂单号")
    result = import_storage.archive(
        db,
        content=content,
        original_name=(
            f"{date.today().isoformat()}_{order.order_no}_{line.sub_order_no}_"
            f"畔色{line.factory_no}单.jpg"
        ),
        kind="order_sheet_sent",
        source=source,
        on_date=date.today(),
        row_summary={
            "order_no": order.order_no,
            "sub_order_no": line.sub_order_no,
            "line_id": line.id,
            "factory_no_at_render": int(line.factory_no),
            "factory_label_at_render": f"畔色{line.factory_no}单",
            "render_width": 1684,
            "pushed": True,
            "line_delivery": True,
            # 激活态是送达幂等的一部分。缺少它会让下一轮
            # repush_activated 把刚发成功的子订单再次判成旧图并重推。
            "activated": order_flags.is_activated(order),
        },
    )
    return result.file


def reconcile_order_line_delivery(db: Session, *, limit: int = 50) -> dict:
    """按淘宝子订单逐件生成并推送尚未送达的实体商品。

    仅处理 ``factory_delivery_required`` 的新/已迁移行；历史未绑定记录不会被全量重推。
    退款且从未发送的行直接排除；退款且已有发送凭证留给子订单作废链处理。
    """
    import os
    from app.services import (
        feishu_client,
        order_line_delivery_service as line_delivery,
        settings_service,
    )

    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return {"pushed": 0, "failed": 0, "order_nos": [], "reason": "notify_disabled"}
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if not chat_id:
        return {"pushed": 0, "failed": 0, "order_nos": [], "reason": "no_chat_id"}
    sent = line_delivery.sent_line_evidence(db)
    pushed: list[str] = []
    failed: list[dict] = []
    for line in line_delivery.active_lines(db):
        sub_order_no = str(line.sub_order_no or "")
        if sub_order_no in sent or len(pushed) + len(failed) >= limit:
            continue
        if line.factory_delivery_state in {"sending_caption", "sending_image", "uncertain"}:
            failed.append({
                "order_no": line.order_no,
                "sub_order_no": sub_order_no,
                "reason": "此前发送结果不确定，已停止自动重发，需核对飞书回执",
            })
            continue
        order = db.execute(
            select(Order).where(Order.order_no == line.order_no)
        ).scalar_one_or_none()
        if order is None or (order.status or "") in ("cancelled", "pending_payment"):
            continue
        from app.services import order_flags
        if order_flags.is_remote(order):
            continue
        address_pending_for_production = _can_push_production_only_without_address(order)
        if not _addr_ok_for_factory(order) and not address_pending_for_production:
            failed.append({
                "order_no": order.order_no,
                "sub_order_no": sub_order_no,
                "reason": "收货地址不完整，已暂缓发送",
                "deferred": "address_masked",
            })
            continue
        if line.factory_no is None:
            line.factory_no = line_delivery.next_factory_no(db)
            db.flush()
        delivery_key = f"factory-line:{sub_order_no}:{line.factory_no}"
        line.factory_delivery_key = delivery_key
        line.factory_delivery_state = "rendering"
        line.factory_delivery_error = None
        db.commit()
        send_stage = "rendering"
        try:
            sheet = factory_sheet.build_for_order_line(
                db,
                order.id,
                line.id,
                address_pending_for_production=address_pending_for_production,
            )
            png = render_png(sheet)
            image_key = feishu_client.upload_image(db, png)
            line.factory_delivery_state = "sending_image"
            db.commit()
            send_stage = "sending_image"
            image_result = feishu_client.send_image(db, chat_id, image_key) or {}
            archive_sent_line_snapshot(db, order, line, png)
            line.factory_delivery_state = "sent"
            line.factory_delivery_sent_at = datetime.now().astimezone()
            line.factory_delivery_message_id = _feishu_message_id(image_result)
            db.commit()
            pushed.append(sub_order_no)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            line = db.get(OrderDetail, line.id)
            if line is not None:
                line.factory_delivery_state = (
                    "uncertain" if send_stage in {"sending_caption", "sending_image"} else "failed"
                )
                line.factory_delivery_error = f"{type(exc).__name__}: {exc}"[:500]
                db.commit()
            failed.append({
                "order_no": order.order_no,
                "sub_order_no": sub_order_no,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
            })
            _logger.warning("子订单下单图发送失败 %s", sub_order_no, exc_info=True)
    return {
        "pushed": len(pushed),
        "failed": len(failed),
        "sub_order_nos": pushed,
        "failures": failed,
    }


def reconcile_refunded_order_lines(db: Session, *, limit: int = 50) -> dict:
    """仅作废“该子订单曾经实际发给工厂”的退款商品。

    退款但从未发送的商品不补推、不作废；同主订单其它商品完全不受影响。
    """
    import os
    from app.services import (
        feishu_client,
        order_line_delivery_service as line_delivery,
        settings_service,
    )
    sent = line_delivery.sent_line_evidence(db)
    already_void = line_delivery.void_line_evidence(db)
    targets = [
        line for line in line_delivery.physical_lines(db)
        if line.sub_order_no
        and line_delivery.line_is_refunded(line)
        and str(line.sub_order_no) in sent
        and str(line.sub_order_no) not in already_void
    ]
    if not targets:
        return {"voided": 0, "failed": 0, "sub_order_nos": [], "failures": []}
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return {"voided": 0, "failed": 0, "sub_order_nos": [], "reason": "notify_disabled"}
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if not chat_id:
        return {"voided": 0, "failed": 0, "sub_order_nos": [], "reason": "no_chat_id"}
    voided: list[str] = []
    failed: list[dict] = []
    for line in targets[:limit]:
        order = db.execute(select(Order).where(Order.order_no == line.order_no)).scalar_one_or_none()
        if order is None:
            continue
        try:
            sheet = factory_sheet.build_for_order_line(db, order.id, line.id)
            content = render_void_png(sheet)
            image_key = feishu_client.upload_image(db, content)
            feishu_client.send_image(db, chat_id, image_key)
            import_storage.archive(
                db,
                content=content,
                original_name=(
                    f"{date.today().isoformat()}_{order.order_no}_{line.sub_order_no}_已作废.jpg"
                ),
                kind="order_sheet_void",
                source="line_refund",
                on_date=date.today(),
                row_summary={
                    "order_no": order.order_no,
                    "sub_order_no": line.sub_order_no,
                    "line_id": line.id,
                    "factory_no_at_render": line.factory_no,
                    "factory_label_at_render": (
                        f"畔色{line.factory_no}单" if line.factory_no is not None else None
                    ),
                    "render_width": 1684,
                    "line_void": True,
                    "pushed": True,
                },
            )
            db.commit()
            voided.append(str(line.sub_order_no))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed.append({
                "sub_order_no": line.sub_order_no,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
            })
    return {
        "voided": len(voided),
        "failed": len(failed),
        "sub_order_nos": voided,
        "failures": failed,
    }


def _order_no_from_name(name: Optional[str]) -> Optional[str]:
    """从下单图归档文件名反解订单号 — 兼容新旧两种命名。

    旧: 下单图_{order_no}.html  ;  新(2026-06-19): {下单日期}_{order_no}.jpg (日期 YYYY-MM-DD 不含下划线)
    """
    if not name:
        return None
    if name.startswith("下单图_") and name.endswith(".html"):
        return name[len("下单图_"):-len(".html")]
    if name.endswith(".jpg"):
        parts = name[:-len(".jpg")].split("_")
        if len(parts) == 2:        # {日期}_{订单号}
            return parts[1]
    return None


def _pending_push_records(db: Session, *, include_baseline: bool) -> list[ImportedFile]:
    """【还没推过图】的下单图归档记录, 按生成先后 (旧→新)。

    判定基于归档记录自身的 row_summary.pushed —— 与"HTML 是否新生成"彻底解耦。
    include_baseline=False: 跳过部署前堆积的历史基线 (row_summary.baseline=True),
    避免 18:00 自动推一次性把历史单刷给工厂群; 手动补推时传 True 把历史也纳入。
    """
    recs = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet").order_by(ImportedFile.id.asc())
    ).scalars().all()
    out: list[ImportedFile] = []
    for r in recs:
        st = r.row_summary or {}
        if st.get("pushed"):
            continue
        if not include_baseline and st.get("baseline"):
            continue
        if not _order_no_from_name(r.original_filename):
            continue
        out.append(r)
    return out


def _pushed_sheet_evidence(db: Session) -> dict[str, dict[str, list[ImportedFile]]]:
    """Return trusted factory-delivery evidence keyed by order number.

    ``order_sheet`` is the mutable render queue, while ``order_sheet_sent`` is
    the immutable snapshot that proves what was actually delivered.  Older
    transition code only inspected the mutable queue.  Once queue rows were
    regenerated or lost their ``pushed`` marker, a delivered order could be
    mistaken for an unseen order and keep its stale factory number.

    Sent snapshots are evidence only and must be preserved.  Callers may delete
    ``deletable`` queue rows when regenerating/voiding a sheet, but never delete
    the historical ``evidence`` records.
    """
    rows = db.execute(
        select(ImportedFile).where(
            ImportedFile.kind.in_(("order_sheet", "order_sheet_sent"))
        )
    ).scalars().all()
    out: dict[str, dict[str, list[ImportedFile]]] = {}
    for rec in rows:
        summary = rec.row_summary or {}
        if summary.get("pushed") is not True:
            continue
        # 历史凭证保留审计，但激活重推已经明确取代它，不能继续充当
        # 当前送达证据，否则会出现“任务说成功、工厂群却没有新图”。
        if summary.get("delivery_superseded") is True:
            continue
        order_no = str(summary.get("order_no") or "").strip()
        if not order_no:
            order_no = _order_no_from_name(rec.original_filename) or ""
        if not order_no:
            continue
        item = out.setdefault(order_no, {"evidence": [], "deletable": []})
        item["evidence"].append(rec)
        if rec.kind == "order_sheet":
            item["deletable"].append(rec)
    return out


def _is_pushable(db: Session, rec: ImportedFile) -> bool:
    """这条待推记录是否『真的推得出去（且值得推）』。

    跳过: 订单不存在 / 取消 / 待付款 / 退款; 以及【没有工厂编号的历史老单(<6/19)】——
    它们渲染出来是红字"未能匹配工厂订单号", 推给工厂只是噪音, 系统本就从不主动推它们
    (见 push_pending_images 注释)。可自动顺排编号的新单(>=6/19)推送时会拿到号, 算可推。
    与 include_baseline 无关: 手动按钮也不该把这些无号老单算进「待推」, 否则角标会像用户看到的
    「待推 45」那样其实全是 6 月历史无号单, 怎么点都清不掉。
    """
    no = _order_no_from_name(rec.original_filename)
    if not no:
        return False
    order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
    if order is None:
        return False
    # 双保险：即便旧主单图早于本修复已进入待推队列，只要权威子订单显示
    # “一件有效、一件已退款”，也禁止发送代表商品图。
    if _partial_refund_child_lines(db, order):
        return False
    if db.execute(
        select(OrderDetail.id).where(
            OrderDetail.order_no == no,
            OrderDetail.source == "import",
            OrderDetail.factory_delivery_required.is_(True),
        ).limit(1)
    ).scalar_one_or_none() is not None:
        return False
    if (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
        return False
    from app.services import order_flags
    if order_flags.is_remote(order):
        return False
    if getattr(order, "factory_no", None) is None:
        od = getattr(order, "order_date", None)
        # 老远期单下单时可能早于自动编号启用日，但客户后来明确
        # “开始制作”后就是当前生产任务。生成候选和后续编号逻辑本来都
        # 接纳这种激活单；这里也必须使用同一口径，不能把已生成的图
        # 永久卡在待推队列。
        if not ((od and od >= _AUTO_NUMBER_SINCE) or order_flags.is_activated(order)):
            return False
    return True


def count_pending_push(db: Session, *, include_baseline: bool = True) -> int:
    """待推飞书的下单图张数 (前端按钮角标用)。

    只数『真能推且值得推』的: 订单存在、非取消/待付款/退款、且有工厂编号(或为可自动顺排的新单)。
    修复历史 bug (用户 2026-07-01: 资料存档库显示「待推 49」但其实全推过了): 旧计数只看 row_summary.pushed
    标记, 把订单已删/取消/退款、以及 6 月无工厂编号的历史基线单也算进去 → 角标卡在一个怎么点都清不掉的数。
    """
    recs = _pending_push_records(db, include_baseline=include_baseline)
    return sum(1 for r in recs if _is_pushable(db, r))


# 工厂制单编号: 历史(<6/19)靠 ZIP 回填; 6/19 起新单推送时按订单顺序自动顺排 (用户拍板 2026-06-19)
_AUTO_NUMBER_SINCE = date(2026, 6, 19)


def _next_factory_no(db: Session) -> int:
    """下一个工厂制单编号 = 现有最大 + 1 (新单按订单顺序往后排)。"""
    order_max = db.execute(select(func.max(Order.factory_no))).scalar() or 241
    line_max = db.execute(select(func.max(OrderDetail.factory_no))).scalar() or 241
    return max(int(order_max), int(line_max)) + 1


def _next_remote_seq(db: Session) -> int:
    """下一个远期单内部序号 = 现有最大 + 1 (顺序无所谓, 只要唯一)。"""
    mx = db.execute(select(func.max(Order.remote_seq))).scalar()
    return (mx or 0) + 1


def assign_remote_seqs(db: Session) -> dict:
    """给【现远期挂起(工厂看板口径, 含日期式延期)且还没序号】的单补发 remote_seq → 显示"远期单 N"。
    幂等(已有号跳过)。远期单不占工厂号(畔色X单), 只发这个内部序号, 仅内部看 (用户 2026-07-09)。"""
    from app.services import order_flags
    today = date.today()
    cols = (Order.remark, Order.production_note, Order.buyer_message, Order.seller_memo)
    # 候选 = 手动远期 / 客户延期 / 有任一备注(可能含远期词/日期式发货)；
    # 客户延期可能没有文字备注，仍必须拿到远期序号。精确判定交给 is_factory_remote。
    cond = or_(
        Order.is_remote_ship.is_(True),
        Order.is_customer_delayed.is_(True),
        *[c.is_not(None) for c in cols],
    )
    cands = db.execute(select(Order).where(cond, Order.remote_seq.is_(None))).scalars().all()
    nxt = _next_remote_seq(db)
    assigned: list = []
    for o in cands:
        if order_flags.is_factory_remote(o, today):
            o.remote_seq = nxt
            assigned.append((o.order_no, nxt))
            nxt += 1
    if assigned:
        db.commit()
    return {"assigned_remote_seq": len(assigned), "detail": assigned[:50]}


def _addr_ok_for_factory(order: Order) -> bool:
    """收货地址是否可用于发工厂下单图: 非空 且 未被星号脱敏/加密。

    与下单图自身的红字"没有抓取到收货地址"判据 (factory_sheet 用 validation.is_address_encrypted)
    保持一致 —— 决定一张下单图是「地址完整推送」还是「缺地址被推」(后者待解密后重推)。
    """
    addr = order.customer_address
    if not addr:
        return False
    from app.services import validation
    return not validation.is_address_encrypted(addr).is_encrypted


def _feishu_message_id(result: object) -> Optional[str]:
    """兼容飞书客户端真实返回值与旧测试桩的嵌套返回值。"""
    if not isinstance(result, dict):
        return None
    direct = result.get("message_id")
    if direct:
        return str(direct)
    nested = result.get("data")
    if isinstance(nested, dict) and nested.get("message_id"):
        return str(nested["message_id"])
    return None


def _can_push_production_only_without_address(order: Order) -> bool:
    """平台已发货且已有运单号、但历史报表不再返回地址时，允许只下生产图。

    这类订单已经离开淘宝待发货地址报表，继续重拉不会补出地址。系统不得伪造或
    回填客户地址，但可以把工厂图明确标为“地址待补，仅生产，禁止发货”，避免
    生产任务永久卡住。普通待发货订单仍执行完整地址硬门，不受此例外影响。
    """
    return (
        not _addr_ok_for_factory(order)
        and (order.status or "") == "shipped"
        and bool((getattr(order, "tracking_no", None) or "").strip())
    )


def _send_no_addr_notice(db: Session, chat_id: str, missing: list) -> None:
    """兼容旧入口：缺地址诊断只发微信 Push，不进入飞书订单群。

    missing: [(order_no, factory_no), ...]; 这些单已做制单图(带编号)推送, 但收货为空。
    """
    if not missing:
        return
    from app.services import agent_ingest_service, notify_service
    lines = [f"  · {('畔色 '+str(fno)+' 单') if fno else '未编号'}　订单号 {no}" for no, fno in missing]
    quota = agent_ingest_service.get_order_quota_result(db)
    if quota.get("verified"):
        diagnosis = (
            "本轮导出前已核验为【当日不限额度】；这些订单仍未返回完整地址，"
            "属于平台报表/历史订单地址未回填，并非系统漏做提额。"
        )
    else:
        diagnosis = "本轮没有取得【当日不限额度】核验证据，系统已按安全门停止发送缺地址图片。"
    txt = (f"⚠️ 下列 {len(missing)} 单【没有抓取到完整收货地址】，已暂缓发送工厂下单图:\n"
           + "\n".join(lines)
           + "\n" + diagnosis
           + "\n系统已单独挂起；下一次正常拉单若补齐地址，只释放对应下单图，不发送额外日报。"
           + "\n地址仍不可用时继续暂缓，绝不发送缺地址图片。")
    try:
        notify_service.notify(
            db,
            txt,
            level="warn",
            title="畔色 ERP | 下单图暂缓",
            wechat_allowed=True,
        )
    except Exception:  # noqa: BLE001
        _logger.warning("无收货地址微信Push提示发送失败", exc_info=True)


def _send_sheets_zip(db: Session, chat_id: str, items: list) -> None:
    """兼容旧调用；飞书订单群治噪后不再追加批次 ZIP。"""
    return None


# 样块/样品/小样单: 永不推工厂下单图 (用户 2026-07-04: 样块只在系统里记成本, 绝不发飞书工厂群,
# 也不占用「畔色X单」编号)。看 product_name + sku 关键词, 覆盖全部样块产品编码(如 PPS23980010606/50606)。
_SAMPLE_KEYWORDS = ("样块", "样品", "小样", "样木")


def _is_sample_order(o) -> bool:
    text = (getattr(o, "product_name", "") or "") + " " + (getattr(o, "sku", "") or "")
    return any(k in text for k in _SAMPLE_KEYWORDS)


# 定制补差/加价/尾款单: 不推工厂下单图 (用户 2026-07-12: 补差单套了柜子产品名, 推给工厂被当成整柜重复做,
# 例: 严小蓝 ¥315「其他定制」补差被推成畔色292单)。两条口径, 命中任一即判补差 → 不推、不占「畔色X单」编号:
#   ① 订单备注含补差类关键词; ② 订单实付金额 < 阈值 (默认¥400, 工厂制作单页面可配 factory_push_min_amount)。
_PARTS_TOPUP_KEYWORDS = (
    "补差", "补价", "补款", "补拍", "补邮费", "补运费", "邮费差", "运费差",
    "补尾款", "尾款差", "补货款", "定制补", "加价", "改价补", "补配件", "补链接",
    "拍差价", "差价链接", "补拍链接", "定制加",
)
_DEFAULT_PUSH_MIN_AMOUNT = 400.0


def _push_min_amount(db: Session) -> float:
    """工厂下单图推送的最低金额门槛 (低于此值判定为补差/加价单, 不推)。0 = 关闭金额规则。"""
    from app.services import settings_service
    raw = settings_service.get(db, "factory_push_min_amount", env_fallback=False)
    try:
        v = float(raw)  # type: ignore[arg-type]
        return v if v >= 0 else _DEFAULT_PUSH_MIN_AMOUNT
    except (TypeError, ValueError):
        return _DEFAULT_PUSH_MIN_AMOUNT


def _is_parts_topup(db: Session, o) -> "tuple[bool, str]":
    """是否定制补差/加价单(不推工厂)。返回 (是否, 原因)。命中备注关键词 或 金额<阈值。"""
    text = " ".join(str(getattr(o, f, "") or "") for f in
                    ("remark", "seller_memo", "production_note", "buyer_message"))
    for k in _PARTS_TOPUP_KEYWORDS:
        if k in text:
            return True, f"备注含「{k}」"
    thr = _push_min_amount(db)
    amt = float(getattr(o, "paid_amount", 0) or 0) or float(getattr(o, "buyer_payable_amount", 0) or 0)
    if thr > 0 and 0 < amt < thr:
        return True, f"金额¥{amt:.0f} < 门槛¥{thr:.0f}"
    return False, ""


def _reclaim_remote_numbers(db: Session) -> list[dict]:
    """远期单不占「畔色X单」号(只拿内部 remote_seq) —— 自动收回 (用户 2026-07-12: 299远期单占号留空洞)。

    命中 = 有 factory_no + 现为远期挂起(is_remote) + 该单下单图【从没推给工厂】(工厂没见过这个号)。
    时序漏洞根因: 编号时还不是远期(推送又失败), 之后客户备注延期才变远期 → 号卡在远期单手里。
    已推过的远期单不在此动 —— 工厂见过号, 走 void_remote_pushed 显式作废(会通知工厂), 不悄悄收。"""
    from app.services import order_flags
    pushed_nos = set(_pushed_sheet_evidence(db))
    reclaimed: list[dict] = []
    for o in db.execute(select(Order).where(Order.factory_no.isnot(None))).scalars().all():
        if o.order_no in pushed_nos or not order_flags.is_remote(o):
            continue
        old = o.factory_no
        o.factory_no = None
        if getattr(o, "remote_seq", None) is None:
            o.remote_seq = _next_remote_seq(db)
        db.flush()
        reclaimed.append({"order_no": o.order_no, "old_factory_no": old, "remote_seq": o.remote_seq})
        _logger.info("远期单收回工厂号: %s 原畔色%s单 → 远期单%s", o.order_no, old, o.remote_seq)
    if reclaimed:
        db.commit()
    return reclaimed


def push_pending_images(db: Session, *, limit: int = 20, include_baseline: bool = False,
                        quiet: bool = False, only_order_nos: "set[str] | None" = None) -> dict:
    """把【还没推过图】的下单图渲染成图片推飞书工厂群, 推成功就在该归档记录标记 pushed=True。

    quiet=True (每小时自愈补推用): 只推单张图片, 跳过末尾 ZIP 打包 + 无收货地址提醒
    (那两样留给 18:00 日报一次性发, 避免每小时刷屏)。

    与"生成 HTML"彻底解耦 —— 不论 HTML 是 18:00 日推、每小时补生成、还是手动生成的,
    只要这条归档还没推过图就在这里补推一次。修复历史 bug: 旧逻辑只推「本次新生成」的单号,
    一旦被每小时补生成任务抢先生成, 该单就永远不再被推 (归档里全是 HTML、飞书一张图没有)。

    返回 {pushed, failed, remaining, order_nos, reason?}。单张失败不抛 (不阻断整批)。
    """
    import os
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return {"pushed": 0, "failed": 0, "remaining": 0, "order_nos": [], "reason": "notify_disabled"}
    _reclaim_remote_numbers(db)   # 自愈: 编了号但没推过、现变远期的单 → 收回工厂号(远期单不占号)
    from app.services import feishu_client, settings_service
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if not chat_id:
        return {"pushed": 0, "failed": 0,
                "remaining": count_pending_push(db, include_baseline=include_baseline),
                "order_nos": [], "reason": "no_chat_id"}
    pushed = failed = 0
    sent_nos: list[str] = []
    failed_nos: list[str] = []
    _missing_addr: list = []
    _held_skeleton: list[str] = []   # SKU未回填暂缓的单(留队列, 回填后自动补推)
    _held_address: list[str] = []
    _pushed_address_pending: list[str] = []
    _released_address_hold: list[str] = []
    _held_remote: list[str] = []
    _delivery_uncertain: list[str] = []
    _skipped_sample: list[str] = []
    _skipped_topup: list[dict] = []
    records = _pending_push_records(db, include_baseline=include_baseline)
    if only_order_nos is not None:
        # 定向重推 (解密补地址后): 只推指定单号, 不误扫其它待推/历史基线
        records = [r for r in records if _order_no_from_name(r.original_filename) in only_order_nos]
    for rec in records[:limit]:
        no = _order_no_from_name(rec.original_filename)
        was_address_hold = bool((rec.row_summary or {}).get("held_no_address"))
        delivery_state = str((rec.row_summary or {}).get("delivery_state") or "")
        if delivery_state in {"sending", "sending_caption", "sending_image", "uncertain"}:
            _delivery_uncertain.append(no)
            continue
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order:
            continue
        if (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue   # 取消/退款/待付款 不推工厂 (待付款大概率会取消, 用户拍板 2026-06-20)
        from app.services import order_flags
        if order_flags.is_remote(order):
            _held_remote.append(no)
            continue   # 远期挂起单 不推工厂, 等激活后以新号推 (用户 2026-07-08; 挂起单本就没生成图, 此处双保险)
        if _is_sample_order(order):
            _skipped_sample.append(no)
            # 样块/样品单永不推工厂下单图 (用户 2026-07-04); 标记已处理, 清出待推队列 + 不占工厂编号。
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True, "skipped_sample": True}
            db.commit()
            continue
        _topup, _topup_reason = _is_parts_topup(db, order)
        if _topup:
            _skipped_topup.append({"order_no": no, "reason": _topup_reason})
            # 定制补差/加价单永不推工厂 (用户 2026-07-12): 标记已处理清出队列 + 不占工厂编号, 记原因备查。
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True,
                               "skipped_topup": True, "topup_reason": _topup_reason}
            db.commit()
            continue
        if not getattr(order, "sku_code", None) and not getattr(order, "sku", None):
            # 骨架单(付款赶在报表导出之后, SKU还没回填): 图/尺寸都解析不出, 推出去就是"无产品图纸+未对应尺寸"
            # (用户 2026-07-12: 畔色301单空图空尺寸推给工厂)。→ 先不推、不占号、【留在待推队列】,
            # 等取数回填(每小时ingest/次日18:00)后, 下一轮 push 自动带图带尺寸补推。自愈, 不会静默丢单。
            _held_skeleton.append(no)
            continue
        # Historical/non-production rows are not factory-delivery candidates.
        # Filter them before the address gate so a stale signed row cannot
        # create a false "address masked" failure.
        if not _is_pushable(db, rec):
            continue
        # 普通待发货订单仍执行完整地址硬门。平台已经标记发货且已有运单号的历史单
        # 已经离开待发货地址报表，继续重拉不会补出地址；这类单只发送“仅生产、禁
        # 止发货”的醒目标记图，后续若地址回填，再由地址释放流程补发完整图。
        address_pending_for_production = _can_push_production_only_without_address(order)
        if not _addr_ok_for_factory(order) and not address_pending_for_production:
            _held_address.append(no)
            _missing_addr.append((no, order.factory_no))
            rec.row_summary = {
                **(rec.row_summary or {}),
                "pushed": False,
                "pushed_addr_ok": False,
                "held_no_address": True,
            }
            db.commit()
            continue
        # 6/19 起新单按订单顺序自动顺排工厂编号 (历史靠 ZIP 回填, 不在此动)。
        # 已激活的老远期单(备注开始制作)也顺排新号 —— 它们早下但现在要做, 不该被日期线卡住 (用户 2026-07-08)。
        if (getattr(order, "factory_no", None) is None and order.order_date
                and (order.order_date >= _AUTO_NUMBER_SINCE or order_flags.is_activated(order))):
            order.factory_no = _next_factory_no(db)
            db.flush()
        # 没有正式工厂编号的图片禁止发送。手动补推也必须先补齐编号，不能再生成
        # “未能匹配工厂订单号”的生产图，确保图片、表格第一列和工厂群标题完全一致。
        if getattr(order, "factory_no", None) is None:
            continue
        delivery_key = f"factory-sheet:{no}:{order.factory_no}"
        rec.row_summary = {
            **(rec.row_summary or {}),
            "delivery_key": delivery_key,
            "delivery_state": "sending",
            "delivery_started_at": datetime.now().astimezone().isoformat(),
            "delivery_error": None,
        }
        db.commit()
        send_stage = "render"
        try:
            png = render_png(factory_sheet.build(
                db,
                order.id,
                address_pending_for_production=address_pending_for_production,
            ))
            send_stage = "upload"
            key = feishu_client.upload_image(db, png)
            rec.row_summary = {
                **(rec.row_summary or {}),
                "delivery_state": "sending_image",
                "delivery_image_key": key,
                "delivery_caption_message_id": None,
            }
            db.commit()
            send_stage = "sending_image"
            image_result = feishu_client.send_image(db, chat_id, key) or {}
            # Only a confirmed image response becomes the trusted factory-sheet
            # source used by the ERP/Feishu dispatch table.
            archive_sent_snapshot(
                db,
                order,
                png,
                source=("addr_pending" if address_pending_for_production else "factory_push"),
            )
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True,
                               "pushed_addr_ok": not address_pending_for_production,
                               "held_no_address": False,
                               "delivery_state": "sent",
                               "delivery_sent_at": datetime.now().astimezone().isoformat(),
                               "delivery_message_id": _feishu_message_id(image_result),
                               "address_pending_pushed": address_pending_for_production,
                               "address_pending_reason": (
                                   "shipped_before_full_address_backfill"
                                   if address_pending_for_production else None
                               ),
                               "activated": order_flags.is_activated(order)}   # 激活态推的图不再被 repush_activated 重推
            db.commit()
            pushed += 1
            sent_nos.append(no)
            if address_pending_for_production:
                _pushed_address_pending.append(no)
            elif was_address_hold:
                _released_address_hold.append(no)
        except Exception as exc:  # noqa: BLE001 - 单张失败不阻断整批
            db.rollback()
            uncertain = send_stage in {"sending_caption", "sending_image"}
            rec.row_summary = {
                **(rec.row_summary or {}),
                "pushed": False,
                "delivery_state": "uncertain" if uncertain else "failed",
                "delivery_error": f"{type(exc).__name__}: {exc}"[:500],
            }
            db.commit()
            failed += 1
            failed_nos.append(no)
            _logger.warning("下单图推飞书失败 %s", no, exc_info=True)
    if not quiet:
        # 飞书订单群只留单张下单图；ZIP、缺地址诊断和运行状态均不再写入该群。
        if _missing_addr:
            try:
                from app.services import notify_service
                notify_service.notify(
                    db,
                    "下列订单因收货地址不完整已暂缓发送下单图：\n"
                    + "\n".join(f"  · {no}" for no, _ in _missing_addr[:20]),
                    level="warn",
                    title="畔色 ERP | 下单图暂缓",
                    wechat_allowed=True,
                )
            except Exception:  # noqa: BLE001
                _logger.warning("缺地址订单微信Push提醒失败", exc_info=True)
        if _held_skeleton:
            # 内部提醒(非工厂群): 骨架单暂缓名单, 回填后自动补推; 若连日重复出现 = 取数没跑, 人来查。
            try:
                from app.services import notify_service
                notify_service.notify(
                    db, "⏳ %d 单因SKU未回填暂缓推工厂(等取数回填后自动补推):\n%s"
                    % (len(_held_skeleton), "\n".join(f"  · {n}" for n in _held_skeleton[:10])),
                    level="warn", title="畔色 ERP [下单图暂缓·等SKU回填]",
                    wechat_allowed=True)
            except Exception:  # noqa: BLE001
                pass
    return {"pushed": pushed, "failed": failed,
            "remaining": count_pending_push(db, include_baseline=include_baseline),
            "order_nos": sent_nos, "failed_order_nos": failed_nos,
            "held_no_sku": _held_skeleton, "held_no_address": _held_address,
            "pushed_address_pending": _pushed_address_pending,
            "released_after_address_fill": _released_address_hold,
            "held_remote": _held_remote,
            "delivery_uncertain": _delivery_uncertain,
            "skipped_sample": _skipped_sample, "skipped_topup": _skipped_topup}


def find_pushed_without_address(db: Session) -> list[ImportedFile]:
    """此前因缺地址被推错或被暂缓、现在已有可用地址的下单图记录。

    判据: row_summary.pushed=True 且 pushed_addr_ok=False (上次推时没地址/被脱敏),
    且订单当前 _addr_ok_for_factory()=True (口令解密后地址已补上)。这些就是值得重推的单。
    """
    recs = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet").order_by(ImportedFile.id.asc())
    ).scalars().all()
    out: list[ImportedFile] = []
    for r in recs:
        st = r.row_summary or {}
        # 只释放两种明确的地址异常状态：
        # 1) 旧逻辑曾把缺地址图片发出；2) 新安全门已拦截并暂缓。
        # 历史记录若只有 pushed=True、没有新标记，仍不自动重发。
        legacy_bad_push = bool(st.get("pushed")) and st.get("pushed_addr_ok") is False
        safely_held = not bool(st.get("pushed")) and st.get("held_no_address") is True
        if not (legacy_bad_push or safely_held):
            continue
        no = _order_no_from_name(r.original_filename)
        if not no:
            continue
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or not _addr_ok_for_factory(order):
            continue   # 订单不存在 / 仍无可用地址 → 重推也还是空, 不推
        out.append(r)
    return out


def repush_after_address_fill(db: Session, *, limit: int = 50, quiet: bool = True) -> dict:
    """飞书口令补上地址后，重推错图或释放此前被安全门暂缓的图。

    根治: 缺地址的下单图被推一次后即标 pushed=True, 任何自动推送都会永久跳过它 ——
    地址解密回来也不会再发。这里把这些单的 pushed 标记清掉再定向重推一次。
    幂等: 重推成功后 pushed_addr_ok 置 True (push_pending_images 内), 不会再被选中。
    quiet=True: 不重复发 ZIP / 无地址提醒 (这些单已补上地址)。
    返回 {repushed, failed, order_nos, candidates}。
    """
    targets = find_pushed_without_address(db)
    if not targets:
        return {"repushed": 0, "failed": 0, "order_nos": [], "candidates": 0}
    order_nos: set[str] = set()
    for r in targets[:limit]:
        no = _order_no_from_name(r.original_filename)
        if not no:
            continue
        r.row_summary = {**(r.row_summary or {}), "pushed": False}   # 清标记, 让 push 重新选中
        order_nos.add(no)
    db.commit()
    res = push_pending_images(db, limit=max(limit, len(order_nos)),
                              include_baseline=True, quiet=quiet, only_order_nos=order_nos)
    return {"repushed": res.get("pushed", 0), "failed": res.get("failed", 0),
            "order_nos": res.get("order_nos", []), "candidates": len(order_nos)}


def reconcile_pending_delivery(db: Session, *, limit: int = 50, quiet: bool = True) -> dict:
    """幂等收口“订单已入库 → 下单图已送达”的整条链。

    给口令回调和晚间补跑共用：每次都先处理远期状态，再补生成并只推尚未成功送达的增量图。
    ``row_summary.pushed`` 是送达幂等标记，重复调用不会重复发图。
    """
    remote = void_remote_pushed(db)
    activated = repush_activated(db)
    assign_remote_seqs(db)
    from app.services import remote_report_service
    remote_report = remote_report_service.send_pending_reminders(db)
    generated = generate_pending(db)
    push = push_pending_images(db, limit=limit, include_baseline=False, quiet=quiet)
    from app.services import order_line_delivery_service as line_delivery
    legacy_binding = line_delivery.bind_unambiguous_legacy_evidence(db)
    line_void = reconcile_refunded_order_lines(db, limit=limit)
    line_push = reconcile_order_line_delivery(db, limit=limit)
    line_gate = line_delivery.delivery_count_gate(db)
    result = {
        "generated": generated,
        "generation_failed": int(generated.get("generation_failed") or 0),
        "generation_failed_order_nos": generated.get("generation_failed_order_nos") or [],
        "generation_failures": generated.get("generation_failures") or [],
        "images_pushed": int(push.get("pushed") or 0),
        "images_failed": int(push.get("failed") or 0),
        "images_remaining": int(push.get("remaining") or 0),
        "order_nos": push.get("order_nos") or [],
        "failed_order_nos": push.get("failed_order_nos") or [],
        "held_no_sku": push.get("held_no_sku") or [],
        "held_no_address": push.get("held_no_address") or [],
        "released_after_address_fill": push.get("released_after_address_fill") or [],
        "held_remote": push.get("held_remote") or [],
        "delivery_uncertain": push.get("delivery_uncertain") or [],
        "skipped_sample": push.get("skipped_sample") or [],
        "skipped_topup": push.get("skipped_topup") or [],
        "push_reason": push.get("reason"),
        "remote_voided": remote["voided_remote"],
        "remote_transitions": remote["remote_transitions"],
        "remote_feishu_notified": remote["feishu_notified"],
        "remote_feishu_failed": remote["feishu_failed"],
        "remote_report_due": remote_report["due"],
        "remote_report_sent": remote_report["sent"],
        "remote_report_failed": remote_report["failed"],
        "activated_repush_reset": activated.get("reset_for_new_no") or [],
        "activated_baseline_released": activated.get("released_activated_baseline") or [],
        "line_images_pushed": int(line_push.get("pushed") or 0),
        "line_images_failed": int(line_push.get("failed") or 0),
        "line_failures": line_push.get("failures") or [],
        "line_delivery_gate": line_gate,
        "legacy_line_binding": legacy_binding,
        "line_voided": int(line_void.get("voided") or 0),
        "line_void_failed": int(line_void.get("failed") or 0),
        "line_void_failures": line_void.get("failures") or [],
    }
    errors: list[str] = []
    if result["generation_failed"]:
        details = "; ".join(
            f"{item.get('order_no')}: {item.get('error')}"
            for item in result["generation_failures"][:10]
        )
        errors.append(
            f"下单图生成失败 {result['generation_failed']} 张"
            + (f": {details}" if details else "")
        )
    if result["push_reason"] in ("no_chat_id", "notify_disabled"):
        errors.append(f"飞书通道不可用: {result['push_reason']}")
    if result["images_failed"]:
        errors.append(
            f"图片发送失败 {result['images_failed']} 张"
            + (f": {','.join(result['failed_order_nos'])}" if result["failed_order_nos"] else "")
        )
    if result["held_no_sku"]:
        errors.append(
            f"SKU未回填暂缓 {len(result['held_no_sku'])} 张: {','.join(result['held_no_sku'])}"
        )
    # Address masking is an expected platform-data wait state, not a delivery
    # failure. Only unexplained pending rows beyond that known defer are errors.
    deferred_addresses = len(set(result["held_no_address"]))
    result["images_deferred_no_address"] = deferred_addresses
    unexplained_remaining = max(0, result["images_remaining"] - deferred_addresses)
    if unexplained_remaining:
        errors.append(f"仍有 {unexplained_remaining} 张未说明原因的下单图未送达")
    if result["remote_feishu_failed"]:
        errors.append(
            "远期改单作废通知失败: "
            + "; ".join(
                f"{x.get('order_no')}: {x.get('reason')}"
                for x in result["remote_feishu_failed"]
            )
        )
    if result["remote_report_failed"]:
        errors.append(
            "远期单淘宝报备卡片发送失败: "
            + "; ".join(
                f"{x.get('order_no')}: {x.get('reason')}"
                for x in result["remote_report_failed"]
            )
        )
    if result["delivery_uncertain"]:
        errors.append(
            "飞书是否送达无法确定，已停止盲目重发，需人工核对: "
            + ",".join(result["delivery_uncertain"])
        )
    if result["line_images_failed"]:
        actionable_line_failures = [
            item for item in result["line_failures"]
            if item.get("deferred") != "address_masked"
        ]
        result["line_deferred_no_address"] = [
            item.get("sub_order_no") for item in result["line_failures"]
            if item.get("deferred") == "address_masked"
        ]
    else:
        actionable_line_failures = []
        result["line_deferred_no_address"] = []
    if actionable_line_failures:
        errors.append(
            "子订单下单图发送失败: "
            + "; ".join(
                f"{item.get('sub_order_no')}: {item.get('reason')}"
                for item in actionable_line_failures[:10]
            )
        )
    if result["line_void_failed"]:
        errors.append(
            "子订单退款作废发送失败: "
            + "; ".join(
                f"{item.get('sub_order_no')}: {item.get('reason')}"
                for item in result["line_void_failures"][:10]
            )
        )
    gate_missing = set(line_gate.get("missing_sub_order_nos") or [])
    address_deferred = set(result.get("line_deferred_no_address") or [])
    unexplained_line_missing = sorted(gate_missing - address_deferred)
    result["line_delivery_gate"]["deferred_no_address"] = sorted(address_deferred)
    result["line_delivery_gate"]["unexplained_missing_sub_order_nos"] = unexplained_line_missing
    if unexplained_line_missing or line_gate.get("unvoided_refunded_sub_order_nos"):
        errors.append(
            "子订单送达数量不一致: 有效商品 "
            f"{line_gate.get('active_product_count')} 件，已发送工厂图 "
            f"{line_gate.get('sent_factory_sheet_count')} 张；缺少 "
            + ",".join(unexplained_line_missing)
            + ("；退款未作废 " + ",".join(
                line_gate.get("unvoided_refunded_sub_order_nos") or []
            ) if line_gate.get("unvoided_refunded_sub_order_nos") else "")
        )
    if errors:
        result["_run_status"] = "fail"
        result["_error"] = "; ".join(errors)
    return result


def baseline_existing_sheets(db: Session) -> int:
    """一次性: 把"现存且还没推过图"的下单图标记为历史基线 (baseline=True)。

    使 18:00 自动推送不会把部署前堆积的历史单一次性刷给工厂群; 这些历史单仍可在
    「资料存档库」用手动按钮补推。幂等 (已 pushed / 已 baseline 的跳过)。返回新打标条数。"""
    n = 0
    for rec in _pending_push_records(db, include_baseline=True):
        st = rec.row_summary or {}
        if st.get("baseline"):
            continue
        rec.row_summary = {**st, "baseline": True}
        n += 1
    if n:
        db.commit()
    return n


def repush_activated(db: Session, *, limit: int = 50) -> dict:
    """任何已推过图的单, 现备注改成【开始制作】(激活) → 作废旧号 + 以新号重推一次 (用户 2026-07-09:
    不管是不是远期单, 读到开始制作都重推一次, 免遗漏; 原"仅远期身份"限制已去)。

    命中 = 有推过的下单图 + 那张不是"激活态"推的(row_summary.activated != True) + 订单现已激活。
    幂等 = 激活态推的图带 activated=True 下次跳过; 未激活的单被上面 is_activated 判定挡掉, 不误动。
    动作 = 有旧工厂号则给工厂群发"原X号作废"提示 + 删旧下单图归档 + 清 order.factory_no
        → 随后 generate_pending/push_pending_images 生成新图、顺排【新工厂号】重推(记 activated=True)。
    """
    from app.services import order_flags, feishu_client, settings_service
    seen = _pushed_sheet_evidence(db)
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    changed: list = []
    superseded_sub_orders: list[str] = []
    for no, sheet_info in seen.items():
        evidence = sheet_info["evidence"]
        recs = sheet_info["deletable"]
        if len(changed) >= limit:
            break
        if any((r.row_summary or {}).get("activated") is True for r in evidence):
            continue   # 已有"激活态"推的图 → 不再重推
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue
        if not _is_active_factory_order(order):
            continue
        if not order_flags.is_activated(order):
            continue   # 现在没激活(还是远期挂起) → 不动
        # 用户 2026-07-09: 不管是不是远期单, 只要备注读到"开始制作"就重推一次(免遗漏)。
        # 原限"曾是远期身份"已去掉; 幂等靠 activated=True 标记(激活态推过的不再进来)。
        old_no = getattr(order, "factory_no", None)
        if chat_id and old_no:
            try:
                feishu_client.send_text(
                    db, chat_id,
                    f"⚠️ 订单 {no} 原【畔色 {old_no} 单】作废 —— 客户已通知开始制作, 稍后以新号重推, 请以新号为准。")
            except Exception:  # noqa: BLE001 - 通知失败不阻断
                _logger.warning("重开作废提示发送失败 %s", no, exc_info=True)
        # order_sheet_sent 是不可删除的历史审计快照，但激活后它只代表旧一轮，
        # 必须显式标成已取代。尤其 factory_backfill 只重建了存档、没有发送群消息，
        # 不能继续让子订单数量门把它当成当前已送达。
        for evidence_rec in evidence:
            summary = evidence_rec.row_summary or {}
            evidence_rec.row_summary = {
                **summary,
                "delivery_superseded": True,
                "delivery_superseded_reason": "activated_repush",
                "delivery_superseded_at": datetime.now().astimezone().isoformat(),
            }
            line = None
            if summary.get("line_id") is not None:
                line = db.get(OrderDetail, int(summary["line_id"]))
            if line is None and summary.get("sub_order_no"):
                line = db.execute(
                    select(OrderDetail).where(
                        OrderDetail.sub_order_no == str(summary["sub_order_no"])
                    )
                ).scalar_one_or_none()
            if line is not None and line.order_no == no:
                if line.sub_order_no:
                    superseded_sub_orders.append(str(line.sub_order_no))
                line.factory_no = None
                line.factory_delivery_state = None
                line.factory_delivery_key = None
                line.factory_delivery_error = None
                line.factory_delivery_sent_at = None
                line.factory_delivery_message_id = None
        for r in recs:
            import_storage.delete_record(db, r.id)   # 删旧号下单图归档
        order.factory_no = None                       # 清工厂号 → 重推时顺排新号
        db.flush()
        changed.append(no)
    if changed:
        db.commit()

    # 历史基线并不代表“已送达”。早期远期单可能在部署基线阶段已经留下一张
    # baseline=True、pushed!=True 的占位图；之后备注改为“开始制作”时，
    # generate_pending 会因为“已有归档”而跳过，push_pending_images 又会因为
    # baseline 而跳过，最终永远不会进入工厂。这里只释放【从未送达】且现在
    # 已明确激活的基线队列记录；已送达证据仍由上面的 seen 分支处理并保留。
    released_baseline: list[str] = []
    baseline_rows = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet")
    ).scalars().all()
    grouped: dict[str, list[ImportedFile]] = {}
    for rec in baseline_rows:
        summary = rec.row_summary or {}
        if summary.get("baseline") is not True or summary.get("pushed") is True:
            continue
        no = _order_no_from_name(rec.original_filename)
        if no and no not in seen:
            grouped.setdefault(no, []).append(rec)

    for no, recs in grouped.items():
        if len(changed) + len(released_baseline) >= limit:
            break
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue
        if not _is_active_factory_order(order) or not order_flags.is_activated(order):
            continue
        for rec in recs:
            import_storage.delete_record(db, rec.id)
        # 这张基线从未送达工厂，不发送“旧号作废”通知，也不强制换号；
        # 下一步 generate_pending 会按最新备注重建，push_pending_images 会在
        # 缺号时为激活老单正常分配工厂号。
        released_baseline.append(no)

    if released_baseline:
        db.commit()
    return {
        "reset_for_new_no": changed,
        "released_activated_baseline": released_baseline,
        "superseded_sub_order_nos": sorted(set(superseded_sub_orders)),
    }


def repush_to_factory(db: Session, order_no: str) -> dict:
    """手动重推单张下单图到工厂群 (工厂生产看板「重推给工厂」按钮, 用户 2026-07-09):
    删旧图 → 按最新数据/备注重新生成 → 推。用于"工厂没收到 / 改了备注要重发"。
    远期挂起 / 取消 / 退款 / 未付款 / 样品单一律拒绝(它们本就不该进工厂)。"""
    from app.services import order_flags
    o = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
    if o is None:
        return {"ok": False, "error": "订单不存在"}
    if (o.status or "") in ("cancelled", "pending_payment") or _is_refunded(o) or not _is_paid(o):
        return {"ok": False, "error": "取消/退款/未付款单不推工厂"}
    if order_flags.is_factory_remote(o):
        return {"ok": False, "error": "远期挂起单不推工厂 —— 等客户通知「开始制作」后再推"}
    if _is_sample_order(o):
        return {"ok": False, "error": "样品/样块单不推工厂"}
    _topup, _reason = _is_parts_topup(db, o)
    if _topup:
        return {"ok": False, "error": f"定制补差/加价单不推工厂（{_reason}）—— 如确需推送, 去工厂制作单页调低推送金额门槛"}
    if not getattr(o, "sku_code", None) and not getattr(o, "sku", None):
        return {"ok": False, "error": "该单SKU还没回填(取数报表没赶上), 图/尺寸都出不来 —— "
                                      "等每小时取数回填后会自动补推; 急的话先重拉订单再推"}
    # 正式单没工厂号 → 顺排一个(否则下单图是"未能匹配工厂订单号")
    if getattr(o, "factory_no", None) is None:
        o.factory_no = _next_factory_no(db)
        db.flush()
    # 删旧图(为了带上最新备注/数据重渲染) → 生成 → 定向推这一张
    for r in db.execute(select(ImportedFile).where(ImportedFile.kind == "order_sheet")).scalars().all():
        if order_no in (r.original_filename or ""):
            import_storage.delete_record(db, r.id)
    db.commit()
    generate_for_order(db, o)
    push = push_pending_images(db, limit=5, include_baseline=True, only_order_nos={order_no})
    return {"ok": push["pushed"] > 0, "pushed": push["pushed"], "failed": push["failed"],
            "factory_no": o.factory_no, "order_label": order_flags.factory_label(o)}


def void_remote_pushed(db: Session, *, limit: int = 50, order_nos: "set[str] | None" = None) -> dict:
    """已推工厂、但现在已延期/远期(挂起)的单 → 作废旧工厂号 + 通知工厂勿做 + 清号挂起 (用户 2026-07-08)。

    由每日/补推任务自动执行; order_nos 可限定只处理指定单。命中 = 有推过的下单图 + 现 is_remote
    (远期且未激活) + 未取消/退款 + 有工厂号。动作 = 先分配远期单号, 飞书明确通知
    "原畔色X单作废 → 已改远期单N", 再删旧下单图并清 factory_no; 等激活后以新号重下。
    飞书发送成功/失败会逐单返回, 供调度器准确报警。"""
    from app.services import order_flags, feishu_client, settings_service
    seen = _pushed_sheet_evidence(db)
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    voided: list = []
    notified: list = []
    notify_failed: list = []
    transitions: list = []
    for no, sheet_info in seen.items():
        recs = sheet_info["deletable"]
        if len(voided) >= limit:
            break
        if order_nos is not None and no not in order_nos:
            continue
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue
        if not _is_active_factory_order(order):
            continue
        if not order_flags.is_remote(order):
            continue   # 现在不是远期挂起(已激活/普通) → 不作废
        old_no = getattr(order, "factory_no", None)
        if old_no is None:
            continue
        if getattr(order, "remote_seq", None) is None:
            order.remote_seq = _next_remote_seq(db)
            db.flush()
        remote_seq = order.remote_seq
        if chat_id:
            try:
                feishu_client.send_text(
                    db, chat_id,
                    f"⚠️ 订单 {no} 原【畔色 {old_no} 单】已作废，现已改为【远期单 {remote_seq}】。"
                    f"备注命中延期/等通知，请暂停生产；客户通知开始制作后，系统会以新的畔色单号重新下单。")
                notified.append(no)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("延期作废提示发送失败 %s", no, exc_info=True)
                notify_failed.append({"order_no": no, "reason": f"{type(exc).__name__}: {exc}"})
        else:
            notify_failed.append({"order_no": no, "reason": "未配置 feishu_push_chat_id"})
        for r in recs:
            import_storage.delete_record(db, r.id)
        order.factory_no = None
        db.flush()
        voided.append(no)
        transitions.append({"order_no": no, "old_factory_no": old_no, "remote_seq": remote_seq})
    if voided:
        db.commit()
    return {"voided_remote": voided, "remote_transitions": transitions,
            "feishu_notified": notified, "feishu_failed": notify_failed}


def remind_remote_pushed(db: Session) -> dict:
    """已推工厂、但现在已延期/远期的单 → 只【提醒用户】(不自动作废, 防误废工厂已在做的单) (用户 2026-07-08)。
    列出这些单发到用户通知渠道; 用户确认后再用 void_remote_pushed(order_nos=...) 作废其工厂号。"""
    from app.services import order_flags
    seen = set(_pushed_sheet_evidence(db))
    items: list = []
    for no in seen:
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue
        if not _is_active_factory_order(order):
            continue
        if getattr(order, "factory_no", None) is None:
            continue
        if order_flags.is_remote(order):
            items.append((no, order.factory_no))
    if items:
        lines = "\n".join(f"  · 畔色 {fno} 单 (订单 {no})" for no, fno in sorted(items, key=lambda x: x[1]))
        txt = (f"⚠️ 以下 {len(items)} 单已备注【延期/等通知】但仍挂着工厂制单号(已推工厂):\n{lines}\n"
               f"👉 若确认要暂缓, 请作废其工厂号(客户通知开始制作后再以新号重下); 若工厂已在做则忽略。")
        try:
            from app.services import notify_service
            notify_service.notify(
                db, txt, level="warn", title="畔色 ERP [延期单仍挂工厂号]",
                wechat_allowed=True,
            )
        except Exception:  # noqa: BLE001
            _logger.warning("延期单提醒发送失败", exc_info=True)
    return {"remind_remote": [no for no, _ in items]}


def renumber_order(db: Session, order_no: str, *, reason: str = "重开") -> dict:
    """把指定单作废旧工厂号、以【新工厂号】重推 (手动/激活老单重编号共用)。
    删旧下单图 + 清 factory_no + 通知工厂旧号作废, 再生成新图 + 推 → 顺排新号。返回新旧号。"""
    from app.services import feishu_client, settings_service
    order = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
    if not order:
        return {"error": "order_not_found", "order_no": order_no}
    old_no = getattr(order, "factory_no", None)
    for rec in db.execute(select(ImportedFile).where(ImportedFile.kind == "order_sheet")).scalars().all():
        if _order_no_from_name(rec.original_filename) == order_no:
            import_storage.delete_record(db, rec.id)
    order.factory_no = None
    db.flush()
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if chat_id and old_no:
        try:
            feishu_client.send_text(
                db, chat_id, f"⚠️ 订单 {order_no} 原【畔色 {old_no} 单】作废({reason}), 现以新号重推如下。")
        except Exception:  # noqa: BLE001
            _logger.warning("重编号作废提示发送失败 %s", order_no, exc_info=True)
    db.commit()
    generate_for_order(db, order)
    db.commit()
    push = push_pending_images(db, limit=5, include_baseline=False, only_order_nos={order_no})
    db.refresh(order)
    return {"order_no": order_no, "old_no": old_no, "new_no": getattr(order, "factory_no", None),
            "pushed": push.get("pushed")}


def push_daily(db: Session) -> dict:
    """每日 18:00: 补生成 + 把"还没推过图"的新下单图渲染成图片推飞书工厂群。

    历史基线 (部署前堆积) 不在此自动推, 避免刷屏; 需要时在「资料存档库」手动补推。
    """
    # 日报、口令回调、人工恢复和晚间补跑必须共用同一收口逻辑；否则同一批
    # 数据会出现不同的成功口径。quiet=False 仅控制日报附加通知，不改变状态机。
    result = reconcile_pending_delivery(db, limit=20, quiet=False)
    generated = result.get("generated") or {}
    n = int(generated.get("generated") or 0)
    released = set(result.get("released_after_address_fill") or [])
    sent = set(result.get("order_nos") or [])
    release_only = bool(sent) and sent == released and n == 0
    if release_only and result.get("_run_status") != "fail":
        # 地址补齐后的安全门释放只发对应图片；不追加日报或成功说明。
        result["pushed"] = True
        result["summary_notification_pushed"] = False
        result["summary_suppressed"] = "address_release_only"
        return result
    if result.get("_run_status") == "fail":
        text = "下单图自动推送未完成：" + str(result.get("_error") or "未返回原因")
    elif result["images_pushed"]:
        head = f"今日推送 {result['images_pushed']} 张工厂下单图到工厂群"
        if result["images_remaining"]:
            head += f" (仍有 {result['images_remaining']} 张待明确处理)"
        text = head + "。\n单号: " + "、".join(result["order_nos"][:10]) + ("…" if len(result["order_nos"]) > 10 else "")
    elif result.get("skipped_topup") or result.get("skipped_sample"):
        text = (f"今日没有需要推给工厂的正常下单图。已识别并跳过 "
                f"{len(result.get('skipped_topup') or [])} 笔小额补差/加价单、"
                f"{len(result.get('skipped_sample') or [])} 笔样品单。")
    elif n:
        text = f"今日新生成 {n} 张工厂下单图, 已存「资料存档库」(类型: 工厂下单图), 可下载/打印发工厂。"
    else:
        text = "今日没有需要生成/推送的下单图。"
    try:
        from app.services import notify_service
        ok, detail = notify_service.notify(
            db,
            text,
            level="warn" if result.get("_run_status") == "fail" else "info",
            title="畔色 ERP [下单图日报]",
            wechat_allowed=True,
        )
        result["summary_notification_channels"] = {"wechat_push": bool(ok)}
        result["summary_notification_detail"] = detail
        result["pushed"] = bool(ok)
        result["summary_notification_pushed"] = result["pushed"]
    except Exception:  # pragma: no cover
        result["pushed"] = False
        result["summary_notification_pushed"] = False
    return result


# ---------------- 退款作废图 (用户拍板 2026-06-11) ----------------
# 条件: 付款 + 已生成下单图 + 退款 → 原下单图画超大红叉生成「作废图」,
# 归档 kind=order_sheet_void (档案页单独分类), 删掉原下单图, 只生成一次并推送。

_VOID_OVERLAY = """
<div style="position:fixed;inset:0;pointer-events:none;z-index:99;">
  <div style="position:absolute;inset:0;background:
    linear-gradient(45deg, transparent 47.5%, rgba(220,38,38,.8) 47.5%, rgba(220,38,38,.8) 52.5%, transparent 52.5%),
    linear-gradient(-45deg, transparent 47.5%, rgba(220,38,38,.8) 47.5%, rgba(220,38,38,.8) 52.5%, transparent 52.5%);"></div>
  <div style="position:absolute;top:42%;left:50%;transform:translate(-50%,-50%) rotate(-16deg);
    font-size:56px;font-weight:900;color:#dc2626;background:rgba(255,255,255,.88);
    border:6px solid #dc2626;padding:10px 36px;border-radius:10px;white-space:nowrap;">已退款 · 作废</div>
</div>
"""


def _void_order_no_from_name(name: Optional[str]) -> Optional[str]:
    """作废图文件名反解订单号 — 旧: 作废图_X.html ; 新(2026-06-19): {退款日期}_X_已作废.jpg"""
    if not name:
        return None
    if name.startswith("作废图_") and name.endswith(".html"):
        return name[len("作废图_"):-len(".html")]
    if name.endswith("_已作废.jpg"):
        parts = name[:-len("_已作废.jpg")].split("_")
        if len(parts) == 2:        # {日期}_{订单号}
            return parts[1]
    return None


def _voided_order_nos(db: Session) -> set[str]:
    names = db.execute(
        select(ImportedFile.original_filename).where(ImportedFile.kind == "order_sheet_void")
    ).scalars().all()
    return {no for n in names if (no := _void_order_no_from_name(n))}


def generate_void_sheets(db: Session, *, limit: int = 100) -> dict:
    """退款订单 → 作废图。幂等: 每单只作废一次 (kind=order_sheet_void 已有则跳过)。"""
    sheet_nos = _archived_order_nos(db)
    voided_done = _voided_order_nos(db)
    orders = db.execute(
        select(Order).where(Order.order_date >= AUTO_SINCE)
    ).scalars().all()
    voided: list[str] = []
    for o in orders:
        if len(voided) >= limit:
            break
        if o.order_no not in sheet_nos or o.order_no in voided_done:
            continue
        if not _is_refunded(o) or not _is_paid(o):
            continue
        try:
            sheet = factory_sheet.build(db, o.id)
            d = o.refund_date or date.today()
            import_storage.archive(
                db, content=render_void_png(sheet),
                original_name=f"{d.isoformat()}_{o.order_no}_已作废.jpg",
                kind="order_sheet_void", source="auto",
                on_date=o.refund_date or date.today(),
                row_summary={
                    "note": f"退款作废 ¥{o.refund_amount or 0}",
                    "order_no": o.order_no,
                    "factory_no_at_render": getattr(o, "factory_no", None),
                    "factory_label_at_render": (
                        f"畔色{o.factory_no}单" if getattr(o, "factory_no", None) else None
                    ),
                    "render_width": 1684,
                },
            )
            # 删掉原下单图 (用户拍板) — 工厂只该看到作废版; 按订单号匹配 (兼容新旧命名)
            for fid, fname in db.execute(
                select(ImportedFile.id, ImportedFile.original_filename).where(
                    ImportedFile.kind == "order_sheet")
            ).all():
                if _order_no_from_name(fname) == o.order_no:
                    import_storage.delete_record(db, fid)
            voided.append(o.order_no)
        except Exception:  # pragma: no cover - 单张失败不阻断
            _logger.warning("作废图生成失败 %s", o.order_no, exc_info=True)
    db.commit()
    return {"voided": len(voided), "order_nos": voided}


def push_void_daily(db: Session) -> dict:
    """每日 10:00: 检查昨日导入刷出来的退款单, 生成作废图并推送 (无新作废不打扰)。"""
    result = generate_void_sheets(db)
    n = result["voided"]
    if n:
        text = (f"{n} 张工厂下单图因订单退款已作废 (原图已删, 作废图存「工具 → 导入档案 → 作废图」):\n"
                + "、".join(result["order_nos"][:10]) + ("…" if n > 10 else "")
                + "\n请通知工厂停止/确认这些单的生产。")
        title = "畔色 ERP [下单图作废提醒]"
        try:
            from app.services import notify_service
            ok, _ = notify_service.notify(
                db, text, level="warn", title=title, wechat_allowed=True,
            )
            result["pushed"] = bool(ok)
        except Exception:  # pragma: no cover
            result["pushed"] = False
        result["feishu_pushed"] = False
    else:
        result["pushed"] = False
    return result
