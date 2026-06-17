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
    # 已收货/已结算/交易完成/待评价/已签收 = 真实成交完结 (用户实测 2026-06-18: 这些没被识别→错标待付款)
    "已收货": "signed", "买家已收货": "signed", "已签收": "signed", "交易完成": "signed",
    "已完成": "signed", "待评价": "signed", "已结算": "signed",
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
    # 成交完结类: 成功/收货/签收/完成/已结算/待评价 → 已签收 (扩展, 防"已收货"这类掉进待付款)
    if any(k in s for k in ("成功", "收货", "签收", "交易完成", "已完成", "已结算", "待评价")):
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
    updated: int = 0                # 已存在订单被回填状态/金额 (再次导入更新)
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    needs_review: int = 0           # 订单号损坏等, 已入库但标注待核
    multi_line_orders: int = 0      # 一单多商品的订单数
    status_changed: int = 0         # 重导时状态被覆盖的单数 (日报用)
    amount_changed: int = 0         # 重导时实付/退款被覆盖的单数 (日报用)
    vanished: int = 0               # 库里有、新文件里没有的单数 (报异常, 不动行)
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
    paid_amount: Any = None          # 买家应付货款 (兜底, 老逻辑沿用)
    status_text: Any = None
    # 财务列 (订单报表/销售明细自带, 现金流"待确认收货/未发货/平台费"全靠它们)
    buyer_payable: Any = None        # 买家应付货款
    paid_real: Any = None            # 买家实付金额 (买家真实支付 = 我方应收)
    shop_received: Any = None        # 打款商家金额 (店铺实收, 历史CSV有)
    platform_fee: Any = None         # 卖家服务费 (平台服务费)
    refund: Any = None               # 退款金额
    ship_time: Any = None            # 发货时间
    confirm_time: Any = None         # 确认收货时间
    shop: Any = None                 # 店铺名称
    buyer_message: Any = None        # 买家留言 (平台, 重导覆盖)
    seller_memo: Any = None          # 卖家备注/商家备注 (平台, 重导覆盖)
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
        # 千牛「订单报表」单表导出 (Web-Agent 自动取数, 2026-06-12): 主单级,
        # 列名 _parse_sales_detail 的 gv 兜底全覆盖 → 直接走 sales_detail 解析。
        if "订单编号" in header and "订单创建时间" in header and "订单状态" in header:
            return "sales_detail"
        if "订单编号" in header and ("买家应付金额" in header or "产品编码" in header):
            return "order_master"
        # 淘宝卖家中心「已卖出的宝贝」导出 (ExportOrderList, sheet 多名 'export'; Web-Agent 自动下载):
        # 列为 订单编号/订单状态/买家应付货款/收货人姓名/买家留言… 无"订单创建时间"。
        # _parse_sales_detail 的 gv 兜底全覆盖这些列名 → 走 sales_detail 解析 (2026-06-12)。
        if "订单编号" in header and "订单状态" in header:
            return "sales_detail"
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
                # 财务列 (订单报表自带): 应付/实付/实收/平台费/退款/发货确认时间/店铺
                "payable": g(row, "买家应付货款"),
                "paid_real": g(row, "买家实付金额", "买家实际支付金额"),
                "shop_received": g(row, "打款商家金额"),
                "platform_fee": g(row, "卖家服务费", "平台服务费"),
                "refund": g(row, "退款金额"),
                "ship_time": g(row, "发货时间"),
                "confirm_time": g(row, "确认收货时间"),
                "shop": g(row, "店铺名称"),
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
                    buyer_payable=r.get("payable") or g3(row, "买家应付货款"),
                    paid_real=r.get("paid_real") or g3(row, "买家实付金额"),
                    shop_received=r.get("shop_received"),
                    platform_fee=r.get("platform_fee"),
                    refund=r.get("refund") or g3(row, "退款金额"),
                    ship_time=r.get("ship_time") or g3(row, "发货时间"),
                    confirm_time=r.get("confirm_time") or g3(row, "确认收货时间"),
                    shop=r.get("shop"),
                    buyer_message=g3(row, "买家留言", "买家留言备注", "买家备注"),
                    seller_memo=g3(row, "卖家备注", "商家备注", "卖家留言"),
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
                buyer_payable=gv(row, "买家应付货款"),
                paid_real=gv(row, "买家实付金额", "买家实际支付金额", "实付金额"),
                shop_received=gv(row, "打款商家金额"),
                platform_fee=gv(row, "卖家服务费", "平台服务费"),
                refund=gv(row, "退款金额"),
                ship_time=gv(row, "发货时间"),
                confirm_time=gv(row, "确认收货时间"),
                shop=gv(row, "店铺名称"),
                buyer_message=gv(row, "买家留言", "买家留言备注", "买家备注"),
                seller_memo=gv(row, "卖家备注", "商家备注", "卖家留言", "常用备注"),
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
        if no in seen:
            rep.skipped_duplicate += 1
            continue
        seen.add(no)

        # 2025 订单已物理清理(用户拍板 2026-06-15); 淘宝"近3月"报表会带回老的已成交单 →
        # 不再重建, 否则每次取数都把已删的 2025 单灌回来(并再生缺收款等异常)。order_date 为空
        # (个别发货报表行)无法判断 → 保留交后续处理。
        _od = _to_date(o.order_date)
        if _od is not None and _od.year < 2026:
            continue

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
        if o.order_no_bad:
            rep.needs_review += 1
            remark = (remark + " | " if remark else "") + "⚠️ 订单号科学计数法损坏(需人工核对)"

        _pname = _clean(primary.get("product_name"))
        _sku = _clean(primary.get("sku"))
        _product_code = primary.get("product_code") or None
        _sku_code = primary.get("sku_code")
        _shop = _clean(o.shop)
        # Task 6: 用对应表按 skuId(精确) / 16位商家编码 反查 SKU编码/产品编码/店铺
        hit = taobao_listing_service.resolve_line(
            resolver, sku_id=primary.get("sku_id"), merchant_code=primary.get("sku_code"),
        )
        if hit:
            _sku_code = hit.get("sku_code") or _sku_code
            _product_code = hit.get("product_code") or _product_code
            _shop = hit.get("shop") or _shop

        # 财务字段 (淘宝导出为准): 应付/实付/实收/平台费/退款/发货日
        status = _map_status(o.status_text)
        payable = _to_decimal(o.buyer_payable)
        paid = _to_decimal(o.paid_real) or _to_decimal(o.paid_amount)
        received = _to_decimal(o.shop_received)
        pfee = _to_decimal(o.platform_fee)
        refund = _to_decimal(o.refund)
        ship_dt = _to_date(o.ship_time)
        # 防错标"待付款" 兜底 (用户拍板 2026-06-18): 状态文本应优先按 _STATUS_MAP 翻译(已补"已收货"等)。
        # 若仍落"待付款"但有【真实收款】凭据(店铺实收>0 或 买家实付>0)→ 纠正为已付款。
        # ⚠ 不用物流单号作凭据: 实测物流号会出现在 ¥0/补拍/未成交单上, 不可靠(全刷会把假单也算进来)。
        if status == "pending_payment":
            _paid_real = _to_decimal(o.paid_real)
            if (received and received > 0) or (_paid_real and _paid_real > 0):
                status = "paid"

        existing = db.execute(select(Order).where(Order.order_no == no)).scalar_one_or_none()
        if existing is not None:
            # 再次导入: 状态/金额以淘宝导出为准(覆盖); 描述/客户仅在缺失时回填; 不动 is_refill/remark。
            # is_historical 置 False: 淘宝真实订单应进统计(预测/月度报表/现金流), 旧通用导入误标历史在此纠正。
            # 用户拍板 (2026-06-11): 被覆盖的关键字段记修改档案 (source=import), 旧值可回溯。
            def _trace(fname: str, flabel: str, old_v, new_v) -> bool:
                if str(old_v) == str(new_v):
                    return False
                try:
                    from app.services import field_change_service
                    field_change_service.record(
                        db, table="orders", pk=no, field=fname, old=old_v, new=new_v,
                        actor="订单重导", source="import",
                        row_label=(existing.product_name or "")[:40], field_label=flabel,
                    )
                except Exception:
                    pass
                return True
            if _trace("status", "订单状态", existing.status, status):
                rep.status_changed += 1
            existing.is_historical = False
            existing.status = status
            if payable is not None:
                existing.buyer_payable_amount = payable
            if paid is not None:
                if _trace("paid_amount", "实付金额", existing.paid_amount, paid):
                    rep.amount_changed += 1
                existing.paid_amount = paid
            if received is not None:
                existing.shop_received_amount = received
            if pfee is not None:
                existing.platform_fee = pfee
            if refund is not None:
                if _trace("refund_amount", "退款金额", existing.refund_amount, refund):
                    rep.amount_changed += 1
                existing.refund_amount = refund
            if ship_dt is not None:
                _trace("ship_date", "发货日期", existing.ship_date, ship_dt)
                existing.ship_date = ship_dt
            # 物流单号/承运商: 重导时回填(只填空, 不覆盖已手工改的)。
            # 修复(2026-06-15): 原更新分支漏了这两个 → 已发货订单重导也补不上物流号(图四常驻27)。
            _trk = _clean(o.tracking_no)
            if _trk and not existing.tracking_no:
                _trace("tracking_no", "物流单号", existing.tracking_no, _trk)
                existing.tracking_no = _trk
            _car = _clean(o.carrier)
            if _car and not existing.carrier:
                existing.carrier = _car
            # 平台备注随重导覆盖 (用户拍板: 买家留言/商家备注是淘宝侧会变的数据;
            # 非空才覆盖, 防止不含该列的旧格式文件把留言抹掉)
            _bmsg = _clean(o.buyer_message)
            if _bmsg:
                _trace("buyer_message", "买家留言", existing.buyer_message, _bmsg)
                existing.buyer_message = _bmsg
            _smemo = _clean(o.seller_memo)
            if _smemo:
                _trace("seller_memo", "商家备注", existing.seller_memo, _smemo)
                existing.seller_memo = _smemo
            if _shop and not existing.shop:
                existing.shop = _shop
            if _pname and not existing.product_name:
                existing.product_name = _pname
            if _sku and not existing.sku:
                existing.sku = _sku
            if _product_code and not existing.product_code:
                existing.product_code = _product_code
            if _sku_code and not existing.sku_code:
                existing.sku_code = _sku_code
            if o.customer_name and not existing.customer_name:
                existing.customer_name = _clean(o.customer_name)
            if o.customer_phone and not existing.customer_phone:
                existing.customer_phone = _clean(o.customer_phone)
            if o.customer_address and not existing.customer_address:
                existing.customer_address = _clean(o.customer_address)
            rep.updated += 1
            continue

        order = Order(
            platform=(platform or "淘宝").strip(),
            order_no=no,
            order_date=_to_date(o.order_date),
            ship_date=ship_dt,
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
            buyer_payable_amount=payable,
            paid_amount=paid,
            shop_received_amount=received,
            platform_fee=pfee,
            refund_amount=refund,
            status=status,
            # 现金流"待确认收货/未发货"靠活跃单的金额; 故不再一律 historical
            is_historical=False,
            remark=remark,
            buyer_message=_clean(o.buyer_message),
            seller_memo=_clean(o.seller_memo),
            warehouse=order_cost_service.default_warehouse_for(_pname, _sku, False),
        )
        db.add(order)
        rep.inserted += 1
    db.commit()


