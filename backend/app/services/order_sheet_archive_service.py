# -*- coding: utf-8 -*-
"""工厂下单图 自动生成 + 归档 + 飞书日推 (用户方案 D+E)。

D: 规范化版式 (尺寸/整数数量/木作命名/单件×N/发货=下单+25天/备注完整) — 数据在
   factory_sheet.build, 这里负责渲染成独立可打印 HTML。
E: 订单 (order_date >= 2026-06-06) 自动生成下单图 HTML → 存导入档案 (kind=order_sheet),
   每天定时把当日生成情况推飞书群。
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from html import escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.services import factory_sheet, import_storage

_logger = logging.getLogger("panse.order_sheet")

AUTO_SINCE = date(2026, 6, 6)   # 用户指定: 从这天的订单开始自动生成

# 用户拍板 (2026-06-11): 下单图生成条件必须是已付款订单
_PAID_STATUSES = {"paid", "production", "shipped", "signed", "aftersales"}


def _is_paid(o: Order) -> bool:
    return (o.paid_amount or 0) > 0 or (o.status or "") in _PAID_STATUSES


def _is_refunded(o: Order) -> bool:
    """是否退款作废: 状态关闭/退货, 或退款状态含退款/退货, 或【全额(≥90%)退款】。
    小额差价/运费退款(如 ¥5)不算作废 (用户拍板 2026-06-12: 避免误把差价单作废/拦推送)。"""
    rs = o.refund_status or ""
    if (o.status or "") == "cancelled" or any(k in rs for k in ("退款", "退货", "关闭")):
        return True
    ra = Decimal(str(o.refund_amount or 0))
    pa = Decimal(str(o.paid_amount or 0))
    return ra > 0 and pa > 0 and ra >= pa * Decimal("0.9")


def _int_qty(v) -> str:
    """2.0000 → 2; 2.5 保留小数。用户要求数量别带一串零。"""
    d = Decimal(str(v or 0))
    if d == d.to_integral_value():
        return str(int(d))
    return str(d.normalize())


def _gallery_data_uri(rel, max_w: int = 440):
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
        im.save(buf, format="JPEG", quality=78)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - 图库读取/解码失败不阻断
        return None


def render_html(sheet: "factory_sheet.FactorySheet") -> str:
    """下单图 HTML — 方向二「图文卡片」版式 (用户拍板 2026-06-12)。

    品牌绿横幅 + 左图右信息(规格尺寸大字); 时间只写下单/发货日期 (不标"+25天")。
    独立文件, 打印即 A4 单页。
    """
    e = escape
    mat_rows = "".join(
        f"<tr><td><code>{e(m.material_code)}</code></td>"
        f"<td>{e(m.material_name or m.material_code)}</td>"
        f"<td class='c'>{_int_qty(m.qty_per_product)}</td>"
        f"<td class='c'>× {sheet.qty} = <b>{_int_qty(m.total_qty)}</b></td>"
        f"<td class='c'>{e(m.unit or '件')}</td>"
        f"<td>{e(m.spec or m.note or '')}</td></tr>"
        for m in sheet.materials
    )
    rows = []

    def _row(k: str, v: str, color: str = "") -> None:
        style = f" style='color:{color}'" if color else ""
        rows.append(f"<div class='row'><div class='k'>{e(k)}</div><div{style}>{v}</div></div>")

    _row("产品", f"{e(sheet.product_name or '-')} <span style='color:#9ca3af'>({e(sheet.product_code or '-')})</span>")
    addr = "，".join(x for x in (sheet.customer_name, sheet.customer_phone, sheet.customer_address) if x) or "-"
    _row("收件", e(addr))
    # 用户拍板 (2026-06-12): 只写下单/发货日期, 不标注"+25天"
    _row("时间", f"下单 {sheet.order_date or '-'} → 发货 <b>{sheet.ship_date or '-'}</b>")
    # 图4 (2026-06-12): 下单图先主材后辅材
    if getattr(sheet, "main_material", None):
        _row("主材", e(sheet.main_material), color="#222")
    if getattr(sheet, "aux_material", None):
        _row("辅材", e(sheet.aux_material), color="#555")
    if sheet.material_desc:
        _row("说明", e(sheet.material_desc), color="#888")
    if sheet.remark:
        _row("备注", e(sheet.remark), color="#a8743a")
    if sheet.production_note:
        _row("生产备注", e(sheet.production_note), color="#a8743a")
    # 产品主图: 优先真实 image_url(淘宝CDN http链接); 缺则用图库主图(内嵌base64)。
    # SKU尺寸图: 一律内嵌图库 SKU 图 (用户拍板 2026-06-18: 下单图都要连着 SKU 图一起发)。
    _main = sheet.image_url if (sheet.image_url and str(sheet.image_url).startswith("http")) \
        else _gallery_data_uri(getattr(sheet, "gallery_main_image", None))
    _sku = _gallery_data_uri(getattr(sheet, "sku_image", None))
    _parts = [f"<img class='pimg' src='{e(_main)}' alt='产品图'/>" if _main
              else "<div class='noimg'>无产品图</div>"]
    if _sku:
        _parts.append("<div class='skucap'>SKU 尺寸图</div>"
                      f"<img class='skimg' src='{e(_sku)}' alt='SKU尺寸图'/>")
    img = "".join(_parts)
    custom_tag = ""
    if sheet.is_custom_variant:
        dims = " ".join(f"{k}={v}" for k, v in (sheet.dimension_changes or {}).items())
        custom_tag = f"<span class='tag'>尺寸定制 {e(dims)}</span>"
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"/>
<title>{e(sheet.sheet_title)}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 0; color: #1f2937;
          background:#f3f4f6; }}
  .card {{ max-width: 760px; margin: 18px auto; background:#fff; border-radius:14px;
           overflow:hidden; box-shadow:0 2px 14px rgba(0,0,0,.12); }}
  .banner {{ background:linear-gradient(95deg,#1a7a3c,#2f9b58); color:#fff; padding:14px 20px;
             display:flex; justify-content:space-between; align-items:center; }}
  .banner .t {{ font-size:18px; font-weight:700; }}
  .banner .no {{ background:rgba(255,255,255,.18); border-radius:99px; padding:4px 14px; font-size:13px; }}
  .main {{ display:flex; gap:18px; padding:18px 20px; }}
  .pics {{ width:230px; flex-shrink:0; }}
  .pimg {{ width:230px; max-height:260px; object-fit:contain; border:1px solid #f0f0f0; border-radius:8px; }}
  .noimg {{ width:230px; height:180px; border:1px dashed #ccc; border-radius:8px; display:flex;
            align-items:center; justify-content:center; color:#bbb; }}
  .skucap {{ font-size:12px; color:#888; margin:10px 0 2px; }}
  .skimg {{ width:230px; max-height:240px; object-fit:contain; border:1px solid #f0f0f0; border-radius:8px; }}
  .kv {{ flex:1; min-width:0; }}
  .lbl {{ color:#888; font-size:13px; }}
  .size {{ font-size:22px; font-weight:800; color:#1a7a3c; line-height:1.35; margin:2px 0 4px; }}
  .qty {{ font-size:14px; font-weight:600; color:#5b8c6e; margin-bottom:8px; }}
  .row {{ display:flex; padding:7px 0; border-bottom:1px dashed #e5e7eb; font-size:14px; }}
  .row .k {{ width:64px; color:#999; flex-shrink:0; }}
  .tag {{ display:inline-block; font-size:12px; padding:2px 10px; border-radius:99px;
          background:#fff4e0; color:#b76e00; margin-right:8px; }}
  .ono {{ color:#bbb; font-size:12px; font-family:monospace; }}
  .mat {{ margin:0 20px 18px; }}
  .mat h3 {{ margin:4px 0 8px; font-size:14px; }}
  .mat h3 span {{ font-weight:normal; color:#999; font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; color:#888; font-weight:600; padding:6px 8px; border-bottom:2px solid #1a7a3c; }}
  td {{ padding:6px 8px; border-bottom:1px solid #f0f0f0; }}
  tr:nth-child(even) td {{ background:#fafcfa; }}
  td.c {{ text-align:center; }}
  .foot {{ padding:0 20px 16px; font-size:11px; color:#9ca3af; }}
  @media print {{ body {{ background:#fff; }} .card {{ box-shadow:none; margin:0; max-width:none; }} }}
</style></head><body>
<div class="card">
  <div class="banner">
    <div class="t">畔色木作 · 工厂下单图</div>
    <div class="no">{e(sheet.sheet_title)}</div>
  </div>
  <div class="main">
    <div class="pics">{img}</div>
    <div class="kv">
      <div class="lbl">规格尺寸</div>
      <div class="size">{e(sheet.size_info or sheet.sku or '-')}</div>
      <div class="qty">数量 {sheet.qty} 件</div>
      {''.join(rows)}
      <div style="margin-top:10px">{custom_tag}<span class="ono">{e(sheet.order_no)}</span></div>
    </div>
  </div>
  <div class="mat">
    <h3>物料明细 <span>(供配件采购, 工厂备料参考)</span></h3>
    <table>
      <tr><th style="width:90px">物料编码</th><th>物料名称</th><th style="width:60px">单件</th><th style="width:110px">总量</th><th style="width:50px">单位</th><th>备注</th></tr>
      {mat_rows or "<tr><td colspan='6' style='color:#9ca3af'>无 BOM 物料</td></tr>"}
    </table>
  </div>
  <div class="foot">畔色孚格 ERP 自动生成 · 单号 {e(sheet.order_no)}</div>
</div>
</body></html>"""


