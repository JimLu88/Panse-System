"""Inspect or apply the exact audited YuLiBao opening repair."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal

from app.database import SessionLocal
from app.services import yulibao_opening_repair_service as service


def _json_default(value):
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="精确核验/修正2026-07-31余利宝期初缺口",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入三笔内部调拨分类、余利宝日终基准及审计回执；默认仅检查",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        try:
            result = service.repair(db, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except service.RepairScopeError as exc:
            db.rollback()
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - transaction must fail closed
            db.rollback()
            print(json.dumps({"ok": False, "error": f"unexpected:{exc}"}, ensure_ascii=False), file=sys.stderr)
            return 2

    print(json.dumps(result, ensure_ascii=False, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
