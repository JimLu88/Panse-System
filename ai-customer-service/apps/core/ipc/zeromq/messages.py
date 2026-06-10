from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonDict = dict[str, Any]

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PubMessage:
    v: int
    topic: str
    data: JsonDict

    def to_dict(self) -> JsonDict:
        return {"v": self.v, "topic": self.topic, "data": self.data}


@dataclass(frozen=True, slots=True)
class ReqMessage:
    v: int
    cmd: str
    args: JsonDict
    request_id: str

    def to_dict(self) -> JsonDict:
        return {"v": self.v, "cmd": self.cmd, "args": self.args, "request_id": self.request_id}


@dataclass(frozen=True, slots=True)
class RepMessage:
    v: int
    ok: bool
    request_id: str
    result: JsonDict | None = None
    error: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "v": self.v,
            "ok": self.ok,
            "request_id": self.request_id,
            "result": self.result,
            "error": self.error,
        }

