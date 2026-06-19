# -*- coding: utf-8 -*-
"""工厂下单图 自动生成 + 归档 + 飞书日推 (用户方案 D+E)。

D: 规范化版式 (尺寸/整数数量/木作命名/单件×N/发货=下单+25天/备注完整) — 数据在
   factory_sheet.build, 这里负责渲染成独立可打印 HTML。
E: 订单 (order_date >= 2026-06-06) 自动生成下单图 HTML → 存导入档案 (kind=order_sheet),
   每天定时把当日生成情况推飞书群。
"""
from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal
from html import escape
from typing import Optional

from sqlalchemy import func, select
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


def render_html(sheet: "factory_sheet.FactorySheet") -> str:
    """下单图 HTML — 藏青蓝 A4 横版工厂生产单 (用户拍板 2026-06-19, 方案C·藏青蓝)。

    A4 横版(渲染 1684×1190) + 四周安全留白; 藏青蓝顶栏; 图左规格右分区;
    成品尺寸/制单/发货 红字大字; 辅料只写 BOM(去木作); 加急红敲印;
    无编号标"未能匹配工厂订单号"; 无尺寸标红"未对应尺寸"。
    """
    e = escape
    A = "#1f3a5f"  # 藏青蓝 主色
    if sheet.factory_no:
        no_html = f"<div class='no'>畔色 {sheet.factory_no} 单</div>"
    else:
        no_html = "<div class='no' style='color:#fda4a4'>未能匹配工厂订单号</div>"
    # 成品尺寸 (无 → 红字"未对应尺寸")
    _szt = _fmt_size(sheet.size_info)
    if _szt:
        _n = len(_szt)
        _szfs = 52 if _n <= 16 else (44 if _n <= 28 else 32)
        _nw = "white-space:nowrap;" if _n <= 28 else ""   # 长尺寸(箱体床等)允许换行不裁切
        size_html = f"<div class='sz' style='font-size:{_szfs}px;{_nw}'>{e(_szt)}</div>"
    else:
        size_html = "<div class='sz'>未对应尺寸</div>"
    # 材质 = 主材 · 辅材 (去掉工艺/说明)
    _mat = [x for x in (getattr(sheet, "main_material", None), getattr(sheet, "aux_material", None)) if x]
    mat_txt = e(" · ".join(_mat)) if _mat else "—"
    # 辅料 BOM: 去木作 + 简化为 "名称 ×数量 单位" (给木作厂看, 不写木作本身)
    bom = []
    for m in sheet.materials:
        code = (m.material_code or "").upper()
        nm = m.material_name or m.material_code or ""
        if code.startswith("WD") or "木作" in nm:
            continue
        bom.append(f"{e(nm)}　×{_int_qty(m.total_qty)} {e(m.unit or '件')}")
    bom_txt = "<br>".join(bom) if bom else "—"
    # 图纸: 优先 SKU 尺寸图(高清内嵌 900px), 回退主图
    _sku = _gallery_data_uri(getattr(sheet, "sku_image", None))
    _main = (sheet.image_url if (sheet.image_url and str(sheet.image_url).startswith("http"))
             else _gallery_data_uri(getattr(sheet, "gallery_main_image", None)))
    _img = _sku or _main
    pic_html = f"<img src='{e(_img)}'>" if _img else "<div class='noimg'>无产品图纸</div>"
    stamp_html = "<div class='stamp'>加急</div>" if sheet.urgent else ""
    made, ship, odate = sheet.made_date or "-", sheet.ship_date or "-", sheet.order_date or "-"
    # 收货: 全空(淘宝解密额度不足/未抓到) → 红字提示, 但编号照常 (用户拍板 2026-06-20)
    if sheet.customer_name or sheet.customer_phone or sheet.customer_address:
        ship_html = (f"{e(sheet.customer_name or '')}　{e(sheet.customer_phone or '')}"
                     f"<br>{e(sheet.customer_address or '—')}")
    else:
        ship_html = "<span style='color:#dc2626;font-weight:800'>⚠ 没有抓取到收货地址（淘宝解密额度不足，待提升后重拉）</span>"
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"/>
<title>{e(sheet.sheet_title)}</title><style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:"Microsoft YaHei","PingFang SC",sans-serif;}}
body{{background:#fff;}}
.page{{width:1684px;height:1190px;background:#fff;padding:22px;}}
.card{{position:relative;width:1640px;height:1146px;background:#fff;border:3px solid {A};}}
table{{border-collapse:collapse;}}
.hd{{width:100%;height:120px;background:{A};color:#fff;}}
.hd .co{{font-size:36px;font-weight:900;padding-left:28px;}}
.hd .co small{{display:block;font-size:17px;opacity:.82;letter-spacing:2px;margin-top:5px;}}
.hd .r{{text-align:right;padding-right:28px;}}
.hd .no{{font-size:50px;font-weight:900;}}
.hd .ono{{font-size:22px;opacity:.92;margin-top:4px;font-family:monospace;letter-spacing:1px;}}
.mid{{width:100%;height:740px;}}
.mid .pic{{width:660px;border-right:3px solid {A};text-align:center;vertical-align:middle;}}
.mid .pic img{{max-width:640px;max-height:700px;}}
.mid .noimg{{color:#bbb;font-size:30px;}}
.zwrap{{vertical-align:top;}}
.z{{border-bottom:2px solid {A};}}
.zt{{background:#eef2f7;color:{A};font-size:22px;font-weight:800;padding:10px 24px;letter-spacing:1px;}}
.zb{{padding:16px 26px;font-size:31px;line-height:1.35;word-break:break-all;}}
.sz{{font-size:58px;font-weight:900;color:#dc2626;letter-spacing:1px;line-height:1.15;}}
.dt{{font-size:38px;font-weight:900;color:#dc2626;}}
.ft{{width:100%;border-top:3px solid {A};}}
.ft td{{padding:18px 26px;vertical-align:top;font-size:30px;}}
.ft .l{{font-size:20px;color:{A};font-weight:800;letter-spacing:1px;}}
.stamp{{position:absolute;top:150px;right:800px;border:5px double #dc2626;color:#dc2626;
        font-size:46px;font-weight:900;padding:3px 20px;transform:rotate(-13deg);border-radius:10px;
        letter-spacing:8px;z-index:9;background:rgba(255,255,255,.4);}}
@media print{{.page{{padding:14px;}}}}
</style></head><body><div class="page"><div class="card">
<table class="hd" style="width:100%"><tr>
  <td class="co">畔色木作<small>工厂生产单 · PRODUCTION ORDER</small></td>
  <td class="r">{no_html}<div class="ono">订单编号：{e(sheet.order_no)}</div></td>
</tr></table>
<table class="mid"><tr>
  <td class="pic">{pic_html}</td>
  <td class="zwrap">
    <div class="z"><div class="zt">产品 / 规格　PRODUCT</div><div class="zb">{e(sheet.product_name or '-')}　<span style="font-family:monospace;font-size:23px;color:#555">{e(sheet.product_code or '-')}</span><br>{mat_txt}</div></div>
    <div class="z"><div class="zt">成品尺寸　FINISHED SIZE (mm)</div><div class="zb">{size_html}<div class="dt">制单 {made}</div><div class="dt" style="margin-top:6px">发货 {ship}</div></div></div>
    <div class="z" style="border-bottom:none"><div class="zt">辅料清单　BOM</div><div class="zb">{bom_txt}</div></div>
  </td></tr></table>
<table class="ft" style="width:100%"><tr>
  <td><div class="l">收货信息 SHIP TO</div>{ship_html}</td>
  <td style="text-align:right;width:360px;border-left:2px solid #ccc"><div class="l">下单日期</div><span>{odate}</span></td>
</tr></table>
{stamp_html}
</div></div></body></html>"""


def _archived_order_nos(db: Session) -> set[str]:
    """已归档下单图的订单号集合 (兼容 下单图_X.html / {date}_X.jpg 两种命名)。"""
    names = db.execute(
        select(ImportedFile.original_filename).where(ImportedFile.kind == "order_sheet")
    ).scalars().all()
    return {no for n in names if (no := _order_no_from_name(n))}


def generate_for_order(db: Session, order: Order, *, source: str = "auto") -> Optional[dict]:
    """生成一张下单图 JPEG 并归档 (命名 {下单日期}_{订单号}.jpg)。返回 {order_no, file_id, duplicate} 或 None。

    用户拍板 2026-06-19: 存档直接存成图片 (非 HTML), 日期+订单号命名, 打开即看、可直接转发工厂。
    """
    # 铁律 (用户拍板 2026-06-20): 未付款单【永不】生成工厂下单图 —— 付了定金(paid_amount>0)
    # 但 status=pending_payment 的也算未付款, 大概率会取消, 绝不能给工厂下单/编号。
    if (order.status or "") == "pending_payment":
        return None
    try:
        sheet = factory_sheet.build(db, order.id)
        jpg = render_png(sheet)   # render_png 现已输出 JPEG 字节
        d = order.order_date or date.today()
        res = import_storage.archive(
            db, content=jpg,
            original_name=f"{d.isoformat()}_{order.order_no}.jpg",
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
        if (o.status or "") in ("cancelled", "pending_payment"):
            continue   # 取消 + 未付款(含付定金的待付款)永不生成 (用户拍板 2026-06-20 铁律)
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
    """下单图 → PNG 字节 (发飞书图片用)。A4 横版工单宽 1684px (方案C·藏青蓝)。"""
    return _html_to_png(render_html(sheet), width=1684)


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


def count_pending_push(db: Session, *, include_baseline: bool = True) -> int:
    """待推飞书的下单图张数 (前端按钮角标用)。"""
    return len(_pending_push_records(db, include_baseline=include_baseline))


# 工厂制单编号: 历史(<6/19)靠 ZIP 回填; 6/19 起新单推送时按订单顺序自动顺排 (用户拍板 2026-06-19)
_AUTO_NUMBER_SINCE = date(2026, 6, 19)


def _next_factory_no(db: Session) -> int:
    """下一个工厂制单编号 = 现有最大 + 1 (新单按订单顺序往后排)。"""
    mx = db.execute(select(func.max(Order.factory_no))).scalar()
    return (mx or 241) + 1


def _send_no_addr_notice(db: Session, chat_id: str, missing: list) -> None:
    """无收货地址的单 → 飞书提示哪些单缺地址 + 提醒去淘宝后台提升解密额度 (用户拍板 2026-06-20)。

    missing: [(order_no, factory_no), ...]; 这些单已做制单图(带编号)推送, 但收货为空。
    """
    if not missing:
        return
    from app.services import feishu_client
    lines = [f"  · {('畔色 '+str(fno)+' 单') if fno else '未编号'}　订单号 {no}" for no, fno in missing]
    txt = (f"⚠️ 下列 {len(missing)} 单【没有抓取到收货地址】(已做制单图+编号一并推送, 但收货栏为空):\n"
           + "\n".join(lines)
           + "\n👉 大概率是淘宝后台每日可解密收货信息额度不足。请去后台【提升解密额度】, 然后让我对这些单重新拉取, 收货就能补上。")
    try:
        feishu_client.send_text(db, chat_id, txt)
    except Exception:  # noqa: BLE001
        _logger.warning("无收货地址提示发送失败", exc_info=True)


def _send_sheets_zip(db: Session, chat_id: str, items: list) -> None:
    """把本批下单图打成 ZIP 发飞书 (用户拍板 2026-06-19: 每次推送末尾附 ZIP, 含所有订单图)。

    items: [(order, jpg_bytes), ...]; 文件名 畔色{编号}单_{订单号}.jpg / 未匹配_{订单号}.jpg。
    """
    if not items:
        return
    import io
    import zipfile
    from datetime import date as _date

    from app.services import feishu_client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for order, png in items:
            fno = getattr(order, "factory_no", None)
            prefix = f"畔色{fno}单_" if fno else "未匹配_"
            zf.writestr(f"{prefix}{order.order_no}.jpg", png)
    try:
        fk = feishu_client.upload_file(db, buf.getvalue(), f"工厂下单图_{_date.today().isoformat()}.zip")
        feishu_client.send_text(db, chat_id, f"以上 {len(items)} 张工厂下单图打包(ZIP)如下:")
        feishu_client.send_file(db, chat_id, fk)
    except Exception:  # noqa: BLE001 - ZIP 发送失败不阻断主推送
        _logger.warning("工厂下单图 ZIP 发送失败", exc_info=True)


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
    _zip_items: list = []
    _missing_addr: list = []
    for rec in _pending_push_records(db, include_baseline=include_baseline)[:limit]:
        no = _order_no_from_name(rec.original_filename)
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order:
            continue
        if (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue   # 取消/退款/待付款 不推工厂 (待付款大概率会取消, 用户拍板 2026-06-20)
        # 6/19 起新单按订单顺序自动顺排工厂编号 (历史靠 ZIP 回填, 不在此动)
        if (getattr(order, "factory_no", None) is None and order.order_date
                and order.order_date >= _AUTO_NUMBER_SINCE):
            order.factory_no = _next_factory_no(db)
            db.flush()
        try:
            png = render_png(factory_sheet.build(db, order.id))
            key = feishu_client.upload_image(db, png)
            _fno = (f"畔色 {order.factory_no} 单" if getattr(order, "factory_no", None)
                    else "未能匹配工厂订单号")
            cap = f"{_fno} · {no}" + (f" · {order.product_name[:20]}" if order.product_name else "")
            feishu_client.send_text(db, chat_id, cap)
            feishu_client.send_image(db, chat_id, key)
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True}
            db.commit()
            pushed += 1
            sent_nos.append(no)
            _zip_items.append((order, png))
            if not (order.customer_name or order.customer_phone or order.customer_address):
                _missing_addr.append((no, order.factory_no))
        except Exception:  # noqa: BLE001 - 单张失败不阻断整批
            db.rollback()
            failed += 1
            _logger.warning("下单图推飞书失败 %s", no, exc_info=True)
    _send_sheets_zip(db, chat_id, _zip_items)   # 末尾附 ZIP (用户拍板 2026-06-19)
    _send_no_addr_notice(db, chat_id, _missing_addr)   # 无收货地址提示+提醒提额度 (用户拍板 2026-06-20)
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
            html = render_html(sheet).replace("</body>", _VOID_OVERLAY + "</body>")
            d = o.refund_date or date.today()
            import_storage.archive(
                db, content=_html_to_png(html),   # 作废图也存成 JPEG (红叉随 HTML 一起渲染进图)
                original_name=f"{d.isoformat()}_{o.order_no}_已作废.jpg",
                kind="order_sheet_void", source="auto",
                on_date=o.refund_date or date.today(),
                row_summary={"note": f"退款作废 ¥{o.refund_amount or 0}"},
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