def _archived_order_nos(db: Session) -> set[str]:
    """已归档下单图的订单号集合 (从文件名 下单图_{order_no}.html 反解)。"""
    names = db.execute(
        select(ImportedFile.original_filename).where(ImportedFile.kind == "order_sheet")
    ).scalars().all()
    out = set()
    for n in names:
        if n and n.startswith("下单图_") and n.endswith(".html"):
            out.add(n[len("下单图_"):-len(".html")])
    return out


def generate_for_order(db: Session, order: Order, *, source: str = "auto") -> Optional[dict]:
    """生成一张下单图 HTML 并归档。返回 {order_no, file_id, duplicate} 或 None (失败)。"""
    try:
        sheet = factory_sheet.build(db, order.id)
        html = render_html(sheet)
        res = import_storage.archive(
            db, content=html.encode("utf-8"),
            original_name=f"下单图_{order.order_no}.html",
            kind="order_sheet", source=source,
            on_date=order.order_date,
        )
        return {"order_no": order.order_no, "file_id": res.file.id,
                "duplicate": res.is_duplicate}
    except Exception:  # pragma: no cover - 单张失败不阻断批量
        _logger.warning("下单图生成失败 %s", order.order_no, exc_info=True)
        return None


def generate_pending(db: Session, *, limit: int = 200) -> dict:
    """给 2026-06-06 起、还没归档过下单图的订单批量补生成 (导入兜底 + 日常增量)。"""
    done = _archived_order_nos(db)
    orders = db.execute(
        select(Order).where(
            Order.order_date >= AUTO_SINCE,
            Order.is_refill == False,            # noqa: E712 - 补单不发工厂
        ).order_by(Order.id.desc()).limit(500)
    ).scalars().all()
    generated = []
    for o in orders:
        if o.order_no in done or len(generated) >= limit:
            continue
        if (o.status or "") == "cancelled":
            continue
        if not _is_paid(o):   # 用户拍板: 必须已付款才生成下单图
            continue
        if _is_refunded(o):   # 已退款/退货/关闭 → 不生成也不推送 (改走作废图流程)
            continue
        r = generate_for_order(db, o)
        if r and not r["duplicate"]:
            generated.append(r["order_no"])
    db.commit()
    return {"generated": len(generated), "order_nos": generated[:50]}


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
    """下单图 → PNG 字节 (发飞书图片用)。"""
    return _html_to_png(render_html(sheet))


