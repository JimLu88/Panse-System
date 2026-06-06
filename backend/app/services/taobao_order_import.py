"""淘宝订单多格式导入 + 自动识别 (Phase B 扩展)。

支持未来两种导出格式自动识别、解析、入库, 无需手动指定:

1. **千牛后台多表 Excel** (``订单报表`` / ``销售明细`` / ``发货报表`` 三个 Sheet)
   - 销售明细 = 行级主数据 (产品/SKU/数量/退款)
   - 订单报表 = 单级 (地址/物流/金额/状态)
   - 发货报表 = 单级 (收货人/电话)
   - 三表按订单号关联, 一张订单一行 Order (多商品单取主商品行 + 备注其余)

2. **销售明细 CSV / Excel** (子订单编号,主订单编号,标题,…,商家编码,…)
   - GBK 或 UTF-8 编码均可
   - 同样按订单号聚合成 Order

公共转换:
  - 商家编码 ``PPS`` + 13 位 (SKU 级) → 老格式产品编码 ``P`` + 11 位
  - 商品属性 ``颜色分类:xxx[规格];安装方式:yyy`` → 取 ``xxx`` 作 SKU 名
  - 订单号科学计数法 (历史 CSV 损坏) → 标记 needs_review, 不静默丢弃

入库口径与 order_import 一致: 订单号唯一, 重复跳过; 历史批量导入默认 is_historical=True。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import order_cost_service, taobao_listing_service

# ── 格式指纹 ──────────────────────────────────────────────────────────────────
# 千牛多表: Sheet 名命中其一即视为该格式
QIANNIU_SHEETS = {"订单报表", "销售明细", "发货报表"}
# 销售明细列指纹 (任一格式只要含这些列即可识别)
SALES_DETAIL_KEYS = {"子订单编号", "主订单编号", "商品属性"}

# 淘宝订单状态文本 → 内部 status
_STATUS_MAP = {
    "等待买家付款": "pending_payment",
    "买家已付款,等待卖家发货": "paid",
    "买家已付款，等待卖家发货": "paid",
    "卖家已发货,等待买家确认": "shipped",
    "卖家已发货，等待买家确认": "shipped",
    "交易成功": "signed",
    "交易关闭": "cancelled",
    "退款成功": "aftersales",
}


def _map_status(raw: Any) -> str:
    if not raw:
        return "pending_payment"
    s = str(raw).strip()
    if s in _STATUS_MAP:
        return _STATUS_MAP[s]
    # 模糊兜底
    if "退款" in s:
        return "aftersales"
    if "关闭" in s:
        return "cancelled"
    if "成功" in s:
        return "signed"
    if "发货" in s and "等待买家" in s:
        return "shipped"
    if "付款" in s and "等待卖家" in s:
        return "paid"
    if "等待买家付款" in s:
        return "pending_payment"
    return "pending_payment"


# ── 字段转换 ──────────────────────────────────────────────────────────────────
def extract_sku(attr: Any) -> str:
    """商品属性 '颜色分类:xxx[规格];安装方式:yyy' → 'xxx' (去[..]与;后内容)。"""
    if not attr:
        return ""
    s = str(attr)
    first = re.split(r"[;；]", s)[0]
    first = re.sub(r"^[^:：]*[:：]", "", first)   # 去 '颜色分类:' 前缀
    first = re.sub(r"\[[^\]]*\]", "", first)       # 去 [长45cm] 规格
    return first.strip()


def product_code_from_merchant(mc: Any) -> str:
    """商家编码 → 老格式产品编码 P+11位。

    - ``PPS`` + 13 位 (SKU 级) → ``P`` + 前 11 位 (产品级)
    - 纯 11+ 位数字 (历史编码) → ``P`` + 前 11 位
    - 其它 / 空 → 空串
    """
    if mc is None:
        return ""
    s = str(mc).strip()
    if not s:
        return ""
    if s.upper().startswith("PPS"):
        digits = s[3:]
        return "P" + digits[:11] if len(digits) >= 11 else "P" + digits
    if s.isdigit() and len(s) >= 11:
        return "P" + s[:11]
    return ""


def _is_sci(v: Any) -> bool:
    """检测科学计数法损坏的订单号 (含 E+ / e+)。"""
    return v is not None and re.search(r"\d[eE]\+?\d+", str(v)) is not None


def _to_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    part = s.split(" ")[0].replace("/", "-")
    try:
        y, m, d = part.split("-")
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _to_int(v: Any, default: int = 1) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _to_decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    s = str(v).replace(",", "").replace("¥", "").replace("元", "").strip()
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ── 报告 ──────────────────────────────────────────────────────────────────────
@dataclass
class TaobaoImportReport:
    detected_format: str = "unknown"
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    needs_review: int = 0           # 订单号损坏等, 已入库但标注待核
    multi_line_orders: int = 0      # 一单多商品的订单数
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── 统一记录结构 (聚合到订单级) ────────────────────────────────────────────────
@dataclass
class _OrderRow:
    order_no: str
    order_no_bad: bool = False
    order_date: Any = None
    customer_name: Any = None
    customer_phone: Any = None
    customer_address: Any = None
    carrier: Any = None
    tracking_no: Any = None
    paid_amount: Any = None
    status_text: Any = None
    lines: list[dict] = field(default_factory=list)   # 每个商品行


# ── 格式识别 ──────────────────────────────────────────────────────────────────
def detect_format(filename: str, raw: bytes) -> str:
    """返回 'qianniu_multi' | 'sales_detail' | 'order_master' | 'unknown'。"""
    name = (filename or "").lower()
    is_xlsx = name.endswith((".xlsx", ".xlsm", ".xls")) or raw[:2] == b"PK"
    if is_xlsx:
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True)
            sheets = set(wb.sheetnames)
            wb.close()
        except Exception:
            sheets = set()
        if sheets & QIANNIU_SHEETS:
            return "qianniu_multi"
        # 单 Sheet xlsx: 看首行表头
        header = _xlsx_first_header(raw)
        if SALES_DETAIL_KEYS & set(header):
            return "sales_detail"
        if "订单编号" in header and ("买家应付金额" in header or "产品编码" in header):
            return "order_master"
        return "unknown"
    # CSV
    header = _csv_header(raw)
    if SALES_DETAIL_KEYS & set(header):
        return "sales_detail"
    if "订单编号" in header or "订单号" in header:
        return "order_master"
    return "unknown"


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


def _csv_header(raw: bytes) -> list[str]:
    text = _decode(raw)
    rdr = csv.reader(io.StringIO(text))
    for row in rdr:
        return [(c or "").strip() for c in row]
    return []


def _xlsx_first_header(raw: bytes) -> list[str]:
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True)
        ws = wb[wb.sheetnames[0]]
        for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
            wb.close()
            return [str(c).strip() for c in row if c is not None]
        wb.close()
    except Exception:
        pass
    return []


# ── 解析: 千牛多表 Excel ──────────────────────────────────────────────────────
def _parse_qianniu_multi(raw: bytes, rep: TaobaoImportReport) -> dict[str, _OrderRow]:
    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    orders: dict[str, _OrderRow] = {}

    def header_idx(ws):
        for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
            return {str(v).strip(): i for i, v in enumerate(row) if v is not None}
        return {}

    # 订单报表: 单级
    rpt: dict[str, dict] = {}
    if "订单报表" in wb.sheetnames:
        ws = wb["订单报表"]; h = header_idx(ws)
        def g(row, *names):
            for n in names:
                if n in h and h[n] < len(row):
                    return row[h[n]]
            return None
        for row in ws.iter_rows(min_row=2, values_only=True):
            no = _clean(g(row, "订单编号"))
            if not no:
                continue
            rpt[no] = {
                "addr": g(row, "收货地址"),
                "carrier": g(row, "物流公司", "快递公司"),
                "tracking": g(row, "物流单号", "运单号"),
                "buyer_due": g(row, "买家应付货款", "买家实付金额"),
                "status": g(row, "订单状态"),
                "create": g(row, "订单创建时间", "订单付款时间"),
            }

    # 发货报表: 单级 客户信息
    ship: dict[str, dict] = {}
    if "发货报表" in wb.sheetnames:
        ws = wb["发货报表"]; h = header_idx(ws)
        def g2(row, *names):
            for n in names:
                if n in h and h[n] < len(row):
                    return row[h[n]]
            return None
        for row in ws.iter_rows(min_row=2, values_only=True):
            no = _clean(g2(row, "订单编号"))
            if not no:
                continue
            ship[no] = {
                "name": g2(row, "收货人姓名"),
                "phone": g2(row, "联系手机", "联系电话"),
                "addr": g2(row, "收货地址"),
            }

    # 销售明细: 行级主数据
    if "销售明细" in wb.sheetnames:
        ws = wb["销售明细"]; h = header_idx(ws)
        def g3(row, *names):
            for n in names:
                if n in h and h[n] < len(row):
                    return row[h[n]]
            return None
        for row in ws.iter_rows(min_row=2, values_only=True):
            main = g3(row, "主订单编号", "订单编号")
            if main is None or str(main).strip() == "":
                continue
            no = str(main).strip()
            r = rpt.get(no, {})
            s = ship.get(no, {})
            o = orders.get(no)
            if o is None:
                o = _OrderRow(
                    order_no=no, order_no_bad=_is_sci(main),
                    order_date=r.get("create") or g3(row, "订单创建时间"),
                    customer_name=s.get("name"),
                    customer_phone=s.get("phone"),
                    customer_address=s.get("addr") or r.get("addr"),
                    carrier=r.get("carrier"), tracking_no=r.get("tracking"),
                    paid_amount=r.get("buyer_due") or g3(row, "买家应付货款"),
                    status_text=r.get("status") or g3(row, "订单状态"),
                )
                orders[no] = o
            merchant = g3(row, "商家编码", "外部系统编号")
            o.lines.append({
                "product_name": g3(row, "商品标题", "标题"),
                "sku": extract_sku(g3(row, "商品属性")),
                "sku_code": _clean(merchant),
                "product_code": product_code_from_merchant(merchant),
                "sku_id": _clean(g3(row, "skuId", "sku id", "SKU ID", "sku_id")),
                "qty": g3(row, "购买数量", "数量"),
                "amount": _to_decimal(g3(row, "买家应付货款", "商品价格")),
            })
    wb.close()
    return orders


# ── 解析: 销售明细 CSV / 单 Sheet xlsx ─────────────────────────────────────────
def _parse_sales_detail(filename: str, raw: bytes, rep: TaobaoImportReport) -> dict[str, _OrderRow]:
    name = (filename or "").lower()
    rows: list[dict] = []
    if name.endswith((".xlsx", ".xlsm", ".xls")) or raw[:2] == b"PK":
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else "" for c in next(it)]
        for r in it:
            rows.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
        wb.close()
    else:
        text = _decode(raw)
        rows = list(csv.DictReader(io.StringIO(text)))

    def gv(row, *names):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    orders: dict[str, _OrderRow] = {}
    for row in rows:
        main = gv(row, "主订单编号", "订单编号", "子订单编号")
        if main is None or str(main).strip() == "":
            continue
        no = str(main).strip()
        o = orders.get(no)
        if o is None:
            o = _OrderRow(
                order_no=no, order_no_bad=_is_sci(main),
                order_date=gv(row, "订单创建时间", "订单付款时间", "下单时间"),
                customer_name=gv(row, "收货人姓名", "买家昵称"),
                customer_phone=gv(row, "联系手机", "联系电话", "手机"),
                customer_address=gv(row, "收货地址"),
                carrier=gv(row, "物流公司", "快递公司"),
                tracking_no=gv(row, "物流单号", "运单号"),
                paid_amount=gv(row, "买家应付货款", "买家实付金额", "实付金额"),
                status_text=gv(row, "订单状态"),
            )
            orders[no] = o
        merchant = gv(row, "商家编码", "外部系统编号")
        o.lines.append({
            "product_name": gv(row, "商品标题", "标题", "商品名称"),
            "sku": extract_sku(gv(row, "商品属性")),
            "sku_code": _clean(merchant),
            "product_code": product_code_from_merchant(merchant),
            "sku_id": _clean(gv(row, "skuId", "sku id", "SKU ID", "sku_id")),
            "qty": gv(row, "购买数量", "数量"),
            "amount": _to_decimal(gv(row, "买家应付货款", "价格", "商品价格")),
        })
    return orders


# ── 聚合订单行 → Order 入库 ───────────────────────────────────────────────────
def _commit_orders(db: Session, orders: dict[str, _OrderRow], platform: str,
                   rep: TaobaoImportReport) -> None:
    resolver = taobao_listing_service.build_resolver(db)  # 对应表: skuId/编码 → SKU编码/产品编码/店铺
    seen: set[str] = set()
    for no, o in orders.items():
        if not no:
            rep.skipped_invalid += 1
            continue
        if no in seen or db.execute(select(Order.id).where(Order.order_no == no)).first():
            rep.skipped_duplicate += 1
            seen.add(no)
            continue
        seen.add(no)

        # 主商品行: 取金额最大的一行 (定制差价等小额行不抢主位)
        lines = o.lines or [{}]
        if len(lines) > 1:
            rep.multi_line_orders += 1
        primary = max(lines, key=lambda x: (x.get("amount") or Decimal(0)))

        remark = None
        if len(lines) > 1:
            others = [
                f"{l.get('product_name') or ''}/{l.get('sku') or ''}×{l.get('qty') or 1}"
                for l in lines if l is not primary
            ]
            remark = "本单含%d个商品, 其余: %s" % (len(lines), "; ".join(others))

        flags = []
        if o.order_no_bad:
            flags.append("订单号科学计数法损坏(需人工核对)")
            rep.needs_review += 1
        if flags:
            remark = (remark + " | " if remark else "") + "⚠️ " + "; ".join(flags)

        _pname = _clean(primary.get("product_name"))
        _sku = _clean(primary.get("sku"))
        _product_code = primary.get("product_code") or None
        _sku_code = primary.get("sku_code")
        _shop = None
        # Task 6: 用对应表按 skuId(精确) / 16位商家编码 反查 SKU编码/产品编码/店铺
        hit = taobao_listing_service.resolve_line(
            resolver, sku_id=primary.get("sku_id"), merchant_code=primary.get("sku_code"),
        )
        if hit:
            _sku_code = hit.get("sku_code") or _sku_code
            _product_code = hit.get("product_code") or _product_code
            _shop = hit.get("shop")
        order = Order(
            platform=(platform or "淘宝").strip(),
            order_no=no,
            order_date=_to_date(o.order_date),
            customer_name=_clean(o.customer_name),
            customer_phone=_clean(o.customer_phone),
            customer_address=_clean(o.customer_address),
            product_code=_product_code,
            product_name=_pname,
            sku=_sku,
            sku_code=_sku_code,
            shop=_shop,
            qty=_to_int(primary.get("qty"), default=1),
            carrier=_clean(o.carrier),
            tracking_no=_clean(o.tracking_no),
            paid_amount=_to_decimal(o.paid_amount),
            status=_map_status(o.status_text),
            is_historical=True,
            remark=remark,
            warehouse=order_cost_service.default_warehouse_for(_pname, _sku, False),
        )
        db.add(order)
        rep.inserted += 1
    db.commit()


# ── 对外统一入口 ──────────────────────────────────────────────────────────────
def import_taobao_orders(db: Session, filename: str, raw: bytes,
                         platform: str = "淘宝",
                         force_format: Optional[str] = None) -> TaobaoImportReport:
    """自动识别格式并导入。force_format 可强制指定 (绕过自动识别)。"""
    rep = TaobaoImportReport()
    fmt = force_format or detect_format(filename, raw)
    rep.detected_format = fmt

    if fmt == "qianniu_multi":
        orders = _parse_qianniu_multi(raw, rep)
    elif fmt == "sales_detail":
        orders = _parse_sales_detail(filename, raw, rep)
    elif fmt == "order_master":
        # 老订单总表格式 → 交回既有通用导入 (列名已对齐), 这里仅提示
        rep.warnings.append("识别为『订单总表』格式, 请使用 /import-csv 或智能导入。")
        return rep
    else:
        rep.errors.append(
            "无法识别订单文件格式。支持: 千牛多表Excel(订单报表/销售明细/发货报表) "
            "或 销售明细(含 子订单编号/主订单编号/商品属性)。"
        )
        return rep

    if not orders:
        rep.warnings.append("未解析到任何订单行。")
        return rep
    _commit_orders(db, orders, platform, rep)
    return rep
