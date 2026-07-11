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

from sqlalchemy import func, or_, select
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
    # 头部右: 畔色 N 单 + 制单日期 + 订单编号
    if sheet.factory_no:
        no_html = f"<div class='no'>畔色 {sheet.factory_no} 单</div>"
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
    <div class="l" style="margin-top:14px">发货日期</div><div class="shipdt">{e(_cn_date(ship))}</div>
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
        from app.services import order_flags
        if order_flags.is_remote(o):
            continue   # 远期挂起单: 不生成下单图, 等激活(备注开始制作)后再以新号推 (用户 2026-07-08)
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
    if (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
        return False
    if getattr(order, "factory_no", None) is None:
        od = getattr(order, "order_date", None)
        if not (od and od >= _AUTO_NUMBER_SINCE):   # 无工厂编号的老单 → 推出去是噪音, 不算待推
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
    mx = db.execute(select(func.max(Order.factory_no))).scalar()
    return (mx or 241) + 1


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
    # 候选 = 手动远期 或 有任一备注(可能含远期词/日期式发货); 精确判定交给 is_factory_remote
    cond = or_(Order.is_remote_ship.is_(True), *[c.is_not(None) for c in cols])
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
           + "\n👉 补齐两步(缺一不可):"
           + "\n  ① 淘宝后台【提升每日收货信息解密额度】—— 额度不够时, 超额的单收货地址会被星号脱敏, 系统不收, 故为空;"
           + "\n  ② 在订单页点「更新拉取订单」重新拉取, 然后把淘宝发的『发货密码 xxxx』转发到这里"
           + " —— 我会自动解密发货报表、补上收货地址并重推这些单的下单图。"
           + "\n(发货报表是淘宝固定加密的, 每天新导出都要重发一次密码; 密码 60 分钟内有效。)")
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
    pushed_nos: set[str] = set()
    for r in db.execute(select(ImportedFile).where(ImportedFile.kind == "order_sheet")).scalars().all():
        if (r.row_summary or {}).get("pushed"):
            no = _order_no_from_name(r.original_filename)
            if no:
                pushed_nos.add(no)
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
    _zip_items: list = []
    _missing_addr: list = []
    records = _pending_push_records(db, include_baseline=include_baseline)
    if only_order_nos is not None:
        # 定向重推 (解密补地址后): 只推指定单号, 不误扫其它待推/历史基线
        records = [r for r in records if _order_no_from_name(r.original_filename) in only_order_nos]
    for rec in records[:limit]:
        no = _order_no_from_name(rec.original_filename)
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order:
            continue
        if (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue   # 取消/退款/待付款 不推工厂 (待付款大概率会取消, 用户拍板 2026-06-20)
        from app.services import order_flags
        if order_flags.is_remote(order):
            continue   # 远期挂起单 不推工厂, 等激活后以新号推 (用户 2026-07-08; 挂起单本就没生成图, 此处双保险)
        if _is_sample_order(order):
            # 样块/样品单永不推工厂下单图 (用户 2026-07-04); 标记已处理, 清出待推队列 + 不占工厂编号。
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True, "skipped_sample": True}
            db.commit()
            continue
        _topup, _topup_reason = _is_parts_topup(db, order)
        if _topup:
            # 定制补差/加价单永不推工厂 (用户 2026-07-12): 标记已处理清出队列 + 不占工厂编号, 记原因备查。
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True,
                               "skipped_topup": True, "topup_reason": _topup_reason}
            db.commit()
            continue
        # 6/19 起新单按订单顺序自动顺排工厂编号 (历史靠 ZIP 回填, 不在此动)。
        # 已激活的老远期单(备注开始制作)也顺排新号 —— 它们早下但现在要做, 不该被日期线卡住 (用户 2026-07-08)。
        if (getattr(order, "factory_no", None) is None and order.order_date
                and (order.order_date >= _AUTO_NUMBER_SINCE or order_flags.is_activated(order))):
            order.factory_no = _next_factory_no(db)
            db.flush()
        # 自动推送(catchup/18:00, include_baseline=False)【绝不】推没有工厂编号的老单(<6/19 且未 ZIP 回填):
        # 它们渲染出来是红字"未能匹配工厂订单号", 推给工厂只是噪音 (用户 2026-06-26: 飞书一直跳这些)。
        # 新单上面已自动顺排到号; 没号的只剩历史老单, 留给「资料存档库」手动按钮(include_baseline=True)人工补号后再推。
        if getattr(order, "factory_no", None) is None and not include_baseline:
            continue
        try:
            png = render_png(factory_sheet.build(db, order.id))
            key = feishu_client.upload_image(db, png)
            _fno = (f"畔色 {order.factory_no} 单" if getattr(order, "factory_no", None)
                    else "未能匹配工厂订单号")
            cap = f"{_fno} · {no}" + (f" · {order.product_name[:20]}" if order.product_name else "")
            feishu_client.send_text(db, chat_id, cap)
            feishu_client.send_image(db, chat_id, key)
            addr_ok = _addr_ok_for_factory(order)
            # pushed_addr_ok 记录"这张图推送时收货地址是否完整": False=缺地址被推,
            # 待飞书口令解密补上地址后由 repush_after_address_fill 自动重推 (用户 2026-06-30)。
            rec.row_summary = {**(rec.row_summary or {}), "pushed": True, "pushed_addr_ok": addr_ok,
                               "activated": order_flags.is_activated(order)}   # 激活态推的图不再被 repush_activated 重推
            db.commit()
            pushed += 1
            sent_nos.append(no)
            _zip_items.append((order, png))
            if not addr_ok:
                _missing_addr.append((no, order.factory_no))
        except Exception:  # noqa: BLE001 - 单张失败不阻断整批
            db.rollback()
            failed += 1
            _logger.warning("下单图推飞书失败 %s", no, exc_info=True)
    if not quiet:
        _send_sheets_zip(db, chat_id, _zip_items)   # 末尾附 ZIP (用户拍板 2026-06-19)
        _send_no_addr_notice(db, chat_id, _missing_addr)   # 无收货地址提示+提醒提额度 (用户拍板 2026-06-20)
    return {"pushed": pushed, "failed": failed,
            "remaining": count_pending_push(db, include_baseline=include_baseline),
            "order_nos": sent_nos}


