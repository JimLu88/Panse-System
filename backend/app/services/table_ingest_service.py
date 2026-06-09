"""表格(Excel/CSV)统一识别 + 路由入库 —— 供飞书机器人收文件后按类型自动导入。

识别: 文件名关键词 + 表头结构(列名指纹)结合(用户选定)。
路由: 每个类型对应一个已有导入器(经 tabular 桥接, CSV/xlsx 通吃)。
新增类型只需往 TABLE_TYPES 加一条(关键词 + 指纹 + importer)。
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.services import tabular


# ── 各类型导入适配器 (统一签名 (db, content, filename) -> {ok, summary}) ──
def _imp_order(db: Session, content: bytes, filename: Optional[str]) -> dict:
    from app.services import taobao_order_import
    rep = taobao_order_import.import_taobao_orders(db, filename or "orders.xlsx", content)
    return {"ok": True, "summary": (
        f"订单表导入完成({rep.detected_format}): 新增 **{rep.inserted}**, 更新 {rep.updated}, "
        f"重复 {rep.skipped_duplicate}, 无效 {rep.skipped_invalid}。"
        + (f"\n⚠️ {'; '.join(map(str, rep.errors[:2]))}" if rep.errors else ""))}


def _imp_factory_recon(db: Session, content: bytes, filename: Optional[str]) -> dict:
    from app.services import factory_recon_import_service as fr
    rep = fr.import_factory_recon_xlsx(db, content)
    if rep.errors:
        return {"ok": False, "summary": f"导入失败: {'; '.join(map(str, rep.errors[:2]))}"}
    return {"ok": True, "summary": (
        f"工厂对账单导入完成: 新增 **{rep.inserted}** 行, 重复 {rep.skipped_duplicate}, "
        f"无效 {rep.skipped_invalid}, 回填成本 {rep.backfilled_cost} 单。")}


_ALIPAY_ACCOUNTS = {"企业": "企业号", "爱群": "爱群号", "主力": "主力号", "佳宝": "佳宝号"}


def _alipay_account(filename: Optional[str]) -> str:
    n = filename or ""
    for kw, acct in _ALIPAY_ACCOUNTS.items():
        if kw in n:
            return acct
    return "企业号"   # 默认主结算账户


def _imp_alipay(db: Session, content: bytes, filename: Optional[str]) -> dict:
    from app.services import alipay_import
    account = _alipay_account(filename)
    text = tabular.to_csv_text(content, filename)
    rep = alipay_import.import_alipay_csv(db, text, account=account, commit=False)
    if rep.errors:
        return {"ok": False, "summary": f"导入失败: {'; '.join(map(str, rep.errors[:2]))}"}
    return {"ok": True, "summary": (
        f"支付宝流水导入完成(账户「{account}」): 新增 **{rep.inserted}**, "
        f"重复 {rep.skipped_duplicate}, 无效 {rep.skipped_invalid}。")}


def _imp_wechat(db: Session, content: bytes, filename: Optional[str]) -> dict:
    from app.services import settlement_import_service
    res = settlement_import_service.import_bill(db, content, source="wechat")
    if isinstance(res, dict) and res.get("error"):
        return {"ok": False, "summary": f"导入失败: {res['error']}"}
    ins = res.get("inserted", 0) if isinstance(res, dict) else 0
    upd = res.get("updated", 0) if isinstance(res, dict) else 0
    return {"ok": True, "summary": f"微信账单导入完成: 新增 **{ins}**, 更新 {upd} 条。"}


_PREPAY_CATS = {"佣金": "refill_commission", "快递": "refill_express", "售后": "aftersales"}


def _prepay_category(filename: Optional[str]) -> str:
    n = filename or ""
    for kw, cat in _PREPAY_CATS.items():
        if kw in n:
            return cat
    return "refill_commission"


def _imp_prepay(db: Session, content: bytes, filename: Optional[str]) -> dict:
    from app.services import prepay_import_service
    category = _prepay_category(filename)
    text = tabular.to_csv_text(content, filename)
    rep = prepay_import_service.import_prepay_csv(db, text, category=category)
    if rep.errors:
        return {"ok": False, "summary": f"导入失败: {'; '.join(map(str, rep.errors[:2]))}"}
    return {"ok": True, "summary": (
        f"代付台账导入完成({category}): 新增 **{rep.inserted}**, "
        f"重复 {rep.skipped_duplicate}, 无效 {rep.skipped_invalid}。")}


def _bill_importer(fn_name: str, label: str) -> Callable:
    def _imp(db: Session, content: bytes, filename: Optional[str]) -> dict:
        from app.services import bill_import_service
        text = tabular.to_csv_text(content, filename)
        rep = getattr(bill_import_service, fn_name)(db, text)
        if rep.errors:
            return {"ok": False, "summary": f"导入失败: {'; '.join(map(str, rep.errors[:2]))}"}
        extra = ""
        if getattr(rep, "unmapped_columns", None):
            extra = f" · 未识别列: {','.join(rep.unmapped_columns)}"
        return {"ok": True, "summary": (
            f"{label}导入完成: 新增 **{rep.inserted}**, 重复 {rep.skipped_duplicate}, "
            f"无效 {rep.skipped_invalid}。{extra}")}
    return _imp


# ── 类型注册表: key → 标签 / 文件名关键词 / 表头列名指纹 / 归档去向 / 导入器 ──
TABLE_TYPES: dict[str, dict] = {
    "order": {
        "label": "订单表", "archive": "orders",
        "keywords": ["订单", "千牛", "淘宝", "销售明细"],
        "fingerprint": ["订单编号", "子订单编号", "主订单编号", "商家编码", "宝贝标题", "买家会员名", "收货人"],
        "importer": _imp_order,
    },
    "factory_recon": {
        "label": "工厂对账表", "archive": "factory_recon",
        "keywords": ["工厂对账", "工厂", "明细表"],   # 不用裸"对账"(补单对账也含), 靠"工厂"或表头
        "fingerprint": ["订单号", "价格", "下单时间", "详情", "追加订单号1"],
        "importer": _imp_factory_recon,
    },
    "alipay": {
        "label": "支付宝流水", "archive": "alipay",
        "keywords": ["支付宝", "流水"],
        "fingerprint": ["交易流水号", "收支金额", "交易时间", "交易对象", "对方账户名称"],
        "importer": _imp_alipay,
    },
    "wechat_bill": {
        "label": "微信账单", "archive": "settlement",
        "keywords": ["微信", "billdetail", "聚合"],
        "fingerprint": ["微信订单号", "商户单号", "交易时间", "金额", "微信支付订单号"],
        "importer": _imp_wechat,
    },
    "wanshifu": {
        "label": "万师傅安装账单", "archive": "wanshifu",
        "keywords": ["万师傅", "安装"],
        "fingerprint": ["服务类型", "安装", "金额", "订单号", "状态"],
        "importer": _bill_importer("import_wanshifu_csv", "万师傅账单"),
    },
    "logistics": {
        "label": "物流账单", "archive": "logistics",
        "keywords": ["物流", "运费", "承运"],   # 不用"快递"(代付台账有"快递代付"会冲突), 靠这些或表头
        "fingerprint": ["承运商", "运单号", "运费", "重量(kg)", "重量"],
        "importer": _bill_importer("import_logistics_csv", "物流账单"),
    },
    "promotion": {
        "label": "推广费流水", "archive": "promotion",
        "keywords": ["推广", "直通车", "万相台"],
        "fingerprint": ["充值", "支出", "推广", "交易日期", "流水类型"],
        "importer": _bill_importer("import_promotion_flows_csv", "推广费流水"),
    },
    "refill": {
        "label": "补单对账表", "archive": "refill",
        "keywords": ["补单", "刷单"],
        "fingerprint": ["补单", "返款", "买家", "订单号", "补单成本"],
        "importer": _bill_importer("import_refill_records_csv", "补单对账表"),
    },
    "account_balance": {
        "label": "账户余额表", "archive": "account_balance",
        "keywords": ["余额", "账户"],
        "fingerprint": ["账户", "期末余额", "余额", "统计日期", "账户名"],
        "importer": _bill_importer("import_account_balances_csv", "账户余额表"),
    },
    "prepay": {
        "label": "代付台账", "archive": "refill",
        "keywords": ["代付", "打款"],
        "fingerprint": ["打款", "收款方", "打款流水号", "打款日期"],
        "importer": _imp_prepay,
    },
}


def _fp_score(header: set, t: dict) -> int:
    return len(header & set(t["fingerprint"]))


def classify_table(filename: Optional[str], content: bytes) -> Optional[str]:
    """文件名关键词 + 表头指纹 结合判类型。返回 TABLE_TYPES 的 key, 拿不准返回 None。"""
    name = (filename or "")
    name_l = name.lower()
    header = set(tabular.read_header(content, filename))

    def _kw_len(t: dict) -> int:
        return max((len(kw) for kw in t["keywords"] if (kw in name) or (kw in name_l)), default=0)

    fn_hits = [k for k, t in TABLE_TYPES.items()
               if any((kw in name) or (kw in name_l) for kw in t["keywords"])]
    if len(fn_hits) == 1:
        return fn_hits[0]
    if len(fn_hits) > 1:
        # 多个文件名命中 → 先比表头指纹, 再比命中关键词的具体程度(越长越具体)
        return max(fn_hits, key=lambda k: (_fp_score(header, TABLE_TYPES[k]), _kw_len(TABLE_TYPES[k])))

    # 文件名没命中 → 纯看表头
    if header:
        best_k = max(TABLE_TYPES, key=lambda k: _fp_score(header, TABLE_TYPES[k]))
        if _fp_score(header, TABLE_TYPES[best_k]) >= 2:
            return best_k
    return None


def import_table(db: Session, key: str, content: bytes, filename: Optional[str]) -> dict:
    """按类型导入(不 commit, 由调用方 commit)。返回 {ok, summary}。"""
    t = TABLE_TYPES.get(key)
    if not t:
        return {"ok": False, "summary": f"未知表格类型: {key}"}
    return t["importer"](db, content, filename)
