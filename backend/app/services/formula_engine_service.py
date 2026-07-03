"""Formula engine service — safe AST-based evaluation of pricing formula rules.

Supports:
  - Chinese field name resolution across pricing_sku / pricing_sku_costs / pricing_sku_promo
  - Safe evaluation: no eval(), uses ast module
  - Built-in functions: IF(), SUM(), MIN(), MAX(), ABS(), ROUND()
  - Topological dependency resolution
  - Batch recompute for all SKUs
"""
from __future__ import annotations

import ast
import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.pricing_formula import PricingFormulaRule

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chinese name → (object_key, python_attr) mapping
# object_key: "sku" | "costs" | "promo"
# ---------------------------------------------------------------------------

FIELD_MAP: dict[str, tuple[str, str]] = {
    # pricing_sku
    "产品编码": ("sku", "product_code"),
    "SKU编码": ("sku", "sku_code"),
    "大小分类": ("sku", "size_category"),
    "标价": ("sku", "list_price"),
    "日常价": ("sku", "daily_price"),
    "小促价": ("sku", "small_promo"),
    "中促价": ("sku", "mid_promo"),
    "大促价": ("sku", "big_promo"),
    "大促利润": ("sku", "big_promo_margin"),
    "毛利率": ("sku", "gross_margin_rate"),
    "会计总成本": ("sku", "accounting_cost"),
    "平台费率": ("sku", "platform_fee_rate"),
    "税费": ("sku", "tax"),
    "物理总成本": ("sku", "physical_cost"),
    "物流费用": ("sku", "logistics_cost"),
    "安装费": ("sku", "install_cost"),
    "总出厂成本": ("sku", "factory_cost"),
    "木作成本": ("sku", "wood_cost"),
    "包装成本": ("sku", "packaging_cost"),
    "外采配件成本": ("sku", "external_parts_cost"),
    # pricing_sku_costs
    "岩板": ("costs", "rock_slab"),
    "抽屉轨道": ("costs", "drawer_rail"),
    "灯带": ("costs", "led_strip"),
    "玻璃": ("costs", "glass"),
    "电力轨道": ("costs", "electric_rail"),
    "打包纸片": ("costs", "packing_sheet"),
    "铁销": ("costs", "iron_pin"),
    "连接片": ("costs", "connector"),
    "铝合金轨道": ("costs", "aluminum_rail"),
    "塑料轨道": ("costs", "plastic_rail"),
    "mini把手": ("costs", "mini_handle"),
    "免钉胶": ("costs", "nail_free_glue"),
    "雕刻": ("costs", "engraving"),
    "亚克力条": ("costs", "acrylic_strip"),
    "预埋套杆": ("costs", "embedded_sleeve"),
    "理线架插排": ("costs", "cable_mgmt"),
    "背板": ("costs", "back_panel"),
    "装饰条": ("costs", "stainless_trim"),
    "腿部": ("costs", "leg"),
    "软包": ("costs", "soft_pack"),
    "床铺板": ("costs", "bed_board"),
    "其他配件": ("costs", "other_cost"),
    # pricing_sku_promo
    "单品立减系数": ("promo", "shop_promo_rate"),
    "店铺宝系数": ("promo", "shop_promo_rate"),   # 旧名别名(店铺宝已停用): 保留令已存公式表达式不失效
    "小促到手价": ("promo", "shop_internal_final"),
    "无国补中促系数": ("promo", "mid_shop_rate"),
    "中促到手价": ("promo", "mid_buyer_price"),
    "中促店铺到账": ("promo", "mid_shop_receipt"),
    "中促会员价": ("promo", "mid_vip_final"),
    "无国补大促系数": ("promo", "big_shop_rate"),
    "大促到手价": ("promo", "big_buyer_price"),
    "大促店铺到账": ("promo", "big_shop_receipt"),
    "大促会员价": ("promo", "big_vip_final"),
    "小红书活动价": ("promo", "xhs_activity_price"),
    "小红书折扣": ("promo", "xhs_promo_discount"),
    "小红书促销价": ("promo", "xhs_promo_price"),
}

