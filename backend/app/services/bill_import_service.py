"""万师傅安装账单 / 物流费账单 / 售后表 / 推广记录 / 补单对账 / 账户余额 CSV 导入。

每个导入函数都容错: 关键字段缺失的行跳过, 其余继续; 支持 UTF-8-BOM 与 GBK。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, LogisticsBill, RefillRecord, WanshifuBill
from app.models.marketing import AfterSales, PromotionFlow

_WANSHIFU_MAP = {
    "日期": "bill_date", "账单日期": "bill_date", "结算日期": "bill_date",
    "订单号": "order_no", "关联订单号": "order_no", "平台订单号": "order_no",
    "服务类型": "service_type", "类型": "service_type",
    "金额": "amount", "扣款金额": "amount", "结算金额": "amount", "费用": "amount",
    "状态": "status", "结算状态": "status",
    "备注": "remark",
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
                "%Y/%m/%d %H:%M:%S", "%Y年%m月%d日", "%Y年%m月%d号"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


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


def import_wanshifu_csv(db: Session, text: str, *, import_job_id: Optional[int] = None) -> BillImportReport:
    """导入万师傅安装账单。金额缺失 / 无法解析的行跳过。"""
    rep = BillImportReport()
    for i, rec in enumerate(_rows(text, _WANSHIFU_MAP), start=2):
        amount = _decimal(rec.get("amount"))
        if amount is None:
            rep.skipped_invalid += 1
            continue
        db.add(WanshifuBill(
            bill_date=_date(rec.get("bill_date")),
            order_no=(rec.get("order_no") or None),
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
    """导入物流费月结账单。运费缺失 / 无法解析的行跳过。"""
    rep = BillImportReport()
    for rec in _rows(text, _LOGISTICS_MAP):
        freight = _decimal(rec.get("freight_amount"))
        if freight is None:
            rep.skipped_invalid += 1
            continue
        db.add(LogisticsBill(
            bill_date=_date(rec.get("bill_date")),
            carrier=(rec.get("carrier") or None),
            tracking_no=(rec.get("tracking_no") or None),
            order_no=(rec.get("order_no") or None),
            weight_kg=_decimal(rec.get("weight_kg")),
            freight_amount=freight,
            remark=(rec.get("remark") or None),
            import_job_id=import_job_id,
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
    """导入推广记录 CSV（直通车/万相台充值+支出）。金额缺失则跳过。"""
    rep = BillImportReport()
    for rec in _rows(text, _PROMO_MAP):
        amount = _decimal(rec.get("amount"))
        if amount is None:
            rep.skipped_invalid += 1
            continue
        db.add(PromotionFlow(
            transaction_date=_date(rec.get("transaction_date")),
            flow_type=rec.get("flow_type") or None,
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
        order_no = (rec.get("order_no") or "").strip()
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
