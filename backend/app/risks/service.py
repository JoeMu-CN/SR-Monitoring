"""风险管线服务：AI 后分类门禁与规则引擎兼容入口。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.engine.engine import (
    _compute_expires_at,
    event_dedup_key,
    expire_alerts,
    match_suppliers,
    process_event,
)
from app.risks.engine.matching import MATCH_ORDER, MatchCandidate
from app.risks.schemas import RiskProcessResult
from app.risks.scoring import ScoringSettings
from app.risks.validity import (
    InactiveRiskSignalError,
    classify_pending_signal,
    is_raw_signal_effective,
)
from app.signals.models import RawSignal

__all__ = [
    "MATCH_ORDER",
    "MatchCandidate",
    "InactiveRiskSignalError",
    "_compute_expires_at",
    "event_dedup_key",
    "expire_alerts",
    "match_suppliers",
    "process_analysis",
    "process_event",
]


def process_analysis(
    session: Session,
    signal: RawSignal,
    analysis: AIAnalysisRecord,
    scoring: ScoringSettings | None = None,
    *,
    now_utc: datetime | None = None,
) -> RiskProcessResult:
    """同一事务完成 pending 分类，并仅让当前有效证据进入规则引擎。"""
    del scoring
    if analysis.status != "succeeded" or analysis.result is None:
        raise ValueError("AI 分析尚未成功")
    now = now_utc or datetime.now(UTC)
    result = SignalAnalysisResult.model_validate(analysis.result)
    classify_pending_signal(session, signal, analysis, result, now_utc=now)
    if not is_raw_signal_effective(signal, now_utc=now):
        session.commit()
        raise InactiveRiskSignalError(signal.id, str(signal.validity_reason["code"]))
    return process_event(session, signal, analysis, now_utc=now)