# Reverse map: field_name (python attr) → Chinese display name (for easy lookup)
_ATTR_TO_CN: dict[str, str] = {v[1]: k for k, v in FIELD_MAP.items()}

# Map: field_name → object_key (which object owns this attr)
_FIELD_TO_OBJ: dict[str, str] = {v[1]: v[0] for v in FIELD_MAP.values()}

# ---------------------------------------------------------------------------
# Built-in formula rules
# ---------------------------------------------------------------------------

BUILTIN_RULES = [
    {
        "field_name": "list_price",
        "display_name": "标价",
        "expression": "物理总成本 / 0.4",
        "description": "标价 = 物理总成本 / 40% 毛利率基准",
        "sort_order": 10,
    },
    {
        "field_name": "daily_price",
        "display_name": "日常价",
        "expression": "标价 * 0.75",
        "description": "日常价 = 标价 × 75%",
        "sort_order": 20,
    },
    # 小促/中促/大促价 = 物理总成本 ÷ (基数 − 0.02抽佣 − 0.006税), 基数按 SKU 不同(定价总表 I/J/K 列)。
    # 不做全局公式 → 由定价页「改系数(仅这行)」按行编辑; 对齐时禁用旧的全局规则(REMOVED_RULE_FIELDS), 避免重算覆盖按行价。
    {
        "field_name": "install_cost",
        "display_name": "安装费",
        "expression": "IF(大小分类 == '大型', 150, IF(大小分类 == '中型', 100, IF(大小分类 == '小型', 0, 0)))",
        "description": "安装费 = 按尺寸 大150/中100/小0",
        "sort_order": 5,
    },
    {
        "field_name": "factory_cost",
        "display_name": "总出厂成本",
        "expression": "木作成本 + 包装成本 + 外采配件成本",
        "description": "总出厂成本 = 木作 + 打包 + 外采配件",
        "sort_order": 7,
    },
    {
        "field_name": "physical_cost",
        "display_name": "物理总成本",
        "expression": "物流费用 + 安装费 + 总出厂成本",
        "description": "物理总成本 = 物流 + 安装 + 总出厂",
        "sort_order": 8,
    },
    {
        "field_name": "platform_fee_rate",
        "display_name": "平台费",
        "expression": "大促价 * 0.006",
        "description": "平台费 = 大促价 × 0.6% (定价总表口径, 是金额)",
        "sort_order": 55,
    },
    {
        "field_name": "tax",
        "display_name": "税费",
        "expression": "大促价 * 0.02",
        "description": "税费 = 大促价 × 2%",
        "sort_order": 56,
    },
    {
        "field_name": "logistics_cost",
        "display_name": "物流费用",
        "expression": "IF(大小分类 == '大型', 700, IF(大小分类 == '中型', 300, IF(大小分类 == '小型', 80, 0)))",
        "description": "物流费 = 按尺寸 大700/中300/小80",
        "sort_order": 5,
    },
    {
        "field_name": "external_parts_cost",
        "display_name": "外采配件成本",
        "expression": "SUM(岩板, 抽屉轨道, 灯带, 玻璃, 电力轨道, 打包纸片, 铁销, 连接片, 铝合金轨道, 塑料轨道, mini把手, 免钉胶, 雕刻, 亚克力条, 预埋套杆, 理线架插排, 背板, 装饰条, 腿部, 软包, 床铺板, 其他配件)",
        "description": "外采配件成本 = 22项配件成本之和",
        "sort_order": 6,
    },
    {
        "field_name": "accounting_cost",
        "display_name": "会计总成本",
        "expression": "物理总成本 + 平台费率 + 税费",
        "description": "会计总成本 = 物理总成本 + 平台费 + 税费 (定价总表 N=O+P+Q)",
        "sort_order": 60,
    },
    {
        "field_name": "gross_margin_rate",
        "display_name": "毛利率",
        "expression": "大促利润 / 大促价",
        "description": "毛利率 = 大促利润 ÷ 大促价 (定价总表 M=L/K)",
        "sort_order": 70,
    },
    {
        "field_name": "big_promo_margin",
        "display_name": "大促利润",
        "expression": "大促价 - 会计总成本",
        "description": "大促利润 = 大促价 − 会计总成本 (定价总表 L=K-N)",
        "sort_order": 80,
    },
    {
        "field_name": "shop_internal_final",
        "display_name": "小促到手价",
        "expression": "日常价 * 单品立减系数",
        "description": "单品立减小促到手价 = 日常价 × 单品立减系数",
        "sort_order": 90,
    },
    {
        "field_name": "mid_buyer_price",
        "display_name": "中促到手价",
        "expression": "日常价 * 0.88 * 无国补中促系数",
        "description": "中促到手价 = 日常价 × 88折 × 中促系数",
        "sort_order": 100,
    },
    {
        "field_name": "mid_shop_receipt",
        "display_name": "中促店铺到账",
        "expression": "中促到手价 * 0.99",
        "description": "中促店铺到账 = 中促到手价 × 99% (1%手续费)",
        "sort_order": 110,
    },
    {
        "field_name": "mid_vip_final",
        "display_name": "中促会员价",
        "expression": "中促到手价 - 150",
        "description": "中促会员价 = 中促到手价 - 150元会员券",
        "sort_order": 120,
    },
    {
        "field_name": "big_buyer_price",
        "display_name": "大促到手价",
        "expression": "日常价 * 0.88 * 无国补大促系数",
        "description": "大促到手价 = 日常价 × 88折 × 大促系数",
        "sort_order": 130,
    },
    {
        "field_name": "big_shop_receipt",
        "display_name": "大促店铺到账",
        "expression": "大促到手价",
        "description": "大促店铺到账 = 大促到手价 (不含额外手续费)",
        "sort_order": 140,
    },
    {
        "field_name": "big_vip_final",
        "display_name": "大促会员价",
        "expression": "大促到手价 - 150",
        "description": "大促会员价 = 大促到手价 - 150元会员券",
        "sort_order": 150,
    },
    {
        "field_name": "xhs_promo_price",
        "display_name": "小红书促销价",
        "expression": "小红书活动价 * (1 - 小红书折扣)",
        "description": "小红书促销价 = 活动价 × (1 - 折扣率, 默认15%)",
        "sort_order": 160,
    },
]

