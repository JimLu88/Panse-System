from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import scanner_service

router = APIRouter(prefix="/api/scanners", tags=["scanners"])


class FindingOut(BaseModel):
    source_table: str
    source_pk: str
    exception_type: str
    severity: str
    description: str
    suggestion_action: str
    context: dict


class ScannerResultOut(BaseModel):
    scanner: str
    findings: list[FindingOut]
    written: int
    skipped_duplicate: int


@router.get("", response_model=list[str])
def list_scanners():
    return list(scanner_service.SCANNERS)


@router.post("/{name}/run", response_model=ScannerResultOut)
def run_one(name: str, dry_run: bool = False, db: Session = Depends(get_db)):
    try:
        r = scanner_service.run_scanner(db, name, dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if not dry_run:
        db.commit()
    return ScannerResultOut(
        scanner=r.scanner,
        findings=[FindingOut(**f.__dict__) for f in r.findings],
        written=r.written,
        skipped_duplicate=r.skipped_duplicate,
    )


@router.post("/run-all", response_model=dict[str, ScannerResultOut])
def run_all(dry_run: bool = False, db: Session = Depends(get_db)):
    results = scanner_service.run_all(db, dry_run=dry_run)
    if not dry_run:
        db.commit()
    return {
        name: ScannerResultOut(
            scanner=r.scanner,
            findings=[FindingOut(**f.__dict__) for f in r.findings],
            written=r.written,
            skipped_duplicate=r.skipped_duplicate,
        )
        for name, r in results.items()
    }
