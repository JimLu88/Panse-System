"""支付宝流水 CSV 导入。

约定列名（支付宝标准导出 + 我们的 Excel 表 9a 都用这些）：
    交易时间 / 交易流水号 / 交易类型 / 交易对象 / 交易账户 / 收支金额 /
    关联订单号 / 余额 / 核销状态 / 核销类型 / 备注
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow

COLUMN_MAP = {
    "交易时间": "transaction_time",
    "交易流水号": "transaction_no",
    "流水号": "transaction_no",
    "交易类型": "transaction_type",
    "交易对象": "counterparty",
    "对方账户名称": "counterparty",
    "交易账户": "counterparty_account",
    "对方账号": "counterparty_account",
    "收支金额": "amount",
    "金额": "amount",
    "关联订单号": "related_order_no",
    "商户订单号": "related_order_no",
    "余额": "balance",
    "核销状态": "reconciliation_status",
    "核销类型": "reconciliation_type",
    "备注": "remark",
}


@dataclass
class AlipayImportReport:
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    errors: list[str] = field(default_factory=list)


def _decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _datetime(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def date_from_flow_no(no: Any) -> Optional[datetime]:
    """支付宝交易流水号前 8 位编码交易日期 (YYYYMMDD): 2026040722001183631429552504 → 2026-04-07。

    企业号等账户导出常缺『交易时间』列, 用流水号前缀兜底反推交易日 (设为当天 00:00)。
    仅在前 8 位是合法日期且年份在 2018~2035 时才认, 否则 None。
    """
    s = str(no or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        d = datetime.strptime(s[:8], "%Y%m%d")
    except ValueError:
        return None
    return d if 2018 <= d.year <= 2035 else None


def import_alipay_csv(db: Session, csv_text: str, *, account: str, commit: bool = True) -> AlipayImportReport:
    # 自动识别格式(同一导入口吃三种): 个人号交易记录明细 / 商家资金账单 / 标准CSV。
    head = csv_text[:2000]
    if ("支付宝交易记录明细查询" in head or ("交易号" in head and "收/支" in head)
            or "电子客户回单" in head
            or ("交易订单号" in head and "收/支" in head)):   # 回单变体(注意"交易号"不是"交易订单号"的子串)
        return import_alipay_personal_csv(db, csv_text, account=account, commit=commit)
    if "账务流水号" in head and "账户余额" in head:
        return import_alipay_bill(db, csv_text, account=account, commit=commit)

    report = AlipayImportReport()
    reader = csv.DictReader(StringIO(csv_text))
    field_map: dict[str, str] = {}
    for raw in reader.fieldnames or []:
        norm = (raw or "").strip()
        if norm in COLUMN_MAP:
            field_map[raw] = COLUMN_MAP[norm]

    if "transaction_no" not in field_map.values():
        report.errors.append("CSV 缺少『交易流水号』列")
        return report
    if "amount" not in field_map.values():
        report.errors.append("CSV 缺少『收支金额』列")
        return report

    rows: list[dict[str, Any]] = []
    for row in reader:
        payload: dict[str, Any] = {}
        for raw, fld in field_map.items():
            payload[fld] = row.get(raw)
        rows.append(payload)
    return import_alipay_rows(db, rows, account=account, report=report, commit=commit)


def import_alipay_bill(
    db: Session, text: str, *, account: str, commit: bool = True,
) -> AlipayImportReport:
    """支付宝商家『对账单』(开放平台 API / PC 下载, signcustomer 资金账单) 导入。

    格式与标准 CSV 不同 (用户 2026-06-12 反馈):
    - 头尾各有若干 `#` 注释行 (账号/起止日期/合计/导出时间);
    - 列头: 账务流水号,业务流水号,商户订单号,商品名称,发生时间,对方账号,
            收入金额(+元),支出金额(-元),账户余额(元),交易渠道,业务类型,备注;
    - 每个字段值带尾随制表符 (防 Excel 截断长数字);
    - 收入/支出分两列 → 合成带符号金额 (收入+ / 支出-)。
    """
    report = AlipayImportReport()
    lines = text.splitlines()
    hdr_idx = None
    for i, ln in enumerate(lines):
        if "账务流水号" in ln and "账户余额" in ln:
            hdr_idx = i
            break
    if hdr_idx is None:
        report.errors.append("非支付宝对账单格式 (未找到『账务流水号…账户余额』列头)")
        return report
    header = [h.strip().strip("\t") for h in lines[hdr_idx].split(",")]

    def col(sub: str) -> Optional[int]:
        for i, h in enumerate(header):
            if sub in h:
                return i
        return None

    ci = {k: col(k) for k in (
        "账务流水号", "业务流水号", "商户订单号", "商品名称", "发生时间",
        "对方账号", "收入金额", "支出金额", "账户余额", "交易渠道", "业务类型", "备注")}
    bal_idx = ci.get("账户余额") or 0
    rows: list[dict[str, Any]] = []
    for ln in lines[hdr_idx + 1:]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        cells = [c.strip().strip("\t").strip() for c in ln.split(",")]
        if len(cells) <= bal_idx:
            continue

        def g(k: str) -> Optional[str]:
            idx = ci.get(k)
            return cells[idx] if idx is not None and idx < len(cells) else None

        income = _decimal(g("收入金额")) or Decimal("0")
        expense = _decimal(g("支出金额")) or Decimal("0")
        # 支出金额列本身带负号 (表头『支出金额(-元)』) → 用 abs 兜底, 兼容正负两种导出;
        # 合成带符号金额: 收入正 / 支出负 (与历史企业号流水 amount 符号一致)。
        amount = income - abs(expense)
        # transaction_no 用『业务流水号』(28/32位) 与历史企业号流水一致, 避免重复计数;
        # 一个业务流水号下的收款+手续费是不同 type/amount, 去重键 (no,type,amount) 不会误判。
        rows.append({
            "transaction_no": g("业务流水号"),
            "transaction_time": g("发生时间"),
            "transaction_type": g("业务类型"),
            "counterparty_account": g("对方账号"),
            "amount": amount,
            "related_order_no": g("商户订单号"),
            "balance": g("账户余额"),
            "remark": " ".join(x for x in (g("商品名称"), g("备注")) if x)[:500] or None,
        })
    return import_alipay_rows(db, rows, account=account, report=report, commit=commit)


def import_alipay_personal_csv(
    db: Session, csv_text: str, *, account: str, commit: bool = True,
) -> AlipayImportReport:
    """支付宝【个人号】交易记录明细 CSV (consumeprod 高级查询下载, 2026-07-06 主力号)。

    结构: 前导 4 行(标题「支付宝交易记录明细查询」/账号/起止日期/分隔线) + 列头
      (交易号,商家订单号,交易创建时间,付款时间,…,类型,交易对方,商品名称,金额（元）,收/支,交易状态,…,备注,资金状态) +
      数据行 + 页脚(已收入/已支出合计/导出时间)。
    金额（元）**无符号**, 由『收/支』定正负: 支出→负 / 收入→正 / 不计收支→正(中性)。无余额列。

    变体「电子客户回单」(2026-07-11): 主力号 App/网页导出的交易明细, 列头以「交易时间」开头
      (交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注),
      交易号列叫「交易订单号」。历史上这格式曾以无符号金额入库致 140 笔支出记成正数
      (2026-07-11 按源表整批翻负修复), 本解析器统一由『收/支』定符号, 治本。
    """
    report = AlipayImportReport()
    lines = csv_text.splitlines()
    hdr_idx = next((i for i, ln in enumerate(lines)
                    if ln.lstrip().startswith("交易号") and "收/支" in ln), None)
    if hdr_idx is None:
        # 电子客户回单变体: 列头以「交易时间」开头, 且有 收/支 + 交易订单号
        hdr_idx = next((i for i, ln in enumerate(lines)
                        if ln.lstrip().startswith("交易时间") and "收/支" in ln and "交易订单号" in ln), None)
    if hdr_idx is None:
        report.errors.append("非支付宝个人交易记录格式 (未找到『交易号…收/支』列头)")
        return report
    header = [h.strip() for h in lines[hdr_idx].split(",")]

    def col(sub: str) -> Optional[int]:
        return next((i for i, h in enumerate(header) if sub in h), None)

    ci = {k: col(k) for k in ("交易号", "商家订单号", "交易创建时间", "类型",
                              "交易对方", "金额", "收/支", "备注", "商品名称")}
    # 回单变体列名兜底: 交易号列叫「交易订单号」(注意"交易号"不是它的子串, col 匹配不到)、
    # 时间列叫「交易时间」、类型列叫「交易分类」、商品列叫「商品说明」
    if ci.get("交易号") is None:
        ci["交易号"] = col("交易订单号")
    if ci.get("交易创建时间") is None:
        ci["交易创建时间"] = col("交易时间")
    if ci.get("类型") is None:
        ci["类型"] = col("交易分类")
    if ci.get("商品名称") is None:
        ci["商品名称"] = col("商品说明")
    _SKIP_PREFIXES = ("---", "已收入", "待收入", "已支出", "待支出", "导出时间", "用户:", "合计")
    rows: list[dict[str, Any]] = []
    for ln in lines[hdr_idx + 1:]:
        s = ln.strip()
        if not s or s.startswith(_SKIP_PREFIXES):
            continue
        cells = [c.strip() for c in ln.split(",")]

        def g(k: str) -> Optional[str]:
            i = ci.get(k)
            return cells[i] if i is not None and i < len(cells) else None

        no = (g("交易号") or "").strip()
        if not no or not no[0].isdigit():   # 页脚/分隔行(交易号非数字)一律跳过
            continue
        mag = _decimal(g("金额")) or Decimal("0")
        direction = (g("收/支") or "").strip()
        amount = -mag if direction == "支出" else mag   # 收入/不计收支 → 正
        rows.append({
            "transaction_no": no,
            "transaction_time": g("交易创建时间"),
            "transaction_type": g("类型"),
            "counterparty": g("交易对方"),
            "amount": amount,
            "related_order_no": g("商家订单号"),
            "remark": (" ".join(x for x in (g("商品名称"), g("备注")) if x)[:500]) or None,
        })
    return import_alipay_rows(db, rows, account=account, report=report, commit=commit)


# 同一笔分账在不同支付宝导出里可能标 "分账" 或 "交易分账" — 去重时归一为同类,
# 防同一笔结算因标签不同被重复入库 (2026-06-22; 历史已清 6 条双算)。
_DEDUP_TYPE_ALIASES = {"交易分账": "分账"}


def _dedup_type(t: Optional[str]) -> str:
    s = (t or "").strip()
    return _DEDUP_TYPE_ALIASES.get(s, s)


def import_alipay_rows(
    db: Session, rows: list[dict[str, Any]], *, account: str,
    report: Optional[AlipayImportReport] = None, commit: bool = True,
) -> AlipayImportReport:
    """把已规范化的流水行(payload dict 列表)写入 AlipayFlow, 带去重。

    供 CSV 导入 与 截图 OCR(parse_alipay_flow_screenshot)共用。
    去重键含「交易类型 + 金额」: 同号配对流水(在线支付货款 + 分账手续费)都能入库,
    仅「同号 + 同类型 + 同金额」才算真重复 (与 uq_alipay_flow_acct_no / migration 0039 一致)。
    """
    report = report or AlipayImportReport()
    seen: set[tuple] = set()
    for payload in rows:
        tx_no = (payload.get("transaction_no") or "").strip()
        amount = _decimal(payload.get("amount"))
        if not tx_no or amount is None:
            report.skipped_invalid += 1
            continue
        tx_type = payload.get("transaction_type")
        nat_type = _dedup_type(tx_type)   # 分账/交易分账 归一, 防同笔双入
        nat_key = (tx_no, nat_type, amount)
        if nat_key in seen:
            report.skipped_duplicate += 1
            continue
        # DB 存在性: 归一类型(分账∪交易分账)匹配所有别名; 其它(含 NULL)保持原精确比对(== 处理 IS NULL)
        variants = [nat_type] + [k for k, v in _DEDUP_TYPE_ALIASES.items() if v == nat_type]
        type_cond = (AlipayFlow.transaction_type.in_(variants) if len(variants) > 1
                     else AlipayFlow.transaction_type == tx_type)
        if db.execute(
            select(AlipayFlow.id).where(
                AlipayFlow.account == account,
                AlipayFlow.transaction_no == tx_no,
                type_cond,
                AlipayFlow.amount == amount,
            )
        ).first():
            report.skipped_duplicate += 1
            seen.add(nat_key)
            continue
        seen.add(nat_key)
        db.add(AlipayFlow(
            account=account,
            transaction_no=tx_no,
            transaction_time=_datetime(payload.get("transaction_time")) or date_from_flow_no(tx_no),
            transaction_type=payload.get("transaction_type"),
            counterparty=payload.get("counterparty"),
            counterparty_account=payload.get("counterparty_account"),
            amount=amount,
            related_order_no=payload.get("related_order_no"),
            balance=_decimal(payload.get("balance")),
            reconciliation_status=payload.get("reconciliation_status") or "open",
            reconciliation_type=payload.get("reconciliation_type"),
            remark=payload.get("remark"),
        ))
        report.inserted += 1
    if commit:
        db.commit()
    return report