# ---------------------------------------------------------------------------
# Safe AST evaluator
# ---------------------------------------------------------------------------

_ALLOWED_FUNCTIONS = {"IF", "SUM", "MIN", "MAX", "ABS", "ROUND"}


def _eval_compare(node: ast.AST, ctx: dict) -> bool:
    """Evaluate a comparison node, returning Python bool."""
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        right = _eval_node(node.comparators[0], ctx)
        op = node.ops[0]
        ops = {
            ast.Eq: lambda a, b: a == b,
            ast.NotEq: lambda a, b: a != b,
            ast.Lt: lambda a, b: a < b,
            ast.Gt: lambda a, b: a > b,
            ast.LtE: lambda a, b: a <= b,
            ast.GtE: lambda a, b: a >= b,
        }
        fn = ops.get(type(op))
        if fn is None:
            raise ValueError(f"不支持比较运算符: {type(op).__name__}")
        return fn(left, right)
    # Boolean fallback — evaluate as truthy
    return bool(_eval_node(node, ctx))


def _eval_node(node: ast.AST, ctx: dict) -> Any:
    """Recursively evaluate an AST node using ctx for variable resolution."""
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v  # str constants (e.g., '大型')

    if isinstance(node, ast.Name):
        name = node.id
        if name not in ctx:
            raise ValueError(f"未知字段名: {name!r}")
        val = ctx[name]
        if val is None:
            return None
        try:
            return Decimal(str(val))
        except (InvalidOperation, TypeError):
            return val  # return as-is (e.g. string fields like size_category)

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, ctx)
        right = _eval_node(node.right, ctx)
        if left is None or right is None:
            return None
        ops = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b if b else None,
            ast.Pow: lambda a, b: a ** b,
        }
        op_fn = ops.get(type(node.op))
        if not op_fn:
            raise ValueError(f"不支持运算符: {type(node.op).__name__}")
        return op_fn(left, right)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _eval_node(node.operand, ctx)
        return -val if val is not None else None

    if isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else None
        if func_name not in _ALLOWED_FUNCTIONS:
            raise ValueError(f"不支持函数: {func_name!r}")

        if func_name == "IF":
            if len(node.args) != 3:
                raise ValueError("IF() 需要3个参数: IF(条件, 真值, 假值)")
            cond = _eval_compare(node.args[0], ctx)
            return _eval_node(node.args[1], ctx) if cond else _eval_node(node.args[2], ctx)

        if func_name == "SUM":
            vals = [_eval_node(a, ctx) for a in node.args]
            return sum((v for v in vals if v is not None), Decimal(0))

        if func_name in ("MIN", "MAX"):
            vals = [_eval_node(a, ctx) for a in node.args]
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            return (min if func_name == "MIN" else max)(vals)

        if func_name == "ABS":
            if len(node.args) != 1:
                raise ValueError("ABS() 需要1个参数")
            val = _eval_node(node.args[0], ctx)
            return abs(val) if val is not None else None

        if func_name == "ROUND":
            if not node.args:
                raise ValueError("ROUND() 需要至少1个参数")
            val = _eval_node(node.args[0], ctx)
            places = int(_eval_node(node.args[1], ctx)) if len(node.args) > 1 else 2
            return round(val, places) if val is not None else None

    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


