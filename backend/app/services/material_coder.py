"""物料编码生成器。

业务规则 (plan §3 + 用户确认)：
    AC-0001 ~ AC-0999  标准配件
    AC-1000 ~          定制配件 (custom)
    新建定制物料时取 max(existing AC ≥ 1000) + 1，从 1000 起步。
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material

CUSTOM_PREFIX = "AC"
CUSTOM_THRESHOLD = 1000
CODE_RE = re.compile(r"^([A-Z]{2})-(\d+)$")


def parse_code(code: str) -> tuple[str, int] | None:
    m = CODE_RE.match((code or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def format_code(prefix: str, serial: int) -> str:
    return f"{prefix}-{serial:04d}"


def next_custom_code(db: Session, prefix: str = CUSTOM_PREFIX) -> str:
    """分配下一个定制编码。

    扫描数据库中 prefix-<n> 且 n >= CUSTOM_THRESHOLD 的最大序号，+1。
    如果一条都没有，返回 prefix-1000。
    """
    pattern = f"{prefix}-%"
    rows = db.execute(
        select(Material.code).where(Material.code.like(pattern))
    ).scalars()
    max_serial = CUSTOM_THRESHOLD - 1
    for code in rows:
        parsed = parse_code(code)
        if parsed is None:
            continue
        _, serial = parsed
        if serial >= CUSTOM_THRESHOLD and serial > max_serial:
            max_serial = serial
    return format_code(prefix, max_serial + 1)
