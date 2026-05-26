from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class DataExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_table: str
    source_pk: Optional[str]
    exception_type: str
    severity: str
    description: str
    suggestion_action: Optional[str]
    context: Optional[dict[str, Any]]
    status: str
    created_at: datetime


class DataExceptionResolve(BaseModel):
    status: str  # resolved / ignored
    resolved_by: Optional[str] = None