def build_context(sku, costs, promo) -> dict:
    """Build evaluation context mapping Chinese field names → values.

    Keys match the Chinese names used in expressions.
    """
    ctx: dict[str, Any] = {}
    obj_map = {"sku": sku, "costs": costs, "promo": promo}
    for cn_name, (obj_key, attr) in FIELD_MAP.items():
        obj = obj_map.get(obj_key)
        if obj is None:
            ctx[cn_name] = None
        else:
            ctx[cn_name] = getattr(obj, attr, None)
    return ctx


def eval_safe(expression: str, context: dict) -> Optional[Decimal]:
    """Safely evaluate a formula expression using AST parsing.

    Returns None if any required input is None or evaluation fails.
    Raises ValueError on parse/syntax errors.
    """
    # Preprocess: replace Chinese identifiers that contain special chars
    # (The AST parser treats Chinese chars as valid identifier chars in Python 3)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"公式语法错误: {e}") from e
    return _eval_node(tree.body, context)


def extract_field_names(expression: str) -> list[str]:
    """Parse expression and return all Name nodes (Chinese field names)."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_FUNCTIONS:
            if node.id not in names:
                names.append(node.id)
    return names


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

def get_topo_order(rules: list[PricingFormulaRule]) -> list[PricingFormulaRule]:
    """Return rules sorted so dependencies come before dependents.

    Falls back to sort_order for rules without inter-dependencies.
    """
    # Build field_name → rule map
    rule_by_field: dict[str, PricingFormulaRule] = {r.field_name: r for r in rules}

    # Build: field_name → set of dependency field_names (only those in rule_by_field)
    # We need to map Chinese names back to field_names
    cn_to_field: dict[str, str] = {}
    for cn_name, (obj_key, attr) in FIELD_MAP.items():
        cn_to_field[cn_name] = attr

    deps: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        cn_inputs = extract_field_names(rule.expression)
        for cn in cn_inputs:
            dep_field = cn_to_field.get(cn)
            if dep_field and dep_field in rule_by_field and dep_field != rule.field_name:
                deps[rule.field_name].add(dep_field)

    # DFS topological sort
    visited: set[str] = set()
    temp: set[str] = set()
    result: list[PricingFormulaRule] = []

    def visit(field_name: str):
        if field_name in temp:
            # Cycle detected — skip to avoid infinite loop
            log.warning("公式循环依赖检测到: %s，跳过", field_name)
            return
        if field_name in visited:
            return
        temp.add(field_name)
        for dep in deps.get(field_name, set()):
            visit(dep)
        temp.discard(field_name)
        visited.add(field_name)
        if field_name in rule_by_field:
            result.append(rule_by_field[field_name])

    # Sort by sort_order first so stable within same level
    for rule in sorted(rules, key=lambda r: r.sort_order):
        if rule.field_name not in visited:
            visit(rule.field_name)

    return result


# ---------------------------------------------------------------------------
# Main compute function
# ---------------------------------------------------------------------------

def compute_all(db: Session, sku, costs, promo, rules=None, force: bool = False) -> None:
    """Apply all enabled formula rules in topological order.

    Mutates sku / costs / promo in-place.
    Only overwrites NULL fields unless force=True.
    """
    if rules is None:
        rules = (
            db.query(PricingFormulaRule)
            .filter(PricingFormulaRule.enabled.is_(True))
            .all()
        )

    ordered = get_topo_order([r for r in rules if r.enabled])

    for rule in ordered:
        # Determine which object to write to
        obj_key = _FIELD_TO_OBJ.get(rule.field_name)
        if obj_key is None:
            log.debug("字段 %s 不在已知对象映射中，跳过", rule.field_name)
            continue

        obj = {"sku": sku, "costs": costs, "promo": promo}.get(obj_key)
        if obj is None:
            # Object (costs/promo) may not exist for this SKU
            continue

        current_val = getattr(obj, rule.field_name, None)
        if current_val is not None and not force:
            # Don't overwrite existing values unless forced
            continue

        # Rebuild context after each write so dependencies pick up fresh values
        ctx = build_context(sku, costs, promo)
        try:
            result = eval_safe(rule.expression, ctx)
            if result is not None:
                setattr(obj, rule.field_name, result)
        except Exception as exc:
            log.warning("公式计算失败 field=%s: %s", rule.field_name, exc)


# ---------------------------------------------------------------------------
# Seed built-in rules
# ---------------------------------------------------------------------------

def seed_builtin_rules(db: Session) -> int:
    """Insert BUILTIN_RULES if not already present. Returns count of newly inserted rows."""
    inserted = 0
    for rule_data in BUILTIN_RULES:
        existing = (
            db.query(PricingFormulaRule)
            .filter(PricingFormulaRule.field_name == rule_data["field_name"])
            .first()
        )
        if existing is None:
            rule = PricingFormulaRule(
                field_name=rule_data["field_name"],
                display_name=rule_data.get("display_name"),
                expression=rule_data["expression"],
                description=rule_data.get("description"),
                sort_order=rule_data.get("sort_order", 0),
                enabled=True,
                is_builtin=True,
            )
            db.add(rule)
            inserted += 1
    if inserted:
        db.commit()
    return inserted


# 这些字段按 SKU 基数不同(定价总表 I/J/K), 不做全局公式; 对齐时禁用其旧规则, 避免重算覆盖按行价。
REMOVED_RULE_FIELDS = {"small_promo", "mid_promo", "big_promo"}


def align_rules_to_builtin(db: Session) -> dict:
    """把现有公式规则对齐成 BUILTIN（更新表达式 / 插入缺失），并禁用 REMOVED_RULE_FIELDS。

    只改规则元数据，不动任何 SKU 的价格。对齐后引擎重算口径 = 定价总表口径。
    """
    by_field = {r.field_name: r for r in db.query(PricingFormulaRule).all()}
    updated = inserted = disabled = 0
    for rd in BUILTIN_RULES:
        ex = by_field.get(rd["field_name"])
        if ex is None:
            db.add(PricingFormulaRule(
                field_name=rd["field_name"], display_name=rd.get("display_name"),
                expression=rd["expression"], description=rd.get("description"),
                sort_order=rd.get("sort_order", 0), enabled=True, is_builtin=True,
            ))
            inserted += 1
        elif ex.expression != rd["expression"] or not ex.enabled:
            ex.expression = rd["expression"]
            ex.description = rd.get("description")
            ex.display_name = rd.get("display_name")
            ex.sort_order = rd.get("sort_order", 0)
            ex.enabled = True
            updated += 1
    for f in REMOVED_RULE_FIELDS:
        ex = by_field.get(f)
        if ex is not None and ex.enabled:
            ex.enabled = False
            disabled += 1
    db.commit()
    return {"updated": updated, "inserted": inserted, "disabled": disabled}