def apply_refill_flags(db: Session) -> int:
    """每次导入订单后调用: 凡出现在补单对账(RefillRecord)里的订单号, 一律优先标成 is_refill=True。

    补单表是补单与否的最高优先级来源 —— 否则这些单会被当成"真实订单"少算补单(报表失真)。
    """
    from app.models.finance import RefillRecord
    refill_nos = {n for (n,) in db.execute(select(RefillRecord.order_no)).all() if n}
    if not refill_nos:
        return 0
    n = 0
    for o in db.execute(
        select(Order).where(Order.order_no.in_(refill_nos), Order.is_refill == False)  # noqa: E712
    ).scalars().all():
        o.is_refill = True
        n += 1
    return n


_OOXML_ENCRYPTED_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def is_encrypted_ooxml(raw: bytes) -> bool:
    """前 8 字节是 OOXML 加密复合文档魔数 → 加密发货报表。"""
    return raw[:8] == _OOXML_ENCRYPTED_MAGIC


def maybe_decrypt(raw: bytes, password: Optional[str]) -> bytes:
    """加密 OOXML(发货报表) → 用口令解成明文 xlsx 字节; 明文则原样返回。

    口令缺失/错误抛 ValueError, 调用方据此标「待口令」或「口令错误」。
    """
    if not is_encrypted_ooxml(raw):
        return raw
    if not password:
        raise ValueError("发货报表已加密, 但未取到飞书口令 (请转发『发货密码 xxx』到飞书机器人)")
    import msoffcrypto
    fin = io.BytesIO(raw)
    fout = io.BytesIO()
    office = msoffcrypto.OfficeFile(fin)
    try:
        office.load_key(password=password)
        office.decrypt(fout)
    except Exception as e:  # msoffcrypto 口令错误 → InvalidKeyError 等
        raise ValueError(f"发货报表解密失败 (口令可能已过期或不对): {e}") from e
    return fout.getvalue()