def _order_no_from_name(name: Optional[str]) -> Optional[str]:
    """从归档文件名 下单图_{order_no}.html 反解订单号。"""
    if name and name.startswith("下单图_") and name.endswith(".html"):
        return name[len("下单图_"):-len(".html")]
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


def count_pending_push(db: Session, *, include_baseline: bool = True) -> int:
    """待推飞书的下单图张数 (前端按钮角标用)。"""
    return len(_pending_push_records(db, include_baseline=include_baseline))


def push_pending_images(db: Session, *, limit: int = 20, include_baseline: bool = False) -> dict:
    """把【还没推过图】的下单图渲染成图片推飞书工厂群, 推成功就在该归档记录标记 pushed=True。

    与"生成 HTML"彻底解耦 —— 不论 HTML 是 18:00 日推、每小时补生成、还是手动生成的,
    只要这条归档还没推过图就在这里补推一次。修复历史 bug: 旧逻辑只推「本次新生成」的单号,
    一旦被每小时补生成任务抢先生成, 该单就永远不再被推 (归档里全是 HTML、飞书一张图没有)。

    返回 {pushed, failed, remaining, order_nos, reason?}。单张失败不抛 (不阻断整批)。
    """
    import os
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return {"pushed": 0, "failed": 0, "remaining": 0, "order_nos": [], "reason": "notify_disabled"}
    from app.services import feishu_client, settings_service
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if not chat_id:
        return {"pushed": 0, "failed": 0,
                "remaining": count_pending_push(db, include_baseline=include_baseline),
                "order_nos": [], "reason": "no_chat_id"}
    pushed = failed = 0
    sent_nos: list[str] = []
    for rec in _pending_push_records(db, include_baseline=include_baseline)[:limit]:
        no = _order_no_from_name(rec.original_filename)
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order:
            continue
        if (order.status or "") == "cancelled" or _is_refunded(order):
            continue   # 退款/取消单不推工厂 (走作废图流程, 不在此补推)
        try:
            png = render_png(factory_sheet.build(db, order.id))
            key = feishu_client.upload_image(db, png)
            cap = f"工厂下单图 · {no}" + (f" · {order.product_name[:20]}" if order.product_name else "")
            feishu_client.send_text(db, chat_id, cap)
            feishu_client.send_image(db, chat_id, key)
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True}
            db.commit()
            pushed += 1
            sent_nos.append(no)
        except Exception:  # noqa: BLE001 - 单张失败不阻断整批
            db.rollback()
            failed += 1
            _logger.warning("下单图推飞书失败 %s", no, exc_info=True)
    return {"pushed": pushed, "failed": failed,
            "remaining": count_pending_push(db, include_baseline=include_baseline),
            "order_nos": sent_nos}


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