def find_pushed_without_address(db: Session) -> list[ImportedFile]:
    """此前【缺地址被推】、而对应订单现在已有可用收货地址的下单图归档记录。

    判据: row_summary.pushed=True 且 pushed_addr_ok=False (上次推时没地址/被脱敏),
    且订单当前 _addr_ok_for_factory()=True (口令解密后地址已补上)。这些就是值得重推的单。
    """
    recs = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet").order_by(ImportedFile.id.asc())
    ).scalars().all()
    out: list[ImportedFile] = []
    for r in recs:
        st = r.row_summary or {}
        if not st.get("pushed"):
            continue   # 没推过 → 不在重推范围
        # 只重推【明确记录为缺地址被推】的 (pushed_addr_ok is False)。
        # True=上次已带地址; None=本次部署前推的历史单(无此标记)——都不自动重推,
        # 避免一次口令把整批历史已正确发送的单刷给工厂群 (安全默认: 靠新标记 opt-in)。
        if st.get("pushed_addr_ok") is not False:
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
    """飞书口令解密补上收货地址后, 自动重推此前【缺地址被推】的下单图 (用户 2026-06-30)。

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
    seen: dict = {}
    for rec in db.execute(select(ImportedFile).where(ImportedFile.kind == "order_sheet")).scalars().all():
        if not (rec.row_summary or {}).get("pushed"):
            continue
        no = _order_no_from_name(rec.original_filename)
        if no:
            seen.setdefault(no, []).append(rec)
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    changed: list = []
    for no, recs in seen.items():
        if len(changed) >= limit:
            break
        if any((r.row_summary or {}).get("activated") is True for r in recs):
            continue   # 已有"激活态"推的图 → 不再重推
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
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
        for r in recs:
            import_storage.delete_record(db, r.id)   # 删旧号下单图归档
        order.factory_no = None                       # 清工厂号 → 重推时顺排新号
        db.flush()
        changed.append(no)
    if changed:
        db.commit()
    return {"reset_for_new_no": changed}


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

    **显式作废动作**(会给工厂群发"X号作废"): 只在用户确认后调用; order_nos 限定只作废指定单
    (缺省 None = 全部延期挂号单, 慎用)。命中 = 有推过的下单图 + 现 is_remote(远期且未激活) + 未取消/退款
    + 有工厂号。动作 = 飞书"X号作废(已延期)" + 删旧下单图 + 清 factory_no → 回到挂起态后续不再推;
    等激活(开始制作)后再以新号重下。日常只【提醒】不自动作废 → 见 remind_remote_pushed。"""
    from app.services import order_flags, feishu_client, settings_service
    seen: dict = {}
    for rec in db.execute(select(ImportedFile).where(ImportedFile.kind == "order_sheet")).scalars().all():
        if not (rec.row_summary or {}).get("pushed"):
            continue
        no = _order_no_from_name(rec.original_filename)
        if no:
            seen.setdefault(no, []).append(rec)
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    voided: list = []
    for no, recs in seen.items():
        if len(voided) >= limit:
            break
        if order_nos is not None and no not in order_nos:
            continue
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
            continue
        if not order_flags.is_remote(order):
            continue   # 现在不是远期挂起(已激活/普通) → 不作废
        old_no = getattr(order, "factory_no", None)
        if old_no is None:
            continue
        if chat_id:
            try:
                feishu_client.send_text(
                    db, chat_id,
                    f"⚠️ 订单 {no} 原【畔色 {old_no} 单】作废 —— 该单已备注延期/等通知, 请勿生产; "
                    f"待客户通知开始制作后会以新号重下。")
            except Exception:  # noqa: BLE001
                _logger.warning("延期作废提示发送失败 %s", no, exc_info=True)
        for r in recs:
            import_storage.delete_record(db, r.id)
        order.factory_no = None
        db.flush()
        voided.append(no)
    if voided:
        db.commit()
    return {"voided_remote": voided}


