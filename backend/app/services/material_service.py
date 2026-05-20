"""物料服务。

核心方法 ensure_by_name(): 根据物料名称精确查找，缺则自动创建定制物料 (AC-1000+)，
并向 data_exceptions 写一条 missing_material_autocreated 条目提醒人工补价格 / 单位。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.services import exception_service, material_coder

CUSTOM_NAME_PREFIX = "定制"


@dataclass
class EnsureResult:
    material: Material
    created: bool


def get_by_name(db: Session, name: str) -> Optional[Material]:
    name = (name or "").strip()
    if not name:
        return None
    return db.execute(select(Material).where(Material.name == name)).scalar_one_or_none()


def ensure_by_name(db: Session, name: str) -> EnsureResult:
    """精确名称查找，缺失则自动建定制物料。

    用户确认的字段策略：「全部留空，进异常表等人工补」。
    所以只填 code/name/is_custom 三个字段；price/unit/size_type 留 NULL。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("material name is required")

    existing = get_by_name(db, name)
    if existing is not None:
        return EnsureResult(material=existing, created=False)

    code = material_coder.next_custom_code(db)
    display_name = name if name.startswith(CUSTOM_NAME_PREFIX) else f"{CUSTOM_NAME_PREFIX}{name}"

    # 极少数情况：拼上「定制」前缀后又撞到已有记录（例如别处已有同名定制条目），
    # 走名字查一遍兜底。
    existing = get_by_name(db, display_name)
    if existing is not None:
        return EnsureResult(material=existing, created=False)

    mat = Material(
        code=code,
        name=display_name,
        is_custom=True,
    )
    db.add(mat)
    db.flush()

    exception_service.record(
        db,
        source_table="materials",
        source_pk=code,
        exception_type="missing_material_autocreated",
        severity="warning",
        description=(
            f"录入「{name}」时未在物料库找到精确匹配，已自动创建定制物料 {code}。"
            f"请补全 单位 / 尺寸类型 / 价格 后再使用。"
        ),
        suggestion_action="fill_material_fields",
        context={"original_name": name, "assigned_code": code, "display_name": display_name},
    )
    return EnsureResult(material=mat, created=True)
