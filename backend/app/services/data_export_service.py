# -*- coding: utf-8 -*-
"""全类目 Excel 导出 + 存档轮转 (用户需求 2026-06-12, 2026-06-17 大改)。

- 按 table_explorer.ENTITY_MODELS 每个类目一个 Sheet, 全行导出。
- 每个 Sheet 末列「异常批注」: 该行若有未处理(open)异常, 写进去 + 加单元格批注(Comment)。
- 导出后归档到「资料存档库」(ImportedFile kind=full_export); 超过 MAX_KEEP 份自动删最早(轮转)。

2026-06-17 用户大改:
- **数字按数字**: 复用修好的 exceptions_export_service._cell (Decimal→float、日期原生) +
  金额/百分比/日期 number_format。
- **产品总表按 SKU 展开**: Product ⨝ PricingSku 每 SKU 一行; 价格/成本列用 VLOOKUP 关联「定价总表」
  (改定价总表自动联动) + 「毛利率验算」公式列 (=1-会计成本/大促价)。
- **美化**: 细边框 / 冻结首行 / 自动筛选 / 隔行底色 / 深蓝表头白字 / 合理列宽。
- **类目分色**: 有「类目」列的表, 该列按品类底色区隔。
- **英文转中文**: 表头(原有) + 常见枚举值(状态/类型/重要程度…)转中文。
复用 exceptions_export_service 的源表键/取值助手, 不重复实现。
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.table_explorer import ENTITY_MODELS
from app.models.exception import DataException
from app.models.import_file import ImportedFile
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import import_storage
from app.services.exceptions_export_service import (
    _SEVERITY_CN, _cell, _join_notes, _key_column,
)

MAX_KEEP = 30          # 资料存档库里全量导出最多留几份, 超出删最早 (用户拍板)
_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")

# 常见英文字段 → 中文表头兜底 (ENTITY_SCHEMAS 没覆盖的表/列用这个; 内容不翻, 仅表头)
_COMMON_HEADER_CN = {
    "id": "ID", "code": "编码", "name": "名称", "remark": "备注", "unit": "单位",
    "spec": "规格", "price": "单价", "amount": "金额", "qty": "数量", "status": "状态",
    "created_at": "创建时间", "updated_at": "更新时间", "warehouse": "仓库",
    "material_code": "物料编码", "material_name": "物料名称", "product_code": "产品编码",
    "product_name": "产品名称", "sku": "SKU", "sku_code": "SKU编码", "order_no": "订单号",
    "customer_name": "客户", "customer_phone": "电话", "platform": "平台",
    "order_date": "下单日期", "ship_date": "发货日期", "paid_amount": "实付金额",
    "tracking_no": "物流单号", "carrier": "快递公司", "is_custom": "是否定制",
    "is_refill": "是否补单", "lead_time_days": "提前期(天)", "priority": "优先级",
    "physical_qty": "物理库存", "locked_qty": "锁定库存", "safety_stock": "安全库存",
    "size_type": "尺寸类型", "is_discontinued": "已停产", "base_material_code": "基础物料",
    "primary_supplier_id": "主供应商", "alt_supplier_ids": "备选供应商",
    "area": "面积", "width_mm": "宽(mm)", "height_mm": "高(mm)",
    "sub_name": "副名称", "image_url": "图片链接", "category": "类目", "brand": "品牌",
    "listing_status": "上架状态", "main_material": "主材", "aux_material": "辅材",
    "import_job_id": "导入批次", "import_batch_id": "导入批次", "is_factory_provided": "工厂提供",
    "qty_required": "需求数量", "purchase_no": "采购单号", "self_delivered": "自送",
    "order_id": "订单ID", "factory_order_no": "工厂单号", "platform_order_no": "平台订单号",
    "transaction_no": "交易流水号", "transaction_time": "交易时间", "balance": "余额",
    "counterparty": "对方", "account": "账户", "bill_date": "账单日期", "service_type": "服务类型",
    "flow_type": "类型", "transaction_date": "交易日期", "gross_margin_rate": "毛利率",
    "accounting_cost": "会计成本", "big_promo": "大促价", "mid_promo": "中促价",
    "small_promo": "小促价", "daily_price": "日常价", "list_price": "标价",
    "factory_cost": "工厂成本", "wood_cost": "木作成本", "size_category": "尺寸类型",
    "payment_status": "付款状态", "compensation_fee": "赔付金额", "refund_amount": "退款金额",
    "refund_status": "退款状态", "expense_type": "费用类型",
}

# ── 枚举值 → 中文 (英文转中文, 让普通人看懂) ──────────────────────────────────
_VALUE_CN: dict[str, dict[str, str]] = {
    "status": {
        "pending_payment": "待付款", "unpaid": "待付款", "paid": "已付款",
        "production": "生产中", "producing": "生产中", "in_production": "生产中",
        "shipped": "已发货", "signed": "已签收", "completed": "已完成", "done": "已完成",
        "aftersales": "售后中", "refunding": "退款中", "refunded": "已退款",
        "cancelled": "已取消", "canceled": "已取消", "closed": "已关闭",
        "open": "未处理", "resolved": "已处理", "ignored": "已忽略", "pending": "待处理",
        "processing": "处理中", "matched": "已匹配", "unmatched": "未匹配",
        "active": "启用", "inactive": "停用", "settled": "已结清", "overdue": "逾期",
    },
    "payment_status": {"unpaid": "未付款", "paid": "已付款", "partial": "部分付款",
                       "overdue": "逾期", "settled": "已结清", "pending": "待付款"},
    "priority": {"high": "高", "mid": "中", "medium": "中", "low": "低"},
    "flow_type": {"recharge": "充值", "expense": "支出", "refund": "退款",
                  "income": "收入", "topup": "充值"},
    "expense_type": {"fixed": "固定成本", "variable": "变动成本", "fixed_cost": "固定成本",
                     "variable_cost": "变动成本"},
    "supplier_type": {"factory": "工厂", "material": "材料商", "logistics": "物流商",
                      "service": "服务商", "hardware": "五金", "accessory": "配件"},
    "sample_type": {"photo": "拍摄", "display": "陈列", "test": "测试", "gift": "赠送"},
    "tier": {"vip": "VIP", "normal": "普通", "new": "新客", "old": "老客"},
    "project_type": {"brand": "品牌", "performance": "效果", "content": "内容"},
    "size_category": {"small": "小型", "mid": "中型", "medium": "中型", "large": "大型"},
    "severity": dict(_SEVERITY_CN),
}

# 以小数存储的「比率」列 → 百分比格式 (gross_margin_rate=0.15 → 15.00%)
_PCT_COLS = {
    "gross_margin_rate", "platform_fee_rate", "shop_promo_rate", "mid_shop_rate",
    "big_shop_rate", "mid_platform_discount", "big_platform_discount", "xhs_promo_discount",
}

# 类目分色调色板 (柔和, 不刺眼)
_CAT_PALETTE = [
    "FCE4D6", "E2EFDA", "DDEBF7", "FFF2CC", "EAD1DC",
    "D9E1F2", "FBE5D6", "E2F0D9", "FFE699", "D6DCE4",
    "F8CBAD", "C6E0B4", "BDD7EE", "FFD966", "D5A6BD",
]


def _cn_header(entity_key: str, col: str) -> str:
    """英文字段名 → 中文表头: 优先取 ENTITY_SCHEMAS 字段 desc(去括号), 再兜底常用映射, 最后原样。"""
    from app.services.excel_schemas import ENTITY_SCHEMAS
    sch = ENTITY_SCHEMAS.get(entity_key)
    if sch and col in sch.get("fields", {}):
        desc = sch["fields"][col].get("desc")
        if desc:
            return re.split(r"[（(]", desc)[0].strip() or col
    return _COMMON_HEADER_CN.get(col, col)


def _safe_sheet_name(label: str, used: set[str]) -> str:
    """openpyxl Sheet 名: 去非法字符 []:*?/\\, ≤31 字符, 去重。"""
    name = _INVALID_SHEET.sub("·", (label or "表").strip())[:31] or "表"
    base = name
    i = 2
    while name in used:
        suffix = f"({i})"
        name = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def _col_type(col) -> str:
    t = str(col.type).lower()
    if "bool" in t:
        return "bool"
    if "int" in t:
        return "int"
    if "numeric" in t or "float" in t or "decimal" in t:
        return "decimal"
    if "date" in t and "time" in t:
        return "datetime"
    if "date" in t or "time" in t:
        return "date"
    return "str"


def _num_fmt(col: str, ctype: str) -> Optional[str]:
    """列 → Excel number_format。金额千分位两位、比率百分比、整数、日期。"""
    if col in _PCT_COLS:
        return "0.00%"
    if ctype == "date":
        return "yyyy-mm-dd"
    if ctype == "datetime":
        return "yyyy-mm-dd hh:mm"
    if col == "id" or col.endswith("_id") or col.endswith("_job_id"):
        return "0"
    if ctype == "int":
        return "#,##0"
    if ctype == "decimal":
        return "#,##0.00"
    return None


def _translate(col: str, v):
    """枚举值英文 → 中文 (仅当列在 _VALUE_CN 且值命中)。"""
    if isinstance(v, str):
        m = _VALUE_CN.get(col)
        if m and v in m:
            return m[v]
    return v


def _cat_fill(cat):
    """类目 → 稳定底色 (md5 → 调色板, 跨次运行一致)。"""
    from openpyxl.styles import PatternFill
    if cat in (None, ""):
        return None
    idx = int(hashlib.md5(str(cat).encode("utf-8")).hexdigest(), 16) % len(_CAT_PALETTE)
    return PatternFill("solid", fgColor=_CAT_PALETTE[idx])


def _exception_notes(db: Session, table_name: str) -> dict[str, list[str]]:
    """该源表所有 open 异常 → {source_pk字符串: [批注...]}。"""
    excs = db.execute(
        select(DataException).where(
            DataException.source_table == table_name,
            DataException.status == "open",
        )
    ).scalars().all()
    out: dict[str, list[str]] = {}
    for e in excs:
        if e.source_pk:
            out.setdefault(str(e.source_pk), []).append(
                f"[{_SEVERITY_CN.get(e.severity, e.severity)}] {e.description}")
    return out


# ── 样式 ─────────────────────────────────────────────────────────────────────
def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color="D9D9D9")
    return {
        "head_fill": PatternFill("solid", fgColor="1F4E79"),
        "head_font": Font(bold=True, color="FFFFFF", size=11),
        "exc_head_fill": PatternFill("solid", fgColor="C0392B"),
        "exc_row_fill": PatternFill("solid", fgColor="FFF3CD"),
        "zebra_fill": PatternFill("solid", fgColor="F4F7FB"),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
        "center": Alignment(horizontal="center", vertical="center"),
    }


def _apply_table_style(ws, headers, *, exc_col_idx=None, note_rows=None,
                       col_fmts=None, cat_col_idx=None, data_end_row=None):
    """表头样式 + 边框 + 隔行底色 + number_format + 类目分色 + 冻结 + 自动筛选 + 列宽。"""
    from openpyxl.utils import get_column_letter

    s = _styles()
    note_rows = note_rows or set()
    col_fmts = col_fmts or {}
    n = len(headers)
    last_data = data_end_row or ws.max_row

    # 表头
    for ci, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=ci)
        c.fill = s["exc_head_fill"] if (exc_col_idx and ci == exc_col_idx) else s["head_fill"]
        c.font = s["head_font"]
        c.alignment = s["center"]
        c.border = s["border"]
        ws.column_dimensions[get_column_letter(ci)].width = min(max(len(str(h)) * 2 + 4, 10), 42)
    ws.row_dimensions[1].height = 20

    # 数据行
    for ri in range(2, last_data + 1):
        is_note = ri in note_rows
        zebra = (ri % 2 == 0) and not is_note
        for ci in range(1, n + 1):
            cell = ws.cell(row=ri, column=ci)
            cell.border = s["border"]
            if is_note:
                cell.fill = s["exc_row_fill"]
            elif zebra:
                cell.fill = s["zebra_fill"]
            fmt = col_fmts.get(ci)
            if fmt and cell.value is not None:
                cell.number_format = fmt
        # 类目分色 (覆盖隔行底色)
        if cat_col_idx:
            cv = ws.cell(row=ri, column=cat_col_idx).value
            fill = _cat_fill(cv)
            if fill is not None:
                ws.cell(row=ri, column=cat_col_idx).fill = fill

    ws.freeze_panes = "A2"
    if last_data >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(n)}{max(last_data, 1)}"


# ── 通用类目表 (产品总表以外) ─────────────────────────────────────────────────
def _build_entity_sheet(db: Session, wb, key: str, cfg: dict, used: set[str]):
    from openpyxl.comments import Comment

    model = cfg["model"]
    label = cfg.get("label", key)
    table_name = model.__tablename__
    ws = wb.create_sheet(_safe_sheet_name(label, used))
    cols = [c.key for c in model.__table__.columns]
    headers = [_cn_header(key, c) for c in cols] + ["异常批注"]
    ws.append(headers)

    # 列 → number_format + 类目列下标
    model_cols = {c.key: c for c in model.__table__.columns}
    col_fmts: dict[int, str] = {}
    cat_col_idx = None
    for i, c in enumerate(cols, start=1):
        fmt = _num_fmt(c, _col_type(model_cols[c]))
        if fmt:
            col_fmts[i] = fmt
        if c == "category":
            cat_col_idx = i

    notes = _exception_notes(db, table_name)
    key_col = _key_column(model)
    consumed: set[str] = set()
    exc_col_idx = len(cols) + 1
    note_rows: set[int] = set()

    for r in db.execute(select(model)).scalars().all():
        cand = {str(getattr(r, "id", "") or "")}
        if key_col != "id":
            cand.add(str(getattr(r, key_col, "") or ""))
        matched: list[str] = []
        for k in cand:
            if k and k in notes:
                matched.extend(notes[k])
                consumed.add(k)
        note = _join_notes(matched) if matched else None
        ws.append([_translate(c, _cell(getattr(r, c, None))) for c in cols] + [note])
        if note:
            rid = ws.max_row
            note_rows.add(rid)
            ws.cell(row=rid, column=exc_col_idx).comment = Comment(note, "异常中心")

    data_end = ws.max_row
    _apply_table_style(ws, headers, exc_col_idx=exc_col_idx, note_rows=note_rows,
                       col_fmts=col_fmts, cat_col_idx=cat_col_idx, data_end_row=data_end)

    # 源表里定位不到行的异常 (行已删/键已变) 附在表尾
    orphans = [(k, ns) for k, ns in notes.items() if k not in consumed]
    if orphans:
        from openpyxl.styles import Font
        ws.append([])
        tip = ws.cell(row=ws.max_row + 1, column=1,
                      value="── 以下异常的关联行在本表已找不到 (行已删 / 业务键已变 / 异常已修复待复核) ──")
        tip.font = Font(bold=True, color="C0392B")
        for k, ns in orphans:
            ws.append([k] + [None] * (len(cols) - 1) + ["; ".join(ns)])
    return ws


# ── 产品总表: 按 SKU 展开 + 价格 VLOOKUP 关联定价总表 + 毛利率验算公式 ─────────
def _build_product_sku_sheet(db: Session, wb, used: set[str], pricing_sheet: Optional[str]):
    """Product ⨝ PricingSku 每 SKU 一行。价格/成本列 = VLOOKUP 关联「定价总表」(改定价总表自动联动);
    末加「毛利率验算」公式列 (=1-会计成本/大促价)。无 SKU 的产品保留一行(价格列空)。"""
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(_safe_sheet_name("产品总表", used))
    headers = [
        "产品编码", "产品名称", "副名称", "品牌", "类目", "上架状态", "重要程度",
        "主材介绍", "辅材介绍",
        "SKU编码", "SKU名称", "尺寸类型",
        "会计成本", "毛利率", "大促利润", "标价", "日常价", "小促价", "中促价", "大促价",
        "工厂成本", "木作成本", "毛利率验算(公式)", "备注",
    ]
    ws.append(headers)

    # 定价总表列布局 (VLOOKUP 用): sku_code 起到末列, 各目标列相对偏移
    pcols = [c.key for c in PricingSku.__table__.columns]
    sku_i = pcols.index("sku_code")            # 0-based
    sku_letter = get_column_letter(sku_i + 1)
    last_letter = get_column_letter(len(pcols))

    def off(field: str) -> int:
        return pcols.index(field) - sku_i + 1

    pname = pricing_sheet or "定价总表 (全列)"
    pref = pname.replace("'", "''")            # 公式里单引号转义

    # 产品总表中 第13..22 列 → 对应定价总表字段 (VLOOKUP)
    vlookup_fields = {
        13: "accounting_cost", 14: "gross_margin_rate", 15: "big_promo_margin",
        16: "list_price", 17: "daily_price", 18: "small_promo", 19: "mid_promo",
        20: "big_promo", 21: "factory_cost", 22: "wood_cost",
    }

    # 产品 → 其 SKU 列表
    skus_by_code: dict[str, list[PricingSku]] = {}
    for s in db.execute(select(PricingSku).order_by(PricingSku.sku_code)).scalars().all():
        skus_by_code.setdefault(s.product_code, []).append(s)

    notes = _exception_notes(db, "products")
    note_rows: set[int] = set()

    products = db.execute(select(Product).order_by(Product.code)).scalars().all()
    r = 1
    for p in products:
        plist = skus_by_code.get(p.code) or [None]
        pnote = notes.get(str(p.code)) or notes.get(str(p.id))
        for s in plist:
            r += 1
            has_sku = s is not None
            row = [
                p.code, p.name, p.sub_name, p.brand, p.category, p.listing_status,
                _translate("priority", p.priority), p.main_material, p.aux_material,
                (s.sku_code if has_sku else None), (s.sku if has_sku else None),
                _translate("size_category", s.size_category) if has_sku else None,
            ]
            # 13..22 价格/成本: VLOOKUP 关联定价总表 (有 SKU 才填公式)
            for ci in range(13, 23):
                if has_sku:
                    row.append(
                        f"=IFERROR(VLOOKUP($J{r},'{pref}'!${sku_letter}:${last_letter},"
                        f"{off(vlookup_fields[ci])},FALSE),\"\")"
                    )
                else:
                    row.append(None)
            # 23 毛利率验算 = 1 - 会计成本(M) / 大促价(T)
            row.append(f"=IFERROR(1-M{r}/T{r},\"\")" if has_sku else None)
            # 24 备注 (SKU 备注; 产品有异常则并入)
            rmk = (s.remark if has_sku else None) or ""
            if pnote:
                rmk = (rmk + "  ⚠" + _join_notes(pnote)).strip()
                note_rows.add(r)
            row.append(rmk or None)
            ws.append(row)

    data_end = ws.max_row
    # number_format: 13..22 金额、14 毛利率%、23 验算%
    col_fmts: dict[int, str] = {ci: "#,##0.00" for ci in range(13, 23)}
    col_fmts[14] = "0.00%"
    col_fmts[23] = "0.00%"
    _apply_table_style(ws, headers, exc_col_idx=None, note_rows=note_rows,
                       col_fmts=col_fmts, cat_col_idx=5, data_end_row=data_end)
    return ws


# ── 定价总表 sheet: 派生列改活公式 (编辑成本→价格/利润 Excel 内自动重算) ─────────────
def _apply_pricing_formulas(ws) -> None:
    """把定价总表 sheet 的派生列(物理/各档价/平台费/税/会计/利润/毛利率)改成活公式,
    口径复刻 pricing_calc_service.recompute (2026-07-01 用户拍板对齐 Excel):
      物理 = 工厂 + 物流 + 安装 ; 会计基准 = 物理 / (1 − 2.6%)
      标价/小促/中促/大促 = ROUNDUP(会计基准 / 对应基数, −1) ; 日常 = 标价 × 0.75
      平台费 = 大促×0.6% ; 税 = 大促×2% ; 会计 = 物理+平台费+税 ; 利润 = 大促−会计 ; 毛利率 = 利润/大促
    价格公式仅在该行有对应基数(base_*)时写(无基数保原值, 与引擎一致); 利润链恒随大促联动。
    → 导出的 Excel 里改工厂成本/物流/安装/基数, 价格与利润会像用户原表一样自动联动。"""
    from openpyxl.utils import get_column_letter
    cols = [c.key for c in PricingSku.__table__.columns]
    idx = {c: i + 1 for i, c in enumerate(cols)}
    if "physical_cost" not in idx or "big_promo" not in idx:
        return
    def L(f):
        return get_column_letter(idx[f])
    RATE = "0.026"
    fac, log, ins, phys = L("factory_cost"), L("logistics_cost"), L("install_cost"), L("physical_cost")
    bl, bs, bm, bb = L("base_list"), L("base_small"), L("base_mid"), L("base_big")
    lp, dp, sp, mp, bp = L("list_price"), L("daily_price"), L("small_promo"), L("mid_promo"), L("big_promo")
    plat, tax, acct, marg, rate = (L("platform_fee_rate"), L("tax"), L("accounting_cost"),
                                   L("big_promo_margin"), L("gross_margin_rate"))
    for r in range(2, ws.max_row + 1):
        first = ws.cell(r, 1).value
        if isinstance(first, str) and first.startswith("──"):
            break  # 到达表尾"孤儿异常"提示区, 停止
        has = lambda col: ws[f"{col}{r}"].value not in (None, "")
        if not has(fac):
            continue
        ws[f"{phys}{r}"] = f"=SUM({fac}{r},{log}{r},{ins}{r})"
        base_expr = f"({phys}{r}/(1-{RATE}))"          # 会计基准 = Excel 成本总计
        if has(bl):
            ws[f"{lp}{r}"] = f"=ROUNDUP({base_expr}/{bl}{r},-1)"
            ws[f"{dp}{r}"] = f"={lp}{r}*0.75"
        if has(bs):
            ws[f"{sp}{r}"] = f"=ROUNDUP({base_expr}/{bs}{r},-1)"
        if has(bm):
            ws[f"{mp}{r}"] = f"=ROUNDUP({base_expr}/{bm}{r},-1)"
        if has(bb):
            ws[f"{bp}{r}"] = f"=ROUNDUP({base_expr}/{bb}{r},-1)"
        if has(bp):                                    # 大促(公式或原值)在 → 利润链联动
            ws[f"{plat}{r}"] = f"={bp}{r}*0.006"
            ws[f"{tax}{r}"] = f"={bp}{r}*0.02"
            ws[f"{acct}{r}"] = f"={phys}{r}+{plat}{r}+{tax}{r}"
            ws[f"{marg}{r}"] = f"={bp}{r}-{acct}{r}"
            ws[f"{rate}{r}"] = f'=IFERROR({marg}{r}/{bp}{r},"")'


def build_full_export_workbook(db: Session):
    """全类目工作簿: 产品总表(按SKU展开+公式) 置顶, 定价总表次之, 其余每类目一 Sheet。"""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used: set[str] = set()

    pricing_name: Optional[str] = None
    for key, cfg in ENTITY_MODELS.items():
        if key == "product":
            continue                      # 产品总表特殊处理, 放最后建(需引用定价表名)
        ws = _build_entity_sheet(db, wb, key, cfg, used)
        if key == "pricing_sku":
            pricing_name = ws.title
            _apply_pricing_formulas(ws)     # 定价总表派生列改活公式(改成本→价格/利润联动)

    _build_product_sku_sheet(db, wb, used, pricing_name)

    # 排序: 产品总表 第一, 定价总表 第二
    order_titles = [t for t in ("产品总表", pricing_name) if t and t in wb.sheetnames]
    for i, t in enumerate(order_titles):
        sh = wb[t]
        wb._sheets.remove(sh)
        wb._sheets.insert(i, sh)

    if not wb.sheetnames:
        ws = wb.create_sheet("空")
        ws.append(["系统当前没有可导出的类目。"])
    return wb


def rotate_full_exports(db: Session, *, keep: int = MAX_KEEP) -> int:
    """资料存档库里 kind=full_export 只留最新 keep 份, 超出删最早(连磁盘文件)。返回删除数。"""
    recs = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "full_export")
        .order_by(ImportedFile.id.desc())
    ).scalars().all()
    removed = 0
    for old in recs[keep:]:
        if import_storage.delete_record(db, old.id):
            removed += 1
    return removed


def run_full_export(db: Session, *, uploaded_by: Optional[str] = None) -> dict:
    """生成全类目 Excel → 存「资料存档库」→ 轮转(留≤30)。返回 {content, filename, ...}。"""
    wb = build_full_export_workbook(db)
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    today = date.today()
    filename = f"全量导出_{today.isoformat()}.xlsx"
    res = import_storage.archive(
        db, content=content, original_name=filename, kind="full_export",
        source="web", uploaded_by=uploaded_by,
        row_summary={"sheets": len(wb.sheetnames), "exported_at": today.isoformat()},
    )
    removed = rotate_full_exports(db)
    db.commit()
    return {
        "content": content, "filename": filename,
        "file_id": res.file.id, "sheets": len(wb.sheetnames), "rotated_removed": removed,
    }
