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


def _imp_logistics(db: Session, content: bytes, filename: Optional[str]) -> dict:
    """物流账单导入(飞书发文件/网页统一从这里进, 导入后都自动配单):
       - 文件名含「德邦/壹米滴答」的月结账单(逐单运费/月结总额, 靠文件名识别承运商)→ 专用 xlsx 导入
         (德邦逐单带收货人/目的地 + 月结汇总行)。
       - 其余通用物流表(标准列 承运商/运单号/运费/收货人…)→ csv 映射导入。"""
    from app.services import bill_import_service
    name = filename or ""
    if name.lower().endswith((".xlsx", ".xls")) and any(k in name for k in ("德邦", "壹米", "滴答")):
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        rep = bill_import_service.import_logistics_xlsx(db, wb, source_name=name)
    else:
        text = tabular.to_csv_text(content, filename)
        rep = bill_import_service.import_logistics_csv(db, text)
    if rep.errors:
        return {"ok": False, "summary": f"物流账单导入失败: {'; '.join(map(str, rep.errors[:2]))}"}
    return {"ok": True, "summary": (
        f"物流账单导入完成: 新增 **{rep.inserted}**, 重复 {rep.skipped_duplicate}, "
        f"无效 {rep.skipped_invalid} · 已自动按运单号/收货人配单。")}


def _imp_part_purchase(db: Session, content: bytes, filename: Optional[str]) -> dict:
    """配件采购表 (飞书直接传 Excel/CSV 也能入库, 与网页上传共用核心)。"""
    from app.services import purchase_table_import
    r = purchase_table_import.import_purchases_table_core(db, content, filename)
    return {"ok": True, "summary": (
        f"配件采购导入完成: 新增 **{r['inserted']}**, 重复 {r['skipped_duplicate']}, "
        f"无效 {r['skipped_invalid']}。"
        + (f" 未识别列: {','.join(r['unmapped_columns'])}" if r['unmapped_columns'] else ""))}


def _imp_order_part_purchase(db: Session, content: bytes, filename: Optional[str]) -> dict:
    """「订单号 + 配件」采购回填: 把订单对应配件标已购买(未采购→已下单), 从待买扣减。"""
    from app.services import order_accessory_purchase_import as oap
    r = oap.import_order_part_purchases_core(db, content, filename)
    extra = (f" 未匹配 {r['unmatched']} 行(如: {', '.join(r['unmatched_list'][:3])})"
             if r["unmatched"] else "")
    return {"ok": True, "summary": (
        f"订单配件采购回填: 共 {r['rows']} 行, 标为已购买 **{r['updated']}** 项配件, "
        f"已是已买 {r['already']}。{extra}")}


# ── 类型注册表: key → 标签 / 文件名关键词 / 表头列名指纹 / 归档去向 / 导入器 ──
TABLE_TYPES: dict[str, dict] = {
    "order_part_purchase": {
        "label": "订单配件采购回填", "archive": "purchases",
        "keywords": ["订单配件", "配件已购", "已购配件", "订单号配件"],
        "fingerprint": ["订单号", "配件名称", "配件编码"],   # 命中≥2, 压过 factory_recon/part_purchase
        "importer": _imp_order_part_purchase,
    },
    "part_purchase": {
        "label": "配件采购表", "archive": "purchases",
        "keywords": ["配件采购", "采购表", "采购明细"],
        "fingerprint": ["配件名称", "供应商", "购买日期", "快递单号"],
        "importer": _imp_part_purchase,
    },
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
        "keywords": ["物流", "运费", "承运", "德邦", "壹米", "顺丰"],
        "fingerprint": ["承运商", "运单号", "运费", "实收运费", "收货人", "重量(kg)", "重量"],
        "importer": _imp_logistics,   # xlsx 走 import_logistics_xlsx(收货人+汇总行+自动配单)
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
        # 多个文件名命中 → 先比表头指纹, 再比命中关键词的具体程度(越长越具体)。
        # 用户拍板 (2026-06-11): 比完仍打平 = 真歧义 → 返回 None, 飞书端强制弹选类型卡。
        scored = sorted(
            ((_fp_score(header, TABLE_TYPES[k]), _kw_len(TABLE_TYPES[k]), k) for k in fn_hits),
            reverse=True,
        )
        if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
            return None
        return scored[0][2]

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