def remind_remote_pushed(db: Session) -> dict:
    """已推工厂、但现在已延期/远期的单 → 只【提醒用户】(不自动作废, 防误废工厂已在做的单) (用户 2026-07-08)。
    列出这些单发到用户通知渠道; 用户确认后再用 void_remote_pushed(order_nos=...) 作废其工厂号。"""
    from app.services import order_flags
    seen: set = set()
    for rec in db.execute(select(ImportedFile).where(ImportedFile.kind == "order_sheet")).scalars().all():
        if not (rec.row_summary or {}).get("pushed"):
            continue
        no = _order_no_from_name(rec.original_filename)
        if no:
            seen.add(no)
    items: list = []
    for no in seen:
        order = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if not order or (order.status or "") in ("cancelled", "pending_payment") or _is_refunded(order):
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
            notify_service.notify(db, txt, level="warn", title="畔色 ERP [延期单仍挂工厂号]")
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
    void_remote_pushed(db)     # 已推工厂但现已延期/远期的单 → 自动作废旧工厂号+通知工厂勿做+挂起 (用户 2026-07-08: 18:30自动作废)
    repush_activated(db)       # 远期老单激活→旧号作废、清号, 下面 generate+push 会以新号重推
    assign_remote_seqs(db)     # 远期挂起单发内部序号"远期单 N"(不占工厂号) (用户 2026-07-09)
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
        title = "畔色 ERP [下单图作废提醒]"
        try:
            from app.services import notify_service
            ok, _ = notify_service.notify(db, text, level="warning", title=title)
            result["pushed"] = bool(ok)
        except Exception:  # pragma: no cover
            result["pushed"] = False
        # 同步推飞书工厂群 (用户 2026-06-26: 微信的作废提醒内容也要进飞书)
        try:
            from app.services import feishu_client, settings_service
            chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
            if chat_id:
                feishu_client.send_text(db, chat_id, f"【{title}】\n{text}")
                result["feishu_pushed"] = True
        except Exception:  # pragma: no cover
            result["feishu_pushed"] = False
            _logger.warning("作废提醒推飞书失败", exc_info=True)
    else:
        result["pushed"] = False
    return result
