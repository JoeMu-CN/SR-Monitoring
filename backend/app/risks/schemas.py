from datetime import datetime

from pydantic import BaseModel


class RiskProcessResult(BaseModel):
    signal_id: int
    event_id: int
    event_created: bool
    signal_linked: bool
    alert_ids: list[int]


class RiskAlertRead(BaseModel):
    id: int
    level: str
    score: int
    score_detail: dict[str, object]
    status: str
    supplier_id: int
    supplier_name: str
    event_id: int
    event_type: str
    event_summary: str
    event_start_at: datetime | None
    event_end_at: datetime | None
    confidence: float
    match_type: str
    match_reasons: list[str]
    match_evidence: list[dict[str, object]]
    source_title: str
    source_url: str | None
    published_at: datetime | None
    updated_at: datetime


class RiskAlertListResponse(BaseModel):
    items: list[RiskAlertRead]
    total: int
    limit: int
    offset: int
