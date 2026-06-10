from __future__ import annotations

import json
from typing import Any


def dumps(obj: Any) -> bytes:
    # Keep it boring and debuggable for now.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))

