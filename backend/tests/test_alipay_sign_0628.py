# -*- coding: utf-8 -*-
"""支付宝导入符号归一 (2026-06-28)。

部分账户/格式(如爱群号手填表)的『收支金额』恒为正数, 收支方向写在单独的『交易类型』列
(收入/支出)。导入时若原样入库, 支出会被存成正数 → 污染 amount<0/>0 的全部下游。
本测试锁定: 导入按『交易类型』定符号(支出→负, 收入→正), 且对已带符号的源幂等。
"""
import io
from decimal import Decimal

import openpyxl
from sqlalchemy import select

from app.models.finance import AlipayFlow
from app.services import excel_importer
from app.services.excel_importer import _signed_alipay_amount


def test_signed_alipay_amount_unit():
    # 明确方向 → 按交易类型定符号
    assert _signed_alipay_amount(Decimal("100"), "支出") == Decimal("-100")
    assert _signed_alipay_amount(Decimal("100"), "收入") == Decimal("100")
    # 幂等: 源已带负 + 支出 → 不翻回正
    assert _signed_alipay_amount(Decimal("-100"), "支出") == Decimal("-100")
    # 纠正: 收入但源是负 → 转正
    assert _signed_alipay_amount(Decimal("-100"), "收入") == Decimal("100")
    # 无方向词 → 原样信任源符号 (标准导出支出本就带负)
    assert _signed_alipay_amount(Decimal("-50"), "在线支付") == Decimal("-50")
    assert _signed_alipay_amount(Decimal("50"), "转账") == Decimal("50")
    assert _signed_alipay_amount(Decimal("50"), None) == Decimal("50")
    assert _signed_alipay_amount(Decimal("50"), "") == Decimal("50")
    assert _signed_alipay_amount(None, "支出") is None


def _build_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["交易流水号", "交易类型", "收支金额", "备注", "平台订单号"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_commit_applies_sign_from_transaction_type(db_session):
    db = db_session
    fb = _build_xlsx([
        ["TX_EXP_1", "支出", 579.97, "PDD岩板-mickey", "5111225679759002307"],
        ["TX_INC_1", "收入", 0.05, "客户回款", ""],
    ])
    mapping = {
        "transaction_no": "交易流水号",
        "transaction_type": "交易类型",
        "amount": "收支金额",
        "remark": "备注",
        "platform_order_no": "平台订单号",
    }
    report = excel_importer.commit_sheet(
        db, file_bytes=fb, sheet_name="Sheet1", entity_type="alipay_flow",
        mapping=mapping, sheet_account="爱群号",
    )
    db.commit()
    assert report.inserted_parents == 2, report.errors
    flows = {f.transaction_no: f
             for f in db.execute(select(AlipayFlow)).scalars().all()}
    assert flows["TX_EXP_1"].amount == Decimal("-579.97")  # 支出 → 负
    assert flows["TX_INC_1"].amount == Decimal("0.05")      # 收入 → 正


def test_commit_trusts_source_sign_when_no_direction_type(db_session):
    """无『收入/支出』方向词时, 信任源符号 (标准支付宝导出: 支出已带负)。"""
    db = db_session
    fb = _build_xlsx([
        ["TX_A", "在线支付", -35.15, "服务费", ""],
        ["TX_B", "分账", 127.00, "货款", ""],
    ])
    mapping = {
        "transaction_no": "交易流水号",
        "transaction_type": "交易类型",
        "amount": "收支金额",
        "remark": "备注",
        "platform_order_no": "平台订单号",
    }
    report = excel_importer.commit_sheet(
        db, file_bytes=fb, sheet_name="Sheet1", entity_type="alipay_flow",
        mapping=mapping, sheet_account="企业号",
    )
    db.commit()
    assert report.inserted_parents == 2, report.errors
    flows = {f.transaction_no: f
             for f in db.execute(select(AlipayFlow)).scalars().all()}
    assert flows["TX_A"].amount == Decimal("-35.15")  # 原样
    assert flows["TX_B"].amount == Decimal("127.00")  # 原样
