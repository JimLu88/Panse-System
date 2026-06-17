"""万师傅安装账单 / 物流费账单 / 售后表 / 推广记录 / 补单对账 / 账户余额 CSV 导入。

每个导入函数都容错: 关键字段缺失的行跳过, 其余继续; 支持 UTF-8-BOM 与 GBK。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, LogisticsBill, RefillRecord, WanshifuBill
from app.models.marketing import AfterSales, PromotionFlow
from app.models.order import Order
from app.services import import_clean

_WANSHIFU_MAP = {
    "日期": "bill_date", "账单日期": "bill_date", "结算日期": "bill_date",
    "订单号": "order_no", "关联订单号": "order_no", "平台订单号": "order_no",
    "服务类型": "service_type", "类型": "service_type",
    "金额": "amount", "扣款金额": "amount", "结算金额": "amount", "费用": "amount",
    "状态": "status", "结算状态": "status",
    "备注": "remark", "常用备注": "remark", "客户备注": "remark", "订单备注": "remark",
}

_LOGISTICS_MAP = {
    "日期": "bill_date", "账单日期": "bill_date",
    "承运商": "carrier", "物流公司": "carrier", "快递公司": "carrier",
    "运单号": "tracking_no", "快递单号": "tracking_no", "物流单号": "tracking_no",
    "订单号": "order_no", "关联订单号": "order_no",
    "重量": "weight_kg", "重量(kg)": "weight_kg", "重量（kg）": "weight_kg",
    "运费": "freight_amount", "费用": "freight_amount", "金额": "freight_amount",
    "备注": "remark",
}


@dataclass
class BillImportReport:
    inserted: int = 0
    skipped_invalid: int = 0
    skipped_duplicate: int = 0
    unmapped_columns: list[str] = field(default_factory=list)  # 未识别(被丢弃)的表头, 提示用户
    errors: list[str] = field(default_factory=list)


def _decimal(v: Any) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(v: Any) -> Optional[date]:
    if v is None or str(v).strip() == "":
        return None
    # Excel/openpyxl 可能直接给 date/datetime 对象
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y年%m月%d日", "%Y年%m月%d号"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # 纯"月日" (如 "6月1日"/"06-01"): 按当年补全 (用户拍板 2026-06-11 统一日期解析器)
    for fmt in ("%m月%d日", "%m月%d号", "%m-%d", "%m/%d"):
        try:
            d = datetime.strptime(s, fmt)
            return date(date.today().year, d.month, d.day)
        except ValueError:
            continue
    # Excel 日期序列号 (46175 → 2026-06-08): Excel 转存 CSV 常见 (C14)
    return import_clean.excel_serial_to_date(s)


def _rows(text: str, colmap: dict) -> list[dict]:
    reader = csv.DictReader(StringIO(text))
    out = []
    for raw in reader:
        rec: dict[str, Any] = {}
        for k, v in raw.items():
            field_name = colmap.get((k or "").strip())
            if field_name:
                rec[field_name] = v
        out.append(rec)
    return out


def _unmapped_headers(text: str, colmap: dict) -> list[str]:
    """返回表头里没被 colmap 识别(因而被丢弃)的列名, 用于兜底提示用户。"""
    reader = csv.DictReader(StringIO(text))
    return [h for h in (reader.fieldnames or []) if h and (h or "").strip() and (h or "").strip() not in colmap]


def import_wanshifu_csv(db: Session, text: str, *, import_job_id: Optional[int] = None) -> BillImportReport:
    """导入万师傅安装账单。金额缺失跳过; 同 (账单日期, 订单号, 金额) 重复跳过 (防重复导入翻倍)。"""
    from sqlalchemy import select
    rep = BillImportReport()
    rep.unmapped_columns = _unmapped_headers(text, _WANSHIFU_MAP)
    existing = {
        (d, o, a) for d, o, a in db.execute(
            select(WanshifuBill.bill_date, WanshifuBill.order_no, WanshifuBill.amount)
        ).all()
    }
    seen: set = set()
    for rec in _rows(text, _WANSHIFU_MAP):
        amount = _decimal(rec.get("amount"))
        if amount is None:
            rep.skipped_invalid += 1
            continue
        bill_date = _date(rec.get("bill_date"))
        order_no = import_clean.clean_no(rec.get("order_no"))
        # 关联订单在「备注/常用备注」里 (用户拍板 2026-06-12): 订单号列空时, 从备注抽取
        # 淘宝订单号(15-19位数字)补成 order_no, 供安装费/售后对账按单匹配。只补不覆盖。
        if not order_no:
            import re as _re
            _m = _re.search(r"\d{15,19}", str(rec.get("remark") or ""))
            if _m:
                order_no = import_clean.clean_no(_m.group(0))
        key = (bill_date, order_no, amount)
        if key in existing or key in seen:
            rep.skipped_duplicate += 1
            continue
        seen.add(key)
        db.add(WanshifuBill(
            bill_date=bill_date,
            order_no=order_no,
            service_type=(rec.get("service_type") or None),
            amount=amount,
            status=(rec.get("status") or None),
            remark=(rec.get("remark") or None),
            import_job_id=import_job_id,
        ))
        rep.inserted += 1
    db.flush()
    return rep


def import_logistics_csv(db: Session, text: str, *, import_job_id: Optional[int] = None) -> BillImportReport:
    """导入物流费月结账单。运费缺失跳过; 有运单号按 (日期,运单号) 去重, 否则按 (日期,承运商,运费) 去重。"""
    from sqlalchemy import select
    rep = BillImportReport()
    rep.unmapped_columns = _unmapped_headers(text, _LOGISTICS_MAP)

    def _key(bill_date, tracking_no, carrier, freight):
        if tracking_no:
            return ("t", bill_date, tracking_no)
        return ("f", bill_date, carrier, freight)

    existing = {
        _key(d, t, c, f) for d, t, c, f in db.execute(
            select(LogisticsBill.bill_date, LogisticsBill.tracking_no,
                   LogisticsBill.carrier, LogisticsBill.freight_amount)
        ).all()
    }
    seen: set = set()
    for rec in _rows(text, _LOGISTICS_MAP):
        freight = _decimal(rec.get("freight_amount"))
        if freight is None:
            rep.skipped_invalid += 1
            continue
        bill_date = _date(rec.get("bill_date"))
        tracking_no = import_clean.clean_no(rec.get("tracking_no"))
        carrier = (rec.get("carrier") or None)
        key = _key(bill_date, tracking_no, carrier, freight)
        if key in existing or key in seen:
            rep.skipped_duplicate += 1
            continue
        seen.add(key)
        db.add(LogisticsBill(
            bill_date=bill_date,
            carrier=carrier,
            tracking_no=tracking_no,
            order_no=import_clean.clean_no(rec.get("order_no")),
            weight_kg=_decimal(rec.get("weight_kg")),
            freight_amount=freight,
            remark=(rec.get("remark") or None),
            import_job_id=import_job_id,
        ))
        rep.inserted += 1
    db.flush()
    return rep


def parse_logi_bill_filename(name: str):
    """物流账单文件名 → (年, 月, 月结总额)。
    '李爱群 2026年1月账单 14540元'→(2026,1,14540); '程卫燕 德邦 3月'→(当年,3,None)。"""
    import re
    name = name or ""
    mo = re.search(r"(\d{1,2})\s*月", name)
    month = int(mo.group(1)) if mo else None
    mt = re.search(r"(\d{3,7})\s*元", name)
    total = _decimal(mt.group(1)) if mt else None
    ym = re.search(r"(20\d{2})\s*年", name)
    year = int(ym.group(1)) if ym else date.today().year
    return year, month, total


def import_logistics_xlsx(db: Session, wb, *, source_name: str = "",
                          import_job_id: Optional[int] = None) -> BillImportReport:
    """物流账单 xlsx 统一导入, 按文件名自动识别承运商 (用户 2026-06-15):
       - 文件名含「德邦」: 逐运单(运单号 + 实收运费/运费)按行入 LogisticsBill。
       - 否则(壹米滴答 / 李爱群月结): 无逐单运费, 月结总额在文件名 → 入 1 条汇总行。
    去重靠 sync_key (重导幂等)。"""
    from sqlalchemy import select
    import calendar
    rep = BillImportReport()
    ws = wb[wb.sheetnames[0]]
    rows = [r for r in ws.iter_rows(values_only=True)
            if r and any(c is not None and str(c).strip() for c in r)]
    if not rows:
        rep.errors.append(f"{source_name}: 空表")
        return rep
    hdr = [str(c or "").strip() for c in rows[0]]
    body = rows[1:]

    def col(*names):
        for nm in names:
            for i, h in enumerate(hdr):
                if h == nm or nm in h:
                    return i
        return None

    year, month, fname_total = parse_logi_bill_filename(source_name)
    month_end = date(year, month, calendar.monthrange(year, month)[1]) if month else None
    existing = {k for (k,) in db.execute(
        select(LogisticsBill.sync_key).where(LogisticsBill.sync_key.isnot(None))).all()}
    seen: set = set()
    c_track = col("运单号")

    if "德邦" in (source_name or ""):
        c_fee = col("实收运费", "运费")
        c_date = col("业务日期", "寄件时间", "日期")
        c_wt = col("计费重量")
        c_to = col("收货人", "收件人姓名")
        c_dest = col("目的地", "目的站", "收件人省市区")
        if c_track is None or c_fee is None:
            rep.errors.append(f"{source_name}: 德邦表缺『运单号』或『运费』列")
            return rep
        for r in body:
            track = import_clean.clean_no(r[c_track])
            fee = _decimal(r[c_fee])
            if not track or fee is None:
                rep.skipped_invalid += 1
                continue
            bdate = (_date(r[c_date]) if c_date is not None else None) or month_end
            to = str(r[c_to]).strip() if (c_to is not None and r[c_to]) else ""
            dest = str(r[c_dest]).strip() if (c_dest is not None and r[c_dest]) else ""
            sk = f"logi|德邦|{bdate}|{track}|{fee}|{to}"
            if sk in existing or sk in seen:
                rep.skipped_duplicate += 1
                continue
            seen.add(sk)
            db.add(LogisticsBill(
                bill_date=bdate, carrier="德邦", tracking_no=track, order_no=None,
                weight_kg=(_decimal(r[c_wt]) if c_wt is not None else None),
                freight_amount=fee, remark=f"德邦 收货:{to} 目的:{dest}".strip(),
                import_job_id=import_job_id, sync_key=sk,
            ))
            rep.inserted += 1
    else:
        if fname_total is None:
            rep.errors.append(f"{source_name}: 壹米滴答月结需在文件名给总额(如「…账单 14540元」)")
            return rep
        cnt = sum(1 for r in body if c_track is not None and r[c_track])
        sk = f"logi|壹米滴答|{year}-{month or 0:02d}|summary"
        if sk in existing:
            rep.skipped_duplicate += 1
        else:
            db.add(LogisticsBill(
                bill_date=month_end, carrier="壹米滴答", tracking_no=None, order_no=None,
                weight_kg=None, freight_amount=fname_total,
                remark=f"壹米滴答 {year}年{month}月月结汇总, 共{cnt}单(逐单运费未单独提供, 总额取自账单)",
                import_job_id=import_job_id, sync_key=sk,
            ))
            rep.inserted += 1
    db.flush()
    return rep


# --------------------- 补单表 xlsx (发中介的简表) -------------------- #
# 用户确认格式 (2026-06-11, 例 "5.31畔色.xlsx"):
#   订单号 | 旺旺（淘宝账号非昵称）/JD填写账户 | 金额（不要加佣金） | (空表头=佣金) | 店铺名字
# 补单日期从文件名解析 (5.31 → 当年 5月31日); 解析不出用今天。

def refill_date_from_filename(name: str, *, today=None):
    import re
    from datetime import date as date_cls
    t = today or date_cls.today()
    m = re.search(r"(\d{1,2})[.\-月](\d{1,2})", name or "")
    if m:
        try:
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return date_cls(t.year, mo, d)
        except ValueError:
            pass
    return t


# ---------------- 补单流水明细 (逐单, 用户「补单记录.xlsx」下半部分主数据) ---------- #
# 真表头(约第45行): 支付时间|补单团队|订单号|买家昵称|打款日期|打款金额|是否回款|回款金额|
#   check|补单佣金|补单快递费|平台服务费|88vip消费券技术服务费|备注。一行 = 一个补单(刷单)订单。
_ORDER_NO_RE = re.compile(r"^\d{6,}$")


def _find_refill_detail_header(ws) -> Optional[tuple[int, dict]]:
    """找「补单流水明细」逐单表头(含 订单号 + 打款金额), 返回 (行号1-indexed, {字段:列}); 无则 None。

    文件上半部是月度汇总(分月核算/补单流水), 下半部才是逐单明细 — 逐单是主数据。
    """
    limit = min(ws.max_row or 60, 80)
    for ridx in range(1, limit + 1):
        cells = [str(c.value).strip() if c.value is not None else "" for c in ws[ridx]]
        if "订单号" not in cells:
            continue
        joined = "".join(cells)
        if "打款金额" not in joined and "金额" not in joined:
            continue
        col: dict = {}
        for i, h in enumerate(cells):
            if h == "订单号" or h.endswith("订单号"):
                col.setdefault("order_no", i)
            elif "买家" in h:
                col["buyer_nick"] = i
            elif "支付时间" in h or h == "打款日期":
                col.setdefault("date", i)
            elif "打款金额" in h or h == "金额":
                col["amount"] = i
            elif "佣金" in h:
                col["commission"] = i
            elif "快递" in h or "运费" in h:
                col["freight"] = i
            elif "平台服务费" in h:
                col["platform_fee"] = i
            elif "技术服务费" in h or "88vip" in h.lower():
                col["tech_fee"] = i
            elif "团队" in h:
                col["team"] = i
            elif h == "备注":
                col["remark"] = i
        if "order_no" in col and "amount" in col:
            return ridx, col
    return None


def is_refill_detail_xlsx(wb) -> bool:
    try:
        return _find_refill_detail_header(wb.worksheets[0]) is not None
    except (IndexError, AttributeError):
        return False


def import_refill_detail_xlsx(db: Session, wb, *, ws=None,
                              import_job_id: Optional[int] = None) -> BillImportReport:
    """补单流水明细(逐单) → 每个补单订单一条 RefillRecord (order_no = 真实淘宝订单号)。

    幂等: 按 order_no upsert(重导覆盖, 不堆积)。导入后把命中订单标 is_refill=True 并
    rederive_refill_flags(双向重判 + 重算成本) —— 这一步会让这些刷单订单不再当真实销售,
    其「缺成本」异常也随之自动消解(成本兜底跳过补单)。answers「导入后自动关联相关异常」。
    """
    from sqlalchemy import select
    rep = BillImportReport()
    ws = ws if ws is not None else wb.worksheets[0]
    found = _find_refill_detail_header(ws)
    if found is None:
        rep.errors.append("未找到补单流水明细表头(订单号+打款金额)")
        return rep
    hdr, col = found
    # 清掉旧的月度合成行(补单月度-YYYY-MM): 逐单明细是更细的真值, 月度汇总会与之重复双算佣金/快递
    for stale in db.execute(
        select(RefillRecord).where(RefillRecord.order_no.like("补单月度-%"))
    ).scalars().all():
        db.delete(stale)
    # 清掉 2025 及以前的补单记录 (系统从 2026 起算, 用户拍板 2026-06-17): 它们的订单不在系统、徒增异常
    for old in db.execute(
        select(RefillRecord).where(RefillRecord.refill_date < date(2026, 1, 1))
    ).scalars().all():
        db.delete(old)
    db.flush()
    existing = {r.order_no: r for r in db.execute(select(RefillRecord)).scalars().all()}

    def _c(row, key):
        i = col.get(key)
        return row[i] if i is not None and i < len(row) else None

    flagged = 0
    for row in ws.iter_rows(min_row=hdr + 1, values_only=True):
        if not row:
            continue
        order_no = import_clean.clean_no(_c(row, "order_no"))
        if not order_no or not _ORDER_NO_RE.match(order_no):
            continue  # 跳过空行/小计/"*标红色订单被查"等非订单行
        # 自动排除 2025 及以前 (用户拍板 2026-06-17: 系统从 2026 起算, 旧补单不导, 去掉大批未匹配异常)
        _rd = _date(_c(row, "date"))
        if _rd is not None and _rd < date(2026, 1, 1):
            rep.skipped_invalid += 1
            continue
        rec = existing.get(order_no)
        if rec is None:
            rec = RefillRecord(order_no=order_no)
            db.add(rec)
            existing[order_no] = rec
            rep.inserted += 1
        else:
            rep.skipped_duplicate += 1  # 已存在 → 覆盖更新
        nick = _c(row, "buyer_nick")
        rec.buyer_nick = str(nick).strip() if nick is not None and str(nick).strip() else rec.buyer_nick
        d = _date(_c(row, "date"))
        if d is not None:
            rec.refill_date = d
        amount = _decimal(_c(row, "amount"))
        if amount is not None:
            rec.order_amount = amount
        rec.commission = _decimal(_c(row, "commission"))
        rec.refill_freight = _decimal(_c(row, "freight"))
        rec.platform_fee = _decimal(_c(row, "platform_fee"))
        team = _c(row, "team")
        tech = _decimal(_c(row, "tech_fee"))
        bits = []
        if team is not None and str(team).strip():
            bits.append(f"团队:{str(team).strip()}")
        if tech is not None and tech != 0:
            bits.append(f"88vip技术服务费:{tech}")
        rec.fee_remark = "; ".join(bits) if bits else None
        rmk = _c(row, "remark")
        rec.remark = str(rmk).strip() if rmk is not None and str(rmk).strip() else None
        # 命中订单 → 标补单 (不当真实销售; 其缺成本异常随后自动消解)
        o = db.query(Order).filter(Order.order_no == order_no).first()
        if o is not None and not o.is_refill:
            o.is_refill = True
            flagged += 1
    db.flush()
    # L3 闭环: 双向重判 is_refill + 重算成本 (与 CSV 路径一致, 自动关联异常)
    if rep.inserted or rep.skipped_duplicate:
        try:
            from app.services import order_sync_service
            order_sync_service.rederive_refill_flags(db)
        except Exception:  # pragma: no cover - 兜底不阻断导入
            pass
    return rep


# ---------------- 月度补单汇总表 (用户 2026-06 起统一格式, 无订单号) -------------- #
# 用户的「补单记录.xlsx」: 一行一个月, 列= 分月核算 | 补单流水 | 补单佣金 | 补单快递费 |
#   平台服务费 | 88vip消费券技术服务费。没有订单号, 旧的逐单导入器会报「缺订单号」。
_MONTH_LABEL_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
_MONTHLY_SKIP_LABELS = ("total", "合计", "总计", "小计")


def _find_monthly_refill_header(ws) -> Optional[tuple[int, dict]]:
    """在前 6 行找月度补单汇总表头, 返回 (表头行号 1-indexed, {字段: 列号}); 对不上 None。"""
    for ridx in range(1, 7):
        cells = [str(c.value).strip() if c.value is not None else "" for c in ws[ridx]]
        if "补单流水" not in "".join(cells):
            continue
        col: dict = {}
        for i, h in enumerate(cells):
            if "分月核算" in h or h in ("月份", "月度"):
                col.setdefault("month", i)
            elif "补单流水" in h:
                col["amount"] = i
            elif "佣金" in h:
                col["commission"] = i
            elif "快递" in h or "运费" in h:
                col["freight"] = i
            elif "平台服务费" in h:
                col["platform_fee"] = i
            elif "技术服务费" in h or "88vip" in h.lower():
                col["tech_fee"] = i
        if "month" in col and "amount" in col:
            return ridx, col
    return None


def is_monthly_refill_ws(ws) -> bool:
    """判断单个 worksheet 是否为「月度补单汇总」格式。"""
    return _find_monthly_refill_header(ws) is not None


def is_monthly_refill_xlsx(wb) -> bool:
    """判断 wb 第一个 sheet 是否为「月度补单汇总」格式。"""
    try:
        return is_monthly_refill_ws(wb.worksheets[0])
    except (IndexError, AttributeError):
        return False


def import_refill_monthly_xlsx(db: Session, wb, *, ws=None,
                               import_job_id: Optional[int] = None) -> BillImportReport:
    """月度补单汇总表 → 每月一条合成 RefillRecord (order_no='补单月度-YYYY-MM')。

    幂等 upsert: 同月已有则覆盖各项金额 (用户每月重导整表也不会重复堆积)。
    跳过 Total/合计/<日期 等汇总边界行 + 整行无任何流水/费用的空月份。
    88vip 技术服务费无独立列, 记入 fee_remark。
    """
    from sqlalchemy import select
    rep = BillImportReport()
    ws = ws if ws is not None else wb.worksheets[0]
    found = _find_monthly_refill_header(ws)
    if found is None:
        rep.errors.append("未识别为月度补单汇总表 (缺「分月核算/补单流水」表头)")
        return rep
    header_row, col = found

    existing = {
        r.order_no: r for r in db.execute(
            select(RefillRecord).where(RefillRecord.order_no.like("补单月度-%"))
        ).scalars().all()
    }

    def _cell(row, key):
        i = col.get(key)
        return row[i] if i is not None and i < len(row) else None

    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if not row:
            continue
        label = str(_cell(row, "month") or "").strip()
        if not label or "<" in label or any(s in label.lower() for s in _MONTHLY_SKIP_LABELS):
            continue
        m = _MONTH_LABEL_RE.search(label)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12):
            continue
        amount = _decimal(_cell(row, "amount"))
        commission = _decimal(_cell(row, "commission"))
        freight = _decimal(_cell(row, "freight"))
        platform_fee = _decimal(_cell(row, "platform_fee"))
        tech_fee = _decimal(_cell(row, "tech_fee"))
        if not any(v and v != 0 for v in (amount, commission, freight, platform_fee, tech_fee)):
            continue  # 未来空月份
        order_no = f"补单月度-{year:04d}-{month:02d}"
        rec = existing.get(order_no)
        if rec is None:
            rec = RefillRecord(order_no=order_no)
            db.add(rec)
            existing[order_no] = rec
        rec.refill_date = date(year, month, 1)
        rec.order_amount = amount
        rec.commission = commission
        rec.refill_freight = freight
        rec.platform_fee = platform_fee
        rec.fee_remark = f"88vip技术服务费:{tech_fee}" if tech_fee else None
        rec.remark = "月度补单汇总(整月合计, 非单条)"
        rep.inserted += 1
    db.flush()
    return rep


def import_refill_simple_xlsx(db: Session, wb, *, refill_date,
                              freight_default=None,
                              import_job_id: Optional[int] = None) -> BillImportReport:
    """补单简表 xlsx → RefillRecord。去重: (订单号, 补单日期) 已有则跳过。

    freight_default: 补单快递费缺省 (用户拍板 ¥5, settings refill_freight_default 可调)。

    2026-06 起用户改用「月度补单汇总表」(分月核算/补单流水, 无订单号) — 自动识别并转月度路径。
    """
    from sqlalchemy import select
    # 补单流水明细(逐单, 真实订单号) → 优先走逐单路径 (用户「补单记录.xlsx」主数据在下半部)
    if is_refill_detail_xlsx(wb):
        return import_refill_detail_xlsx(db, wb, import_job_id=import_job_id)
    # 月度补单汇总表(无逐单明细时) → 月度合成路径
    if is_monthly_refill_xlsx(wb):
        return import_refill_monthly_xlsx(db, wb, import_job_id=import_job_id)
    rep = BillImportReport()
    ws = wb.worksheets[0]
    headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
    col = {"order_no": None, "buyer_nick": None, "amount": None,
           "commission": None, "shop": None}
    for i, h in enumerate(headers):
        if h.startswith("订单号"):
            col["order_no"] = i
        elif "旺旺" in h or "账号" in h:
            col["buyer_nick"] = i
        elif h.startswith("金额"):   # 先于佣金判断 — "金额（不要加佣金）"也含"佣金"二字
            col["amount"] = i
        elif "佣金" in h:   # 用户确认: 现在是空表头, 未来会填上"补单佣金"
            col["commission"] = i
        elif "店铺" in h:
            col["shop"] = i
    if col["order_no"] is None or col["amount"] is None:
        rep.errors.append("表头缺「订单号/金额」列, 请确认是补单简表格式")
        return rep
    # 佣金列 = 金额列右边第一个无表头列 (用户表里该列表头为空)
    if col["amount"] is not None and col["amount"] + 1 < len(headers) \
            and not headers[col["amount"] + 1]:
        col["commission"] = col["amount"] + 1

    existing = {
        (no, d) for no, d in db.execute(
            select(RefillRecord.order_no, RefillRecord.refill_date)
        ).all()
    }
    seen: set = set()
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r:
            continue
        order_no = import_clean.clean_no(r[col["order_no"]] if col["order_no"] < len(r) else None)
        if not order_no:
            continue
        amount = _decimal(r[col["amount"]] if col["amount"] < len(r) else None)
        if amount is None:
            rep.skipped_invalid += 1
            continue
        key = (order_no, refill_date)
        if key in existing or key in seen:
            rep.skipped_duplicate += 1
            continue
        seen.add(key)
        commission = (_decimal(r[col["commission"]])
                      if col["commission"] is not None and col["commission"] < len(r) else None)
        nick = (str(r[col["buyer_nick"]]).strip()
                if col["buyer_nick"] is not None and col["buyer_nick"] < len(r)
                and r[col["buyer_nick"]] is not None else None)
        shop = (str(r[col["shop"]]).strip()
                if col["shop"] is not None and col["shop"] < len(r)
                and r[col["shop"]] is not None else None)
        db.add(RefillRecord(
            order_no=order_no,
            buyer_nick=nick,
            refill_date=refill_date,
            order_amount=amount,          # 金额（不要加佣金）= 本金
            commission=commission,
            refill_freight=freight_default,   # 快递费缺省 ¥5 (设置可调)
            fee_remark=f"店铺:{shop}" if shop else None,
        ))
        rep.inserted += 1
    db.flush()
    return rep


# ----------------------------- 售后表 CSV -------------------------- #

_AFTERSALES_MAP = {
    "订单号": "platform_order_no", "平台订单号": "platform_order_no",
    "原因": "reason", "售后原因": "reason",
    "赔付费": "compensation_fee", "订单赔付": "compensation_fee",
    "好评返": "good_review_refund", "好评/差价返": "good_review_refund",
    "平台内总": "in_platform_total", "平台内售后总": "in_platform_total",
    "直接赔付": "direct_compensation", "赔付客户": "direct_compensation",
    "二次上门": "second_visit_fee", "二次维修费": "second_visit_fee",
    "返厂运费": "return_pack_freight", "返厂打包运费": "return_pack_freight",
    "平台外总": "out_platform_total", "平台外售后总": "out_platform_total",
    "补发SKU": "refill_sku",
    "补发运单": "refill_tracking_no", "补发单号": "refill_tracking_no",
    "补发运费": "refill_freight",
    "万师傅扣款": "wanshifu_deduction", "安装扣款": "wanshifu_deduction",
    "工厂赔付": "factory_compensation",
    "物流赔偿": "logistics_compensation",
    "支付宝流水": "alipay_flow_no", "流水号": "alipay_flow_no",
    "处理日期": "processed_at", "完结日期": "processed_at",
    "状态": "status",
    "备注": "remark",
}


def import_aftersales_csv(db: Session, text: str) -> BillImportReport:
    """导入售后表 CSV。platform_order_no 为必填, 缺失则跳过。"""
    rep = BillImportReport()
    for rec in _rows(text, _AFTERSALES_MAP):
        order_no = (rec.get("platform_order_no") or "").strip()
        if not order_no:
            rep.skipped_invalid += 1
            continue
        db.add(AfterSales(
            platform_order_no=order_no,
            reason=rec.get("reason") or None,
            compensation_fee=_decimal(rec.get("compensation_fee")),
            good_review_refund=_decimal(rec.get("good_review_refund")),
            in_platform_total=_decimal(rec.get("in_platform_total")),
            direct_compensation=_decimal(rec.get("direct_compensation")),
            second_visit_fee=_decimal(rec.get("second_visit_fee")),
            return_pack_freight=_decimal(rec.get("return_pack_freight")),
            out_platform_total=_decimal(rec.get("out_platform_total")),
            refill_sku=rec.get("refill_sku") or None,
            refill_tracking_no=rec.get("refill_tracking_no") or None,
            refill_freight=_decimal(rec.get("refill_freight")),
            wanshifu_deduction=_decimal(rec.get("wanshifu_deduction")),
            factory_compensation=_decimal(rec.get("factory_compensation")),
            logistics_compensation=_decimal(rec.get("logistics_compensation")),
            alipay_flow_no=rec.get("alipay_flow_no") or None,
            processed_at=_date(rec.get("processed_at")),
            status=rec.get("status") or None,
            remark=rec.get("remark") or None,
        ))
        rep.inserted += 1
    db.flush()
    return rep


# ----------------------------- 推广记录 CSV ----------------------- #

_PROMO_MAP = {
    "日期": "transaction_date", "交易日期": "transaction_date", "充值日期": "transaction_date",
    "类型": "flow_type", "流水类型": "flow_type",
    "金额": "amount", "充值金额": "amount", "费用": "amount",
    "支付宝流水号": "alipay_flow_no", "流水号": "alipay_flow_no",
    "备注": "remark",
}


def import_promotion_flows_csv(db: Session, text: str) -> BillImportReport:
    """导入推广记录 CSV（直通车/万相台充值+支出）。金额缺失跳过; 同 (日期,类型,金额) 重复跳过。"""
    from sqlalchemy import select
    rep = BillImportReport()
    rep.unmapped_columns = _unmapped_headers(text, _PROMO_MAP)
    existing = {
        (d, t, a) for d, t, a in db.execute(
            select(PromotionFlow.transaction_date, PromotionFlow.flow_type, PromotionFlow.amount)
        ).all()
    }
    seen: set = set()
    for rec in _rows(text, _PROMO_MAP):
        amount = _decimal(rec.get("amount"))
        if amount is None:
            rep.skipped_invalid += 1
            continue
        tx_date = _date(rec.get("transaction_date"))
        flow_type = rec.get("flow_type") or None
        key = (tx_date, flow_type, amount)
        if key in existing or key in seen:
            rep.skipped_duplicate += 1
            continue
        seen.add(key)
        db.add(PromotionFlow(
            transaction_date=tx_date,
            flow_type=flow_type,
            amount=amount,
            alipay_flow_no=rec.get("alipay_flow_no") or None,
            remark=rec.get("remark") or None,
        ))
        rep.inserted += 1
    db.flush()
    return rep


# ----------------------------- 补单对账 CSV ----------------------- #

_REFILL_MAP = {
    "订单号": "order_no",
    "买家": "buyer_nick", "买家昵称": "buyer_nick",
    "补单日期": "refill_date", "日期": "refill_date",
    "产品编码": "product_code",
    "产品名": "product_name", "产品名称": "product_name",
    "SKU": "sku",
    "订单金额": "order_amount",
    "数量": "qty",
    "补单成本": "refill_cost",
    "补发运费": "refill_freight", "运费": "refill_freight",
    "平台费": "platform_fee",
    "佣金": "commission",
    "总成本": "total_cost",
}


def import_refill_records_csv(db: Session, text: str) -> BillImportReport:
    """导入补单对账 CSV。order_no 必填。"""
    rep = BillImportReport()
    for rec in _rows(text, _REFILL_MAP):
        order_no = import_clean.clean_no(rec.get("order_no")) or ""
        if not order_no:
            rep.skipped_invalid += 1
            continue
        try:
            qty = int(rec.get("qty") or 1)
        except (ValueError, TypeError):
            qty = 1
        db.add(RefillRecord(
            order_no=order_no,
            buyer_nick=rec.get("buyer_nick") or None,
            refill_date=_date(rec.get("refill_date")),
            product_code=rec.get("product_code") or None,
            product_name=rec.get("product_name") or None,
            sku=rec.get("sku") or None,
            order_amount=_decimal(rec.get("order_amount")),
            qty=qty,
            refill_cost=_decimal(rec.get("refill_cost")),
            refill_freight=_decimal(rec.get("refill_freight")),
            platform_fee=_decimal(rec.get("platform_fee")),
            commission=_decimal(rec.get("commission")),
            total_cost=_decimal(rec.get("total_cost")),
        ))
        rep.inserted += 1
        # 补单对账里出现的订单号 → 同步把订单表标成 is_refill, 否则报表会把它当"真实订单"、少算补单。
        o = db.query(Order).filter(Order.order_no == order_no).first()
        if o is not None and not o.is_refill:
            o.is_refill = True
    db.flush()
    # L3 闭环: 导入完成后全量重判 is_refill (双向: 补单消失也会摘标) + 重算成本
    if rep.inserted:
        try:
            from app.services import order_sync_service
            order_sync_service.rederive_refill_flags(db)
        except Exception:  # pragma: no cover - 兜底不阻断导入
            pass
    return rep


# ----------------------------- 活动报名价 CSV (Plan F1) ---------- #

_CAMPAIGN_MAP = {
    "SKU编码": "sku_code", "SKU": "sku_code", "sku_code": "sku_code",
    "渠道": "channel", "平台": "channel",
    "活动名": "campaign_name", "活动名称": "campaign_name", "活动": "campaign_name",
    "报名价": "signup_price", "活动报名价": "signup_price", "报名价格": "signup_price",
    "生效日期": "effective_date", "活动日期": "effective_date",
    "备注": "remark",
}

_CHANNEL_NORM = {"淘宝": "taobao", "天猫": "taobao", "taobao": "taobao",
                 "小红书": "xhs", "xhs": "xhs", "rn": "xhs"}


def import_campaign_signup_csv(db: Session, text: str) -> BillImportReport:
    """Plan F1: 导入活动报名价 CSV。sku_code+报名价必填; 同 (sku,渠道,活动) upsert 覆盖价格。"""
    from sqlalchemy import select
    from app.models.campaign_signup import CampaignSignupPrice
    rep = BillImportReport()
    rep.unmapped_columns = _unmapped_headers(text, _CAMPAIGN_MAP)
    for rec in _rows(text, _CAMPAIGN_MAP):
        sku = import_clean.clean_no(rec.get("sku_code")) or ""
        price = _decimal(rec.get("signup_price"))
        if not sku or price is None:
            rep.skipped_invalid += 1
            continue
        raw_channel = (rec.get("channel") or "").strip()
        channel = _CHANNEL_NORM.get(raw_channel.lower()) or _CHANNEL_NORM.get(raw_channel) or "taobao"
        campaign = (rec.get("campaign_name") or "").strip() or None
        existing = db.execute(
            select(CampaignSignupPrice).where(
                CampaignSignupPrice.sku_code == sku,
                CampaignSignupPrice.channel == channel,
                CampaignSignupPrice.campaign_name == campaign,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.signup_price = price
            existing.effective_date = _date(rec.get("effective_date")) or existing.effective_date
            existing.remark = (rec.get("remark") or None) or existing.remark
            rep.skipped_duplicate += 1   # 视为更新, 不重复插行
            continue
        db.add(CampaignSignupPrice(
            sku_code=sku, channel=channel, campaign_name=campaign,
            signup_price=price, source="import",
            effective_date=_date(rec.get("effective_date")),
            remark=rec.get("remark") or None,
        ))
        rep.inserted += 1
    db.flush()
    return rep


# ----------------------------- 账户余额 CSV ----------------------- #

_BALANCE_MAP = {
    "账户名称": "account_name", "账户名": "account_name", "账户": "account_name",
    "账户号": "account_no", "账号": "account_no",
    "统计日期": "period_date", "日期": "period_date",
    "年": "period_year",
    "月": "period_month",
    "期初余额": "opening_balance", "月初余额": "opening_balance",
    "本月收入": "income", "收入": "income",
    "本月支出": "expense", "支出": "expense",
    "期末余额": "closing_balance", "月末余额": "closing_balance",
    "备注": "remark",
}


def import_account_balances_csv(db: Session, text: str) -> BillImportReport:
    """导入账户余额 CSV/Excel 行数据 (同账户同月 upsert)。支持统计日期列自动提取年月。"""
    from sqlalchemy import select
    rep = BillImportReport()
    for rec in _rows(text, _BALANCE_MAP):
        account_name = (rec.get("account_name") or "").strip()
        # 统计日期: 这条余额是哪天的快照 (余额常是某天手填的, 新鲜度按它算而非入库时间)
        as_of = _date(rec.get("period_date"))
        year = rec.get("period_year")
        month = rec.get("period_month")
        if as_of and not (year and month):  # 缺年月时从统计日期自动提取
            year, month = as_of.year, as_of.month
        try:
            year = int(year or 0)
            month = int(month or 0)
        except (ValueError, TypeError):
            rep.skipped_invalid += 1
            continue
        if not account_name or not year or not month:
            rep.skipped_invalid += 1
            continue
        existing = db.execute(
            select(AccountBalance).where(
                AccountBalance.account_name == account_name,
                AccountBalance.period_year == year,
                AccountBalance.period_month == month,
            )
        ).scalar_one_or_none()
        row = existing or AccountBalance(
            account_name=account_name, period_year=year, period_month=month,
        )
        if not existing:
            db.add(row)
        if as_of is not None:
            row.as_of_date = as_of
        row.opening_balance = _decimal(rec.get("opening_balance")) or row.opening_balance or Decimal("0")
        row.income = _decimal(rec.get("income")) or row.income or Decimal("0")
        row.expense = _decimal(rec.get("expense")) or row.expense or Decimal("0")
        row.closing_balance = _decimal(rec.get("closing_balance")) or row.closing_balance or Decimal("0")
        if rec.get("account_no") is not None:
            raw_no = rec["account_no"]
            # 手机号/数字账号被 Excel 存成浮点 (如 15384030935.0) → 去 .0
            try:
                row.account_no = str(int(float(str(raw_no)))) if str(raw_no).replace(".", "").isdigit() else str(raw_no).strip()
            except Exception:
                row.account_no = str(raw_no).strip()
        if rec.get("remark"):
            row.remark = rec["remark"]
        rep.inserted += 1
    db.flush()
    return rep
