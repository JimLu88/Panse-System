#!/usr/bin/env python
"""一次性种入 3 个默认供应商.

容器内用法: docker compose exec api python scripts/seed_suppliers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 容器内 backend/__file__ = /app/scripts/, parent.parent = /app
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.services.supplier_seed import seed_default_suppliers  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        created = seed_default_suppliers(db)
        db.commit()
        print(f"已种入 {len(created)} 家供应商:")
        for s in created:
            print(f"  - [{s.supplier_type}] {s.name}")
        if not created:
            print("(全部已存在, 无变更)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
