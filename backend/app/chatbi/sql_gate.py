# -*- coding: utf-8 -*-
"""ChatBI 只读 SQL 安全闸门 —— 六道闸的第 2 道 (sqlglot AST 校验 + LIMIT 注入)。

背景 (Plan4 v2 §2.4/§4.6): P2SQL 注入研究 (ICSE'25) 证明 prompt 层防御全部可绕,
真防线必须建在 SQL AST 层与 DB 层。本模块只做 AST 静态校验, 不连库; DB 层防线
(只读角色 chatbi_ro / statement_timeout / EXPLAIN 干跑) 在 executor 侧。任何
LLM 生成 / 半生成的 SQL 执行前必须先过 validate_readonly_select()。

校验规则 (一条不能少):
  1. 单语句     —— 多语句 (分号注入 `SELECT 1; DROP ...`) 直接拒。
  2. 仅 SELECT  —— 顶层必须是 Select/Union; **再全树扫描**黑名单节点
                   (Insert/Update/Delete/Merge/Create/Drop/Alter/Truncate/
                    Command/Into/Lock/Set)。⚠坑1: PG 支持 data-modifying CTE
                   (`WITH x AS (DELETE ...) SELECT`), 顶层是 Select 但树里有
                   Delete, 故必须全树扫描而非只看顶层。
  3. 表白名单   —— 提取所有引用表, ⚠坑2: CTE 别名也会以 exp.Table 出现,
                   先剔除 CTE 别名; 剩余表全部必须在白名单视图内; 禁跨 schema。
  4. LIMIT 注入 —— 强制 min(用户LIMIT, MAX_ROWS); 无 LIMIT 则补上。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_ROWS = 1000

# 全树扫描的写操作/DDL/命令节点黑名单 (命中任一即拒)。用 getattr 兜版本差异
# (不同 sqlglot 版本个别节点命名不同, 缺失的跳过不报错)。
# exp.Command 兜底 GRANT/COPY/CALL/VACUUM 等未细分为专用节点的语句。
_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge",
    "Create", "Drop", "Alter", "TruncateTable",
    "Command", "Into", "Lock", "Set",
)
_FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)

# 允许的顶层查询节点类型 (Select / Union / 其它 Query 子类)。
_ALLOWED_TOP = tuple(
    t for t in (getattr(exp, "Select", None), getattr(exp, "Union", None),
                getattr(exp, "Query", None))
    if t is not None
)

_ALLOWED_SCHEMAS = {"", "public"}


class SqlGateError(ValueError):
    """SQL 未通过只读闸门 (reason 面向用户可读, 供前端展示拒答原因)。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GateResult:
    safe_sql: str                 # 注入 LIMIT 后的最终可执行 SQL
    tables: tuple[str, ...]       # 引用到的白名单表 (小写, 已排序)
    limited_to: int               # 实际生效的行数上限


def _parse_single(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise SqlGateError("空 SQL")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        raise SqlGateError(f"SQL 语法无法解析: {e}") from e
    if len(statements) == 0:
        raise SqlGateError("空 SQL")
    if len(statements) > 1:
        raise SqlGateError("只允许单条语句 (检测到多语句/分号注入)")
    return statements[0]


def _assert_select_only(tree: exp.Expression) -> None:
    if not isinstance(tree, _ALLOWED_TOP):
        raise SqlGateError(f"只允许 SELECT 查询 (顶层为 {type(tree).__name__})")
    for node_type in _FORBIDDEN_NODES:
        found = tree.find(node_type)
        if found is not None:
            raise SqlGateError(f"禁止的操作: {type(found).__name__} (只读问数不允许写/DDL/命令)")


def _referenced_tables(tree: exp.Expression) -> set[str]:
    """全树引用表 (剔除 CTE 别名), 小写; 遇非 public schema 直接拒。"""
    cte_names = {(c.alias or "").lower() for c in tree.find_all(exp.CTE) if c.alias}
    tables: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = (t.name or "").lower()
        if not name or name in cte_names:
            continue
        schema = (t.db or "").lower()
        if schema not in _ALLOWED_SCHEMAS:
            raise SqlGateError(f"禁止跨 schema 引用: {schema}.{name}")
        tables.add(name)
    return tables


def _existing_limit(tree: exp.Expression) -> int | None:
    limit_node = tree.args.get("limit") if hasattr(tree, "args") else None
    if limit_node is None:
        return None
    try:
        val = limit_node.expression
        if isinstance(val, exp.Literal) and val.is_int:
            return int(val.name)
    except Exception:  # noqa: BLE001
        return None
    return None


def validate_readonly_select(
    sql: str, allowed_tables: Iterable[str], max_rows: int = MAX_ROWS
) -> GateResult:
    """校验并加固一条只读 SELECT。

    通过 → 返回 GateResult (含注入 LIMIT 后的 safe_sql); 否则抛 SqlGateError(reason)。
    allowed_tables: 允许引用的表/视图名集合 (大小写不敏感)。
    """
    allowed = {str(t).lower() for t in allowed_tables}
    tree = _parse_single(sql)
    _assert_select_only(tree)
    tables = _referenced_tables(tree)
    if not tables:
        raise SqlGateError("查询未引用任何表 (可疑)")
    illegal = sorted(tables - allowed)
    if illegal:
        raise SqlGateError("引用了白名单外的表: " + ", ".join(illegal))
    existing = _existing_limit(tree)
    if existing is not None and existing <= max_rows:
        limited = existing
        safe_tree = tree
    else:
        limited = max_rows
        safe_tree = tree.limit(max_rows)
    return GateResult(
        safe_sql=safe_tree.sql(dialect="postgres"),
        tables=tuple(sorted(tables)),
        limited_to=limited,
    )


def is_readonly_select(sql: str, allowed_tables: Iterable[str], max_rows: int = MAX_ROWS) -> bool:
    """便捷布尔判定 (不需要 safe_sql 时用)。"""
    try:
        validate_readonly_select(sql, allowed_tables, max_rows)
        return True
    except SqlGateError:
        return False