def push_daily(db: Session) -> dict:
    """每日 18:00: 补生成 + 把"还没推过图"的新下单图渲染成图片推飞书工厂群。

    历史基线 (部署前堆积) 不在此自动推, 避免刷屏; 需要时在「资料存档库」手动补推。
    """
    result = generate_pending(db)
    n = result["generated"]
    push = push_pending_images(db, limit=20, include_baseline=False)
    result["images_pushed"] = push["pushed"]
    result["images_remaining"] = push["remaining"]
    if push["pushed"]:
        head = f"今日推送 {push['pushed']} 张工厂下单图到工厂群"
        if push["remaining"]:
            head += f" (还有 {push['remaining']} 张排队, 明日续推)"
        text = head + "。\n单号: " + "、".join(push["order_nos"][:10]) + ("…" if len(push["order_nos"]) > 10 else "")
    elif n:
        text = f"今日新生成 {n} 张工厂下单图, 已存「资料存档库」(类型: 工厂下单图), 可下载/打印发工厂。"
    else:
        text = "今日没有需要生成/推送的下单图。"
    try:
        from app.services import notify_service
        ok, _ = notify_service.notify(db, text, level="info", title="畔色 ERP [下单图日报]")
        result["pushed"] = bool(ok)
    except Exception:  # pragma: no cover
        result["pushed"] = False
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


def _voided_order_nos(db: Session) -> set[str]:
    names = db.execute(
        select(ImportedFile.original_filename).where(ImportedFile.kind == "order_sheet_void")
    ).scalars().all()
    out = set()
    for n in names:
        if n and n.startswith("作废图_") and n.endswith(".html"):
            out.add(n[len("作废图_"):-len(".html")])
    return out


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
            html = render_html(sheet).replace("</body>", _VOID_OVERLAY + "</body>")
            import_storage.archive(
                db, content=html.encode("utf-8"),
                original_name=f"作废图_{o.order_no}.html",
                kind="order_sheet_void", source="auto",
                on_date=o.refund_date or date.today(),
                row_summary={"note": f"退款作废 ¥{o.refund_amount or 0}"},
            )
            # 删掉原下单图 (用户拍板) — 工厂只该看到作废版
            old_ids = db.execute(
                select(ImportedFile.id).where(
                    ImportedFile.kind == "order_sheet",
                    ImportedFile.original_filename == f"下单图_{o.order_no}.html",
                )
            ).scalars().all()
            for fid in old_ids:
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
        try:
            from app.services import notify_service
            ok, _ = notify_service.notify(db, text, level="warning",
                                          title="畔色 ERP [下单图作废提醒]")
            result["pushed"] = bool(ok)
        except Exception:  # pragma: no cover
            result["pushed"] = False
    else:
        result["pushed"] = False
    return result