# ── 对外统一入口 ──────────────────────────────────────────────────────────────
def import_taobao_orders(db: Session, filename: str, raw: bytes,
                         platform: str = "淘宝",
                         force_format: Optional[str] = None,
                         password: Optional[str] = None) -> TaobaoImportReport:
    """自动识别格式并导入。force_format 可强制指定; password 用于解密加密发货报表。"""
    rep = TaobaoImportReport()
    if is_encrypted_ooxml(raw):
        try:
            raw = maybe_decrypt(raw, password)
        except ValueError as e:
            rep.errors.append(str(e))
            return rep
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

    # 行数骤降拦截已整体移除 (用户拍板 2026-06-15)。根因: 淘宝三张互补报表 —— 订单报表
    # (~897 单, 主单级含物流单号)、宝贝销售明细 (~851 单, 行级含商品/SKU)、发货报表
    # (~108 单, 仅已发货, 含收货人/电话) —— 各自覆盖不同订单子集, 却共用一个全局"上次行数"
    # 计数器互比, 导小表 (发货报表) 时必然误判"骤降 >50%"而拒导。这是拿异质报表当同一总体
    # 比较的设计缺陷, 不是真有坏文件。导入本就是 upsert (只增改、从不删行, 见 _commit_orders),
    # 残缺文件也抹不掉已有数据 → 该防护既误报又多余, 删除 (不再读写 last_order_import_count)。

    _commit_orders(db, orders, platform, rep)
    if apply_refill_flags(db):   # 用补单对账回标 is_refill (导入后立即匹配, 优先级最高)
        db.commit()

    # 理论成本自动反推 (用户拍板 2026-06-12: 订单导入进来就应自动推算, 不再"未反推")。
    # only_missing=True 只补未算/为0的 (已算的不动); 失败不阻断导入。
    try:
        from app.services import order_cost_service
        order_cost_service.recompute_all(db, only_missing=True)
    except Exception as e:  # noqa: BLE001
        rep.warnings.append(f"理论成本自动反推未完成: {type(e).__name__}")

    # 导入消失检测 (用户拍板 2026-06-12; 根因修正后 2026-06-15 默认关闭"报缺")。
    # ⚠ 根因: report_missing 假设"单份文件 = 该时段全量快照", 但淘宝三张报表各覆盖不同子集
    # (订单报表897 / 销售明细851 / 发货报表108) —— 互比会把"另一张报表独有的单"误判成消失
    # (导 108 单的发货报表会一次性误报数百单 import_missing 异常)。这正是用户说的"误报背后有
    # 真问题": 用错了比较口径。导入是 upsert (永不删行), 即便真有单从淘宝消失, 库里也不会被抹,
    # 故"报缺"默认关; 仅当 settings taobao_import_vanish_check=1 且确知本次是单一权威订单表时才开。
    # resolve_reappeared 保留 (只销旧异常、绝不新建), 让历史误报在每次重导时自动收敛归零。
    try:
        from app.models.order import Order as _Order
        from app.services import import_vanish_service
        # orders 是 dict[order_no, _OrderRow] — 必须取 values(), 直接迭代拿到的是字符串键
        _rows = list(orders.values()) if isinstance(orders, dict) else list(orders)
        file_keys = {o.order_no for o in _rows if o.order_no and not o.order_no_bad}
        # 重新出现的单自动销掉旧异常 (拆单恢复/上次导出遗漏这次补上了 / 历史误报自愈)
        import_vanish_service.resolve_reappeared(
            db, source_table="orders", present_keys=file_keys)
        db.commit()
        _vanish_on = False
        try:
            from app.services import settings_service as _ss
            _vanish_on = str(_ss.get(db, "taobao_import_vanish_check",
                                     env_fallback=False) or "").strip().lower() \
                in ("1", "true", "yes", "on")
        except Exception:
            _vanish_on = False
        if _vanish_on:
            from datetime import date as _date_cls
            dates = [o.order_date for o in _rows
                     if isinstance(o.order_date, _date_cls)]
            if dates and file_keys:
                dmin, dmax = min(dates), max(dates)
                db_keys = {
                    r[0] for r in db.query(_Order.order_no).filter(
                        _Order.platform == platform,
                        _Order.is_refill == False,  # noqa: E712
                        _Order.order_date >= dmin,
                        _Order.order_date <= dmax,
                    ).all()
                }
                rep.vanished = import_vanish_service.report_missing(
                    db, source_table="orders", label="订单",
                    missing=sorted(db_keys - file_keys),
                    scope_desc=f"本次文件覆盖 {dmin}~{dmax}",
                )
                db.commit()
    except Exception:  # pragma: no cover - 检测故障不阻断导入
        import logging
        logging.getLogger("panse.taobao_import").warning("订单消失检测失败", exc_info=True)

    # 导入差异日报 (用户拍板): 有实际写入才推, 一眼看到今天发生了什么
    if rep.inserted or rep.updated:
        try:
            from app.services import notify_service
            vanish_line = (f"\n⚠ 有 {rep.vanished} 单在库里有但新文件里消失了, "
                           "已报异常等你确认是否删除 (异常中心 → import_missing)。"
                           if rep.vanished else "")
            notify_service.notify(
                db,
                (f"订单重导完成: 解析 {len(orders)} 单 — 新增 {rep.inserted}, "
                 f"更新 {rep.updated} (状态变更 {rep.status_changed}, 金额变更 {rep.amount_changed}), "
                 f"重复 {rep.skipped_duplicate}, 无效 {rep.skipped_invalid}。"
                 "变更明细见 工具→修改档案 (来源筛「导入覆盖」)。" + vanish_line),
                level="warning" if rep.vanished else "info",
                title="畔色 ERP · 订单导入日报",
            )
        except Exception:  # pragma: no cover
            pass
    return rep
