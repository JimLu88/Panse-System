from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import Material
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate

router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.get("", response_model=list[MaterialOut])
def list_materials(
    q: Optional[str] = Query(None, description="按编码或名称模糊"),
    is_custom: Optional[bool] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Material)
    if q:
        stmt = stmt.where(or_(Material.code.ilike(f"%{q}%"), Material.name.ilike(f"%{q}%")))
    if is_custom is not None:
        stmt = stmt.where(Material.is_custom == is_custom)
    stmt = stmt.order_by(Material.code).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=MaterialOut, status_code=201)
def create_material(payload: MaterialCreate, db: Session = Depends(get_db)):
    if payload.code:
        existing = db.execute(select(Material).where(Material.code == payload.code)).scalar_one_or_none()
        if existing:
            raise HTTPException(409, f"material code {payload.code} already exists")
    mat = Material(
        code=payload.code or "",
        name=payload.name,
        size_type=payload.size_type,
        unit=payload.unit,
        price=payload.price,
        remark=payload.remark,
        is_custom=False,
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)
    return mat


@router.patch("/{material_id}", response_model=MaterialOut)
def update_material(material_id: int, payload: MaterialUpdate, db: Session = Depends(get_db)):
    mat = db.get(Material, material_id)
    if not mat:
        raise HTTPException(404, "material not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(mat, k, v)
    db.commit()
    db.refresh(mat)
    return mat
