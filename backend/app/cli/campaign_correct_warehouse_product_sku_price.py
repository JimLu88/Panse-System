"""Retired warehouse-price CLI; always return the user-rule exclusion."""
from __future__ import annotations

import json
import sys

from app.services import campaign_warehouse_product_price_correction_service as service


def main() -> int:
    body = json.dumps(
        service.user_rule_excluded_result(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    sys.stdout.buffer.write(body + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
